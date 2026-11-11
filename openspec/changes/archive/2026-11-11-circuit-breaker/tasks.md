## 1. Breaker

- [x] 1.1 Чистая машина состояний `app/domain/circuit_breaker.py` (`failed`, `state`, `retry_after`; ответ — `CLOSED`); `tests/domain/test_circuit_breaker.py`
- [x] 1.2 `CircuitBreaker` и `deferral` в `app/application/circuit_breaker.py`, часы инъекцией; `tests/application/test_circuit_breaker.py`
- [x] 1.3 Классы отказа: `LlmUnavailableError` (домен), `VcsOutageError` (адаптер); Ollama и Eurorouter — 5xx и сеть, GitHub — сеть и 5xx до конца попыток, выдача installation token — сеть и 5xx; тесты адаптеров
- [x] 1.4 Breaker в `GitHubVcsGateway._request` и в `FallbackLlmGateway`; `tests/infrastructure/test_github_vcs_gateway.py`, `test_fallback_gateway.py`
- [x] 1.5 `build_breakers` и проводка в `container.py` только у воркера; `tests/test_container.py`

## 2. Отложенный прогон

- [x] 2.1 `ReviewDeferred` и `paused_for` в `run_review` до захвата; `tests/application/test_review_pipeline.py`
- [x] 2.2 `Decision.DEFER`, `nack` с requeue после паузы до `MAX_DEFER_SECONDS`; `tests/worker/test_handling.py`, `test_worker_loop.py`, `test_review_handler.py`
- [x] 2.3 Chaos-тест `tests/chaos/test_provider_outage_chaos.py`: отказ дольше лимита доставок не тратит попыток, после восстановления прогон публикуется

## 3. Наблюдаемость

- [x] 3.1 Gauge `review_circuit_breaker_state` (`instrument_circuit_breakers`); `tests/infrastructure/test_telemetry.py`
- [x] 3.2 Алерты `ProviderCircuitOpen` (critical, прогоны откладываются) и `LlmProviderCircuitOpen` (warning, живой фоллбэк), глушение `WorkerStalled` и `ReviewJobStuck` тем же условием, что у `deferral`; тесты в `alerts.test.yml`, `tests/infrastructure/test_alerting.py`
- [x] 3.3 `infra/README.md`, `docs/BACKEND_ARCHITECTURE.md`
- [x] 3.4 `openspec validate --all --strict`, архив change
