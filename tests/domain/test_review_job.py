"""Сущность `ReviewJob`: замороженная запись сообщения очереди.

Набор полей сверен с `tasks/plan.md` (Task 3.1) и `docs/SYSTEM_DESIGN.md`
§4.2: список ниже фиксирует и отклонение от плана — `action` и
`repository_provider_id`, без которых тело сообщения §4.2 не собрать.
"""

from dataclasses import fields

from app.domain.entities import ReviewJob


def test_field_set_matches_plan_and_section_4_2() -> None:
    """Поля плана плюс `action` и `repository_provider_id` из §4.2 — и ничего сверх.

    `priority` в сущности нет: это метаданное доставки, и выводит его
    адаптер очереди, а не слой приложения.
    """
    names = [field.name for field in fields(ReviewJob)]
    assert names == [
        "job_id",
        "review_run_id",
        "repository_full_name",
        "repository_provider_id",
        "pr_number",
        "head_sha",
        "base_sha",
        "action",
    ]
