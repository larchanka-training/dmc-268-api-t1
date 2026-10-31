"""Сквозной стенд: вебхук → API → база → RabbitMQ → воркер → модель → GitHub.

Всё внутри границ сервиса - боевой код: эндпоинт приёма, use case'ы,
`SqlAlchemyUnitOfWork`, `RabbitMQJobQueue`, consume-цикл `Worker`,
`run_review`, промпты из `prompts/review/`, `GitHubAppAuth` и
`GitHubVcsGateway`. Подменены только внешние системы, и только на своей
границе: GitHub - на уровне HTTP (`FakeGitHub` за `httpx.MockTransport`),
модель - за портом `LlmGateway` (записывающий фейк в E2E, живая модель в eval,
заглушка с задержкой в нагрузочном прогоне).

API и воркер получают каждый свой контейнер, как два процесса в проде: общие у
них только база, брокер и «GitHub».
"""

import hashlib
import hmac
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx2
import pika
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Engine

from app.api.factory import create_app
from app.application.ports.llm_gateway import LlmGateway
from app.config import Settings
from app.domain.entities import (
    ContextPayload,
    Finding,
    PublishedComment,
    Repository,
    ReviewRun,
    ReviewRunStep,
)
from app.domain.enums import Provider
from app.infrastructure.cache.memory import InMemoryCacheStore
from app.infrastructure.container import WORKER_VCS_LIMITS, Container
from app.infrastructure.db.run_changes import PostgresRunChanges, listen_conninfo
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.parsing.tree_sitter_extractor import (
    TreeSitterDefinitionExtractor,
)
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR
from app.infrastructure.queue.rabbitmq import (
    DLQ_NAME,
    QUEUE_NAME,
    RabbitMQJobQueue,
    declare_topology,
)
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from app.worker.factory import (
    build_review_handler,
    build_worker,
    context_limits,
    llm_limits,
    load_checked_prompt_texts,
)

from ..fakes.github_api import (
    APP_ID,
    INSTALLATION_ID,
    REPO_FULL_NAME,
    REPO_PROVIDER_ID,
    FakeGitHub,
    PullRequest,
    app_private_key,
)
from ..fakes.installation_catalog import FakeInstallationCatalog

WEBHOOK_SECRET = "e2e-webhook-secret"
WEBHOOK_PATH = "/api/v1/webhooks/github"
# Дольше любого шага прогона на заглушке модели; превышение - зависание, а не медленный тест.
WORK_TIMEOUT_SECONDS = 60.0
# Сколько ждём остановки воркера после отмены консьюмера.
CANCEL_JOIN_SECONDS = 10.0


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def webhook_body(pull: PullRequest, action: str = "opened") -> bytes:
    """Тело `pull_request` в той форме, что шлёт GitHub; поля - те, что читает приём."""
    payload: dict[str, Any] = {
        "action": action,
        "number": pull.number,
        "installation": {"id": INSTALLATION_ID},
        "repository": {"id": int(REPO_PROVIDER_ID), "full_name": REPO_FULL_NAME},
        "pull_request": {
            "number": pull.number,
            "title": pull.title,
            "body": pull.body or None,
            "user": {"login": pull.author},
            "author_association": "MEMBER",
            "head": {"sha": pull.head_sha, "ref": pull.head_ref, "repo": {"id": int(REPO_PROVIDER_ID)}},
            "base": {"sha": pull.base_sha, "ref": pull.base_ref},
        },
    }
    return json.dumps(payload).encode("utf-8")


def webhook_headers(body: bytes, delivery: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sign(body),
    }


