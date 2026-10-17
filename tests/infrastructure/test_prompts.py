"""Тексты промптов из файлов: что уходит модели и когда воркер не стартует."""

import json
import re
from pathlib import Path

import pytest

from app.application.llm_schema import ReviewOutput
from app.domain.prompt import PromptTexts
from app.infrastructure.prompts import (
    DEFAULT_PROMPTS_DIR,
    REVIEW_SYSTEM_PARTS,
    PromptFileError,
    load_prompt_texts,
)


def _write(root: Path, name: str, header: str, body: str) -> None:
    (root / name).write_text(f"{header}\n\n---\n\n{body}\n", encoding="utf-8")


@pytest.fixture
def prompts(tmp_path: Path) -> Path:
    _write(tmp_path, "system.md", "# Для людей", "Ты - агент ревью.")
    _write(tmp_path, "comment-templates.md", "# Для людей", "## Комментарий\n\nПять шагов.")
    _write(tmp_path, "output-format.md", "# Для людей", "Отвечай JSON.")
    _write(tmp_path, "few-shot.md", "# Для людей", "Примеры ответа.")
    _write(tmp_path, "verifier.md", "# Для людей", "Ты - верификатор.")
    return tmp_path


def test_system_messages_are_the_bodies_in_order(prompts: Path) -> None:
    assert load_prompt_texts(prompts) == PromptTexts(
        review_system=(
            "Ты - агент ревью.\n\n## Комментарий\n\nПять шагов.\n\nОтвечай JSON.\n\nПримеры ответа."
        ),
        verifier_system="Ты - верификатор.",
    )


def test_an_added_part_lands_at_the_end_and_changes_nothing_else(prompts: Path) -> None:
    _write(prompts, "examples.md", "# Few-shot", "Пример: ...")
    texts = load_prompt_texts(prompts, review_parts=(*REVIEW_SYSTEM_PARTS, "examples.md"))
    assert texts.review_system == (
        "Ты - агент ревью.\n\n## Комментарий\n\nПять шагов.\n\nОтвечай JSON.\n\n"
        "Примеры ответа.\n\nПример: ..."
    )
    assert texts.verifier_system == "Ты - верификатор."


def test_only_the_first_separator_splits_header_from_body(prompts: Path) -> None:
    _write(prompts, "verifier.md", "# Для людей", "Часть 1\n\n---\n\nЧасть 2")
    assert load_prompt_texts(prompts).verifier_system == "Часть 1\n\n---\n\nЧасть 2"


def test_a_missing_file_stops_the_worker(prompts: Path) -> None:
    (prompts / "output-format.md").unlink()
    with pytest.raises(PromptFileError, match="output-format.md"):
        load_prompt_texts(prompts)


def test_a_file_without_separator_stops_the_worker(prompts: Path) -> None:
    (prompts / "verifier.md").write_text("Ты - верификатор.\n", encoding="utf-8")
    with pytest.raises(PromptFileError, match="verifier.md"):
        load_prompt_texts(prompts)


def test_an_empty_body_stops_the_worker(prompts: Path) -> None:
    _write(prompts, "system.md", "# Для людей", "")
    with pytest.raises(PromptFileError, match="system.md"):
        load_prompt_texts(prompts)


def test_the_shipped_prompts_load() -> None:
    texts = load_prompt_texts(DEFAULT_PROMPTS_DIR)
    assert "агент ревью кода" in texts.review_system.splitlines()[0]
    assert "## Правила владельца" in texts.review_system
    assert texts.verifier_system.startswith("Ты - верификатор")


def _few_shot_examples() -> list[str]:
    body = load_prompt_texts(DEFAULT_PROMPTS_DIR, review_parts=("few-shot.md",)).review_system
    return re.findall(r"```json\n(.*?)\n```", body, re.DOTALL)


def test_few_shot_examples_close_the_review_system_message() -> None:
    assert REVIEW_SYSTEM_PARTS[-1] == "few-shot.md"
    few_shot = load_prompt_texts(DEFAULT_PROMPTS_DIR, review_parts=("few-shot.md",)).review_system
    assert load_prompt_texts(DEFAULT_PROMPTS_DIR).review_system.endswith(few_shot)


def test_every_few_shot_example_matches_the_review_output_schema() -> None:
    examples = _few_shot_examples()
    assert examples
    for example in examples:
        ReviewOutput.model_validate_json(example)


def test_few_shot_covers_a_rule_finding_a_standard_finding_and_an_empty_review() -> None:
    answers = [json.loads(example) for example in _few_shot_examples()]
    findings = [finding for answer in answers for finding in answer["findings"]]
    by_rule = [f for f in findings if f.get("rule")]
    assert by_rule and all(
        not f["message"].lower().startswith("согласно пользовательскому правилу") for f in by_rule
    )
    assert any(f.get("rule") is None for f in findings)
    assert any(answer["findings"] == [] for answer in answers)
