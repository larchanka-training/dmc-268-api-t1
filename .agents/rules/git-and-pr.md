# Правила: Git, задачи и пул-реквесты

Не зависят от стека. Файл копируется во фронтовый репозиторий целиком — агент по ссылке в чужой
репозиторий не сходит. Критичный минимум — в [`AGENTS.md`](../../AGENTS.md).

## Ветки и коммиты

Ветка отводится от `develop` и мёржится в `develop`. Префиксы: `feat/…`, `fix/…`,
`deps-update-YYYY-MM-DD`. Имя ветки — подсказка ревью-агенту, когда заголовок пул-реквеста
невнятен.

Коммиты — conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.

Ветка, опирающаяся на ещё не смёрженную ветку, ставит базой её, а не `develop`: иначе мердж
утащит и чужие коммиты. После мерджа родителя GitHub переносит базу на `develop` сам.

## Задачи на два стека

Родительский issue несёт постановку и DoD. Подзадачи `<Задача> — backend` и
`<Задача> — frontend` живут каждая в своём репозитории, имеют свой пул-реквест; родитель
закрывается последним. Одна карточка на два репозитория не работает: ключевое слово закрывает
issue только в своём репозитории.

```bash
gh issue create --repo <owner>/<repo> --title "<Задача> — backend" --body-file <файл> --assignee <login>

# id (не номер) берётся из gh api repos/<owner>/<repo>/issues/<n> --jq .id
gh api -X POST repos/<owner>/<repo>/issues/<родитель>/sub_issues -F sub_issue_id=<id>
```

Подзадача может лежать в другом репозитории организации.

## Привязка пул-реквеста к задаче

В своём репозитории — `Closes #N` в описании. В чужом — панель Development, в ленте задачи это
видно как `ConnectedEvent`:

```bash
gh api graphql -f query='
mutation($issue:ID!, $pr:ID!) {
  addCloseIssueReferences(input:{issueId:$issue, pullRequestIds:[$pr]}) { clientMutationId }
}' -f issue=<node_id задачи> -f pr=<node_id пул-реквеста>
```

`node_id` — из `gh api repos/<owner>/<repo>/issues/<n> --jq .node_id` и `…/pulls/<n> --jq .node_id`.

Структура описания — скилл [`pull-request`](../skills/pull-request/SKILL.md).

## Замечания на ревью

Резолвит тот, чьё требование выполнено.

| Случай | Действие | Кто резолвит |
|---|---|---|
| Нужна правка | правим, одной строкой в треде пишем что | мы |
| Вопрос без правки | отвечаем, код не трогаем | задавший |
| Не согласны | аргумент, тред открыт | автор замечания |

Вопрос, обернувшийся правкой, переходит в первый случай. Спор, не сошедшийся за один обмен, —
в тикет или на созвон.

После всех правок — один перезапрос ревью на пачку:

```bash
gh pr edit <n> --add-reviewer <login>
```

## Доска

[`dmc-268-t1`](https://github.com/orgs/larchanka-training/projects/7). Статус двигаем вместе с
работой: In Progress → Review при открытии пул-реквеста → Done после мерджа.

```bash
gh project item-list 7 --owner larchanka-training --format json
gh project field-list 7 --owner larchanka-training --format json
gh project item-edit --id <item> --project-id <project> --field-id <field> --single-select-option-id <option>
```
