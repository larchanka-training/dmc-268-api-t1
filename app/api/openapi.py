"""Схема OpenAPI приложения — то, что отдаёт `/openapi.json` и что экспортируется в
`docs/openapi/openapi.json` (`scripts/export_openapi.py`).

Источник правды — код: пути, модели ответов и `responses` с моделями ошибок у роутов.
Здесь схема FastAPI только дописывается в одном месте тем, что FastAPI выразить не даёт:

- у ответов ошибок (4xx, 5xx, `default`) тип содержимого — `application/problem+json`:
  `responses[...]["model"]` FastAPI кладёт под тип содержимого роута (`application/json`);
- у каждого ответа — заголовок `X-Trace-ID` из `components.headers`;
- модели, которых нет в ответах JSON, но клиенту нужен их тип (`extra_models`): данные
  события потока SSE.
"""

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from app.api.errors import PROBLEM_MEDIA_TYPE
from app.api.problems import TRACE_ID_PATTERN
from app.api.trace_id import HEADER as TRACE_ID_HEADER

_TRACE_ID_HEADER_DOC = {
    "description": (
        "Trace id запроса — 32 строчных hex-символа, формат trace-id из W3C Trace Context, "
        "не все нули. Сервер выдаёт его на каждый запрос сам; входящие `X-Trace-ID`, "
        "`X-Request-ID` и `traceparent` не принимаются. В теле ошибки — то же значение в "
        "`trace_id`, в логе сервера о сбое — тоже."
    ),
    "schema": {"type": "string", "pattern": TRACE_ID_PATTERN},
}
_TRACE_ID_REF = {"$ref": f"#/components/headers/{TRACE_ID_HEADER}"}


def _is_error(status: str) -> bool:
    return status == "default" or status[0] in "45"


def finish_schema(schema: dict[str, Any], extra_models: Sequence[type[BaseModel]] = ()) -> dict[str, Any]:
    """Дописать схеме FastAPI `application/problem+json` у ошибок, `X-Trace-ID` у всех ответов
    и компоненты `extra_models`."""
    components = schema.setdefault("components", {})
    for model in extra_models:
        model_schema = model.model_json_schema(ref_template="#/components/schemas/{model}", mode="serialization")
        components.setdefault("schemas", {}).update(model_schema.pop("$defs", {}))
        components["schemas"][model.__name__] = model_schema
    components.setdefault("headers", {})[TRACE_ID_HEADER] = _TRACE_ID_HEADER_DOC
    for path, item in schema.get("paths", {}).items():
        for method, operation in item.items():
            for status, response in operation.get("responses", {}).items():
                response.setdefault("headers", {})[TRACE_ID_HEADER] = _TRACE_ID_REF
                if not _is_error(status):
                    continue
                schemas = [media["schema"] for media in response.get("content", {}).values() if "schema" in media]
                if len(schemas) != 1:
                    raise ValueError(
                        f"{method.upper()} {path} {status}: у ответа ошибки нужна ровно одна модель "
                        "из app/api/problems.py в responses"
                    )
                response["content"] = {PROBLEM_MEDIA_TYPE: {"schema": schemas[0]}}
    return schema


def install(app: FastAPI, extra_models: Sequence[type[BaseModel]] = ()) -> None:
    """`app.openapi()` — штатная схема FastAPI, дописанная `finish_schema`; строится один раз."""
    base = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            # `base` кладёт схему в `app.openapi_schema` сам; дописываем её один раз.
            app.openapi_schema = finish_schema(base(), extra_models)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
