# Задачи: публикация ревью в GitHub (#50)

- [x] 1.1 Схема `docs/openapi/llm_review_output.schema.json`, расширение `LlmReviewResult`
- [x] 1.2 Домен: формат комментариев, merge gate, `OpenReviewThread`
- [x] 1.3 Миграция `0003_review_publication_fields`, модели и репозитории
- [x] 1.4 `VcsGateway`: публикация review, reply, resolve thread, check run; адаптер GitHub
- [x] 1.5 `publish_review` + интеграция в `run_review`
- [x] 1.6 Use case `record_comment_feedback` для 👍/👎
- [x] 1.7 Тесты: `test_publish_review`, обновление pipeline/handler, domain
