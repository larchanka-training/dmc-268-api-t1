---
name: pull-request
description: Открыть или обновить пул-реквест в dmc-268 и отработать замечания ревью. Используйте, когда ветка готова, нужно описание пул-реквеста или пришли комментарии на ревью.
---

# Пул-реквест

Ветвление, привязка к задаче и правила тредов — [`rules/git-and-pr.md`](../../rules/git-and-pr.md).

Тело адаптировано из [`mattpocock/skills`](https://github.com/mattpocock/skills) (MIT),
`skills/in-progress/pr`, который кредитует `show-me` из
[`humanlayer/skills`](https://github.com/humanlayer/skills) (MIT).

## Тело описания

```markdown
## Summary
<наименьшее изображение сути>

## Evidence
Было: <падающий тест или поведение>
Стало: <проходящий тест или поведение>

## Merge Danger
Дверь: в одну сторону / в обе
Радиус: <что задевает>
```

**Summary** — не пересказ диффа. Берите наименьшее, что доносит суть: псевдокод, дерево вызовов,
дерево файлов, mermaid или `diff` нужной формы.

```text
advance(run, status, now)
  next_status(current, requested)   # решение
  repo.update(run)                  # запись
```

**Evidence** — вывод тестов: какой падал, какой проходит. Для схемы — `alembic upgrade head` и
`downgrade base`.

**Merge Danger** — дверь в обе стороны, если откат дёшев. Миграция без рабочего `downgrade`,
удаление колонки, смена формата хранения — дверь в одну сторону; это пишется прямо.

## Открытие

```bash
gh auth status
ls .github/pull_request_template.md .github/PULL_REQUEST_TEMPLATE.md 2>/dev/null
gh pr view "$(git branch --show-current)" --json number,url   # уже есть?
gh pr create --base develop --head "$(git branch --show-current)" --title "<тип>: <суть>" --body-file <файл>
```

- Пул-реквест для ветки уже есть — обновляем его (`gh pr edit --body-file`), второй не заводим.
- Описание всегда через `--body-file`: инлайн приезжает с экранированными переводами строк.
- Готовый пул-реквест обратно в черновик не переводим.
- `Closes #N` — для задачи в этом же репозитории; для чужой — привязка через панель Development.
- Коммитим и пушим только по явной просьбе: миграции и спеки проходят ревью до коммита.

## Режим: замечания ревью

```bash
gh pr view <n> --json reviews,comments
gh api repos/<owner>/<repo>/pulls/<n>/comments
```

Каждый тред разложить на один из трёх исходов (таблица в `rules/git-and-pr.md`): правка —
правим, отвечаем, резолвим сами; вопрос — отвечаем и не резолвим; несогласие — аргумент, тред
открыт.

Резолв без ответа не делаем. После всей пачки правок — ровно один перезапрос ревью:
`gh pr edit <n> --add-reviewer <login>`.
