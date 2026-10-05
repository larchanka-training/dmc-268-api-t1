"""Адаптер `VcsGateway` для GitHub REST API.

Порт не импортируется — совпадение структурное (`.agents/rules/backend.md`,
«Швы и порты»). Diff приходит телом ответа без преобразований; поля JSON
переводятся в `PRMetadata` один-в-один, включая перевод состояния PR,
описанный в самом `MergeRequestState`. На 429/5xx — экспоненциальный backoff
с ограниченным числом попыток; пауза приходит инъекцией (`sleep`), чтобы
тесты не ждали по-настоящему.

Любой ответ адаптер переводит в своё исключение: наверх добирается 502, а не
голый `JSONDecodeError` или `KeyError` от неожиданного тела (прокси отдал
HTML, GitHub сменил форму ответа).
"""

import time
from collections.abc import Callable
from typing import Any

import httpx

from app.domain.entities import PRMetadata
from app.domain.enums import MergeRequestState
from app.infrastructure.vcs.errors import (
    VcsAuthError,
    VcsError,
    VcsUnavailableError,
)
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
        self, repo_full_name: str, base_sha: str, head_sha: str, installation_id: int
    ) -> str:
        response = self._get(
            f"/repos/{repo_full_name}/compare/{base_sha}...{head_sha}",
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
        try:
            data: dict[str, Any] = response.json()
            state = _translate_state(data)
            return PRMetadata(
                number=data["number"],
                head_sha=data["head"]["sha"],
                base_sha=data["base"]["sha"],
                title=data["title"],
                author=data["user"]["login"],
                source_branch=data["head"]["ref"],
                target_branch=data["base"]["ref"],
                state=state,
            )
        except (ValueError, KeyError, TypeError) as error:
            raise VcsError(
                "GitHub API вернул неожиданное тело на GET"
                f" /repos/{repo_full_name}/pulls/{pr_number}: {error!r}"
            ) from error

    def _get(self, url: str, installation_id: int, accept: str) -> httpx.Response:
        """GET с backoff: 429/5xx повторяются; 401 один раз сбрасывает кэш
        installation token и берёт новый (токен мог быть отозван до истечения);
        прочие клиентские ошибки или обрыв сети — сразу ошибка адаптера, чтобы
        наверх добрался 502, а не 500."""
        token = self._auth.installation_token(installation_id)
        token_refreshed = False
        attempt = 0
        while True:
            response = self._send(url, token, accept)
            if not response.is_error:
                return response
            if response.status_code == 401 and not token_refreshed:
                # Формально не истёкший, но отозванный токен (приостановка App,
                # переустановка, ротация ключа) жил бы в кэше до конца TTL:
                # сбрасываем и пробуем свежим ровно один раз.
                self._auth.invalidate(installation_id)
                token = self._auth.installation_token(installation_id)
                token_refreshed = True
                continue
            if response.status_code == 401:
                raise VcsAuthError(f"GitHub API отклонил токен на GET {url}")
            attempt += 1
            if not _is_retryable(response.status_code):
                raise VcsError(f"GitHub API: HTTP {response.status_code} на GET {url}")
            if attempt >= self._max_attempts:
                raise VcsUnavailableError(
                    f"GitHub API отвечал 429/5xx {self._max_attempts} попытки подряд"
                    f" на GET {url}"
                )
            self._sleep(self._backoff_base_seconds * 2 ** (attempt - 1))

    def _send(self, url: str, token: str, accept: str) -> httpx.Response:
        try:
            return self._client.get(
                url, headers={"Authorization": f"Bearer {token}", "Accept": accept}
            )
        except httpx.RequestError as error:
            raise VcsUnavailableError(
                f"GitHub API недоступен на GET {url}: {error}"
            ) from error
