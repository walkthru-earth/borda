"""Scraper contract. A scraper turns one store into an async stream of RawOffer.

Adding a store: add a `Store` entry in `stores.py`. Adding a platform: subclass
`BaseScraper`, implement `iter_offers`, and register it in `PLATFORMS` (registry.py)."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from ..http import Fetcher
from ..models import RawOffer, ScrapeStatus, Slug, StoreReport

log = logging.getLogger(__name__)


class Store(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: Slug
    name: str
    base_url: HttpUrl
    platform: str
    enabled: bool = True
    note: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class BaseScraper(ABC):
    platform: str = "abstract"

    def __init__(self, store: Store, fetcher: Fetcher, *, max_pages: int = 400) -> None:
        self.store = store
        self.fetch = fetcher
        self.max_pages = max_pages
        if (d := store.params.get("delay_s")) is not None:
            fetcher.host_delay[store.base_url.host or ""] = float(d)
        self.pages = 0
        self.warnings: list[str] = []

    @property
    def base(self) -> str:
        return str(self.store.base_url).rstrip("/")

    @abstractmethod
    def iter_offers(self) -> AsyncIterator[RawOffer]:
        """Yield offers; raise on fatal store-level failures."""

    def make_offer(self, **data: Any) -> RawOffer | None:
        """Validate a single offer; invalid rows are logged, not fatal."""
        data.setdefault("seller", self.store.slug)
        try:
            return RawOffer.model_validate(data)
        except ValidationError as exc:
            msg = f"{self.store.slug}: dropped listing {data.get('raw_name')!r}: {exc.errors()[0]['msg']}"
            if len(self.warnings) < 50:
                self.warnings.append(msg)
            log.debug(msg)
            return None

    async def run(self) -> tuple[list[RawOffer], StoreReport]:
        """Fail-safe wrapper: returns whatever was scraped plus a status report."""
        t0 = time.monotonic()
        offers: list[RawOffer] = []
        error: str | None = None
        try:
            async for o in self.iter_offers():
                offers.append(o)
        except Exception as exc:  # noqa: BLE001 - we must keep the pipeline alive
            error = f"{type(exc).__name__}: {exc}"
            log.error("%s failed after %d offers: %s", self.store.slug, len(offers), error)
        status = (
            ScrapeStatus.OK
            if error is None
            else ScrapeStatus.PARTIAL
            if offers
            else ScrapeStatus.FAILED
        )
        return offers, StoreReport(
            seller=self.store.slug,
            status=status,
            offers=len(offers),
            pages=self.pages,
            duration_s=round(time.monotonic() - t0, 1),
            error=error,
            warnings=self.warnings[:20],
        )
