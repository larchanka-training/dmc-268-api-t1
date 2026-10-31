"""Chaos-сьют: отказы внешних систем и процесса на сквозном стенде (#222).

Стенд тот же, что у E2E (`tests/e2e/stack.py`): настоящие приём, база,
RabbitMQ, воркер и адаптеры. Сбой подаётся там, где он случается в проде: у
модели - из HTTP-транспорта настоящего `OpenAiCompatibleGateway`
(`FakeLlmProvider`), у GitHub - из транспорта `GitHubVcsGateway`
(`FakeGitHub.network_faults`), смерть воркера - `BaseException` посреди
обработки, которую consume-цикл не ловит.

Сьют помечен `chaos` и идёт отдельным job'ом CI; основной `pytest` его не собирает.
"""

from ..e2e.conftest import _no_unexpected_github_requests

__all__ = ["_no_unexpected_github_requests"]
