"""Shared HTTP client: browser-like headers, retry with backoff, per-host politeness delay,
and a small on-disk response cache (TTL) so a retried job or a dev re-run does not
re-download every page. The cache dir is restored/saved by GitHub Actions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    pass


class RateLimited(FetchError):
    """HTTP 429 – honour Retry-After (seconds) when the server sends one."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP 429 for {response.url}")
        try:
            self.retry_after = float(response.headers.get("retry-after", "") or 0)
        except ValueError:
            self.retry_after = 0.0


def _describe(exc: Exception) -> str:
    """`str(exc)` – or the class name when httpx raises with an empty message (timeouts)."""
    return str(exc) or type(exc).__name__


class _RetryBudget:
    """Decide the next backoff for one request: transient errors get `retries` quick attempts
    (1.5 s, 3 s, 6 s, ...), HTTP 429 gets its own, slower budget of `rate_limit_retries`
    attempts (Retry-After or 15 s doubling up to `max_backoff_s`). Returns None to give up."""

    def __init__(self, retries: int, rate_limit_retries: int, max_backoff_s: float) -> None:
        self.retries, self.rate_limit_retries, self.max_backoff_s = (
            retries,
            rate_limit_retries,
            max_backoff_s,
        )
        self.failures = 0
        self.rate_limited = 0

    def next_backoff(self, exc: Exception) -> float | None:
        if isinstance(exc, RateLimited):
            if self.rate_limited >= self.rate_limit_retries:
                return None
            backoff = min(15.0 * 2**self.rate_limited, self.max_backoff_s)
            self.rate_limited += 1
            # A server that says how long to wait knows best (bounded so a bogus header
            # cannot stall the run), otherwise back off progressively.
            return min(max(exc.retry_after, backoff), 2 * self.max_backoff_s)
        if self.failures >= self.retries:
            return None
        backoff = min(2**self.failures * 1.5, 20)
        self.failures += 1
        return backoff


class Fetcher:
    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        delay_s: float | None = None,
        timeout_s: float | None = None,
        retries: int | None = None,
        rate_limit_retries: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cache_dir = cache_dir if cache_dir is not None else settings.cache
        self.delay_s = settings.request_delay_s if delay_s is None else delay_s
        self.retries = settings.max_retries if retries is None else retries
        if rate_limit_retries is None:  # `retries=0` means "never retry", 429 included
            rate_limit_retries = settings.rate_limit_retries if self.retries else 0
        self.rate_limit_retries = rate_limit_retries
        self._last_call: dict[str, float] = {}
        self.host_delay: dict[str, float] = {}  # per-host politeness override
        self._locks: dict[str, asyncio.Lock] = {}
        self.client = httpx.AsyncClient(
            proxy=settings.proxy_url or None,  # optional egress proxy (some stores block DC IPs)
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,ar;q=0.6",
            },
            timeout=timeout_s or settings.request_timeout_s,
            follow_redirects=True,
            http2=transport is None,
            transport=transport,
        )
        self.requests = 0
        self.cache_hits = 0

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.client.aclose()

    # ------------------------------------------------------------------ cache
    def _cache_path(self, url: str, params: dict[str, Any] | None) -> Path:
        key = hashlib.sha1(f"{url}?{json.dumps(params or {}, sort_keys=True)}".encode()).hexdigest()
        return self.cache_dir / key[:2] / f"{key}.json"

    def _cache_get(self, path: Path) -> str | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
        if time.time() - payload["t"] > settings.http_cache_ttl_h * 3600:
            return None
        return payload["body"]

    def _cache_put(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"t": time.time(), "body": body}))

    # ------------------------------------------------------------------ fetch
    async def _throttle(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            delay = self.host_delay.get(host, self.delay_s)
            wait = self._last_call.get(host, 0) + delay - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call[host] = time.monotonic()

    async def text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> str:
        cache_path = self._cache_path(url, params)
        if use_cache and (cached := self._cache_get(cache_path)) is not None:
            self.cache_hits += 1
            return cached
        host = httpx.URL(url).host
        budget = self._budget()
        while True:
            await self._throttle(host)
            try:
                self.requests += 1
                r = await self.client.get(url, params=params, headers=headers)
                self._check_status(r)
                body = r.text
                if use_cache:
                    self._cache_put(cache_path, body)
                return body
            except (httpx.HTTPError, FetchError) as exc:
                if (backoff := budget.next_backoff(exc)) is None:
                    raise FetchError(f"giving up on {url}: {_describe(exc)}") from exc
                log.warning("fetch %s failed (%s), retry in %.1fs", url, _describe(exc), backoff)
                await asyncio.sleep(backoff)

    async def post_json(
        self, url: str, payload: Any, *, headers: dict[str, str] | None = None
    ) -> Any:
        """POST a JSON body (throttled + retried, never cached) and decode the JSON reply."""
        host = httpx.URL(url).host
        budget = self._budget()
        while True:
            await self._throttle(host)
            try:
                self.requests += 1
                r = await self.client.post(url, json=payload, headers=headers)
                self._check_status(r)
                return r.json()
            except (httpx.HTTPError, FetchError, json.JSONDecodeError) as exc:
                if (backoff := budget.next_backoff(exc)) is None:
                    raise FetchError(f"giving up on {url}: {_describe(exc)}") from exc
                log.warning("post %s failed (%s), retry in %.1fs", url, _describe(exc), backoff)
                await asyncio.sleep(backoff)

    def _budget(self) -> _RetryBudget:
        return _RetryBudget(
            self.retries, self.rate_limit_retries, settings.rate_limit_max_backoff_s
        )

    @staticmethod
    def _check_status(r: httpx.Response) -> None:
        if r.status_code == 429:
            raise RateLimited(r)
        if r.status_code in (403, 500, 502, 503, 504):
            raise FetchError(f"HTTP {r.status_code} for {r.url}")
        r.raise_for_status()

    async def json(self, url: str, **kw: Any) -> Any:
        body = await self.text(url, **kw)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise FetchError(f"non-JSON response from {url}: {body[:120]!r}") from exc
