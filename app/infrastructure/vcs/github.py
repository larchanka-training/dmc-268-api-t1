"""Адаптер `VcsGateway` для GitHub REST API.

Порт не импортируется — совпадение структурное (`.agents/rules/backend.md`,
«Швы и порты»). Diff приходит телом ответа без преобразований; поля JSON
переводятся в `PRMetadata` один-в-один, включая перевод состояния PR,
описанный в самом `MergeRequestState`. На 429/5xx — экспоненциальный backoff
с ограниченным числом попыток; пауза приходит инъекцией (`sleep`), чтобы
тесты не ждали по-настоящему.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx

from app.domain.entities import PRMetadata
from app.domain.enums import MergeRequestState
from app.infrastructure.vcs.errors import VcsError, VcsUnavailableError
from app.infrastructure.vcs.github_auth import GitHubAppAuth

_DIFF_ACCEPT = "application/vnd.github.v3.diff"
_JSON_ACCEPT = "application/vnd.github+json"


def _is_retryable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _translate_state(data: dict[str, Any]) -> MergeRequestState:
    if data["state"] == "open":
        return MergeRequestState.OPEN
    if data.get("merged"):
        return MergeRequestState.MERGED
    return MergeRequestState.CLOSED


class GitHubVcsGateway:
    """Дифф и метаданные PR через GitHub API с installation token."""

    def __init__(
        self,
        auth: GitHubAppAuth,
        client: httpx.Client,
        *,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
    ) -> None:
        self._auth = auth
        self._client = client
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._backoff_base_seconds = backoff_base_seconds

    def fetch_diff(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> str:
        response = self._get(
            f"/repos/{repo_full_name}/pulls/{pr_number}",
            installation_id,
            _DIFF_ACCEPT,
        )
        return response.text

    def fetch_pr_metadata(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> PRMetadata:
        response = self._get(
            f"/repos/{repo_full_name}/pulls/{pr_number}",
            installation_id,
            _JSON_ACCEPT,
        )
        data: dict[str, Any] = response.json()
        return PRMetadata(
            number=data["number"],
            head_sha=data["head"]["sha"],
            base_sha=data["base"]["sha"],
            title=data["title"],
            author=data["user"]["login"],
            source_branch=data["head"]["ref"],
            target_branch=data["base"]["ref"],
            state=_translate_state(data),
        )

    def _get(self, url: str, installation_id: int, accept: str) -> httpx.Response:
        """GET с backoff: 429/5xx повторяются; клиентская ошибка или обрыв
        сети — сразу ошибка адаптера, чтобы наверх добрался 502, а не 500."""
        token = self._auth.installation_token(installation_id)
        headers = {"Authorization": f"Bearer {token}", "Accept": accept}
        for attempt in range(self._max_attempts):
            try:
                response = self._client.get(url, headers=headers)
            except httpx.RequestError as error:
                raise VcsUnavailableError(
                    f"GitHub API недоступен на GET {url}: {error}"
                ) from error
            if not response.is_error:
                return response
            if not _is_retryable(response.status_code):
                raise VcsError(f"GitHub API: HTTP {response.status_code} на GET {url}")
            if attempt + 1 < self._max_attempts:
                self._sleep(self._backoff_base_seconds * 2**attempt)
        raise VcsUnavailableError(
            f"GitHub API отвечал 429/5xx {self._max_attempts} попытки подряд"
            f" на GET {url}"
        )
