"""Ошибки HTTP-слоя в формате RFC 9457 Problem Details (`application/problem+json`).

Тело ошибки описывают модели из `app/api/problems.py` — по модели на код из `ErrorCode`:

- `type` — `urn:dmc-268:problem:<код-в-kebab-case>`, постоянный для кода;
- `title` — короткое описание кода по-русски, тоже постоянное;
- `status` — HTTP-статус;
- `detail` — что случилось именно здесь, без эха пользовательского ввода;
- `instance` — путь запроса без query, как он пришёл в запросе: закодированный
  (`/api/v1/a%20b`), то есть URI-reference. Это не эхо ввода в тексте — запрет
  повторять переданное значение относится к `detail` и `errors`;
- расширения `code` (клиент ветвится по нему) и `trace_id` (тот же, что в заголовке
  `X-Trace-ID`);
- у `VALIDATION_ERROR` ещё `errors: [{where, reason}]`.

Ручка, которой нужен отказ, бросает `ApiError` — обработчик из `install` превращает
его в ответ. `HTTPException` ручки не бросают: он стал бы общим `HTTP_ERROR` без
своего кода и `detail`. Тело собирает один построитель — `problem_response` — через
модель кода; им же пользуются обработчики ошибок проверки параметров, `HTTPException`
фреймворка и необработанного исключения.

Ответы ошибок в схеме описывают словари `ERRORS_*` и `ERRORS_DEFAULT`: роуты подключают
их в `responses`. Тип содержимого `application/problem+json` и заголовок `X-Trace-ID`
схеме дописывает `app/api/openapi.py`.
"""

import logging
from collections.abc import Sequence
from http import HTTPStatus
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.problems import (
    PROBLEM_MODELS,
    AnyProblem,
    CommitNotFoundProblem,
    DiffTooLargeProblem,
    DiffUnavailableProblem,
    ErrorCode,
    NotImplementedProblem,
    OriginForbiddenProblem,
    Problem,
    ProviderNotSupportedProblem,
    RepositoryAlreadyConnectedProblem,
    RepositoryManageRequiredProblem,
    RepositoryNotFoundProblem,
    RepositoryNotInInstallationProblem,
    RequestBodyTooLargeProblem,
    ReviewAlreadyActiveProblem,
    ReviewNotFoundProblem,
    ScmUnavailableProblem,
    UnauthenticatedProblem,
    ValidationProblem,
    WebhookSignatureInvalidProblem,
)
from app.api.trace_id import HEADER as TRACE_ID_HEADER

logger = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"

ResponsesDoc = dict[int | str, dict[str, Any]]


def _doc(status: int | str, model: Any, description: str) -> ResponsesDoc:
    return {status: {"model": model, "description": description}}


