"""Адаптер `VcsGateway` для GitHub REST API.

Порт не импортируется — совпадение структурное (`.agents/rules/backend.md`,
«Швы и порты»). Diff приходит телом ответа без преобразований; поля JSON
переводятся в `PRMetadata` один-в-один, включая перевод состояния PR,
описанный в самом `MergeRequestState`. Повторы - один цикл `_request`: 5xx -
экспоненциальный backoff, отказ лимита - пауза по заголовкам GitHub в пределах
бюджета; запросы без идемпотентности (ревью, ответ в тред) не повторяются.
Пауза и часы приходят инъекцией (`sleep`, `clock`), чтобы тесты не ждали
по-настоящему.

Любой ответ адаптер переводит в своё исключение: наверх добирается 502, а не
голый `JSONDecodeError` или `KeyError` от неожиданного тела (прокси отдал
HTML, GitHub сменил форму ответа).
"""

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote
from uuid import UUID

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
    VcsUnconfirmedError,
)
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from app.infrastructure.vcs.github_installations import is_rate_limited
from app.infrastructure.vcs.github_review import GitHubReviewClient, RetryBudget

_DIFF_ACCEPT = "application/vnd.github.v3.diff"
_JSON_ACCEPT = "application/vnd.github+json"
# Содержимое файла телом ответа, без base64-обёртки JSON.
_RAW_ACCEPT = "application/vnd.github.raw+json"


