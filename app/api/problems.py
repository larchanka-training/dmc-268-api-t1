"""Модели тела ошибки — RFC 9457 Problem Details (`application/problem+json`).

Источник правды для формата ошибок: из этих моделей FastAPI строит схему
(`/openapi.json`, `docs/openapi/openapi.json`), и через них же `problem_response`
собирает тело ответа, поэтому ответ и схема не расходятся.

У каждого кода своя модель: `type`, `title`, `code` и, где он один, `status` — константы
со значением по умолчанию. Значение по умолчанию делает модель единственным местом, где
записан `title` кода, а `json_schema_serialization_defaults_required` оставляет эти поля
обязательными в схеме — клиент получает их в каждом ответе.
"""

from enum import StrEnum
from http import HTTPStatus
from typing import Annotated, Any, ClassVar, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, RootModel

TRACE_ID_PATTERN = "^[0-9a-f]{32}$"
_TRACE_ID_EXAMPLE = "4bf92f3577b34da6a3ce929d0e0e4736"
_TYPE_PREFIX = "urn:dmc-268:problem:"


class ErrorCode(StrEnum):
    """Закрытый список кодов ошибок HTTP-слоя — расширение `code` в теле ошибки."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    REVIEW_NOT_FOUND = "REVIEW_NOT_FOUND"
    REPOSITORY_NOT_FOUND = "REPOSITORY_NOT_FOUND"
    REPOSITORY_NOT_IN_INSTALLATION = "REPOSITORY_NOT_IN_INSTALLATION"
    REPOSITORY_ALREADY_CONNECTED = "REPOSITORY_ALREADY_CONNECTED"
    REPOSITORY_MANAGE_REQUIRED = "REPOSITORY_MANAGE_REQUIRED"
    PROVIDER_NOT_SUPPORTED = "PROVIDER_NOT_SUPPORTED"
    REVIEW_ALREADY_ACTIVE = "REVIEW_ALREADY_ACTIVE"
    WEBHOOK_SIGNATURE_INVALID = "WEBHOOK_SIGNATURE_INVALID"
    SCM_UNAVAILABLE = "SCM_UNAVAILABLE"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    ORIGIN_FORBIDDEN = "ORIGIN_FORBIDDEN"
    ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    HTTP_ERROR = "HTTP_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


def problem_type(code: str) -> str:
    """`VALIDATION_ERROR` → `urn:dmc-268:problem:validation-error`."""
    return _TYPE_PREFIX + code.lower().replace("_", "-")


def _example_from_defaults(schema: dict[str, Any], model: type[BaseModel]) -> None:
    """`json_schema_extra` моделей кода: пример тела из констант модели и её `example`.

    `type`, `title`, `code` и, где он один, `status` берутся из значений по умолчанию полей;
    в `example` модели — только своё: `detail`, `instance`, статус, если их у кода несколько,
    и поля сверх общих (`errors`).
    """
    example: dict[str, Any] | None = getattr(model, "example", None)
    if example is None:
        return
    defaults = {name: field.default for name, field in model.model_fields.items() if not field.is_required()}
    schema["examples"] = [
        {
            "type": defaults["type"],
            "title": defaults["title"],
            "status": int(example.get("status") or defaults["status"]),
            "detail": example["detail"],
            "instance": example["instance"],
            "code": defaults["code"].value,
            "trace_id": _TRACE_ID_EXAMPLE,
            **{key: value for key, value in example.items() if key not in {"status", "detail", "instance"}},
        }
    ]


class Problem(BaseModel):
    """Тело любой ошибки — RFC 9457 Problem Details, тип содержимого `application/problem+json`.

    `type` и `title` постоянны для кода, `detail` — что случилось именно здесь, без
    значений из запроса. Расширения: `code` (клиент ветвится по нему, а не по `title`
    или `detail`) и `trace_id`.
    """

    model_config = ConfigDict(
        json_schema_serialization_defaults_required=True, json_schema_extra=_example_from_defaults
    )

    # Пример тела для схемы — только то, что не следует из констант модели (см. `_example_from_defaults`).
    example: ClassVar[dict[str, Any] | None] = None

    type: str = Field(
        description="URI типа проблемы: `urn:dmc-268:problem:<код в kebab-case>`.",
        json_schema_extra={"format": "uri"},
    )
    title: str = Field(description="Короткое описание кода по-русски, постоянное для кода.")
    status: int = Field(ge=400, le=599, description="HTTP-статус ответа.")
    detail: str = Field(description="Что случилось в этом запросе, по-русски, без значений из запроса.")
    instance: str = Field(
        description=(
            "Путь запроса без query в том виде, в каком он пришёл, — закодированный "
            "(`/api/v1/a%20b`). Это ссылка на запрос, а не эхо ввода: запрет повторять "
            "переданное значение относится к `detail` и `errors`."
        ),
        json_schema_extra={"format": "uri-reference"},
    )
    code: ErrorCode = Field(description="Код ошибки из закрытого списка.")
    trace_id: str = Field(
        pattern=TRACE_ID_PATTERN,
        description=(
            "Trace id запроса — 32 строчных hex-символа (формат trace-id W3C Trace Context). "
            "Тот же, что в заголовке `X-Trace-ID` и в строке лога сервера о сбое."
        ),
        examples=[_TRACE_ID_EXAMPLE],
    )


class ValidationErrorItem(BaseModel):
    """Одна ошибка проверки: где и почему, без самого значения."""

    model_config = ConfigDict(extra="forbid")

    where: str = Field(
        description="Часть запроса и имя через точку: `query.limit`, `path.review_run_id`, `body.trigger`."
    )
    reason: str = Field(description="Почему — по-русски, без самого значения.")


class ValidationProblem(Problem):
    """`422 VALIDATION_ERROR`: параметр пути, запроса или тело не прошли проверку."""

    example = {
        "detail": "Параметры запроса не прошли проверку, ошибок: 1.",
        "instance": "/api/v1/repositories",
        "errors": [{"where": "query.limit", "reason": "ожидается целое число"}],
    }

    type: Literal["urn:dmc-268:problem:validation-error"] = "urn:dmc-268:problem:validation-error"
    title: Literal["Запрос не прошёл проверку"] = "Запрос не прошёл проверку"
    status: Literal[HTTPStatus.UNPROCESSABLE_CONTENT] = HTTPStatus.UNPROCESSABLE_CONTENT
    code: Literal[ErrorCode.VALIDATION_ERROR] = ErrorCode.VALIDATION_ERROR
    errors: list[ValidationErrorItem] = Field(min_length=1, description="Где ошибка и почему.")


class ReviewNotFoundProblem(Problem):
    """`404 REVIEW_NOT_FOUND`: прогона ревью с таким id нет."""

    example = {
        "detail": "Прогона ревью с таким id нет.",
        "instance": "/api/v1/reviews/0b9d6c1e-6a7f-4b8e-9c3d-2e1f0a9b8c7d",
    }

    type: Literal["urn:dmc-268:problem:review-not-found"] = "urn:dmc-268:problem:review-not-found"
    title: Literal["Прогон ревью не найден"] = "Прогон ревью не найден"
    status: Literal[HTTPStatus.NOT_FOUND] = HTTPStatus.NOT_FOUND
    code: Literal[ErrorCode.REVIEW_NOT_FOUND] = ErrorCode.REVIEW_NOT_FOUND


class RepositoryNotFoundProblem(Problem):
    """`404 REPOSITORY_NOT_FOUND`: репозитория с таким id нет."""

    example = {
        "detail": "Репозитория с таким id нет.",
        "instance": "/api/v1/repositories/0b9d6c1e-6a7f-4b8e-9c3d-2e1f0a9b8c7d/pull-requests",
    }

    type: Literal["urn:dmc-268:problem:repository-not-found"] = "urn:dmc-268:problem:repository-not-found"
    title: Literal["Репозиторий не найден"] = "Репозиторий не найден"
    status: Literal[HTTPStatus.NOT_FOUND] = HTTPStatus.NOT_FOUND
    code: Literal[ErrorCode.REPOSITORY_NOT_FOUND] = ErrorCode.REPOSITORY_NOT_FOUND


class RepositoryNotInInstallationProblem(Problem):
    """`404 REPOSITORY_NOT_IN_INSTALLATION`: репозитория нет среди репозиториев установок GitHub App."""

    example = {
        "detail": "Репозитория нет среди репозиториев установок GitHub App.",
        "instance": "/api/v1/repositories",
    }

    type: Literal["urn:dmc-268:problem:repository-not-in-installation"] = (
        "urn:dmc-268:problem:repository-not-in-installation"
    )
    title: Literal["Репозиторий не из установки приложения"] = "Репозиторий не из установки приложения"
    status: Literal[HTTPStatus.NOT_FOUND] = HTTPStatus.NOT_FOUND
    code: Literal[ErrorCode.REPOSITORY_NOT_IN_INSTALLATION] = ErrorCode.REPOSITORY_NOT_IN_INSTALLATION


class RepositoryAlreadyConnectedProblem(Problem):
    """`409 REPOSITORY_ALREADY_CONNECTED`: репозиторий уже зарегистрирован в сервисе."""

    example = {
        "detail": "Репозиторий уже подключён.",
        "instance": "/api/v1/repositories",
    }

    type: Literal["urn:dmc-268:problem:repository-already-connected"] = (
        "urn:dmc-268:problem:repository-already-connected"
    )
    title: Literal["Репозиторий уже подключён"] = "Репозиторий уже подключён"
    status: Literal[HTTPStatus.CONFLICT] = HTTPStatus.CONFLICT
    code: Literal[ErrorCode.REPOSITORY_ALREADY_CONNECTED] = ErrorCode.REPOSITORY_ALREADY_CONNECTED


class RepositoryManageRequiredProblem(Problem):
    """`403 REPOSITORY_MANAGE_REQUIRED`: у пользователя нет прав admin или maintain на репозиторий у провайдера."""

    example = {
        "detail": "Подключать репозиторий может только пользователь с правами admin или maintain на него.",
        "instance": "/api/v1/repositories",
    }

    type: Literal["urn:dmc-268:problem:repository-manage-required"] = "urn:dmc-268:problem:repository-manage-required"
    title: Literal["Нужны права admin или maintain на репозиторий"] = "Нужны права admin или maintain на репозиторий"
    status: Literal[HTTPStatus.FORBIDDEN] = HTTPStatus.FORBIDDEN
    code: Literal[ErrorCode.REPOSITORY_MANAGE_REQUIRED] = ErrorCode.REPOSITORY_MANAGE_REQUIRED


class ProviderNotSupportedProblem(Problem):
    """`PROVIDER_NOT_SUPPORTED`: вход через провайдера — `404`, приём вебхуков от него — `501`."""

    example = {
        "status": HTTPStatus.NOT_FOUND,
        "detail": "Вход через этого провайдера не поддержан.",
        "instance": "/api/v1/auth/bitbucket/login",
    }

    type: Literal["urn:dmc-268:problem:provider-not-supported"] = "urn:dmc-268:problem:provider-not-supported"
    title: Literal["Провайдер не поддержан"] = "Провайдер не поддержан"
    status: Literal[HTTPStatus.NOT_FOUND, HTTPStatus.NOT_IMPLEMENTED]
    code: Literal[ErrorCode.PROVIDER_NOT_SUPPORTED] = ErrorCode.PROVIDER_NOT_SUPPORTED


class ReviewAlreadyActiveProblem(Problem):
    """`409 REVIEW_ALREADY_ACTIVE`: ручной запуск, когда прогон по этому коммиту уже идёт.

    Вебхук на тот же случай отвечает `202` с существующим прогоном.
    """

    example = {
        "detail": "По этому коммиту уже идёт прогон ревью.",
        "instance": "/api/v1/reviews",
    }

    type: Literal["urn:dmc-268:problem:review-already-active"] = "urn:dmc-268:problem:review-already-active"
    title: Literal["Прогон по этому коммиту уже идёт"] = "Прогон по этому коммиту уже идёт"
    status: Literal[HTTPStatus.CONFLICT] = HTTPStatus.CONFLICT
    code: Literal[ErrorCode.REVIEW_ALREADY_ACTIVE] = ErrorCode.REVIEW_ALREADY_ACTIVE


class WebhookSignatureInvalidProblem(Problem):
    """`401 WEBHOOK_SIGNATURE_INVALID`: подписи вебхука нет или она не сошлась."""

    example = {
        "detail": "Подписи X-Hub-Signature-256 нет, или она не совпала с телом доставки.",
        "instance": "/api/v1/webhooks/github",
    }

    type: Literal["urn:dmc-268:problem:webhook-signature-invalid"] = "urn:dmc-268:problem:webhook-signature-invalid"
    title: Literal["Подпись вебхука не прошла проверку"] = "Подпись вебхука не прошла проверку"
    status: Literal[HTTPStatus.UNAUTHORIZED] = HTTPStatus.UNAUTHORIZED
    code: Literal[ErrorCode.WEBHOOK_SIGNATURE_INVALID] = ErrorCode.WEBHOOK_SIGNATURE_INVALID


class ScmUnavailableProblem(Problem):
    """`502 SCM_UNAVAILABLE`: провайдер VCS не ответил или ответил ошибкой — при приёме вебхука или подключении репозитория."""

    example = {
        "detail": "Провайдер VCS не ответил или ответил ошибкой; прогон не создан.",
        "instance": "/api/v1/webhooks/github",
    }

    type: Literal["urn:dmc-268:problem:scm-unavailable"] = "urn:dmc-268:problem:scm-unavailable"
    title: Literal["Провайдер VCS недоступен"] = "Провайдер VCS недоступен"
    status: Literal[HTTPStatus.BAD_GATEWAY] = HTTPStatus.BAD_GATEWAY
    code: Literal[ErrorCode.SCM_UNAVAILABLE] = ErrorCode.SCM_UNAVAILABLE


class UnauthenticatedProblem(Problem):
    """`401 UNAUTHENTICATED`: нет действительной сессии; пришедшая недействительная cookie удаляется."""

    example = {
        "detail": "Нет действительной сессии.",
        "instance": "/api/v1/auth/me",
    }

    type: Literal["urn:dmc-268:problem:unauthenticated"] = "urn:dmc-268:problem:unauthenticated"
    title: Literal["Требуется вход"] = "Требуется вход"
    status: Literal[HTTPStatus.UNAUTHORIZED] = HTTPStatus.UNAUTHORIZED
    code: Literal[ErrorCode.UNAUTHENTICATED] = ErrorCode.UNAUTHENTICATED


class OriginForbiddenProblem(Problem):
    """`403 ORIGIN_FORBIDDEN`: изменяющий запрос пришёл без `Origin` или с чужим; запрос не выполнен."""

    example = {
        "detail": "Изменяющий запрос пришёл без Origin или с Origin другого сайта.",
        "instance": "/api/v1/auth/logout",
    }

    type: Literal["urn:dmc-268:problem:origin-forbidden"] = "urn:dmc-268:problem:origin-forbidden"
    title: Literal["Запрос с чужого Origin"] = "Запрос с чужого Origin"
    status: Literal[HTTPStatus.FORBIDDEN] = HTTPStatus.FORBIDDEN
    code: Literal[ErrorCode.ORIGIN_FORBIDDEN] = ErrorCode.ORIGIN_FORBIDDEN


class RouteNotFoundProblem(Problem):
    """`404 ROUTE_NOT_FOUND`: такого пути нет, в том числе вне `/api/v1`."""

    example = {
        "detail": "По этому пути ресурса нет.",
        "instance": "/api/v1/nope",
    }

    type: Literal["urn:dmc-268:problem:route-not-found"] = "urn:dmc-268:problem:route-not-found"
    title: Literal["Путь не найден"] = "Путь не найден"
    status: Literal[HTTPStatus.NOT_FOUND] = HTTPStatus.NOT_FOUND
    code: Literal[ErrorCode.ROUTE_NOT_FOUND] = ErrorCode.ROUTE_NOT_FOUND


class MethodNotAllowedProblem(Problem):
    """`405 METHOD_NOT_ALLOWED`: путь есть, а метод для него не объявлен; разрешённые — в заголовке `Allow`."""

    example = {
        "detail": "Метод для этого пути не разрешён; разрешённые — в заголовке Allow.",
        "instance": "/api/v1/webhooks/github",
    }

    type: Literal["urn:dmc-268:problem:method-not-allowed"] = "urn:dmc-268:problem:method-not-allowed"
    title: Literal["Метод не разрешён"] = "Метод не разрешён"
    status: Literal[HTTPStatus.METHOD_NOT_ALLOWED] = HTTPStatus.METHOD_NOT_ALLOWED
    code: Literal[ErrorCode.METHOD_NOT_ALLOWED] = ErrorCode.METHOD_NOT_ALLOWED


class NotImplementedProblem(Problem):
    """`501 NOT_IMPLEMENTED`: ручка описана в контракте, но ещё не реализована."""

    example = {
        "detail": "Ручной запуск ревью ещё не реализован.",
        "instance": "/api/v1/reviews",
    }

    type: Literal["urn:dmc-268:problem:not-implemented"] = "urn:dmc-268:problem:not-implemented"
    title: Literal["Не реализовано"] = "Не реализовано"
    status: Literal[HTTPStatus.NOT_IMPLEMENTED] = HTTPStatus.NOT_IMPLEMENTED
    code: Literal[ErrorCode.NOT_IMPLEMENTED] = ErrorCode.NOT_IMPLEMENTED


class HttpErrorProblem(Problem):
    """`HTTP_ERROR`: прочий отказ фреймворка или зависимости со статусом, у которого нет своего кода.

    Например, `400` на неразборчивое тело или `429`. Статус — как у отказа, его заголовки
    (`Retry-After` и др.) сохраняются.
    """

    example = {
        "status": HTTPStatus.TOO_MANY_REQUESTS,
        "detail": "Запрос отклонён со статусом 429.",
        "instance": "/api/v1/repositories",
    }

    type: Literal["urn:dmc-268:problem:http-error"] = "urn:dmc-268:problem:http-error"
    title: Literal["Ошибка запроса"] = "Ошибка запроса"
    code: Literal[ErrorCode.HTTP_ERROR] = ErrorCode.HTTP_ERROR


class InternalErrorProblem(Problem):
    """`500 INTERNAL_ERROR`: необработанная ошибка сервера.

    Деталей в теле нет; сервер пишет строку лога с `trace_id`, методом и путём — искать по `trace_id`.
    """

    example = {
        "detail": "Сервер не смог обработать запрос. Если ошибка повторяется, сообщите trace_id.",
        "instance": "/api/v1/repositories",
    }

    type: Literal["urn:dmc-268:problem:internal-error"] = "urn:dmc-268:problem:internal-error"
    title: Literal["Внутренняя ошибка сервера"] = "Внутренняя ошибка сервера"
    status: Literal[HTTPStatus.INTERNAL_SERVER_ERROR] = HTTPStatus.INTERNAL_SERVER_ERROR
    code: Literal[ErrorCode.INTERNAL_ERROR] = ErrorCode.INTERNAL_ERROR


class AnyProblem(
    RootModel[
        Annotated[
            ValidationProblem
            | ReviewNotFoundProblem
            | RepositoryNotFoundProblem
            | RepositoryNotInInstallationProblem
            | RepositoryAlreadyConnectedProblem
            | RepositoryManageRequiredProblem
            | ProviderNotSupportedProblem
            | ReviewAlreadyActiveProblem
            | WebhookSignatureInvalidProblem
            | ScmUnavailableProblem
            | UnauthenticatedProblem
            | OriginForbiddenProblem
            | RouteNotFoundProblem
            | MethodNotAllowedProblem
            | NotImplementedProblem
            | HttpErrorProblem
            | InternalErrorProblem,
            Field(discriminator="code"),
        ]
    ]
):
    """Любая ошибка — по модели на код, выбор по `code`.

    Коды, которые возможны на любом запросе: `ROUTE_NOT_FOUND` (такого пути нет),
    `METHOD_NOT_ALLOWED` (метод не объявлен), `HTTP_ERROR` (прочий отказ фреймворка) и
    `INTERNAL_ERROR` (сбой сервера). Остальные коды операция объявляет в своих ответах.
    """


# {код: модель} — из объединения, чтобы список моделей был записан в одном месте.
PROBLEM_MODELS: dict[ErrorCode, type[Problem]] = {
    model.model_fields["code"].default: model
    for model in get_args(AnyProblem.model_fields["root"].annotation)
}
