"""Use cases входа: начало, завершение, проверка сессии и выход.

Провайдер скрыт за портом `OAuthProvider`, хранение — за `UnitOfWork`,
поэтому тесты обходятся фейками. Время, идентификаторы, `state` и значение
cookie приходят аргументами: случайность — дело слоя API.

`complete_login` возвращает исход, а не бросает исключение: каждая ошибка
входа — штатный редирект на `/login?error=…`, и роутер переводит исход в
ответ без `try`. Токен пользователя сохраняется при сессии — в той же
транзакции, зашифрованным (change `repositories-via-user-token`).
"""

import hmac
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.application.ports.oauth_provider import OAuthProvider
from app.application.ports.unit_of_work import UnitOfWork
from app.domain.entities import OAuthProfile, Secret, User
from app.domain.login import LoginError, OAuthProviderUnavailable, safe_redirect
from app.domain.provider_token import (
    ProviderTokenBusy,
    ProviderTokenUnavailable,
    token_from_grant,
)
from app.domain.session import hash_token, is_expired, open_session

_log = logging.getLogger(__name__)

# Код отказа пользователя в RFC 6749, §4.1.2.1. Остальные коды `error` —
# сбой на стороне провайдера.
_ACCESS_DENIED = "access_denied"


@dataclass(frozen=True, slots=True)
class LoginStart:
    authorize_url: str
    state: str
    redirect: str


@dataclass(frozen=True, slots=True)
class LoginSucceeded:
    token: str
    redirect: str


@dataclass(frozen=True, slots=True)
class LoginFailed:
    error: LoginError


@dataclass(frozen=True, slots=True)
class SignedIn:
    """Пользователь действующей сессии и её id — к сессии привязан токен провайдера."""

    user: User
    session_id: UUID


def start_login(
    provider: OAuthProvider, redirect: str | None, state: str
) -> LoginStart | LoginFailed:
    """Адрес авторизации и то, что слой API положит в cookie `oauth_state`.

    Ненастроенный провайдер сообщает об этом `OAuthProviderUnavailable`:
    вход тогда — штатная ошибка, а не редирект к GitHub с пустым `client_id`.
    """
    try:
        authorize_url = provider.authorize_url(state)
    except OAuthProviderUnavailable as error:
        # В сообщении причина без секретов: имя настройки, а не её значение.
        _log.warning("вход недоступен: %s", error)
        return LoginFailed(LoginError.PROVIDER_UNAVAILABLE)
    return LoginStart(authorize_url=authorize_url, state=state, redirect=safe_redirect(redirect))


def complete_login(
    uow: UnitOfWork,
    provider: OAuthProvider,
    *,
    state: str | None,
    expected_state: str | None,
    code: str | None,
    error: str | None,
    redirect: str | None,
    token: str,
    now: datetime,
    new_id: Callable[[], UUID],
    previous_token: str | None = None,
) -> LoginSucceeded | LoginFailed:
    """Сверить `state`, получить профиль и токен, записать пользователя, сессию и токен.

    `previous_token` — cookie сессии, с которой пришёл запрос: новый вход
    отзывает её в той же транзакции, чтобы старые сессии не копились (а с ней
    каскадом и её токен). Токен записать нельзя (не задан ключ шифрования) —
    входа нет: сессия без токена не открыла бы список репозиториев.
    """
    # Байты, а не `str`: `compare_digest` на нелатинском `str` бросает `TypeError`.
    # В логе причина без значений `state`, `code` и секретов: по ней видно,
    # где сломался вход на стенде. `error` пишет клиент, поэтому через `%r` и
    # с обрезкой: перевод строки не подделает запись лога.
    if not expected_state:
        _log.warning("вход не завершён: нет cookie oauth_state (истекла или не дошла)")
        return LoginFailed(LoginError.STATE_MISMATCH)
    if not state or not hmac.compare_digest(state.encode(), expected_state.encode()):
        _log.warning("вход не завершён: state не совпал с cookie oauth_state")
        return LoginFailed(LoginError.STATE_MISMATCH)
    if error == _ACCESS_DENIED:
        return LoginFailed(LoginError.DENIED)
    if error or not code:
        _log.warning("вход не завершён: провайдер вернул error=%r без code", (error or "")[:100])
        return LoginFailed(LoginError.PROVIDER_UNAVAILABLE)
    try:
        login = provider.exchange_code(code)
    except OAuthProviderUnavailable as failure:
        _log.warning("вход не завершён: %s", failure)
        return LoginFailed(LoginError.PROVIDER_UNAVAILABLE)

    # Провайдер опрошен до транзакции: соединение не ждёт ответа GitHub.
    try:
        with uow as work:
            user = _upsert_user(work, provider, login.profile, now, new_id)
            if previous_token:
                previous = work.sessions.find_by_token_hash(hash_token(previous_token))
                if previous is not None:
                    work.sessions.delete(previous.id)
            session = open_session(new_id(), user.id, token, now)
            work.sessions.add(session)
            work.provider_tokens.save(session.id, token_from_grant(login.grant, now))
            work.commit()
    except ProviderTokenUnavailable as failure:
        # Транзакция откатана: ни пользователя, ни сессии без токена.
        _log.warning("вход не завершён: %s", failure)
        return LoginFailed(LoginError.PROVIDER_UNAVAILABLE)
    return LoginSucceeded(token=token, redirect=safe_redirect(redirect))


