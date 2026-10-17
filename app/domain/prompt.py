"""Сборка запроса к модели - чистые функции над пакетом контекста.

Вход - тело `ContextPayload` в том виде, в каком оно сохранено, и тексты
промптов сервиса; выход - сообщения запроса. Часов, случайности и
обращений к сети здесь нет, поэтому по `context_payloads` и текстам
промптов запрос восстанавливается побайтно.

Раскладка (спека `review-prompt-assembly`):

1. `system` - инструкции сервиса и сводка метаданных прогона. Из данных
   прогона сюда попадают только значения, проверенные по формату: номер PR,
   sha из 40 шестнадцатеричных символов, язык из перечня `Language`.
2. `user` - раздел правил владельца: имена и файлы - строки JSON, тексты
   пунктов - блоки недоверенных данных из пакета, без изменений.
3. `user` - раздел кода: hunk'и, AST-окружение, перечень не вошедшего.

Запрос верификации повторяет сообщения 2 и 3 и добавляет четвёртое -
замечания первого прохода; их тексты писала модель, читавшая код автора
PR, поэтому они тоже лежат в блоках недоверенных данных.

Строка из репозитория никогда не попадает в сообщение сырой: либо блок из
пакета (`fence` уже обезвредил текст и подобрал границу), либо литерал
`json.dumps` - перевод строки, кавычка или `##` в имени правила не меняют
структуру сообщения.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.entities import Finding, PromptMessage
from app.domain.enums import DiffSide, Language, PromptRole, RulesStatus
from app.domain.untrusted import fence, sanitize


@dataclass(frozen=True, slots=True)
class PromptTexts:
    """Системные сообщения сервиса, прочитанные из `prompts/review/` при старте."""

    review_system: str
    verifier_system: str


_SHA = re.compile(r"[0-9a-f]{40}")
_LANGUAGES = {language.value for language in Language}

_NO_RULES_REASON = {
    RulesStatus.ABSENT.value: "файла `.review/rules.md` в целевой ветке нет",
    RulesStatus.TOO_LARGE.value: "файл `.review/rules.md` больше лимита и не разбирался",
    RulesStatus.UNAVAILABLE.value: "файл `.review/rules.md` не удалось получить",
    RulesStatus.LOADED.value: "ни одно правило не применимо к файлам диффа",
}

_AST_PARTS = {"scopes": "определения", "imports": "импорты"}


def build_review_prompt(texts: PromptTexts, body: Mapping[str, Any]) -> tuple[PromptMessage, ...]:
    """Запрос первого прохода: инструкции, правила, код - тремя сообщениями."""
    return (
        PromptMessage(PromptRole.SYSTEM, _with_metadata(texts.review_system, body)),
        PromptMessage(PromptRole.USER, _rules_message(body)),
        PromptMessage(PromptRole.USER, _code_message(body)),
    )


def build_verification_prompt(
    texts: PromptTexts, body: Mapping[str, Any], candidates: Sequence[Finding]
) -> tuple[PromptMessage, ...]:
    """Запрос верификатора: те же правила и код плюс замечания с идентификаторами."""
    return (
        PromptMessage(PromptRole.SYSTEM, texts.verifier_system),
        PromptMessage(PromptRole.USER, _rules_message(body)),
        PromptMessage(PromptRole.USER, _code_message(body)),
        PromptMessage(PromptRole.USER, _candidates_message(candidates)),
    )


def candidate_ids(count: int) -> tuple[str, ...]:
    """Идентификаторы замечаний в запросе верификации, в порядке замечаний."""
    return tuple(f"F{n}" for n in range(1, count + 1))


def _literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _with_metadata(instructions: str, body: Mapping[str, Any]) -> str:
    facts: list[str] = []
    number = body.get("pull_request")
    if type(number) is int and number > 0:
        facts.append(f"- Номер PR: {number}")
    for key in ("base_sha", "head_sha"):
        value = body.get(key)
        if isinstance(value, str) and _SHA.fullmatch(value):
            facts.append(f"- {key}: {value}")
    languages = sorted({item.get("language") for item in body.get("ast", ())} & _LANGUAGES)
    if languages:
        facts.append(f"- Языки файлов с AST-окружением: {', '.join(languages)}")
    if not facts:
        return instructions
    return (
        f"{instructions}\n\n## Прогон\n\nЗначения ниже проверены сервисом по формату.\n\n"
        + "\n".join(facts)
    )


def _rules_message(body: Mapping[str, Any]) -> str:
    rules = body["rules"]
    heading = "## Раздел правил владельца репозитория"
    items = rules["items"]
    if not items:
        status = rules["status"]
        reason = _NO_RULES_REASON.get(status, "правила недоступны")
        return (
            f"{heading}\n\nСтатус загрузки правил: {status}. Применимых правил нет: {reason}. "
            "Ревью идёт только по стандартным критериям."
        )
    parts = [
        heading,
        (
            "Правила загружены сервисом из `.review/rules.md` целевой ветки PR. "
            "Имена правил и пути - строки JSON, тексты пунктов - в блоках недоверенных данных."
        ),
    ]
    for item in items:
        parts.append(f"### Правило {_literal(item['name'])}")
        parts.append(f"Файлы: {_literal(item['files'])}")
        parts.extend(f"Пункт {n}:\n{check}" for n, check in enumerate(item["checks"], start=1))
    return "\n\n".join(parts)


def _code_message(body: Mapping[str, Any]) -> str:
    parts = ["## Раздел кода", f"Репозиторий: {_literal(body['repository'])}", "### Изменения"]
    if body["diff"]:
        parts.extend(_hunk(item) for item in body["diff"])
    else:
        parts.append("Изменений в пакете нет.")
    if body["ast"]:
        parts.append("### AST-окружение")
        parts.extend(_ast(item) for item in body["ast"])
    if body["omitted_count"]:
        parts.append(
            f"### Не вошло в пакет\n\nВсего: {body['omitted_count']}\n{_literal(body['omitted'])}"
        )
    return "\n\n".join(parts)


def _hunk(item: Mapping[str, Any]) -> str:
    renamed = f" (было {_literal(item['old_path'])})" if "old_path" in item else ""
    return f"Файл {_literal(item['path'])}{renamed}:\n{item['hunk']}"


def _ast(item: Mapping[str, Any]) -> str:
    language = item["language"]
    label = language if language in _LANGUAGES else _literal(language)
    part = next(key for key in _AST_PARTS if key in item)
    return f"Файл {_literal(item['path'])}, {label}, {_AST_PARTS[part]}:\n{item[part]}"


def _candidates_message(candidates: Sequence[Finding]) -> str:
    parts = ["## Замечания первого прохода"]
    for candidate_id, finding in zip(candidate_ids(len(candidates)), candidates, strict=True):
        anchor = finding.anchor
        line = anchor.new_line if anchor.side is DiffSide.NEW else anchor.old_line
        # Путь в находке сырой - по нему публикуется комментарий; модель видит
        # его так же, как в пакете: литерал JSON маркеры не обезвреживает.
        metadata = {
            "filePath": sanitize(anchor.file_path),
            "side": anchor.side.value,
            "line": line,
            "category": finding.category.value,
            "severity": finding.severity.value,
            "rule": (
                {"name": finding.rule.name, "check": finding.rule.check} if finding.rule else None
            ),
        }
        parts.append(f"### {candidate_id}")
        parts.append(_literal(metadata))
        parts.append(f"Текст:\n{fence('finding', finding.message)}")
        if finding.suggestion:
            parts.append(f"Предложенная замена:\n{fence('suggestion', finding.suggestion)}")
    return "\n\n".join(parts)
