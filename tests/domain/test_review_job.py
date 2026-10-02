"""Сущность `ReviewJob`: замороженная запись сообщения очереди.

Набор полей сверен с форматом сообщения `docs/SYSTEM_DESIGN.md` §4.2:
каждое поле тела сообщения берётся из сущности, лишних нет.
"""

from dataclasses import fields

from app.domain.entities import ReviewJob


def test_field_set_matches_section_4_2_wire_format() -> None:
    """Поля, из которых собирается тело сообщения §4.2, — и ничего сверх.

    `priority` в сущности нет: это метаданное доставки, его держит брокер,
    а не тело сообщения.
    """
    names = [field.name for field in fields(ReviewJob)]
    assert names == [
        "id",
        "event_type",
        "action",
        "repository_provider_id",
        "repository_full_name",
        "pull_request_number",
        "head_sha",
        "base_sha",
    ]
