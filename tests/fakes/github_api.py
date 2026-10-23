"""In-memory GitHub REST за `httpx.MockTransport`: граница VCS на уровне HTTP.

Фейк порта (`FakeVcs`, `RecordingVcsGateway`) обходит адаптер целиком. Этот
фейк стоит за ним: настоящие `GitHubAppAuth` и `GitHubVcsGateway` подписывают
JWT приложения, получают installation token, шлют заголовки `Accept`, читают
файл потоком с лимитом и разбирают ответы, а фейк отвечает так, как отвечает
GitHub, и запоминает, что опубликовано.

Строгость там, где её проверяет настоящий GitHub: JWT подписан ключом
приложения, запросы к репозиторию несут выданный токен, inline-комментарий
стоит на строке диффа (иначе 422). Неизвестный маршрут - 404, и он
записывается в `unexpected`: тест видит запрос, которого не ждал.

Потокобезопасен: нагрузочный прогон ходит в него из нескольких воркеров.
"""

import difflib
import itertools
import json
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

API_URL = "https://api.github.com"
APP_ID = "424242"


def app_private_key() -> str:
    """Свежий RSA-ключ приложения в PEM: в репозитории ключей нет даже тестовых."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def file_diff(path: str, before: str | None, after: str | None) -> str:
    """Дифф одного файла в формате `git diff`; `None` - файла на этой стороне нет."""
    old_lines = (before or "").splitlines(keepends=True)
    new_lines = (after or "").splitlines(keepends=True)
    header = [f"diff --git a/{path} b/{path}\n"]
    if before is None:
        header.append("new file mode 100644\n")
    elif after is None:
        header.append("deleted file mode 100644\n")
    body = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="/dev/null" if before is None else f"a/{path}",
        tofile="/dev/null" if after is None else f"b/{path}",
        n=3,
    )
    return "".join(header) + "".join(line if line.endswith("\n") else line + "\n" for line in body)


@dataclass
class PullRequest:
    number: int
    base_sha: str
    head_sha: str
    diff: str
    title: str = "feat: изменение"
    body: str = ""
    author: str = "octocat"
    base_ref: str = "develop"
    head_ref: str = "feat/change"
    # От старого к новому, как отдаёт GitHub.
    commit_messages: tuple[str, ...] = ("feat: изменение",)


@dataclass(frozen=True)
class InlineComment:
    id: int
    path: str
    line: int
    side: str
    body: str


@dataclass(frozen=True)
class Review:
    id: int
    pr_number: int
    commit_id: str
    body: str
    event: str
    comments: tuple[InlineComment, ...]


@dataclass(frozen=True)
class CheckRun:
    id: int
    name: str
    head_sha: str
    conclusion: str
    title: str
    summary: str


@dataclass
class FakeGitHub:
    """Один репозиторий одной инсталляции GitHub App."""

    repo_full_name: str
    installation_id: int
    private_key: str
    app_id: str = APP_ID
    # Задержка каждого ответа: нагрузочный прогон моделирует сеть до GitHub.
    latency_seconds: float = 0.0
    files: dict[tuple[str, str], str] = field(default_factory=dict)
    pulls: dict[int, PullRequest] = field(default_factory=dict)
    reviews: list[Review] = field(default_factory=list)
    check_runs: list[CheckRun] = field(default_factory=list)
    resolved_threads: set[str] = field(default_factory=set)
    # Метод и путь с query каждого запроса к API, кроме выдачи токена.
    requests: list[tuple[str, str]] = field(default_factory=list)
    unexpected: list[tuple[str, str]] = field(default_factory=list)
    tokens_issued: int = 0
    _tokens: set[str] = field(default_factory=set)
    _ids: Any = field(default_factory=lambda: itertools.count(1001))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- состояние -------------------------------------------------------

    def put_file(self, path: str, sha: str, content: str) -> None:
        with self._lock:
            self.files[(path, sha)] = content

    def open_pull(
        self,
        number: int,
        *,
        base_sha: str,
        head_sha: str,
        base_files: Mapping[str, str] | None = None,
        head_files: Mapping[str, str | None],
        **details: Any,
    ) -> PullRequest:
        """PR, чей дифф получается из файлов обеих сторон.

        `head_files` - изменённые в PR файлы (`None` - удалён); `base_files` -
        всё, что лежит на коммите целевой ветки, включая `.review/rules.md`.
        Неизменённые файлы базы видны и на head, как в настоящем репозитории.
        """
        base = dict(base_files or {})
        head = {**base, **{p: c for p, c in head_files.items() if c is not None}}
        for path in head_files:
            if head_files[path] is None:
                head.pop(path, None)
        diff = "".join(file_diff(p, base.get(p), head_files[p]) for p in head_files)
        pull = PullRequest(number, base_sha, head_sha, diff, **details)
        with self._lock:
            for path, content in base.items():
                self.files[(path, base_sha)] = content
            for path, content in head.items():
                self.files[(path, head_sha)] = content
            self.pulls[number] = pull
        return pull

    def reviews_of(self, number: int) -> list[Review]:
        with self._lock:
            return [r for r in self.reviews if r.pr_number == number]

    # --- транспорт -------------------------------------------------------

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=API_URL, transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.latency_seconds:
            time.sleep(self.latency_seconds)
        path = unquote(request.url.path)
        method = request.method
        if (
            m := re.fullmatch(r"/app/installations/(\d+)/access_tokens", path)
        ) and method == "POST":
            return self._issue_token(request, int(m.group(1)))
        target = path + (f"?{request.url.query.decode()}" if request.url.query else "")
        with self._lock:
            self.requests.append((method, target))
        if not self._authorized(request):
            return httpx.Response(401, json={"message": "Bad credentials"})
        repo = f"/repos/{self.repo_full_name}"
        routes: list[tuple[str, str, Any]] = [
            ("GET", rf"{repo}/pulls/(\d+)", self._pull),
            ("GET", rf"{repo}/pulls/(\d+)/commits", self._commits),
            ("GET", rf"{repo}/compare/([0-9a-f]+)\.\.\.([0-9a-f]+)", self._compare),
            ("GET", rf"{repo}/contents/(.+)", self._contents),
            ("POST", rf"{repo}/pulls/(\d+)/reviews", self._create_review),
            ("GET", rf"{repo}/pulls/(\d+)/reviews/(\d+)/comments", self._review_comments),
            ("POST", rf"{repo}/check-runs", self._create_check_run),
            ("POST", r"/graphql", self._graphql),
        ]
        for route_method, pattern, handler in routes:
            if method == route_method and (m := re.fullmatch(pattern, path)):
                response: httpx.Response = handler(request, *m.groups())
                return response
        with self._lock:
            self.unexpected.append((method, target))
        return httpx.Response(404, json={"message": "Not Found"})

    # --- маршруты --------------------------------------------------------

    def _issue_token(self, request: httpx.Request, installation_id: int) -> httpx.Response:
        if installation_id != self.installation_id:
            return httpx.Response(404, json={"message": "Not Found"})
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ")
        public_key = serialization.load_pem_private_key(
            self.private_key.encode("ascii"), password=None
        ).public_key()
        try:
            claims = jwt.decode(bearer, public_key, algorithms=["RS256"])  # type: ignore[arg-type]
        except jwt.PyJWTError:
            return httpx.Response(401, json={"message": "A JSON web token could not be decoded"})
        if str(claims.get("iss")) != self.app_id:
            return httpx.Response(401, json={"message": "Integration not found"})
        with self._lock:
            self.tokens_issued += 1
            token = f"ghs_fake_{self.tokens_issued}"
            self._tokens.add(token)
        expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        return httpx.Response(201, json={"token": token, "expires_at": expires_at})

    def _authorized(self, request: httpx.Request) -> bool:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        with self._lock:
            return token in self._tokens

    def _find_pull(self, number: str) -> PullRequest | None:
        with self._lock:
            return self.pulls.get(int(number))

    def _pull(self, request: httpx.Request, number: str) -> httpx.Response:
        pull = self._find_pull(number)
        if pull is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(
            200,
            json={
                "number": pull.number,
                "state": "open",
                "merged": False,
                "title": pull.title,
                "body": pull.body or None,
                "user": {"login": pull.author},
                "head": {"sha": pull.head_sha, "ref": pull.head_ref},
                "base": {"sha": pull.base_sha, "ref": pull.base_ref},
            },
        )

    def _commits(self, request: httpx.Request, number: str) -> httpx.Response:
        pull = self._find_pull(number)
        if pull is None:
            return httpx.Response(404, json={"message": "Not Found"})
        page = int(request.url.params.get("page", "1"))
        items = [{"commit": {"message": m}} for m in pull.commit_messages] if page == 1 else []
        return httpx.Response(200, json=items)

    def _compare(self, request: httpx.Request, base: str, head: str) -> httpx.Response:
        if "diff" not in request.headers.get("Accept", ""):
            return httpx.Response(415, json={"message": "Unsupported Accept"})
        with self._lock:
            pull = next(
                (p for p in self.pulls.values() if (p.base_sha, p.head_sha) == (base, head)), None
            )
        if pull is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, text=pull.diff)

    def _contents(self, request: httpx.Request, path: str) -> httpx.Response:
        ref = request.url.params.get("ref", "")
        with self._lock:
            content = self.files.get((path, ref))
        if content is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if "raw" not in request.headers.get("Accept", ""):
            return httpx.Response(415, json={"message": "Unsupported Accept"})
        return httpx.Response(200, text=content)

    def _create_review(self, request: httpx.Request, number: str) -> httpx.Response:
        pull = self._find_pull(number)
        if pull is None:
            return httpx.Response(404, json={"message": "Not Found"})
        payload = json.loads(request.content)
        commentable = _commentable_lines(pull.diff)
        comments: list[InlineComment] = []
        for raw in payload.get("comments", []):
            side = raw.get("side", "RIGHT")
            if (raw["path"], side, raw["line"]) not in commentable:
                return httpx.Response(
                    422,
                    json={
                        "message": "Unprocessable Entity",
                        "errors": ["Line could not be resolved"],
                    },
                )
            comments.append(
                InlineComment(next(self._ids), raw["path"], raw["line"], side, raw["body"])
            )
        review = Review(
            id=next(self._ids),
            pr_number=pull.number,
            commit_id=payload["commit_id"],
            body=payload.get("body", ""),
            event=payload["event"],
            comments=tuple(comments),
        )
        with self._lock:
            self.reviews.append(review)
        return httpx.Response(200, json={"id": review.id, "body": review.body})

    def _review_comments(
        self, request: httpx.Request, number: str, review_id: str
    ) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        with self._lock:
            review = next((r for r in self.reviews if r.id == int(review_id)), None)
        if review is None:
            return httpx.Response(404, json={"message": "Not Found"})
        items = (
            [
                {"id": c.id, "path": c.path, "line": c.line, "side": c.side, "body": c.body}
                for c in review.comments
            ]
            if page == 1
            else []
        )
        return httpx.Response(200, json=items)

    def _create_check_run(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        run = CheckRun(
            id=next(self._ids),
            name=payload["name"],
            head_sha=payload["head_sha"],
            conclusion=payload["conclusion"],
            title=payload["output"]["title"],
            summary=payload["output"]["summary"],
        )
        with self._lock:
            self.check_runs.append(run)
        return httpx.Response(201, json={"id": run.id})

    def _graphql(self, request: httpx.Request) -> httpx.Response:
        """Треды ревью: каждый inline-комментарий открывает свой тред, как в GitHub."""
        payload = json.loads(request.content)
        query: str = payload["query"]
        variables: dict[str, Any] = payload.get("variables") or {}
        if "resolveReviewThread" in query:
            with self._lock:
                self.resolved_threads.add(variables["threadId"])
            return httpx.Response(
                200, json={"data": {"resolveReviewThread": {"thread": {"isResolved": True}}}}
            )
        if "reviewThreads" in query:
            with self._lock:
                comments = [
                    c
                    for r in self.reviews
                    if r.pr_number == variables["number"]
                    for c in r.comments
                ]
                nodes = [
                    {
                        "id": f"T_{c.id}",
                        "isResolved": f"T_{c.id}" in self.resolved_threads,
                        "comments": {"nodes": [{"databaseId": c.id}]},
                    }
                    for c in comments
                ]
            threads = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}
            return httpx.Response(
                200, json={"data": {"repository": {"pullRequest": {"reviewThreads": threads}}}}
            )
        with self._lock:
            self.unexpected.append(("POST", f"/graphql {query.split()[0:2]}"))
        return httpx.Response(200, json={"errors": [{"message": "unsupported query"}]})


_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _commentable_lines(diff: str) -> set[tuple[str, str, int]]:
    """Строки, к которым GitHub принимает inline-комментарий: (путь, сторона, номер).

    Свой разбор, а не `parse_diff` сервиса: фейк проверяет адаптер и ядро,
    и общий с ними код сделал бы проверку тавтологией.
    """
    lines: set[tuple[str, str, int]] = set()
    path = old_path = ""
    old = new = 0
    for line in diff.splitlines():
        if line.startswith("--- "):
            old_path = line[4:].removeprefix("a/")
        elif line.startswith("+++ "):
            # Удалённый файл: `+++ /dev/null`, комментарий к нему - по старому пути.
            path = old_path if line[4:] == "/dev/null" else line[4:].removeprefix("b/")
        elif line.startswith("diff --git"):
            continue
        elif m := _HUNK.match(line):
            old, new = int(m.group(1)), int(m.group(2))
        elif line.startswith("+"):
            lines.add((path, "RIGHT", new))
            new += 1
        elif line.startswith("-"):
            lines.add((path, "LEFT", old))
            old += 1
        elif line.startswith(" "):
            lines.add((path, "RIGHT", new))
            lines.add((path, "LEFT", old))
            old += 1
            new += 1
    return lines
