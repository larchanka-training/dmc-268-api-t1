"""Код возврата `scripts/export_openapi.py --check`: на нём держится проверка CI.

Схема подменяется готовым текстом, файл — временным: тест не трогает
`docs/openapi/openapi.json`.
"""

from pathlib import Path

import pytest

from scripts import export_openapi

SCHEMA = '{"openapi": "3.1.0"}\n'


@pytest.fixture
def exported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "docs" / "openapi" / "openapi.json"
    target.parent.mkdir(parents=True)
    monkeypatch.setattr(export_openapi, "ROOT", tmp_path)
    monkeypatch.setattr(export_openapi, "OPENAPI_JSON", target)
    monkeypatch.setattr(export_openapi, "render", lambda: SCHEMA)
    return target


def test_check_fails_on_a_stale_file_and_leaves_it(exported: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exported.write_text('{"openapi": "3.0.0"}\n', encoding="utf-8")

    assert export_openapi.main(["--check"]) == 1

    assert export_openapi.STALE_MESSAGE in capsys.readouterr().err
    assert exported.read_text(encoding="utf-8") == '{"openapi": "3.0.0"}\n'


def test_check_fails_when_the_file_is_missing(exported: Path) -> None:
    assert export_openapi.main(["--check"]) == 1
    assert not exported.exists()


def test_check_passes_on_a_fresh_file(exported: Path) -> None:
    exported.write_text(SCHEMA, encoding="utf-8")
    assert export_openapi.main(["--check"]) == 0


def test_export_writes_the_file_then_check_passes(exported: Path) -> None:
    assert export_openapi.main([]) == 0
    assert exported.read_text(encoding="utf-8") == SCHEMA
    assert export_openapi.main(["--check"]) == 0
