# Правила ревью: TypeScript и React

Шаблон `.review/rules.md` для фронтенда на React и TypeScript. Скопируйте файл в
`.review/rules.md` своего репозитория и поправьте паттерны под свою раскладку каталогов.
Абзацы вроде этого сервис модели не передаёт: правилом считается только раздел `##` с
`pattern:` и нумерованными проверками. Как писать свои правила — `docs/RULES_GUIDE.md`.

## React Components

- pattern: `*.tsx`
- exclude: `*.test.tsx`
- exclude: `*.stories.tsx`

1. Компонент не вызывает `fetch` напрямую: данные приходят через хук запроса (например, TanStack Query)
2. Элементы списка получают устойчивый `key` из данных, а не индекс массива
3. `useEffect` с подпиской, таймером или слушателем возвращает функцию очистки
4. Массивы зависимостей `useEffect`, `useMemo` и `useCallback` полные; отключённое правило `exhaustive-deps` объяснено комментарием
5. Состояние, которое вычисляется из пропсов или другого состояния, не хранится в `useState`

## Type Safety

- pattern: `*.ts`
- pattern: `*.tsx`
- exclude: `*.d.ts`

1. Нет `any`: неизвестные данные имеют тип `unknown` и сужаются проверкой
2. Ответ API проверяется схемой (например, zod) на границе, а не приводится через `as`
3. Нет `!` (non-null assertion) на значениях, которые могут отсутствовать
4. `@ts-ignore` и `@ts-expect-error` сопровождаются комментарием с причиной

## Accessibility

- pattern: `*.tsx`
- exclude: `*.test.tsx`
- exclude: `*.stories.tsx`

1. Кликабельный элемент — `button` или `a`, а не `div` или `span` с `onClick`
2. У изображения есть `alt`, у кнопки-иконки без текста — `aria-label`
3. Поле формы связано с подписью через `label` или `aria-labelledby`

## Browser Security

- pattern: `*.ts`
- pattern: `*.tsx`

1. `dangerouslySetInnerHTML` получает только очищенный HTML (например, через DOMPurify)
2. Токены доступа не хранятся в `localStorage` и `sessionStorage`
3. В переменных окружения сборки (`VITE_*`, `NEXT_PUBLIC_*`) нет секретов: они попадают в бандл
4. URL из пользовательского ввода не попадает в `href` или `window.location` без проверки схемы (`javascript:`)

## Frontend Tests

- pattern: `*.test.ts`
- pattern: `*.test.tsx`

1. Элементы ищутся по роли или тексту (`getByRole`, `getByText`), а не по классам CSS
2. Сетевые запросы подменены (например, MSW), тест не ходит в настоящий API
3. Тест не ждёт фиксированную задержку: используется `findBy*` или `waitFor`
