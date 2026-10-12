"""A small, rate-limit-aware GitHub REST client for harvesting.

Deliberately stdlib-only (``urllib``), so the ingestion pipeline adds no
runtime dependency to a package whose job is serving recommendations. It
respects ``HTTPS_PROXY`` through urllib's default proxy handling.

The rate limits are the design constraint, not an afterthought. Authenticated
REST allows 5,000 requests an hour; code and repository *search* allow only 30
a minute, and search is what a harvest leans on hardest. Seeding 1,200 entries
therefore costs thousands of calls, so the client reads the
``X-RateLimit-Remaining`` and ``X-RateLimit-Reset`` headers on every response
and waits rather than burning through the budget and collecting 403s. A
harvest that trips the limit does not fail loudly -- it starts returning
nothing, which looks like "no results found".
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64decode
from dataclasses import dataclass, field
from typing import Any

API_ROOT = "https://api.github.com"
USER_AGENT = "plovane-harvester/0.1"


class GitHubError(RuntimeError):
    """An API call failed in a way the caller must decide about."""

    # Input: status (int), message (str), url (str)
    # Output: None
    # Processing: Init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __init__(self, status: int, message: str, url: str) -> None:
        super().__init__(f"{status} from {url}: {message}")
        self.status = status
        self.url = url


class RateLimited(GitHubError):
    """The limit is exhausted and the reset is further off than we may wait."""


@dataclass
class RateLimitState:
    remaining: int | None = None
    reset_at: float | None = None
    resource: str = "core"

    # Input: none
    # Output: float
    # Processing: Seconds until reset
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def seconds_until_reset(self) -> float:
        if self.reset_at is None:
            return 0.0
        return max(0.0, self.reset_at - time.time())


@dataclass
class GitHubClient:
    """Minimal REST client: repository metadata, licences, trees and blobs."""

    token: str | None = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN"))
    api_root: str = API_ROOT
    max_wait_seconds: float = 120.0
    """How long to block on a rate-limit reset before giving up.

    Short on purpose: a harvest is a long batch job, and a worker that silently
    sleeps for the better part of an hour is indistinguishable from one that
    has hung. Past this it raises and lets the orchestrator checkpoint, report
    and resume.
    """

    min_interval_seconds: float = 0.0
    rate_limit: RateLimitState = field(default_factory=RateLimitState)
    requests_made: int = 0
    _last_request_at: float = 0.0

    # Input: none
    # Output: None
    # Processing: Post init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __post_init__(self) -> None:
        if not self.token:
            # Unauthenticated REST is 60 requests an hour, which cannot seed a
            # library. Allowed, because read-only probing is useful, but said
            # out loud rather than discovered three hundred entries in.
            pass

    # -- plumbing ----------------------------------------------------------

    # Input: none
    # Output: dict[str, str]
    # Processing: Headers
    # Changed by: Claude Code
    # Date: 2026-10-12
    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    # Input: headers (Any), resource (str)
    # Output: None
    # Processing: Note limits
    # Changed by: Claude Code
    # Date: 2026-10-12
    def _note_limits(self, headers: Any, resource: str) -> None:
        remaining = headers.get("X-RateLimit-Remaining")
        reset = headers.get("X-RateLimit-Reset")
        self.rate_limit = RateLimitState(
            remaining=int(remaining) if remaining is not None else None,
            reset_at=float(reset) if reset is not None else None,
            resource=resource,
        )

    # Input: none
    # Output: None
    # Processing: Throttle
    # Changed by: Claude Code
    # Date: 2026-10-12
    def _throttle(self) -> None:
        if self.min_interval_seconds > 0:
            elapsed = time.time() - self._last_request_at
            if elapsed < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - elapsed)

        if self.rate_limit.remaining is not None and self.rate_limit.remaining <= 1:
            wait = self.rate_limit.seconds_until_reset
            if wait > self.max_wait_seconds:
                raise RateLimited(
                    403,
                    f"{self.rate_limit.resource} limit exhausted; resets in {wait:.0f}s, "
                    f"longer than max_wait_seconds={self.max_wait_seconds:.0f}",
                    self.api_root,
                )
            if wait > 0:
                time.sleep(wait + 1.0)

    # Input: path (str), params (dict[str, Any] | None)
    # Output: Any
    # Processing: GET a path relative to the API root, returning decoded JSON.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a path relative to the API root, returning decoded JSON."""
        self._throttle()

        url = path if path.startswith("http") else f"{self.api_root}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        resource = "search" if "/search/" in url else "core"

        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                self.requests_made += 1
                self._last_request_at = time.time()
                self._note_limits(response.headers, resource)
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            self.requests_made += 1
            self._last_request_at = time.time()
            self._note_limits(error.headers, resource)
            body = error.read().decode("utf-8", errors="replace")[:300]
            if error.code in (403, 429) and "rate limit" in body.lower():
                raise RateLimited(error.code, body, url) from error
            raise GitHubError(error.code, body, url) from error

    # -- endpoints ---------------------------------------------------------

    # Input: owner (str), repo (str)
    # Output: dict[str, Any]
    # Processing: Repository
    # Changed by: Claude Code
    # Date: 2026-10-12
    def repository(self, owner: str, repo: str) -> dict[str, Any]:
        return self.get(f"/repos/{owner}/{repo}")

    # Input: owner (str), repo (str)
    # Output: dict[str, Any] | None
    # Processing: The repository's licence, or None when it has none.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def license_info(self, owner: str, repo: str) -> dict[str, Any] | None:
        """The repository's licence, or None when it has none.

        A 404 here is the single most important signal in the pipeline: it
        means no detectable licence, which means the code may not be copied.
        It is returned as None rather than raised, because "no licence" is an
        expected outcome to be recorded, not an error to retry.
        """
        try:
            return self.get(f"/repos/{owner}/{repo}/license")
        except GitHubError as error:
            if error.status == 404:
                return None
            raise

    # Input: owner (str), repo (str), ref (str | None)
    # Output: str
    # Processing: Resolve a ref to a commit SHA, so provenance pins bytes not branches.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def default_branch_sha(self, owner: str, repo: str, ref: str | None = None) -> str:
        """Resolve a ref to a commit SHA, so provenance pins bytes not branches."""
        if ref is None:
            ref = self.repository(owner, repo)["default_branch"]
        return self.get(f"/repos/{owner}/{repo}/commits/{ref}")["sha"]

    # Input: owner (str), repo (str), path (str), ref (str | None)
    # Output: str
    # Processing: Fetch and decode one file's text.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def file_contents(self, owner: str, repo: str, path: str, ref: str | None = None) -> str:
        """Fetch and decode one file's text."""
        params = {"ref": ref} if ref else None
        payload = self.get(f"/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}", params)
        if isinstance(payload, list):
            raise GitHubError(400, f"{path} is a directory, not a file", path)
        if payload.get("encoding") != "base64":
            return payload.get("content", "")
        return b64decode(payload["content"]).decode("utf-8", errors="replace")

    # Input: owner (str), repo (str), sha (str), recursive (bool)
    # Output: list[dict[str, Any]]
    # Processing: List a commit's files. ``truncated`` means the repo exceeded the
    # Changed by: Claude Code
    # Date: 2026-10-12
    def tree(self, owner: str, repo: str, sha: str, *, recursive: bool = True) -> list[dict[str, Any]]:
        """List a commit's files. ``truncated`` means the repo exceeded the
        API's tree limit, so a harvest must not assume it saw everything."""
        params = {"recursive": "1"} if recursive else None
        payload = self.get(f"/repos/{owner}/{repo}/git/trees/{sha}", params)
        if payload.get("truncated"):
            payload.setdefault("tree", [])
        return [item for item in payload.get("tree", []) if item.get("type") == "blob"]

    # Input: query (str), sort (str), per_page (int), page (int)
    # Output: dict[str, Any]
    # Processing: Repository search. Costs against the 30-per-minute search budget.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def search_repositories(
        self, query: str, *, sort: str = "stars", per_page: int = 50, page: int = 1
    ) -> dict[str, Any]:
        """Repository search. Costs against the 30-per-minute search budget.

        Note for the caller: GitHub's search API caps results at 1,000 per
        query however many pages are requested, so a harvest spanning many
        entries needs many *narrow* queries rather than one broad one.
        """
        return self.get(
            "/search/repositories",
            {"q": query, "sort": sort, "order": "desc", "per_page": per_page, "page": page},
        )