@dataclass
class Stack:
    settings: Settings
    engine: Engine
    github: FakeGitHub
    rabbitmq_url: str

    @classmethod
    def build(
        cls, engine: Engine, rabbitmq_url: str, *, github_latency_seconds: float = 0.0
    ) -> Stack:
        private_key = app_private_key()
        settings = Settings(
            database_url=str(engine.url),
            rabbitmq_url=rabbitmq_url,
            github_webhook_secret=SecretStr(WEBHOOK_SECRET),
            github_app_id=APP_ID,
            github_app_private_key=SecretStr(private_key),
            llm_backend="stub",
        )
        github = FakeGitHub(
            REPO_FULL_NAME,
            INSTALLATION_ID,
            private_key,
            latency_seconds=github_latency_seconds,
        )
        stack = cls(settings=settings, engine=engine, github=github, rabbitmq_url=rabbitmq_url)
        stack._register_repository()
        stack.purge_queues()
        return stack

    # --- процессы --------------------------------------------------------

    def container(self, llm: LlmGateway | None = None) -> Container:
        """Контейнер одного процесса: свои HTTP-клиент GitHub, соединение с брокером и кэш."""
        client = self.github.client()
        auth = GitHubAppAuth(
            APP_ID, self.settings.github_app_private_key.get_secret_value(), client
        )
        return Container(
            engine=self.engine,
            settings=self.settings,
            # Вход и подключение репозиториев стенд не проходит: токенов пользователя нет.
            _token_cipher=None,
            _job_queue=RabbitMQJobQueue(self.rabbitmq_url),
            _vcs_gateway=GitHubVcsGateway(auth, client, sleep=lambda _: None),
            _vcs_limits=WORKER_VCS_LIMITS,
            _installation_catalog=FakeInstallationCatalog([]),
            _oauth_providers={},
            _cache_store=InMemoryCacheStore(),
            _run_changes=PostgresRunChanges(listen_conninfo(self.settings.database_url)),
            _llm_gateway=llm,
            _definition_extractor=TreeSitterDefinitionExtractor(),
            _app_slug_retry_at=0.0,
        )

    def api(self) -> FastAPI:
        app = create_app(self.settings)
        app.state.container = self.container()
        return app

    def work(
        self, llm: LlmGateway, messages: int, *, timeout: float = WORK_TIMEOUT_SECONDS
    ) -> None:
        """Воркер обрабатывает `messages` сообщений из очереди и останавливается.

        Consume-цикл без сообщений крутится вечно, поэтому он идёт в потоке с
        дедлайном: зависание роняет тест, а не весь прогон CI.
        """
        container = self.container(llm)
        handler = build_review_handler(
            container,
            context_limits(self.settings),
            llm_limits(self.settings),
            load_checked_prompt_texts(self.settings, DEFAULT_PROMPTS_DIR),
        )
        worker = build_worker(self.settings)
        errors: list[BaseException] = []

        def consume() -> None:
            try:
                worker.run(handler, limit=messages)
            except BaseException as error:  # noqa: BLE001 — переносится в поток теста
                errors.append(error)

        def cancel() -> None:
            # Число возвращённых сообщений не нужно; из чужого потока это единственный безопасный вызов.
            worker.channel.cancel()

        thread = threading.Thread(target=consume, name="e2e-worker", daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            # Без отмены консьюмера поток крутит цикл до конца сессии и заберёт сообщение
            # следующего теста со старой фейковой моделью.
            worker.connection.add_callback_threadsafe(cancel)
            thread.join(CANCEL_JOIN_SECONDS)
            raise AssertionError(f"воркер не обработал {messages} сообщений за {timeout:g} с")
        if errors:
            raise errors[0]

    # --- брокер ----------------------------------------------------------

    @contextmanager
    def _channel(self) -> Iterator[pika.adapters.blocking_connection.BlockingChannel]:
        connection = pika.BlockingConnection(pika.URLParameters(self.rabbitmq_url))
        try:
            channel = connection.channel()
            declare_topology(channel)
            yield channel
        finally:
            connection.close()

    def purge_queues(self) -> None:
        with self._channel() as channel:
            channel.queue_purge(QUEUE_NAME)
            channel.queue_purge(DLQ_NAME)

    def queue_depth(self, name: str = QUEUE_NAME) -> int:
        with self._channel() as channel:
            count = channel.queue_declare(name, passive=True).method.message_count
        assert count is not None
        return count

    # --- база ------------------------------------------------------------

    def _register_repository(self) -> None:
        now = datetime.now(UTC)
        with SqlAlchemyUnitOfWork(self.engine) as work:
            work.repositories.add(
                Repository(
                    id=UUID(int=0x700100),
                    provider=Provider.GITHUB,
                    provider_id=REPO_PROVIDER_ID,
                    full_name=REPO_FULL_NAME,
                    default_branch="develop",
                    auto_review_enabled=True,
                    created_at=now,
                    updated_at=now,
                    connected_at=now,
                )
            )
            work.commit()

    def run(self, run_id: str | UUID) -> ReviewRun:
        with SqlAlchemyUnitOfWork(self.engine) as work:
            run = work.review_runs.get(UUID(str(run_id)))
        assert run is not None, f"прогона {run_id} нет в базе"
        return run

    def unfinished_runs(self) -> int:
        with SqlAlchemyUnitOfWork(self.engine) as work:
            return len(work.review_runs.list_unfinished())

    def published(self, run_id: str | UUID) -> list[PublishedComment]:
        with SqlAlchemyUnitOfWork(self.engine) as work:
            return work.published_comments.list_for_run(UUID(str(run_id)))

    def steps(self, run_id: str | UUID) -> list[ReviewRunStep]:
        with SqlAlchemyUnitOfWork(self.engine) as work:
            return work.review_run_steps.list_for_run(UUID(str(run_id)))

    def findings(self, run_id: str | UUID) -> list[Finding]:
        with SqlAlchemyUnitOfWork(self.engine) as work:
            return work.findings.list_for_run(UUID(str(run_id)))

    def context(self, run_id: str | UUID) -> ContextPayload:
        with SqlAlchemyUnitOfWork(self.engine) as work:
            (payload,) = work.context_payloads.list_for_run(UUID(str(run_id)))
        return payload


def deliver(
    client: httpx2.Client, pull: PullRequest, *, delivery: str = "", action: str = "opened"
) -> httpx2.Response:
    """Подписанная доставка вебхука `pull_request` по HTTP."""
    body = webhook_body(pull, action)
    headers = webhook_headers(body, delivery or f"delivery-{pull.number}-{pull.head_sha[:7]}")
    return client.post(WEBHOOK_PATH, content=body, headers=headers)
