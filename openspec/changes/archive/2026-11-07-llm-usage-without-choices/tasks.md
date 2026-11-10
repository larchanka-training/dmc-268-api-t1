## 1. Правки

- [x] 1.1 `OpenAiCompatibleGateway._reply`: `usage` до `choices` и `model`, ответ без них — `RawReply.rejected`; тело не объект — «неожиданное тело»
- [x] 1.2 `OllamaGateway._chat`: `eval_count` до `message` и `model`, ответ без них — `RawReply.rejected`
- [x] 1.3 Тесты: расход записан при каждом виде ответа без ответа модели, фоллбэк не теряет первый расход, тело-список — «неожиданное тело» у обоих адаптеров
