## Why

Автоматическое ревью безопасности коммитов #222 нашло дыры в исправлении обхода merge gate сменой целевой ветки (`review-on-base-change`), а разбор нашёл ещё одну, старше:

- Между `edited` и окончанием нового ревью на head-коммите висит прежний `success` по старому диффу - окно для мерджа; если новое ревью упадёт, `success` остаётся навсегда.
- Прогон без записанной базы считался дублем и для `edited` - fail open.
- Пересчёт merge gate по закрытию треда брал `find_reviewed`: после смены ветки - прогон на старой базе; после push, пока ревью нового head идёт, - никакого, и выставлял `success` непроверенному коммиту при отсутствии открытых тредов.
- Прогон, отменённый сменой ветки во время публикации, всё равно выставлял gate.

## What Changes

- Приём при смене целевой ветки до записи нового прогона выставляет check run `failure` («идёт повторное ревью»); сбой GitHub - 502 без записей, доставку повторит #149.
- `edited` не считает прогон без базы дублем.
- Пересчёт gate по закрытию треда - только если последний прогон на head (`find_latest`) `completed`.
- Публикация перечитывает статус прогона и не выставляет gate за `cancelled`.

## Capabilities

### Modified Capabilities

- `webhook-intake`: ADDED «Смена целевой ветки блокирует merge gate до нового ревью»; MODIFIED «Повторная доставка отвечает существующим прогоном».
- `review-pipeline`: ADDED «Пересчёт merge gate только по завершённому ревью head», «Отменённый прогон не выставляет merge gate».

## Impact

- Код: `app/application/use_cases/handle_webhook.py`, `merge_gate_publish.py` (`build_retarget_check`), `sync_thread_resolution.py`, `publish_review.py`, `ReviewRunRepo.find_latest` (порт, SQL, фейк).
- Приём теперь может выставлять check run: API ходит в `POST /check-runs` тем же installation-токеном, разрешение Checks: write у App уже есть.
- Схема базы, контракт API и настройки не меняются.
