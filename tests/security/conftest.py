"""Security-сьют #222: попытки манипуляции через PR на сквозном стенде.

Стенд - `tests/e2e/stack.py`; атакующий - автор PR: он пишет код, описание,
сообщения коммитов, имена файлов, выбирает целевую ветку и может толкать свои
ветки в репозиторий. Владелец репозитория пишет `.review/rules.md` на целевой ветке.
"""

from ..e2e.conftest import _no_unexpected_github_requests

__all__ = ["_no_unexpected_github_requests"]
