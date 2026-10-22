"""`500` под настоящим uvicorn: в логе одна JSON-запись со стектрейсом.

Subprocess поднимает `uvicorn.Server` в отдельном потоке с приложением,
собранным как в бою: uvicorn настраивает свои логгеры при создании `Config`,
затем импорт приложения вызывает `create_app` → `configure_logging`, и записи
uvicorn переходят на JSON-хендлер. stdout процесса подменяется на StringIO и
отдаётся наружу — весь он должен быть JSON, по строке на запись. Запросы
делает stdlib `urllib`, а не httpx: у httpx своя INFO-запись с полным URL
(вместе с query callback'а), которая к сервису отношения не имеет.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Приложение: как в tests/api/test_tracing.py, только роут падает.
_APP_MODULE = '''"""Приложение для проверки логов под настоящим uvicorn."""

from fastapi import FastAPI

from app.api.factory import create_app
from app.config import Settings

app: FastAPI = create_app(
    Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="test-secret",
    )
)


@app.get("/api/v1/_boom", include_in_schema=False)
def boom() -> None:
    raise RuntimeError("сбой под настоящим uvicorn")
'''

# Раннер: сервер в потоке, два запроса (500 и callback с секретами в query),
# затем собранный stdout — наружу, в реальный поток.
_RUNNER = '''
import io
import sys
import threading
import time
import urllib.error
import urllib.request

import uvicorn

real, captured = sys.stdout, io.StringIO()
sys.stdout = captured

server = uvicorn.Server(uvicorn.Config("logtail_app:app", host="127.0.0.1", port=0))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
deadline = time.monotonic() + 30
while not server.started and time.monotonic() < deadline and thread.is_alive():
    time.sleep(0.01)
if not server.started:
    raise SystemExit(1)
port = server.servers[0].sockets[0].getsockname()[1]


def get(path: str) -> tuple[int, dict[str, str]]:
    try:
        response = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    headers = {name.lower(): value for name, value in response.headers.items()}
    response.read()
    return response.code, headers


boom_status, boom_headers = get("/api/v1/_boom")
callback_status, _ = get(
    "/api/v1/auth/github/callback?code=code-secret-1&state=state-secret-1"
)

server.should_exit = True
thread.join(timeout=15)

print(f"BOOM {boom_status} {boom_headers['x-trace-id']}", file=real)
print(f"CALLBACK {callback_status}", file=real)
print("### BEGIN LOGS", file=real)
print(captured.getvalue(), file=real, end="")
print("### END LOGS", file=real)
'''


def test_a_500_logs_one_json_record_with_the_response_trace_id(tmp_path: Path) -> None:
    (tmp_path / "logtail_app.py").write_text(_APP_MODULE, encoding="utf-8")
    (tmp_path / "logtail_runner.py").write_text(_RUNNER, encoding="utf-8")
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("OTEL_")},
        "PYTHONPATH": str(REPO_ROOT),
        # Спаны не экспортируются: консольный экспорт писал бы в stdout не-JSON.
        "OTEL_TRACES_EXPORTER": "none",
    }
    result = subprocess.run(
        [sys.executable, "logtail_runner.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr

    _, _, tail = result.stdout.partition("### BEGIN LOGS\n")
    logs, _, _ = tail.partition("### END LOGS")
    lines = [line for line in logs.splitlines() if line]
    # Весь stdout контейнера — JSON, одна строка на запись.
    records = [json.loads(line) for line in lines]

    _, status, trace_id = result.stdout.splitlines()[0].split()
    assert status == "500"
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id)

    assert all(record["service.name"] == "dmc-268-api" for record in records)
    # Ровно одна запись со стектрейсом — наша, из обработчика 500.
    with_stacktrace = [r for r in records if "exception.stacktrace" in r]
    assert len(with_stacktrace) == 1
    record = with_stacktrace[0]
    assert record["severity"] == "ERROR"
    assert record["trace_id"] == trace_id  # тот же, что X-Trace-ID ответа
    assert record["http.request.method"] == "GET"
    assert record["url.path"] == "/api/v1/_boom"
    assert "RuntimeError: сбой под настоящим uvicorn" in record["exception.stacktrace"]

    # Дубль uvicorn отсечён: его сообщения в логе нет.
    assert not any("Exception in ASGI application" in r["message"] for r in records)

    # Callback входа: `code` и `state` не попали ни в одну запись, query скрыт.
    assert "code-secret-1" not in logs
    assert "state-secret-1" not in logs
    callback_access = [
        r for r in records if "/api/v1/auth/github/callback" in r["message"]
    ]
    assert callback_access
    assert all("?<скрыто>" in r["message"] for r in callback_access)