# Отказ лимита без `retry-after` и `x-ratelimit-reset`: «wait for at least one
# minute before retrying» («Rate limits for the REST API»).
_RATE_LIMIT_DEFAULT_WAIT_SECONDS = 60.0


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
        clock: Callable[[], float] = time.time,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
        rate_limit_budget_seconds: float = 60.0,
    ) -> None:
        """`rate_limit_budget_seconds` - сколько один запрос может ждать снятия лимита
        суммарно; `clock` - стенные часы для `x-ratelimit-reset` (эпоха, секунды)."""
        self._auth = auth
        self._client = client
        self._sleep = sleep
        self._clock = clock
        self._max_attempts = max_attempts
        self._backoff_base_seconds = backoff_base_seconds
        self._rate_limit_budget_seconds = rate_limit_budget_seconds
        self._bot_login: str | None = None
        self._review = GitHubReviewClient(self)

    def fetch_diff(
        self, repo_full_name: str, base_sha: str, head_sha: str, installation_id: int, *, max_bytes: int
    ) -> str:
        response = self._request(
            "GET",
            f"/repos/{repo_full_name}/compare/{base_sha}...{head_sha}",
            installation_id,
            accept=_DIFF_ACCEPT,
            max_bytes=max_bytes,
        )
        return response.text

    def fetch_pr_metadata(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> PRMetadata:
        response = self._request("GET", f"/repos/{repo_full_name}/pulls/{pr_number}", installation_id)
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
        *,
        review_run_id: UUID,
    ) -> PullRequestReviewPublication:
        return self._review.publish_pull_request_review(
            repo_full_name,
            pr_number,
            head_sha,
            installation_id,
            summary_body,
            inline_comments,
            review_run_id=review_run_id,
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
            response = self._request(
                "GET", url, installation_id, accept=_RAW_ACCEPT, max_bytes=max_bytes
            )
        except VcsNotFoundError:
            return None
        return response.text

    def _get_json_list(self, url: str, installation_id: int) -> list[dict[str, Any]]:
        response = self._request("GET", url, installation_id)
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

    def _post_json(
        self,
        url: str,
        installation_id: int,
        payload: dict[str, Any],
        *,
        repeatable: bool = True,
    ) -> dict[str, Any]:
        """POST с JSON-ответом-объектом.

        `repeatable=False` - запрос без идемпотентности (ревью, ответ в тред):
        на обрыв, 5xx и лимит адаптер его не повторяет, а бросает
        `VcsUnconfirmedError` - повтор опубликовал бы второй раз.
        """
        response = self._request("POST", url, installation_id, json=payload, repeatable=repeatable)
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
        # Запросы и `resolveReviewThread` повторяемы: повтор закрытия треда безвреден.
        response = self._request(
            "POST",
            "/graphql",
            installation_id,
            json={"query": query, "variables": variables},
            graphql=True,
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

    def bot_login(self) -> str:
        """Логин бота App на GitHub - `<slug>[bot]`; slug - из `GET /app` с JWT приложения.

        Запрашивается один раз на процесс и только когда нужен: при поиске
        своего ревью по маркеру.
        """
        if self._bot_login is not None:
            return self._bot_login
        try:
            response = self._client.get(
                "/app",
                headers={"Authorization": f"Bearer {self._auth.app_jwt()}", "Accept": _JSON_ACCEPT},
            )
        except httpx.RequestError as error:
            raise VcsUnavailableError(f"GitHub API недоступен на GET /app: {error}") from error
        if response.is_error:
            raise VcsError(f"GitHub API: HTTP {response.status_code} на GET /app")
        try:
            slug = response.json()["slug"]
        except (ValueError, KeyError, TypeError) as error:
            raise VcsError(f"GitHub API вернул неожиданное тело на GET /app: {error!r}") from error
        if not isinstance(slug, str) or not slug:
            raise VcsError("GitHub API вернул пустой slug на GET /app")
        self._bot_login = f"{slug}[bot]"
        return self._bot_login

    def _request(
        self,
        method: str,
        url: str,
        installation_id: int,
        *,
        accept: str = _JSON_ACCEPT,
        json: dict[str, Any] | None = None,
        max_bytes: int | None = None,
        repeatable: bool = True,
        graphql: bool = False,
    ) -> httpx.Response:
        """Один цикл повторов на все запросы к API.

        - 401 - один раз сбросить кэш installation token и взять новый: токен мог
          быть отозван до истечения (приостановка App, переустановка, ротация ключа);
        - лимит (429, 403 лимита, у GraphQL - ещё `RATE_LIMITED` при 200) - пауза
          по `retry-after`, иначе до `x-ratelimit-reset` при `x-ratelimit-remaining: 0`,
          иначе минута и вдвое больше на каждый следующий отказ («Rate limits for
          the REST API»); пауза сверх бюджета - сразу `VcsUnavailableError`;
        - 5xx - экспоненциальный backoff;
        - прочие ошибки - сразу ошибка адаптера (404 на GET - `VcsNotFoundError`).

        Обрыв сети не повторяется. Неповторяемый запрос (`repeatable=False`) на
        обрыв, 5xx и лимит бросает `VcsUnconfirmedError`.
        """
        where = f"{method} {url}"
        token = self._auth.installation_token(installation_id)
        token_refreshed = False
        retries = RetryBudget()
        while True:
            try:
                response = self._send(method, url, token, accept, json, max_bytes)
            except httpx.RequestError as error:
                failure = VcsUnavailableError if repeatable else VcsUnconfirmedError
                raise failure(f"GitHub API недоступен на {where}: {error}") from error
            status = response.status_code
            if status == 401:
                if token_refreshed:
                    raise VcsAuthError(f"GitHub API отклонил токен на {where}")
                self._auth.invalidate(installation_id)
                token = self._auth.installation_token(installation_id)
                token_refreshed = True
                continue
            limited = _rate_limited(response, graphql=graphql)
            if not response.is_error and not limited:
                return response
            if not limited and status < 500:
                if status == 404 and method == "GET":
                    raise VcsNotFoundError(f"GitHub API: HTTP 404 на {where}")
                raise VcsError(f"GitHub API: HTTP {status} на {where}")
            if not repeatable:
                raise VcsUnconfirmedError(
                    f"GitHub API: HTTP {status} на {where}; запрос без идемпотентности не повторяется",
                    rate_limited=response if limited else None,
                )
            self.wait_before_retry(retries, response if limited else None, where)

    def wait_before_retry(
        self, retries: RetryBudget, rate_limited: httpx.Response | None, where: str
    ) -> None:
        """Пауза перед следующей попыткой или `VcsUnavailableError`, если ждать нельзя.

        Попытки кончились - сбой. Отказ лимита (`rate_limited` - его ответ) -
        пауза по заголовкам GitHub, сумма пауз на запрос не больше бюджета;
        прочий сбой - экспоненциальный backoff. Общая для цикла `_request` и
        для публикации ревью, которая между попытками ищет своё ревью.
        """
        retries.attempt += 1
        if retries.attempt >= self._max_attempts:
            raise VcsUnavailableError(
                f"GitHub API отвечал 429/5xx {self._max_attempts} попытки подряд на {where}"
            )
        if rate_limited is not None:
            retries.rate_limit_hits += 1
            pause = _rate_limit_wait(rate_limited, self._clock(), retries.rate_limit_hits)
            if retries.waited + pause > self._rate_limit_budget_seconds:
                raise VcsUnavailableError(
                    f"лимит GitHub API на {where}: ждать {pause:g} с,"
                    f" бюджет ожидания {self._rate_limit_budget_seconds:g} с"
                )
            retries.waited += pause
        else:
            pause = self._backoff_base_seconds * 2 ** (retries.attempt - 1)
        self._sleep(pause)

    def _send(
        self,
        method: str,
        url: str,
        token: str,
        accept: str,
        json: dict[str, Any] | None,
        max_bytes: int | None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}", "Accept": accept}
        if max_bytes is None:
            return self._client.request(method, url, headers=headers, json=json)
        with self._client.stream(method, url, headers=headers, json=json) as response:
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


def _rate_limited(response: httpx.Response, *, graphql: bool) -> bool:
    """Отказ лимита GitHub: 429, 403 лимита, у GraphQL - ошибка лимита при 200.

    GraphQL отдаёт превышение лимита статусом 200 (первичный) или 200/403
    (вторичный) с сообщением в `errors` («Rate limits and query limits for
    the GraphQL API»); тип ошибки - `RATE_LIMITED`.
    """
    status = response.status_code
    if status == 429:
        return True
    if status == 403:
        return is_rate_limited(response)
    if not graphql or status != 200:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    errors = body.get("errors") if isinstance(body, dict) else None
    if not isinstance(errors, list):
        return False
    return any(
        isinstance(item, dict)
        and (item.get("type") == "RATE_LIMITED" or "rate limit" in str(item.get("message", "")).lower())
        for item in errors
    )


def _rate_limit_wait(response: httpx.Response, now: float, hits: int) -> float:
    """Сколько ждать перед повтором после `hits`-го отказа лимита подряд, секунды."""
    retry_after = response.headers.get("retry-after")
    if retry_after is not None:
        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            pass
    if response.headers.get("x-ratelimit-remaining") == "0":
        try:
            # Ждём до момента сброса и секунду сверх: повтор - «после» него.
            return max(float(response.headers["x-ratelimit-reset"]) - now, 0.0) + 1.0
        except (KeyError, ValueError):
            pass
    return _RATE_LIMIT_DEFAULT_WAIT_SECONDS * 2.0 ** (hits - 1)


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
