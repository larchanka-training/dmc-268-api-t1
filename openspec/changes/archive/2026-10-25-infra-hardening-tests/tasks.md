## 1. Защита контейнеров

- [x] 1.1 `infra/tests/hardening.tftest.hcl`: `security_opts`, `read_only`, `capabilities`, `tmpfs`, `init`, `memory == memory_swap`, `log_driver`/`log_opts`
- [x] 1.2 Мутация: снятая защита у одного контейнера краснит `tofu test`

## 2. Чтение конфигурации в Python-тестах

- [x] 2.1 `tests/infra_text.py`: `read_infra_text()` вместо `Path("infra/main.tf")`
- [x] 2.2 `tests/test_stack_descriptions.py` переведён на неё; перенос ресурса в отдельный `.tf` тесты не роняет

## 3. Спека

- [x] 3.1 `backend-delivery`: требование о минимальных привилегиях называет воркера; сценарий «Защиту контейнера сняли правкой конфигурации»
