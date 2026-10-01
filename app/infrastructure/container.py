"""Composition root.

Единственное место, где порт связывается с адаптером. Выше слоя инфраструктуры
его никто не собирает, поэтому подмена реализации — правка здесь и больше
нигде. Все поля контейнера без значений по умолчанию: врывающееся поле с
дефолтом ломает порядок аргументов dataclass при следующем расширении.
"""

from dataclasses import dataclass

import httpx
from sqlalchemy import Engine, create_engine

from app.application.ports import JobQueue, UnitOfWork, VcsGateway
from app.config import Settings
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.queue.rabbitmq import PikaJobQueue
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth

_GITHUB_API = "https://api.github.com"


@dataclass(frozen=True, slots=True)
class Container:
    """Всё, что можно передать слою приложения."""

    engine: Engine
    settings: Settings
    # Заглушка ленивого адаптера до первого обращения. Кэш installation
    # tokens обязан пережить один вызов builder'а, иначе теряется смысл
    # кэша (design D7): поэтому шлюз создаётся один раз и запоминается
    # в frozen-контейнере через `object.__setattr__`.
    _vcs_gateway: VcsGateway | None

    def unit_of_work(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self.engine)

    def job_queue(self) -> JobQueue:
        """Собрать очередь задач; адаптер соединяется с брокером лениво,
        при первом `enqueue`, — старт без RabbitMQ не падает."""
        return PikaJobQueue(self.settings.rabbitmq_url)

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


def build_container(settings: Settings) -> Container:
    # pool_pre_ping: соединение, умершее в простое (перезапуск сервера, idle
    # timeout, NAT сбросил поток), обнаруживается при выдаче из пула и
    # заменяется, а не всплывает OperationalError на следующем запросе.
    return Container(
        engine=create_engine(settings.database_url, future=True, pool_pre_ping=True),
        settings=settings,
        _vcs_gateway=None,
    )