# Ответы ошибок для `responses` роутов. У операции — её собственные коды; коды, возможные
# на любом запросе, покрывает `ERRORS_DEFAULT`, он подключён ко всему приложению.
ERRORS_DEFAULT = _doc(
    "default",
    AnyProblem,
    "Любая другая ошибка — тело из каталога, клиент ветвится по `code`. Так приходят "
    "`404 ROUTE_NOT_FOUND`, `405 METHOD_NOT_ALLOWED`, `413 REQUEST_BODY_TOO_LARGE`, `HTTP_ERROR` и "
    "`500 INTERNAL_ERROR`.",
)
ERRORS_413_BODY = _doc(
    HTTPStatus.CONTENT_TOO_LARGE,
    RequestBodyTooLargeProblem,
    "Тело больше лимита (вебхук — `WEBHOOK_BODY_MAX_BYTES`, 2 МиБ; остальные — `REQUEST_BODY_MAX_BYTES`, "
    "1 МиБ): по `Content-Length` отказ без чтения "
    "тела, поток без него обрывается на лимите. Ни разбора тела, ни сессии, ни подписи до отказа нет; "
    "соединение закрывается.",
)
ERRORS_422 = _doc(
    HTTPStatus.UNPROCESSABLE_CONTENT,
    ValidationProblem,
    "Параметр пути, запроса или тело не прошли проверку; в `errors` — где и почему, без самого значения.",
)
ERRORS_401_SESSION = _doc(
    HTTPStatus.UNAUTHORIZED,
    UnauthenticatedProblem,
    "Нет действительной сессии. Пришедшая недействительная cookie `session` удаляется в том же ответе.",
)
ERRORS_403_ORIGIN = _doc(
    HTTPStatus.FORBIDDEN, OriginForbiddenProblem, "`Origin` изменяющего запроса отсутствует или не совпадает с адресом сервиса."
)
ERRORS_404_REVIEW = _doc(HTTPStatus.NOT_FOUND, ReviewNotFoundProblem, "Прогона ревью с таким id нет.")
ERRORS_404_REPOSITORY = _doc(HTTPStatus.NOT_FOUND, RepositoryNotFoundProblem, "Репозитория с таким id нет.")
ERRORS_404_LOGIN_PROVIDER = _doc(HTTPStatus.NOT_FOUND, ProviderNotSupportedProblem, "Вход через этого провайдера не поддержан.")
ERRORS_409_REVIEW_ACTIVE = _doc(HTTPStatus.CONFLICT, ReviewAlreadyActiveProblem, "По этому коммиту уже идёт прогон ревью.")
ERRORS_401_WEBHOOK_SIGNATURE = _doc(
    HTTPStatus.UNAUTHORIZED, WebhookSignatureInvalidProblem, "Подписи доставки нет или она не прошла проверку; записей нет."
)
ERRORS_501_WEBHOOK_PROVIDER = _doc(
    HTTPStatus.NOT_IMPLEMENTED, ProviderNotSupportedProblem, "Провайдер есть в `Provider`, но приёмник вебхуков для него не написан."
)
ERRORS_502_SCM = _doc(
    HTTPStatus.BAD_GATEWAY, ScmUnavailableProblem, "Провайдер VCS недоступен или ответил ошибкой; прогон не создан."
)
ERRORS_502_SCM_REPOSITORIES = _doc(
    HTTPStatus.BAD_GATEWAY, ScmUnavailableProblem, "GitHub не ответил или ответил ошибкой; репозиторий не подключён."
)
ERRORS_502_SCM_EVENTS = _doc(
    HTTPStatus.BAD_GATEWAY,
    ScmUnavailableProblem,
    "GitHub не ответил или ответил ошибкой, когда считался набор видимых репозиториев или обновлялся токен пользователя; поток не открыт.",
)
ERRORS_502_SCM_VISIBILITY = _doc(
    HTTPStatus.BAD_GATEWAY,
    ScmUnavailableProblem,
    "GitHub не ответил или ответил ошибкой, когда считался набор видимых репозиториев или обновлялся токен пользователя.",
)
ERRORS_404_NOT_IN_INSTALLATION = _doc(
    HTTPStatus.NOT_FOUND,
    RepositoryNotInInstallationProblem,
    "Репозиторий не виден пользователю, приложение на нём не установлено или установка приостановлена — без различия; или провайдер не `github`.",
)
ERRORS_409_ALREADY_CONNECTED = _doc(
    HTTPStatus.CONFLICT, RepositoryAlreadyConnectedProblem, "Репозиторий уже подключён, в том числе параллельным запросом; отвечается только пользователю с правами admin или maintain."
)
# Один статус — две причины: `Origin` проверяет middleware, права — ручка. Модель ответа —
# объединение с discriminator по `code`, в схеме это `oneOf`; ключ `responses` у статуса
# один, поэтому `{**ERRORS_403_ORIGIN, **…}` не годится — второй словарь затёр бы первый.
ERRORS_403_ORIGIN_OR_MANAGE = _doc(
    HTTPStatus.FORBIDDEN,
    Annotated[OriginForbiddenProblem | RepositoryManageRequiredProblem, Field(discriminator="code")],
    "`ORIGIN_FORBIDDEN` — `Origin` отсутствует или не совпадает с адресом сервиса; "
    "`REPOSITORY_MANAGE_REQUIRED` — у пользователя нет прав admin или maintain на репозиторий у провайдера.",
)
# Дифф прогона: у `404` и `422` по два кода — объединения по `code`, как у `ERRORS_403_ORIGIN_OR_MANAGE`.
ERRORS_404_REVIEW_OR_NOT_IN_INSTALLATION = _doc(
    HTTPStatus.NOT_FOUND,
    Annotated[ReviewNotFoundProblem | RepositoryNotInInstallationProblem, Field(discriminator="code")],
    "`REVIEW_NOT_FOUND` — прогона с таким id нет или он не виден пользователю; "
    "`REPOSITORY_NOT_IN_INSTALLATION` — приложение не установлено на репозиторий прогона или установка "
    "приостановлена, дифф у GitHub не запрашивался.",
)
ERRORS_409_DIFF_UNAVAILABLE = _doc(
    HTTPStatus.CONFLICT,
    DiffUnavailableProblem,
    "У прогона не записан base_sha — пары коммитов для диффа нет; запросов к GitHub не было.",
)
ERRORS_410_COMMIT_NOT_FOUND = _doc(
    HTTPStatus.GONE,
    CommitNotFoundProblem,
    "GitHub ответил 404 на сравнение base_sha...head_sha прогона: коммита больше нет (например, после force-push).",
)
ERRORS_422_OR_DIFF_TOO_LARGE = _doc(
    HTTPStatus.UNPROCESSABLE_CONTENT,
    Annotated[ValidationProblem | DiffTooLargeProblem, Field(discriminator="code")],
    "`VALIDATION_ERROR` — параметр пути не прошёл проверку, в `errors` — где и почему; "
    "`DIFF_TOO_LARGE` — дифф прогона больше `RUN_DIFF_MAX_BYTES` (2 МиБ), тело ответа GitHub дальше лимита не читалось.",
)
ERRORS_502_SCM_DIFF = _doc(
    HTTPStatus.BAD_GATEWAY,
    ScmUnavailableProblem,
    "GitHub не ответил или ответил ошибкой (`5xx`, отказ лимита), когда считался набор видимых репозиториев, "
    "обновлялся токен пользователя, запрашивалась установка приложения на репозиторий или дифф.",
)
ERRORS_501_NOT_IMPLEMENTED = _doc(HTTPStatus.NOT_IMPLEMENTED, NotImplementedProblem, "Ручка ещё не реализована.")


