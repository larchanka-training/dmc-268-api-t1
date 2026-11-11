# TC-14 — бинарные файлы, переименования, не-UTF-8

Проверяет, что **парсер диффа не падает** и правильно размечает служебные случаи. В диффе:

| Файл | Что в диффе | Чего ждём от `parse_diff` |
|---|---|---|
| `docs/img/logo.png` | `Binary files … differ` | `is_binary=True`, hunk'ов нет, `filter_diff_files` отбрасывает |
| `app/legacy/text_utils.py` → `app/common/text_utils.py` | `similarity index 100%`, `rename from/to` | `old_path` заполнен, hunk'ов нет |
| `app/reports/csv_export.py` → `app/reports/export_csv.py` | переименование с правкой | путь — новый, hunk'и есть |
| `app/i18n/legacy_fr.py` | байты latin-1 | дифф декодирован как `response.text` httpx (UTF-8 с заменой) — в строках `U+FFFD` |
| `docs/руководство.md` | путь в C-кавычках git (`"a/docs/\321\200…"`), эмодзи, `\f` и `U+2028` внутри строк, `\ No newline at end of file` | путь раскрыт в кириллицу; `split_lines` режет только по `\n`, номера строк не уезжают |
| `scripts/reports.sh` | только `old mode`/`new mode` | файл без hunk'ов |

Дефект один, нарочно в переименованном с правкой файле — чтобы проверить, что якорь ставится на
**новый** путь:

- `export-skips-first-row` — `correctness/medium`, `must_find: true`: `rows[1:]` теряет первый платёж
  в каждой выгрузке (заголовок пишется отдельно). Данные не портятся молча в базе, но отчёт неполный —
  `medium`.

В latin-1 файле дефекта нет: замечание о «битой кодировке» там — FP (символы замены появились при
декодировании диффа, а не в коде). `max_findings: 3`.
