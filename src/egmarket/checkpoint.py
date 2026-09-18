"""Resumable run state, so a failed / timed-out job does not start from zero.

`.cache/checkpoints/<key>/` (key = the run month by default):
  store-<slug>.jsonl      raw offers of a store that finished with status OK
  store-<slug>.report.json  its StoreReport
  enrichment.parquet      mirror of the LLM cache, refreshed after every batch

The directory is cleared once a run has persisted its results. GitHub Actions keeps
`.cache/` across attempts with actions/cache (saved with `if: always()`)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .models import RawOffer, ScrapeStatus, StoreReport

log = logging.getLogger(__name__)


class Checkpoint:
    def __init__(self, root: Path, key: str) -> None:
        self.dir = root / key
        self.key = key

    # ------------------------------------------------------------------ stores
    def _paths(self, slug: str) -> tuple[Path, Path]:
        return self.dir / f"store-{slug}.jsonl", self.dir / f"store-{slug}.report.json"

    def load_store(self, slug: str) -> tuple[list[RawOffer], StoreReport] | None:
        offers_p, report_p = self._paths(slug)
        if not (offers_p.exists() and report_p.exists()):
            return None
        try:
            report = StoreReport.model_validate_json(report_p.read_bytes())
            offers = [
                RawOffer.model_validate_json(line)
                for line in offers_p.read_text(encoding="utf-8").splitlines()
                if line
            ]
        except Exception as exc:  # noqa: BLE001 - a corrupt checkpoint is just ignored
            log.warning("checkpoint for %s unreadable (%s); re-scraping", slug, exc)
            return None
        return offers, report

    def save_store(self, slug: str, offers: list[RawOffer], report: StoreReport) -> None:
        if report.status != ScrapeStatus.OK:
            return  # only complete results are worth resuming from
        self.dir.mkdir(parents=True, exist_ok=True)
        offers_p, report_p = self._paths(slug)
        tmp = offers_p.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for o in offers:
                fh.write(o.model_dump_json(exclude_none=True, exclude={"listing_key"}) + "\n")
        tmp.replace(offers_p)
        report_p.write_text(report.model_dump_json())

    # ------------------------------------------------------------------ enrichment mirror
    @property
    def enrichment_path(self) -> Path:
        return self.dir / "enrichment.parquet"

    # ------------------------------------------------------------------ lifecycle
    def summary(self) -> dict[str, int]:
        stores = list(self.dir.glob("store-*.report.json")) if self.dir.exists() else []
        return {"stores": len(stores), "enrichment": int(self.enrichment_path.exists())}

    def clear(self) -> None:
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)
            log.info("checkpoint %s cleared", self.key)
