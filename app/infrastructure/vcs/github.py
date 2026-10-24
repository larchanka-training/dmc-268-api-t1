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
from urllib.parse import quote

import httpx

from app.application.ports.vcs_review import (
    InlineCommentDraft,
    MergeGateCheck,
    PullRequestReviewPublication,
    ReviewThreadState,
)
from app.domain.entities import PRMetadata
from app.domain.enums import MergeRequestState
from app.domain.vcs_errors import VcsFileTooLargeError
from app.infrastructure.vcs.errors import (
    VcsAuthError,
    VcsError,
    VcsNotFoundError,
    VcsUnavailableError,
)
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from app.infrastructure.vcs.github_review import GitHubReviewClient

_DIFF_ACCEPT = "application/vnd.github.v3.diff"
_JSON_ACCEPT = "application/vnd.github+json"
# Содержимое файла телом ответа, без base64-обёртки JSON.
_RAW_ACCEPT = "application/vnd.github.raw+json"


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
        self._review = GitHubReviewClient(self)

    def fetch_diff(
        self, repo_full_name: str, base_sha: str, head_sha: str, installation_id: int, *, max_bytes: int
    ) -> str:
        response = self._get(
            f"/repos/{repo_full_name}/compare/{base_sha}...{head_sha}",
            installation_id,
            _DIFF_ACCEPT,
            max_bytes=max_bytes,
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
                # PR без описания GitHub отдаёт с `body: null`.
                description=data.get("body") or "",
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

    def fetch_pr_commit_messages(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> tuple[str, ...]:
        # GitHub отдаёт коммиты PR от старого к новому и не больше 250 - первые
        # 250, так что на более длинном PR теряются новые, а не старые. Это
        # не больше трёх страниц по 100.
        url = f"/repos/{repo_full_name}/pulls/{pr_number}/commits"
        commits = self._get_json_list_paginated(url, installation_id)
        try:
            messages = [str(item["commit"]["message"]) for item in commits]
        except (KeyError, TypeError) as error:
            raise VcsError(f"GitHub API вернул коммит без сообщения на GET {url}") from error
        return tuple(reversed(messages))

    def publish_pull_request_review(
        self,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        installation_id: int,
        summary_body: str,
        inline_comments: tuple[InlineCommentDraft, ...],
    ) -> PullRequestReviewPublication:
        return self._review.publish_pull_request_review(
            repo_full_name,
            pr_number,
            head_sha,
            installation_id,
            summary_body,
            inline_comments,
        )

    def reply_to_review_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_id: str,
        body: str,
    ) -> str:
        return self._review.reply_to_review_comment(
            repo_full_name, pr_number, installation_id, provider_comment_id, body
        )

    def fetch_review_threads_state(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_ids: frozenset[str],
    ) -> dict[str, ReviewThreadState]:
        return self._review.fetch_review_threads_state(
            repo_full_name, pr_number, installation_id, provider_comment_ids
        )

    def resolve_review_thread_for_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_id: str,
    ) -> None:
        self._review.resolve_review_thread_for_comment(
            repo_full_name, pr_number, installation_id, provider_comment_id
        )

    def set_merge_gate_check(
        self,
        repo_full_name: str,
        head_sha: str,
        installation_id: int,
        check: MergeGateCheck,
    ) -> None:
        self._review.set_merge_gate_check(repo_full_name, head_sha, installation_id, check)

    def fetch_file(
        self, repo_full_name: str, path: str, sha: str, installation_id: int, *, max_bytes: int
    ) -> str | None:
        url = f"/repos/{repo_full_name}/contents/{quote(path, safe='/')}?ref={quote(sha)}"
        try:
            response = self._get(url, installation_id, _RAW_ACCEPT, max_bytes=max_bytes)
        except VcsNotFoundError:
            return None
        return response.text

    def _get_json_list(self, url: str, installation_id: int) -> list[dict[str, Any]]:
        response = self._request_json("GET", url, installation_id)
        try:
            data = response.json()
        except ValueError as error:
            raise VcsError(f"GitHub API вернул не-JSON на GET {url}") from error
        if not isinstance(data, list):
            raise VcsError(f"GitHub API вернул не массив на GET {url}")
        return [item for item in data if isinstance(item, dict)]

    def _get_json_list_paginated(self, url: str, installation_id: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            separator = "&" if "?" in url else "?"
            page_url = f"{url}{separator}per_page=100&page={page}"
            batch = self._get_json_list(page_url, installation_id)
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1

    def _post_json(self, url: str, installation_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._request_json("POST", url, installation_id, json=payload)
        try:
            data: dict[str, Any] = response.json()
        except ValueError as error:
            raise VcsError(f"GitHub API вернул не-JSON на POST {url}") from error
        if not isinstance(data, dict):
            raise VcsError(f"GitHub API вернул не объект на POST {url}")
        return data

    def _post_graphql(
        self, installation_id: int, query: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        response = self._request_json(
            "POST",
            "/graphql",
            installation_id,
            json={"query": query, "variables": variables},
        )
        try:
            body: dict[str, Any] = response.json()
        except ValueError as error:
            raise VcsError("GitHub GraphQL вернул не-JSON") from error
        if body.get("errors"):
            raise VcsError(f"GitHub GraphQL: {body['errors']}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise VcsError("GitHub GraphQL: нет поля data")
        return data

    def _request_json(
        self,
        method: str,
        url: str,
        installation_id: int,
        *,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        token = self._auth.installation_token(installation_id)
        token_refreshed = False
        attempt = 0
        while True:
            try:
                response = self._client.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {token}", "Accept": _JSON_ACCEPT},
                    json=json,
                )
            except httpx.RequestError as error:
                raise VcsUnavailableError(
                    f"GitHub API недоступен на {method} {url}: {error}"
                ) from error
            if not response.is_error:
                return response
            if response.status_code == 401 and not token_refreshed:
                self._auth.invalidate(installation_id)
                token = self._auth.installation_token(installation_id)
                token_refreshed = True
                continue
            if response.status_code == 401:
                raise VcsAuthError(f"GitHub API отклонил токен на {method} {url}")
            attempt += 1
            if not _is_retryable(response.status_code):
                raise VcsError(f"GitHub API: HTTP {response.status_code} на {method} {url}")
            if attempt >= self._max_attempts:
                raise VcsUnavailableError(
                    f"GitHub API отвечал 429/5xx {self._max_attempts} попытки подряд"
                    f" на {method} {url}"
                )
            self._sleep(self._backoff_base_seconds * 2 ** (attempt - 1))

    def _get(
        self, url: str, installation_id: int, accept: str, *, max_bytes: int | None = None
    ) -> httpx.Response:
        """GET с backoff: 429/5xx повторяются; 401 один раз сбрасывает кэш
        installation token и берёт новый (токен мог быть отозван до истечения);
        прочие клиентские ошибки или обрыв сети — сразу ошибка адаптера, чтобы
        наверх добрался 502, а не 500."""
        token = self._auth.installation_token(installation_id)
        token_refreshed = False
        attempt = 0
        while True:
            response = self._send(url, token, accept, max_bytes)
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
            if response.status_code == 404:
                raise VcsNotFoundError(f"GitHub API: HTTP 404 на GET {url}")
            attempt += 1
            if not _is_retryable(response.status_code):
                raise VcsError(f"GitHub API: HTTP {response.status_code} на GET {url}")
            if attempt >= self._max_attempts:
                raise VcsUnavailableError(
                    f"GitHub API отвечал 429/5xx {self._max_attempts} попытки подряд"
                    f" на GET {url}"
                )
            self._sleep(self._backoff_base_seconds * 2 ** (attempt - 1))

    def _send(self, url: str, token: str, accept: str, max_bytes: int | None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}", "Accept": accept}
        try:
            if max_bytes is None:
                return self._client.get(url, headers=headers)
            with self._client.stream("GET", url, headers=headers) as response:
                if response.is_error:
                    response.read()
                    return response
                content = _read_limited(response, max_bytes, url)
                # Тело уже распаковано: заголовки кодировки и длины относятся
                # к тому, что пришло по сети, а не к этим байтам.
                kept = [
                    (k, v)
                    for k, v in response.headers.items()
                    if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
                ]
                return httpx.Response(
                    response.status_code, headers=kept, content=content, request=response.request
                )
        except httpx.RequestError as error:
            raise VcsUnavailableError(
                f"GitHub API недоступен на GET {url}: {error}"
            ) from error


def _read_limited(response: httpx.Response, max_bytes: int, url: str) -> bytes:
    """Прочитать тело потоком, оборвав на `max_bytes`.

    Объявленная длина отсекает большой файл до первого байта; поток
    распакованного тела — и файл без `Content-Length`, и сжатую «бомбу».
    """
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise VcsFileTooLargeError(f"GET {url}: {declared} байт больше лимита {max_bytes}")
    body = bytearray()
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > max_bytes:
            raise VcsFileTooLargeError(f"GET {url}: тело больше лимита {max_bytes} байт")
    return bytes(body)
