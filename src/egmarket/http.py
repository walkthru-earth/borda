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


class Fetcher:
    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        delay_s: float | None = None,
        timeout_s: float | None = None,
        retries: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cache_dir = cache_dir if cache_dir is not None else settings.cache
        self.delay_s = settings.request_delay_s if delay_s is None else delay_s
        self.retries = settings.max_retries if retries is None else retries
        self._last_call: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.client = httpx.AsyncClient(
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
            wait = self._last_call.get(host, 0) + self.delay_s - time.monotonic()
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
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            await self._throttle(host)
            try:
                self.requests += 1
                r = await self.client.get(url, params=params, headers=headers)
                if r.status_code in (403, 429, 500, 502, 503, 504):
                    raise FetchError(f"HTTP {r.status_code} for {r.url}")
                r.raise_for_status()
                body = r.text
                if use_cache:
                    self._cache_put(cache_path, body)
                return body
            except (httpx.HTTPError, FetchError) as exc:
                last_exc = exc
                if attempt == self.retries:
                    break
                backoff = min(2**attempt * 1.5, 20)
                log.warning("fetch %s failed (%s), retry in %.1fs", url, exc, backoff)
                await asyncio.sleep(backoff)
        raise FetchError(f"giving up on {url}: {last_exc}") from last_exc

    async def post_json(
        self, url: str, payload: Any, *, headers: dict[str, str] | None = None
    ) -> Any:
        """POST a JSON body (throttled + retried, never cached) and decode the JSON reply."""
        host = httpx.URL(url).host
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            await self._throttle(host)
            try:
                self.requests += 1
                r = await self.client.post(url, json=payload, headers=headers)
                if r.status_code in (403, 429, 500, 502, 503, 504):
                    raise FetchError(f"HTTP {r.status_code} for {r.url}")
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, FetchError, json.JSONDecodeError) as exc:
                last_exc = exc
                if attempt == self.retries:
                    break
                backoff = min(2**attempt * 1.5, 20)
                log.warning("post %s failed (%s), retry in %.1fs", url, exc, backoff)
                await asyncio.sleep(backoff)
        raise FetchError(f"giving up on {url}: {last_exc}") from last_exc

    async def json(self, url: str, **kw: Any) -> Any:
        body = await self.text(url, **kw)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise FetchError(f"non-JSON response from {url}: {body[:120]!r}") from exc
