# TC-19 — синтаксический шум

Код корректен, но перегружен: `== True` и `if/else` вокруг булева выражения, цикл по
`range(len(...))` с `append` вместо генератора списка, `list(sorted(list(set([str(...)]))))`.

- `bool-compare-and-if-return` (readability, low, must_find) — строка `== True`.
- `manual-index-loop` (readability, low, must_find) — `for i in range(len(findings))`.
- `redundant-conversions` (readability, low, must_find) — цепочка преобразований в
  `file_paths`.

`security` и `performance` запрещены: лишние копии на списке находок не измеримы, а
уязвимостей нет. В эталонном ответе третья находка указана на строку выше — на
заголовок функции `file_paths`, в окне ±3.