# Что `quote` оставляет как есть: уже закодированные `%XX`, разделители пути и
# допустимые в нём символы RFC 3986. Остальное — пробел, управляющие символы,
# байты вне ASCII — кодируется, поэтому `instance` всегда URI-reference и в
# строку лога не попадёт перевод строки.
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"


def request_path(request: Request) -> str:
    """Путь запроса без query в том виде, в каком он пришёл: `/api/v1/a%20b`.

    Берётся из `raw_path` ASGI, а не из `request.url.path`: тот уже раскодирован
    (`/api/v1/a b`) и URI-reference не является. Если сервер `raw_path` не дал,
    путь кодируется заново.
    """
    raw_path = request.scope.get("raw_path")
    if isinstance(raw_path, bytes):
        path_bytes = raw_path.partition(b"?")[0]
    else:
        path_bytes = str(request.scope.get("path", "")).encode("utf-8")
    return quote(path_bytes, safe=_PATH_SAFE) or "/"


def problem_response(
    request: Request,
    status: int,
    code: ErrorCode,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
    errors: list[dict[str, str]] | None = None,
) -> JSONResponse:
    """Единственный построитель тела ошибки — через модель кода из `app/api/problems.py`.

    `type`, `title` и `code` берутся из модели; статус, не разрешённый моделью кода
    (например, `REVIEW_NOT_FOUND` с `409`), — ошибка в коде, `ValidationError`.
    """
    trace_id: str = request.state.trace_id
    fields: dict[str, Any] = {
        "status": status,
        "detail": detail,
        "instance": request_path(request),
        "trace_id": trace_id,
    }
    if errors is not None:
        fields["errors"] = errors
    problem: Problem = PROBLEM_MODELS[code].model_validate(fields)
    # Заголовок ставит и middleware, но ответ на необработанное исключение
    # отдаётся снаружи всех middleware — поэтому он ставится и здесь.
    return JSONResponse(
        status_code=status,
        content=problem.model_dump(mode="json"),
        media_type=PROBLEM_MEDIA_TYPE,
        headers={**(headers or {}), TRACE_ID_HEADER: trace_id},
    )


