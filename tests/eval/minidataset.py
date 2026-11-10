"""Мини-датасет для тестов загрузчика и раннера: кейсы пишутся в `tmp_path`.

Дифф один на все кейсы - SQL-инъекция в `app/search.py`. Новая сторона:
1 `def search`, 2-4 добавлены, 5 - контекст; старая: 1 и 3 - контекст, 2 удалена.
"""

import json
from pathlib import Path
from typing import Any

PATH = "app/search.py"

DIFF = """\
diff --git a/app/search.py b/app/search.py
index 1111111..2222222 100644
--- a/app/search.py
+++ b/app/search.py
@@ -1,3 +1,5 @@
 def search(db, name):
-    return db.query(name)
+    sql = f"SELECT * FROM users WHERE name = '{name}'"
+    rows = db.execute(sql)
+    return rows
 # конец
"""

MR = {
    "title": "feat: поиск пользователя по имени",
    "description": "Поиск по имени без ORM.",
    "source_branch": "feat/user-search",
    "target_branch": "develop",
}


def expected_finding(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "sql-injection",
        "file_path": PATH,
        "side": "new",
        "line": 2,
        "category": "security",
        "severity": "critical",
        "must_find": True,
    } | overrides


def model_finding(**overrides: Any) -> dict[str, Any]:
    return {
        "filePath": PATH,
        "side": "new",
        "line": 2,
        "category": "security",
        "severity": "critical",
        "message": "SQL-инъекция: имя подставляется в запрос f-строкой.",
        "confidence": 0.9,
        "suggestion": None,
        "rule": None,
    } | overrides


def response(*findings: dict[str, Any], problem: str = "Поиск по имени.") -> str:
    return json.dumps(
        {
            "schemaVersion": "1",
            "findings": list(findings),
            "summary": {"problem": problem, "positives": [], "effort": "низкие"},
        },
        ensure_ascii=False,
    )


def write_case(
    root: Path,
    case_id: str,
    *,
    kind: str = "bug",
    expected: list[dict[str, Any]] | None = None,
    responses: list[str] | None = None,
    forbidden_categories: list[str] | None = None,
    max_findings: int | None = None,
    injection: dict[str, Any] | None = None,
    diff: str = DIFF,
    overrides: dict[str, Any] | None = None,
) -> Path:
    """Каталог кейса; `adversarial` и `ADV-*` - в `adversarial/`. Возвращает каталог."""
    base = root / "adversarial" if case_id.startswith("ADV-") else root
    case = base / case_id
    (case / "responses").mkdir(parents=True)
    (case / "mr.json").write_text(json.dumps(MR, ensure_ascii=False), encoding="utf-8")
    (case / "diff.patch").write_text(diff, encoding="utf-8")
    (case / "notes.md").write_text("Кейс для теста.\n", encoding="utf-8")
    data = {
        "case_id": case_id,
        "kind": kind,
        "expected_findings": [expected_finding()] if expected is None else expected,
        "forbidden_categories": forbidden_categories or [],
        "max_findings": max_findings,
        "injection": injection,
    } | (overrides or {})
    (case / "expected.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for n, text in enumerate(responses or [response(model_finding())], start=1):
        (case / "responses" / f"run-{n}.json").write_text(text, encoding="utf-8")
    return case
