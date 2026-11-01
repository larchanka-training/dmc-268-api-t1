"""Сборка контекста симуляции песочницы: вход пользователя вместо VCS (design D6).

Та же `assemble_context`, что у ревью запроса на изменение, с тем же фильтром
файлов и бюджетом. Отличается только источник: файлы - фрагмент как новый файл
или разобранный дифф пользователя, правила - его текст, контекста PR и
заголовка пакета нет. AST-окружение строится из самого фрагмента; у диффа
полного текста файла нет, и файл поддержанного языка попадает в `omitted` с
причиной `no_source`.

В лог не пишется ни код, ни правила, ни путь из входа (design D9): только то,
что сборка деградировала.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.application.context_assembly import (
    AssembledContext,
    ContextLimits,
    ContextSubject,
    FileAst,
    PrContext,
    SkippedAst,
    assemble_context,
)
from app.application.ports.definition_extractor import DefinitionExtractor
from app.domain.code_context import language_for_path
from app.domain.diff_parser import filter_diff_files
from app.domain.entities import ParsedFile, RepoRules, Simulation
from app.domain.enums import RulesStatus, SimulationSourceKind
from app.domain.repo_rules import rules_from_text
from app.domain.simulation import simulation_files

logger = logging.getLogger(__name__)

# У симуляции нет запроса на изменение: текста автора нет, а не «недоступен».
NO_PR_CONTEXT = PrContext(title="", description="", commit_messages=())


def build_simulation_context(
    simulation: Simulation,
    *,
    extractor: DefinitionExtractor,
    limits: ContextLimits,
    now: datetime,
    new_id: Callable[[], UUID],
) -> AssembledContext:
    """Пакет контекста симуляции; вход есть только у незавершённой симуляции.

    Пакет не хранится в `context_payloads`: его идентификатор прогона - id
    симуляции, а что видела модель, симуляция хранит в своём результате.
    """
    if simulation.source is None or simulation.rules is None:
        raise ValueError(f"simulation {simulation.id} has no input: it is already finished")
    files = simulation_files(simulation.source_kind, simulation.source, simulation.file_path)
    # Пустой редактор правил - как отсутствующий файл: ревью по стандартным критериям.
    rules = RepoRules(status=RulesStatus.ABSENT) if not simulation.rules else rules_from_text(simulation.rules)
    asts, skipped = _collect_asts(simulation.source_kind, simulation.source, files, extractor, limits)
    return assemble_context(
        ContextSubject(),
        files,
        rules,
        asts,
        skipped,
        pr_context=NO_PR_CONTEXT,
        limits=limits,
        review_run_id=simulation.id,
        now=now,
        new_id=new_id,
    )


def _collect_asts(
    source_kind: SimulationSourceKind,
    source: str,
    files: list[ParsedFile],
    extractor: DefinitionExtractor,
    limits: ContextLimits,
) -> tuple[list[FileAst], list[SkippedAst]]:
    """Контур фрагмента или `no_source` у файлов диффа - для файлов поддержанного языка.

    Файл, который ревью отфильтровало бы или в котором нечего ревьюить, AST не
    получает и не упоминается, как у ревью. Фрагмент больше `ast_max_file_bytes`
    - `too_large`, сломавший парсер - `parse_error`.
    """
    candidates = [
        (parsed, language)
        for parsed in filter_diff_files(files)
        if not parsed.is_deleted
        and parsed.hunks
        and (language := language_for_path(parsed.file_path)) is not None
    ]
    if source_kind is SimulationSourceKind.DIFF:
        return [], [SkippedAst(parsed.file_path, "no_source") for parsed, _ in candidates]
    asts: list[FileAst] = []
    skipped: list[SkippedAst] = []
    for parsed, language in candidates:
        if len(source.encode("utf-8")) > limits.ast_max_file_bytes:
            skipped.append(SkippedAst(parsed.file_path, "too_large"))
            continue
        try:
            outline = extractor.extract(source, language)
        except Exception:
            logger.exception("парсер не справился с фрагментом симуляции")
            skipped.append(SkippedAst(parsed.file_path, "parse_error"))
            continue
        asts.append(FileAst(parsed.file_path, language, outline))
    return asts, skipped
