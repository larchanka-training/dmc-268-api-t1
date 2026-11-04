## Why

#279. По DoD задачи «VCS Write-Back» итоговый комментарий ревью содержит вердикт, список
проверенных файлов и сводку рисков. Сейчас `format_summary_comment` выводит только проблему,
«Сделано хорошо» и объём доработки. Автор PR не видит, какие файлы ревью пропустило: lock-файлы,
сгенерированные или изменения сверх лимита контекста.

## What Changes

- Охват считает чистая функция `review_coverage` (`app/domain/review_coverage.py`) по разобранным
  файлам диффа и hunk'ам, вошедшим в пакет. Результат — `AssembledContext.coverage`, его получает
  `publish_review`.
- `format_summary_comment` дописывает под сводкой модели блок «Риски» (`critical` и `high` по
  категориям, из замечаний прогона) и список файлов под `<details>` по группам.
- Пути в списке — через `sanitize`; перевод строки, табуляция и обратная кавычка — видимой
  записью, забор code span одинарный; путь не длиннее 200 символов, в группе не больше 50 путей.
- Модель не вызывается и её промпт не меняется.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-pipeline`: требование «Сводка ревью называет риски и проверенные файлы».
- `ai-review-prompting`: сводка от модели — только проблема, сделанное хорошо и объём; риски и файлы строит код.

## Impact

- `app/domain/review_coverage.py`, `app/domain/review_comment.py`, `app/application/context_assembly.py`,
  `app/application/publish_review.py`, `app/application/review_pipeline.py`, тесты.
- API, схема базы и формат ответа модели не меняются.
