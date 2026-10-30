"""Проверка tree-sitter и грамматик для `scripts/sandbox-outline-doctor.sh --grammars`.

Версии пакетов - из метаданных установленного окружения; `tree-sitter` 0.26.0
роняет процесс на файлах длиннее 256 строк (#226) и исключён в `pyproject.toml`,
но мог попасть в окружение мимо `uv sync`. Затем - парсер на каждый язык и
разбор однострочного образца: битое колесо грамматики ловится до прогона.
Код выхода: 0 - всё ✓, 1 - есть ✗.
"""

import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version

from app.domain.enums import Language

PACKAGES = (
    "tree-sitter",
    "tree-sitter-python",
    "tree-sitter-javascript",
    "tree-sitter-typescript",
    "tree-sitter-go",
)
# Версии, на которых песочница заведомо не работает, и почему.
BROKEN_VERSIONS = {
    ("tree-sitter", "0.26.0"): "segfault на файлах длиннее 256 строк (#226)",
}
FIX = "uv sync --all-extras"
# Образец на язык: одно определение `f`, которое экстрактор обязан найти.
SAMPLES = {
    Language.PYTHON: "def f(): pass",
    Language.TYPESCRIPT: "function f(): void {}",
    Language.TSX: "function f() { return <div />; }",
    Language.JAVASCRIPT: "function f() {}",
    Language.GO: "package p; func f() {}",
}


def _colors() -> tuple[str, str, str, str]:
    if sys.stdout.isatty():
        return "\033[32m", "\033[31m", "\033[2m", "\033[0m"
    return "", "", "", ""


def main(*, version: Callable[[str], str] = installed_version) -> int:
    """Напечатать ✓/✗ по каждой проверке; код 1, если есть ✗."""
    green, red, dim, reset = _colors()
    errors = 0

    def ok(text: str) -> None:
        print(f"  {green}✓{reset} {text}")

    def fail(text: str, hint: str) -> None:
        nonlocal errors
        errors += 1
        print(f"  {red}✗{reset} {text}")
        print(f"    {dim}{hint}{reset}")

    for package in PACKAGES:
        try:
            found = version(package)
        except PackageNotFoundError:
            fail(f"{package} не установлен", f"запустите {FIX}")
            continue
        broken = BROKEN_VERSIONS.get((package, found))
        if broken is None:
            ok(f"{package} {found}")
        else:
            fail(
                f"{package} {found}: {broken}",
                f"исключена в pyproject.toml - запустите {FIX}",
            )

    try:
        # Импорт здесь, а не в начале модуля: адаптер импортирует все грамматики, и без
        # одной из них модуль упал бы до строк версий.
        from app.infrastructure.parsing.tree_sitter_extractor import (
            TreeSitterDefinitionExtractor,
        )

        extractor = TreeSitterDefinitionExtractor()
    except Exception as error:  # noqa: BLE001 - нет или битое колесо: любой сбой загрузки грамматики
        fail(f"парсеры tree-sitter не создаются: {error}", f"запустите {FIX}")
        extractor = None
    if extractor is not None:
        for language, sample in SAMPLES.items():
            try:
                names = [
                    d.name for d in extractor.extract(sample, language).definitions
                ]
            except Exception as error:  # noqa: BLE001 - любой сбой разбора образца - ✗
                fail(f"разбор {language.value}: {error}", f"запустите {FIX}")
                continue
            if names == ["f"]:
                ok(f"разбор {language.value}")
            else:
                fail(
                    f"разбор {language.value}: вместо определения f найдено {names}",
                    f"грамматика повреждена - запустите {FIX}",
                )

    if errors:
        print(f"\n{red}Грамматики не готовы: ошибок - {errors}.{reset}")
        return 1
    print(f"\n{green}tree-sitter и грамматики готовы.{reset}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
