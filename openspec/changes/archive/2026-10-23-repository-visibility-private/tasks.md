# Tasks

- [x] 1.1 `InstallationRepository.private` из `GET /installation/repositories`, не булево — `VcsError`
- [x] 1.2 Приватный репозиторий: `GET /repos/{owner}/{repo}/collaborators/{login}` до запроса роли; `404` — доступа нет, не `204` — `VcsError`
- [x] 1.3 Тест-регрессия: посторонний не видит приватный репозиторий, что бы ни отвечала ручка прав
