## 1. Фильтр

- [x] 1.1 Бандлы в `app/domain/diff_parser.py`: каталог `dist/`, `*.bundle.js`, `*.bundle.css`, `*.js.map`, `*.css.map`
- [x] 1.2 `is_filtered_out(file, max_file_bytes)`: шаблоны и лимит размера изменения файла; `filter_diff_files` по нему
- [x] 1.3 Настройка `DIFF_FILE_MAX_BYTES` и поле `ContextLimits.diff_file_max_bytes`

## 2. Охват и итоговый комментарий

- [x] 2.1 `review_coverage` считает «отброшено фильтром» по тому же правилу
- [x] 2.2 Подпись группы «Отброшены фильтром» называет бандлы и файлы больше лимита

## 3. Завершение

- [x] 3.1 Тесты на каждый новый шаблон, на границу лимита и на охват; `make check`, `pytest`
- [x] 3.2 Синхронизация спеки и `openspec archive diff-noise-filter`
