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

from app.application.ports.vcs_review import (
    InlineCommentDraft,
    MergeGateCheck,
    PullRequestReviewPublication,
)
from app.domain.entities import PRMetadata

CHECK_RUN_NAME = "dmc-268-ai-review"


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

    def fetch_pr_commit_messages(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> tuple[str, ...]:
        """Сообщения коммитов PR от нового к старому - не больше, чем отдаёт VCS.

        GitHub отдаёт не больше 250 коммитов PR, и это самые старые: на более
        длинном PR новые коммиты не приходят. Крайний случай, не обходится.

        Текст автора PR: в пакет контекста он попадает только блоком
        недоверенных данных (change `add-llm-gateway`).
        """
        ...

    def fetch_file(
        self, repo_full_name: str, path: str, sha: str, installation_id: int, *, max_bytes: int
    ) -> str | None:
        """Содержимое файла на коммите `sha` или `None`, если файла там нет.

        Отсутствие — штатный исход (у репозитория нет `.review/rules.md`), а не
        сбой: `VcsError` означает, что VCS ответить не смогла. Путь и размер
        файла задаёт автор PR, поэтому лимит `max_bytes` соблюдается во время
        загрузки, а не после: тело больше лимита не дочитывается —
        `VcsFileTooLargeError`.
        """
        ...

    def publish_pull_request_review(
        self,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        installation_id: int,
        summary_body: str,
        inline_comments: tuple[InlineCommentDraft, ...],
    ) -> PullRequestReviewPublication:
        """Один review на прогон: сводка и пакет инлайн-комментариев."""
        ...

    def reply_to_review_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_id: str,
        body: str,
    ) -> str:
        """Ответ в существующий review comment; возвращает id нового комментария."""
        ...

    def fetch_review_threads_resolved_state(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_ids: frozenset[str],
    ) -> dict[str, bool]:
        """Для каждого id корневого комментария — `isResolved` треда на хостинге."""
        ...

    def resolve_review_thread_for_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_id: str,
    ) -> None:
        """Закрыть тред, в котором лежит корневой комментарий."""
        ...

    def set_merge_gate_check(
        self,
        repo_full_name: str,
        head_sha: str,
        installation_id: int,
        check: MergeGateCheck,
    ) -> None:
        """Check run на head коммита — блокировка мерджа при conclusion=failure."""
        ...
