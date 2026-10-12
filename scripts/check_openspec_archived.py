"""Change со всеми закрытыми задачами должен быть заархивирован в том же PR.

Читает `openspec list --json` со stdin. Падает, если в `openspec/changes/`
лежит change, у которого закрыты все задачи: такой change забыли заархивировать,
и его дельты не попали в `openspec/specs/`. Change с незакрытой задачей —
работа идёт или ждёт приёмки после мерджа — проверку проходит, но попадает в
список открытых в summary прогона, чтобы висящие change были видны.

    npx @fission-ai/openspec@1.14.1 list --json | python3 scripts/check_openspec_archived.py
"""

import json
import os
import sys


def main() -> int:
    changes = json.load(sys.stdin)["changes"]
    done = [
        c
        for c in changes
        if c["totalTasks"] > 0 and c["completedTasks"] == c["totalTasks"]
    ]
    open_ = [c for c in changes if c not in done]

    lines = ["## Открытые OpenSpec change", ""]
    if open_:
        lines += ["| Change | Задачи |", "| --- | --- |"]
        lines += [
            f"| `{c['name']}` | {c['completedTasks']}/{c['totalTasks']} |"
            for c in open_
        ]
    else:
        lines.append("Нет.")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))

    for c in done:
        print(
            f"::error::change {c['name']}: все задачи закрыты, а change не заархивирован — "
            f"`openspec archive {c['name']}` в этом PR"
        )
    return 1 if done else 0


if __name__ == "__main__":
    sys.exit(main())