def authenticate(uow: UnitOfWork, token: str | None, now: datetime) -> SignedIn | None:
    """Пользователь действующей сессии и её id; истёкшая сессия удаляется."""
    if not token:
        return None
    with uow as work:
        session = work.sessions.find_by_token_hash(hash_token(token))
        if session is None:
            return None
        if is_expired(session, now):
            work.sessions.delete(session.id)
            work.commit()
            return None
        user = work.users.get(session.user_id)
        return None if user is None else SignedIn(user=user, session_id=session.id)


def logout(uow: UnitOfWork, token: str | None) -> Secret | None:
    """Удалить сессию этой cookie, а с ней каскадом и токен провайдера.

    Возвращает access token удалённой сессии — его стоит отозвать у
    провайдера (best-effort, `revoke_quietly`), — или `None`, если отзывать
    нечего: нет cookie, сессии, токена или он не читается. Токен читается
    под блокировкой записи: параллельный refresh не подменит его между
    чтением и удалением, и отзывается тот токен, что жил последним. Если
    чужой refresh держит запись дольше предела, сессия всё равно удаляется,
    а отзыва нет. Нет cookie или сессии — нечего удалять, не ошибка.
    """
    if not token:
        return None
    with uow as work:
        session = work.sessions.find_by_token_hash(hash_token(token))
        if session is None:
            return None
        try:
            stored = work.provider_tokens.get_for_update(session.id)
        except ProviderTokenBusy:
            busy = True
        except ProviderTokenUnavailable:
            busy, stored = False, None
        else:
            busy = False
        if not busy:
            work.sessions.delete(session.id)
            work.commit()
            return None if stored is None else stored.access_token
    # Транзакция с ошибкой блокировки откатана; удаление дождётся чужого refresh.
    _log.info("выход: токен обновляется параллельно, отзыва не будет")
    with uow as work:
        work.sessions.delete(session.id)
        work.commit()
    return None

def _upsert_user(
    work: UnitOfWork,
    provider: OAuthProvider,
    profile: OAuthProfile,
    now: datetime,
    new_id: Callable[[], UUID],
) -> User:
    existing = work.users.find_by_provider(provider.provider, profile.provider_user_id)
    if existing is not None:
        return _refresh(work, existing, profile, now)
    user = User(
        id=new_id(),
        provider=provider.provider,
        provider_user_id=profile.provider_user_id,
        login=profile.login,
        name=profile.name,
        avatar_url=profile.avatar_url,
        created_at=now,
        updated_at=now,
    )
    if work.users.add(user):
        return user
    # Параллельный первый вход того же аккаунта успел раньше: его строка
    # уже есть, и мы обновляем её, а не создаём вторую.
    existing = work.users.find_by_provider(provider.provider, profile.provider_user_id)
    if existing is None:
        raise RuntimeError("пользователь не вставлен и не найден")
    return _refresh(work, existing, profile, now)


def _refresh(work: UnitOfWork, existing: User, profile: OAuthProfile, now: datetime) -> User:
    updated = replace(
        existing,
        login=profile.login,
        name=profile.name,
        avatar_url=profile.avatar_url,
        updated_at=now,
    )
    work.users.update(updated)
    return updated
