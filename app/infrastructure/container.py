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
from app.application.ports.installation_catalog import InstallationCatalog
from app.application.ports.job_queue import JobQueue
from app.application.ports.llm_gateway import LlmGateway
from app.application.ports.oauth_provider import OAuthProvider
from app.application.ports.vcs_gateway import VcsGateway
from app.config import Settings
from app.infrastructure.cache.memory import InMemoryCacheStore
from app.infrastructure.db.token_cipher import TokenCipher
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork, UntracedUnitOfWork
from app.infrastructure.llm.fallback import FallbackLlmGateway
from app.infrastructure.llm.ollama import OllamaGateway
from app.infrastructure.llm.openai_compatible import OpenAiCompatibleGateway
from app.infrastructure.llm.stub import StubLlmGateway
from app.infrastructure.oauth.github import GitHubOAuthProvider
from app.infrastructure.parsing.tree_sitter_extractor import (
    TreeSitterDefinitionExtractor,
)
from app.infrastructure.queue.rabbitmq import RabbitMQJobQueue
from app.infrastructure.vcs.cached_installations import CachedInstallationCatalog
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from app.infrastructure.vcs.github_installations import GitHubInstallationCatalog

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
# Запросы от имени пользователя (вход, refresh, отзыв, список репозиториев):
# таймаут на каждую фазу httpx, подключение — короче. Refresh держит
# блокировку строки токена, и худший случай — сумма фаз, а не 10 секунд.
_GITHUB_USER_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
# Callback входа: путь роутера `/auth`, зарегистрированный в настройках GitHub App.
_OAUTH_CALLBACK_PATH = "/api/v1/auth/{provider}/callback"


@dataclass(frozen=True, slots=True)
class Container:
    """Всё, что можно передать слою приложения."""

    engine: Engine
    settings: Settings
    # Ключ шифрования токенов пользователя; `None` — не задан, и токены не
    # пишутся и не читаются (вход отказывает), остальное работает.
    _token_cipher: TokenCipher | None
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
    # Репозитории, видимые пользователю, — запросы с его токеном; App-реквизиты
    # и кэш installation tokens ему не нужны. Конструирование — без сети.
    _installation_catalog: InstallationCatalog
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
        return SqlAlchemyUnitOfWork(self.engine, self._token_cipher)

    def untraced_unit_of_work(self) -> UnitOfWork:
        """Единица работы без спанов SQL — для опроса в потоке событий."""
        return UntracedUnitOfWork(self.engine, self._token_cipher)

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

    def installation_catalog(self) -> InstallationCatalog:
        """Репозитории установок GitHub App, видимые пользователю по его токену."""
        return self._installation_catalog

    def visibility_catalog(self, catalog: InstallationCatalog) -> InstallationCatalog:
        """`catalog` с кэшем списка видимых репозиториев — для видимости прогонов и репозиториев.

        Каталог — аргументом, а не свой: тесты подменяют каталог зависимостью API,
        и кэш должен лечь поверх подменённого.
        """
        return CachedInstallationCatalog(
            catalog, self._cache_store, self.settings.visible_repositories_cache_ttl_seconds
        )

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
    # hide_parameters: текст ошибки SQLAlchemy не несёт `[parameters: …]` — в
    # параметрах бывает код из диффа, а текст ошибки попадает в лог и в спан
    # (`record_exception`, статус спана).
    return Container(
        engine=create_engine(
            settings.database_url, future=True, pool_pre_ping=True, hide_parameters=True
        ),
        settings=settings,
        _token_cipher=_token_cipher(settings),
        _job_queue=RabbitMQJobQueue(settings.rabbitmq_url),
        _vcs_gateway=None,
        _installation_catalog=_build_installation_catalog(settings),
        _oauth_providers={
            "github": GitHubOAuthProvider(
                settings.github_app_client_id,
                settings.github_app_client_secret.get_secret_value(),
                settings.public_origin + _OAUTH_CALLBACK_PATH.format(provider="github"),
                httpx.Client(timeout=_GITHUB_USER_TIMEOUT),
            ),
        },
        _cache_store=InMemoryCacheStore(),
        _llm_gateway=None,
        _definition_extractor=None,
    )


def _build_installation_catalog(settings: Settings) -> GitHubInstallationCatalog:
    """Свой клиент и свой `GitHubAppAuth`: каталогу нужен только JWT приложения
    (проверка установки), кэш installation tokens шлюза ему не нужен.
    Конструирование — без сети."""
    client = httpx.Client(base_url=_GITHUB_API, timeout=_GITHUB_USER_TIMEOUT)
    auth = GitHubAppAuth(settings.github_app_id, settings.github_app_private_key.get_secret_value(), client)
    return GitHubInstallationCatalog(client, auth)


def _token_cipher(settings: Settings) -> TokenCipher | None:
    # Предупреждение о пустом ключе пишет только API (`app/api/factory.py`):
    # контейнер собирает и воркер, а ему ключ не нужен и не передаётся.
    key = settings.user_token_key
    return None if key is None else TokenCipher(key)


# Подключение в нашей сети или к hosted-провайдеру дольше этого - сбой, а не
# медленная модель: выключенный хост Ollama иначе держал бы вызов весь таймаут
# ответа (change `llm-gateway-review-fixes`, design D3).
_LLM_CONNECT_TIMEOUT_SECONDS = 10.0


def llm_http_timeout(settings: Settings) -> httpx.Timeout:
    """Таймауты HTTP к провайдерам модели: ответ - из настроек, подключение - свой."""
    return httpx.Timeout(settings.llm_timeout_seconds, connect=_LLM_CONNECT_TIMEOUT_SECONDS)


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
    timeout = llm_http_timeout(settings)
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
