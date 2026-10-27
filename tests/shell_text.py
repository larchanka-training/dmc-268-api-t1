"""Текст скрипта деплоя для тестов, которые разбирают команды регулярками.

Общие аргументы `docker run` в `migrate.sh` лежат в массиве bash (ревью #220:
сеть и ограничения задаются в одном месте), а тесты сверяют флаги каждого
запуска. `expand_arrays` подставляет тело массива на место `"${имя[@]}"` одной
строкой и убирает само определение: иначе первая строка определения выглядела
бы запуском без флагов.
"""

import re

ARRAY = re.compile(r"^(\w+)=\(\n(.*?)^\)\n?", re.MULTILINE | re.DOTALL)


def expand_arrays(script: str) -> str:
    """Скрипт, где `"${имя[@]}"` заменено телом массива `имя=( … )` одной строкой."""
    arrays = {
        name: " ".join(
            line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")
        )
        for name, body in ARRAY.findall(script)
    }
    text = ARRAY.sub("", script)
    for name, words in arrays.items():
        text = text.replace(f'"${{{name}[@]}}"', words)
    return text


def commands(script: str) -> str:
    """`expand_arrays` без строк-комментариев: флаг в комментарии — не флаг запуска."""
    return "\n".join(line for line in expand_arrays(script).splitlines() if not line.lstrip().startswith("#"))
