# Спринт 3: порядок выполнения задач и мерджей в `develop`

Порядок выведен из зависимостей задач в [`Спринт.md`](../Спринт.md):

- **Спеки Теклида** — фундамент: формат `.review/rules.md`, AST-контекст, маппинг строк и write-back → первый мердж.
- **DevOps** — не зависит ни от кого, идёт параллельным треком в любой волне.
- **Волна 1** — Инженер 3 (нужен формат правил и контекст-спека) и Инженер 5 (нужен маппинг строк и write-back).
- **Волна 2** — Инженер 4 (нужен `ContextPayload` от Инж 3), Инженер 1 (нужен API статуса правил от Инж 3), Инженер 2 (нужен SSE-эндпоинт от Инж 5).
- **QA** — последним: E2E требует полного конвейера (3 → 4 → 5).

## Граф зависимостей

```mermaid
flowchart TD
    TL["Теклид: спецификации<br/>RULES_FORMAT_SPEC.md + CONTEXT_AND_VERIFICATION_SPEC.md<br/>merge #1"]

    DEV["DevOps: Observability-стек<br/>Prometheus, Grafana, Sentry/OTel, Loki<br/>(параллельный трек, мердж в любой волне)"]

    subgraph W1["Волна 1 — merge #2 и #3, параллельно"]
        E3["Инженер 3: Context Engine<br/>RepoConfigLoader, Tree-sitter/AST, ContextPayload"]
        E5["Инженер 5: VCS Write-Back<br/>Publisher GitHub/GitLab, Overview, SSE-эндпоинт"]
    end

    subgraph W2["Волна 2 — merge #4–#6, параллельно"]
        E4["Инженер 4: Верификатор<br/>изоляция rules.md, Reflection Pass"]
        E1["Инженер 1: Frontend Settings<br/>статус и превью rules.md"]
        E2["Инженер 2: Frontend Real-Time<br/>прогресс по SSE, фильтры замечаний"]
    end

    QA["QA: E2E в CI, бенчмарк Precision/Recall,<br/>нагрузочные тесты — merge #7, финал"]

    TL -->|"формат правил, AST-спека"| E3
    TL -->|"маппинг строк, write-back"| E5
    E3 -->|"ContextPayload + rules.md"| E4
    E3 -->|"API статуса правил"| E1
    E5 -->|"SSE-события шагов"| E2
    E3 --> QA
    E4 --> QA
    E5 --> QA
```

## Порядок мерджей в `develop`

```mermaid
gitGraph
    commit id: "init"
    branch develop
    checkout develop
    branch tl-specs
    commit id: "Спеки ТЛ"
    checkout develop
    merge tl-specs
    branch devops
    commit id: "Observability-стек"
    branch eng3-context
    commit id: "Context Engine + rules loader"
    checkout develop
    branch eng5-vcs
    commit id: "VCS Publisher + SSE"
    checkout develop
    merge devops
    merge eng3-context
    merge eng5-vcs
    branch eng4-verifier
    commit id: "Reflection Pass"
    branch eng1-settings
    commit id: "Settings UI"
    checkout develop
    branch eng2-realtime
    commit id: "Real-Time UI"
    checkout develop
    merge eng1-settings
    merge eng2-realtime
    merge eng4-verifier
    branch qa-e2e
    commit id: "E2E + нагрузочные"
    checkout develop
    merge qa-e2e
```

## Сводная таблица

| # | Задача | Роль | Зависит от |
|---|---|---|---|
| 1 | Спецификации формата правил, AST-контекста, write-back | Теклид | — |
| 2–3 | Context Engine (RepoConfigLoader, AST, ContextPayload) | Инженер 3 | #1 |
| 2–3 | VCS Publisher + SSE-эндпоинт | Инженер 5 | #1 |
| — | Observability-стек, бэкапы | DevOps | — (параллельно) |
| 4–6 | Верификатор, анти-инъекция, Reflection Pass | Инженер 4 | #1, Инж 3 |
| 4–6 | Frontend Settings, статус и превью rules.md | Инженер 1 | #1, Инж 3 |
| 4–6 | Frontend Real-Time: SSE-статус, фильтры | Инженер 2 | #1, Инж 5 |
| 7 | E2E в CI, бенчмарки, нагрузочные тесты | QA | Инж 3, Инж 4, Инж 5 |
