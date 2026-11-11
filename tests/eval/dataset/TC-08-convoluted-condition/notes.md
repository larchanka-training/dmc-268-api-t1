# TC-08 — запутанное условие

Основной дефект — читаемость: одно выражение с вложенными `not`, смешанными
`and`/`or` без скобок, `labels.count(...) > 0` вместо `in` и `== False`.

- `convoluted-condition` (readability, medium, must_find) — начало выражения
  `return not (`; строки условия в окне ±3.
- `bot-check-inverted` (correctness, high, nice-to-have) — `not (A or B) == False`
  равно `A or B`, то есть третья ветка исключает людей и разрешённых ботов. Дефект
  спрятан в нечитаемом условии — ради этого кейс и смешанный; раскрутить его
  модели трудно, поэтому nice-to-have.

`security` и `performance` запрещены: чистая функция над событием.
