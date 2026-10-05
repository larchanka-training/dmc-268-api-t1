"""Шлюз к системе контроля версий.

Структурный протокол: адаптер никогда не импортирует этот модуль, и у слоя
инфраструктуры нет compile-time зависимости от слоя приложения. Каждая
сигнатура говорит в доменных типах: ниже нет ни httpx, ни GitHub.

Каждый метод принимает `installation_id` из payload'а вебхука: один сервис
работает с любым числом репозиториев без настройки инсталляции в конфигурации
(design `add-webhook-intake-and-vcs-gateway`, решение D2). Метода
`request_reviewer` нет и не будет: бот никогда не назначается нашей системой.

`VcsError` — общая шапка сбоев VCS, которую ловит вызывающий (по спеке вебхук
отвечает 502 на любой сбой VCS). Живёт в домене (`app.domain.vcs_errors`):
оттуда её импортируют и слой приложения, и адаптеры, не завися друг от друга.
"""

from typing import Protocol

from app.domain.entities import PRMetadata


class VcsGateway(Protocol):
    def fetch_diff(
        self, repo_full_name: str, base_sha: str, head_sha: str, installation_id: int
    ) -> str:
        """Unified diff пары коммитов base...head как есть, строкой.

        Пара SHA берётся из сообщения задачи, а не актуальный head `/pulls/{n}`:
        между доставкой вебхука и обработкой мог прийти `synchronize` — воркер
        обязан ревьюить тот коммит, для которого создан прогон.
        """
        ...

    def fetch_pr_metadata(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> PRMetadata:
        """Свежие метаданные; базовый коммит берётся только отсюда."""
        ...
