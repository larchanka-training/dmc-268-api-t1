"""Стенд не запускается в демо-режиме (change `demo-mode`, спека `backend-delivery`).

Приложение стенда получает переменные окружения явным списком из `infra/`, а `.env` в образ не
попадает: пока `infra/` не передаёт `DEMO_MODE`, демо-режим на стенде не включить.
"""

from tests.infra_text import read_infra_text


def test_infra_does_not_pass_demo_mode_to_the_stand() -> None:
    assert "DEMO_MODE" not in read_infra_text()
