"""Composition root.

Единственное место, где порт связывается с адаптером. Выше слоя инфраструктуры
его никто не собирает, поэтому подмена реализации — правка здесь и больше
нигде. Все поля контейнера без значений по умолчанию: врывающееся поле с
дефолтом ломает порядок аргументов dataclass при следующем расширении.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass

import httpx
from sqlalchemy import Engine, create_engine

from app.application.ports import CacheStore, DefinitionExtractor, UnitOfWork
from app.application.ports.job_queue import JobQueue
from app.application.ports.llm_gateway import LlmGateway
from app.application.ports.oauth_provider import OAuthProvider
from app.application.ports.vcs_gateway import VcsGateway
from app.config import Settings
from app.infrastructure.cache.memory import InMemoryCacheStore
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.llm.fallback import FallbackLlmGateway
from app.infrastructure.llm.ollama import OllamaGateway
from app.infrastructure.llm.openai_compatible import OpenAiCompatibleGateway
from app.infrastructure.llm.stub import StubLlmGateway
from app.infrastructure.oauth.github import GitHubOAuthProvider
from app.infrastructure.parsing.tree_sitter_extractor import (
    TreeSitterDefinitionExtractor,
)
from app.infrastructure.queue.rabbitmq import RabbitMQJobQueue
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
# Callback входа: путь роутера `/auth`, зарегистрированный в настройках GitHub App.
_OAUTH_CALLBACK_PATH = "/api/v1/auth/{provider}/callback"


@dataclass(frozen=True, slots=True)
class Container:
    """Всё, что можно передать слою приложения."""

    engine: Engine
    settings: Settings
    # Один экземпляр на процесс, а не фабрика: `RabbitMQJobQueue` сам держит
    # переиспользуемое AMQP-соединение, и это работает, только если каждый
    # запрос получает один и тот же объект, а не новый на каждый вызов
    # `job_queue()`.
    _job_queue: JobQueue
    # Заглушка ленивого адаптера до первого обращения. Кэш installation
    # tokens обязан пережить один вызов builder'а, иначе теряется смысл
    # кэша (design D7): поэтому шлюз создаётся один раз и запоминается
    # в frozen-контейнере через `object.__setattr__`.
    _vcs_gateway: VcsGateway | None
    # Провайдеры входа по имени из пути `/auth/{provider}`. Конструирование —
    # без сети, поэтому они собираются сразу, а не лениво.
    _oauth_providers: Mapping[str, OAuthProvider]
    # In-process кэш живёт, пока жив процесс, — только если он один на
    # контейнер: фабрика на каждый вызов теряла бы всё между прогонами.
    _cache_store: CacheStore
    # Ленивый, как VCS-шлюз: процессу API параметры модели не нужны, а
    # HTTP-клиенты провайдеров создаются один раз на процесс.
    _llm_gateway: LlmGateway | None
    # Ленивый, как VCS-шлюз: процессу API парсеры грамматик не нужны.
    _definition_extractor: DefinitionExtractor | None

    def unit_of_work(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self.engine)

    def job_queue(self) -> JobQueue:
        """Выдать очередь задач; соединение с брокером открывается лениво,
        при первом `enqueue`, — старт без RabbitMQ не падает."""
        return self._job_queue

    def llm_gateway(self) -> LlmGateway:
        """Собрать адаптер модели при первом обращении; конструирование - без сети."""
        gateway = self._llm_gateway
        if gateway is None:
            gateway = _build_llm_gateway(self.settings)
            object.__setattr__(self, "_llm_gateway", gateway)
        return gateway

    def vcs_gateway(self) -> VcsGateway:
        """Собрать VCS-шлюз при первом обращении; конструирование — без сети."""
        gateway = self._vcs_gateway
        if gateway is None:
            client = httpx.Client(base_url=_GITHUB_API)
            auth = GitHubAppAuth(
                self.settings.github_app_id,
                self.settings.github_app_private_key.get_secret_value(),
                client,
            )
            gateway = GitHubVcsGateway(auth, client)
            object.__setattr__(self, "_vcs_gateway", gateway)
        return gateway

    def oauth_provider(self, name: str) -> OAuthProvider | None:
        """Провайдер входа по имени из пути; неизвестное имя — `None`."""
        return self._oauth_providers.get(name)

    def cache_store(self) -> CacheStore:
        return self._cache_store

    def definition_extractor(self) -> DefinitionExtractor:
        extractor = self._definition_extractor
        if extractor is None:
            extractor = TreeSitterDefinitionExtractor()
            object.__setattr__(self, "_definition_extractor", extractor)
        return extractor


def build_container(settings: Settings) -> Container:
    # pool_pre_ping: соединение, умершее в простое (перезапуск сервера, idle
    # timeout, NAT сбросил поток), обнаруживается при выдаче из пула и
    # заменяется, а не всплывает OperationalError на следующем запросе.
    return Container(
        engine=create_engine(settings.database_url, future=True, pool_pre_ping=True),
        settings=settings,
        _job_queue=RabbitMQJobQueue(settings.rabbitmq_url),
        _vcs_gateway=None,
        _oauth_providers={
            "github": GitHubOAuthProvider(
                settings.github_app_client_id,
                settings.github_app_client_secret.get_secret_value(),
                settings.public_origin + _OAUTH_CALLBACK_PATH.format(provider="github"),
                httpx.Client(timeout=10.0),
            ),
        },
        _cache_store=InMemoryCacheStore(),
        _llm_gateway=None,
        _definition_extractor=None,
    )


def _build_llm_gateway(settings: Settings) -> LlmGateway:
    """Порядок провайдеров не выбирается: локальная модель первая, hosted -
    только вторым и только по флагу (change `add-llm-gateway`, design D3).

    Итог пишется в лог: по нему видно, может ли код проверяемых
    репозиториев уйти за пределы нашей сети.
    """
    if settings.llm_backend is None:
        raise RuntimeError("LLM_BACKEND не задан: выберите stub или ollama")
    if settings.llm_backend == "stub":
        logger.info("модель: заглушка без похода к модели")
        return StubLlmGateway()
    timeout = httpx.Timeout(settings.llm_timeout_seconds)
    providers: list[tuple[str, LlmGateway]] = [
        (
            "Ollama",
            OllamaGateway(
                httpx.Client(base_url=settings.ollama_base_url, timeout=timeout),
                settings.ollama_model,
                window_tokens=settings.llm_context_window_tokens,
                max_attempts=settings.llm_max_attempts,
            ),
        )
    ]
    local = f"модель: Ollama ({settings.ollama_model})"
    if not settings.llm_hosted_fallback:
        logger.info("%s; hosted-фоллбэк выключен", local)
        return FallbackLlmGateway(providers)
    providers.append(
        (
            "Eurorouter",
            OpenAiCompatibleGateway(
                httpx.Client(base_url=settings.eurorouter_base_url, timeout=timeout),
                settings.eurorouter_model,
                settings.eurorouter_keys,
                name="Eurorouter",
                max_attempts=settings.llm_max_attempts,
            ),
        )
    )
    logger.info(
        "%s; фоллбэк - Eurorouter (%s): при сбое локальной модели код уходит hosted-провайдеру",
        local,
        settings.eurorouter_model,
    )
    return FallbackLlmGateway(providers)
