# Proposal

## Why

Job `image` в `ci.yml` собирает образ и гоняет hadolint и trivy, но итог виден только в логе:
CI показывает красное или зелёное. Размер образа и его рост после новой зависимости никто не
видит, находки trivy, которые пропускает `ignore-unfixed`, не видны вовсе, как и замечания
hadolint ниже порога.

## What Changes

- Job `image` пишет в `$GITHUB_STEP_SUMMARY`: размер образа и сжатый размер с разницей к
  develop; находки trivy по severity с разбивкой на «с исправлением» и `unfixed`; замечания
  hadolint по уровням, включая ниже порога job'а.
- Разница с develop — к метрикам последнего успешного прогона develop из кэша Actions.
- Логика — `scripts/build_summary.py` (`collect` — размеры из docker, `render` — markdown),
  только стандартная библиотека; тест на фикстурах — `tests/test_build_summary.py`.
- Имена значений — по семантическим соглашениям OpenTelemetry (`cicd.pipeline.*`,
  `container.image.*`, `vcs.ref.*`), своё — `dmc268.build.*`, как во фронтенде
  ([ui#85](https://github.com/larchanka-training/dmc-268-ui-t1/issues/85)).
- Шаги summary ничего не роняют: падает только gate (hadolint, trivy с `ignore-unfixed`), как
  раньше.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет: поведение сервиса и стенда не меняется, спеки не правятся (`skip_specs: true`).

## Impact

- `.github/workflows/ci.yml` (job `image`), `scripts/build_summary.py`,
  `tests/test_build_summary.py` и фикстуры.
- Новый action — `actions/cache` v6.1.0 (restore, save), закреплён по SHA, тот же пин, что
  во фронтенде.
- Закрывает #206.