class ApiError(Exception):
    """Отказ ручки: статус, код из `ErrorCode` и `detail` этого случая."""

    def __init__(self, status_code: int, code: ErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail

    def finish(self, response: Response) -> None:
        """Дописать в ответ то, что нужно этому отказу кроме тела (например, удалить cookie)."""


def _api_error_handler(request: Request, error: Exception) -> Response:
    if not isinstance(error, ApiError):
        raise error
    response = problem_response(request, error.status_code, error.code, error.detail)
    error.finish(response)
    return response


# --- 422: параметры не прошли проверку -------------------------------------------

# Причины по типу ошибки pydantic. Текст pydantic не берём: он по-английски и
# для части типов цитирует ввод.
_REASONS = {
    "missing": "обязательный параметр не передан",
    "extra_forbidden": "лишнее поле",
    "int_parsing": "ожидается целое число",
    "int_type": "ожидается целое число",
    "int_parsing_size": "число слишком большое",
    "uuid_parsing": "ожидается UUID",
    "enum": "значение не из допустимого списка",
    "literal_error": "значение не из допустимого списка",
    "string_type": "ожидается строка",
    "model_type": "ожидается объект",
    "model_attributes_type": "ожидается объект",
    "json_invalid": "тело запроса — не JSON",
    # Свой тип `PydanticCustomError`: диапазон, где начало позже конца (`ReviewRunListQuery`).
    "range_reversed": "начало диапазона позже его конца",
    # Свой тип `PydanticCustomError`: повтор паттерна в фильтре веток (`RepositorySettingsUpdate`).
    "duplicate_patterns": "паттерны веток повторяются",
    "timezone_aware": "ожидается время с зоной",
    # Свой тип `PydanticCustomError`: время во входе — только строка ISO 8601 (`UtcDatetimeParam`).
    "datetime_not_iso": "ожидается время в ISO 8601",
    # Свои типы `PydanticCustomError` входных параметров (`app/api/schemas.py`).
    "datetime_out_of_range": "время вне допустимого диапазона",
    "string_nul": "строка не должна содержать символ NUL",
}
_DEFAULT_REASON = "недопустимое значение"
# Ошибки, у которых последний элемент `loc` — не поле схемы: имя лишнего поля (ввод клиента,
# эхом не отдаём) или смещение в битом JSON (`body.5` читалось бы как поле `5`). `where`
# указывает на объект, где ошибка, — без этого элемента.
_LOC_TAIL_DROPPED = frozenset({"extra_forbidden", "json_invalid"})


def _reason(error: dict[str, Any]) -> str:
    kind = error.get("type")
    ctx = error.get("ctx") or {}
    if kind == "greater_than_equal":
        return f"должно быть не меньше {ctx.get('ge')}"
    if kind == "greater_than":
        return f"должно быть больше {ctx.get('gt')}"
    if kind == "less_than_equal":
        return f"должно быть не больше {ctx.get('le')}"
    if kind == "less_than":
        return f"должно быть меньше {ctx.get('lt')}"
    if kind == "string_too_short":
        return f"длина строки должна быть не меньше {ctx.get('min_length')}"
    if kind == "string_too_long":
        return f"длина строки должна быть не больше {ctx.get('max_length')}"
    if kind == "too_short":
        return f"элементов должно быть не меньше {ctx.get('min_length')}"
    if kind == "too_long":
        return f"элементов должно быть не больше {ctx.get('max_length')}"
    return _REASONS.get(str(kind), _DEFAULT_REASON)


def _where(error: dict[str, Any]) -> str:
    location = list(error.get("loc", ()))
    if error.get("type") in _LOC_TAIL_DROPPED and location:
        location.pop()
    return ".".join(str(part) for part in location) or "запрос"


def validation_errors(errors: Sequence[Any]) -> list[dict[str, str]]:
    """`[{"where": "query.limit", "reason": "ожидается целое число"}]` — без самих значений.

    Ключи от клиента в `loc` попадают только у лишних полей: словарей с ключами клиента во
    входных моделях нет. Появится такое поле — его тип ошибки добавляется в `_LOC_TAIL_DROPPED`.
    """
    return [{"where": _where(error), "reason": _reason(error)} for error in errors]


def _validation_error_handler(request: Request, error: Exception) -> Response:
    if not isinstance(error, RequestValidationError):
        raise error
    errors = validation_errors(error.errors())
    return problem_response(
        request,
        HTTPStatus.UNPROCESSABLE_CONTENT,
        ErrorCode.VALIDATION_ERROR,
        f"Параметры запроса не прошли проверку, ошибок: {len(errors)}.",
        errors=errors,
    )


# --- HTTPException фреймворка: 404 и 405 маршрутизатора и прочие статусы -----------


def _http_exception_handler(request: Request, error: Exception) -> Response:
    """`HTTPException` Starlette и FastAPI — тоже problem.

    `404` неизвестного пути и `405` неразрешённого метода бросает маршрутизатор. Прочие
    статусы бросает сам фреймворк (например, `400` на неразборчивое тело формы) или
    сторонняя зависимость (`401` схем безопасности FastAPI, `429` ограничителя частоты).
    Им отдельный код не положен — они получают общий `HTTP_ERROR` со статусом
    исключения. Текст `detail` исключения в тело не идёт: он по-английски и может
    повторять ввод. Заголовки исключения (`Allow`, `Retry-After`, `WWW-Authenticate`)
    сохраняются. Статус вне 4xx/5xx — ошибка в коде, она уходит в `500`.
    """
    if not isinstance(error, StarletteHTTPException):
        raise error
    headers = dict(error.headers or {})
    if error.status_code == HTTPStatus.NOT_FOUND:
        return problem_response(
            request, HTTPStatus.NOT_FOUND, ErrorCode.ROUTE_NOT_FOUND, "По этому пути ресурса нет.", headers=headers
        )
    if error.status_code == HTTPStatus.METHOD_NOT_ALLOWED:
        return problem_response(
            request,
            HTTPStatus.METHOD_NOT_ALLOWED,
            ErrorCode.METHOD_NOT_ALLOWED,
            "Метод для этого пути не разрешён; разрешённые — в заголовке Allow.",
            headers=headers,
        )
    if 400 <= error.status_code <= 599:
        return problem_response(
            request,
            error.status_code,
            ErrorCode.HTTP_ERROR,
            f"Запрос отклонён со статусом {error.status_code}.",
            headers=headers,
        )
    raise error


# --- 500: необработанное исключение ---------------------------------------------


def _internal_error_handler(request: Request, error: Exception) -> Response:
    """Ответ на необработанное исключение — без текста исключения и стектрейса.

    Стектрейс пишется в лог — ровно одной записью уровня error: с `trace_id`
    (поле записи ставит `TraceContextFilter` из текущего спана запроса — ответ
    отдаётся снаружи middleware, но ещё внутри спана встроенной телеметрии
    FastAPI), методом и путём без query атрибутами semconv. После ответа
    Starlette поднимает исключение дальше, и uvicorn пишет его ещё раз («Exception
    in ASGI application»), уже без `trace_id`; этот дубль отсекает
    `UvicornDuplicateFilter` (`app/infrastructure/logging.py`).
    """
    response = problem_response(
        request,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        ErrorCode.INTERNAL_ERROR,
        "Сервер не смог обработать запрос. Если ошибка повторяется, сообщите trace_id.",
    )
    logger.error(
        "Необработанное исключение",
        exc_info=error,
        extra={
            "http.request.method": request.method,
            "url.path": request_path(request),
        },
    )
    return response


def install(app: FastAPI) -> None:
    """Подключить обработчики всех ошибок приложения."""
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _internal_error_handler)
