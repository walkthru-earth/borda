"""Compact Pydantic models shared by scrapers, normalization, storage and export.

Storage is Parquet (see storage/parquet.py); the models below are the row schemas.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    computed_field,
    field_validator,
)

from .taxonomy import Group

Currency = Annotated[str, StringConstraints(to_upper=True, min_length=3, max_length=3)]
Price = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)]


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class Availability(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    PREORDER = "preorder"
    UNKNOWN = "unknown"


class Base(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        populate_by_name=True,
    )


# --------------------------------------------------------------------------- scraping


class RawOffer(Base):
    """One listing as observed on one seller's site during one run.

    This is the only thing a scraper has to produce. Everything downstream is derived.
    """

    seller: Slug
    raw_name: Annotated[str, StringConstraints(min_length=2, max_length=300)]
    url: HttpUrl
    price: Price | None = None
    currency: Currency = "EGP"
    availability: Availability = Availability.UNKNOWN
    ts: datetime = Field(default_factory=utcnow)
    sku: str | None = None
    brand: str | None = None
    category: str | None = None
    store_tags: list[str] = Field(default_factory=list)
    image: HttpUrl | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("raw_name", mode="before")
    @classmethod
    def _collapse_ws(cls, v: Any) -> Any:
        return re.sub(r"\s+", " ", html.unescape(v)) if isinstance(v, str) else v

    @field_validator("price", mode="before")
    @classmethod
    def _coerce_price(cls, v: Any) -> Any:
        if v is None or v == "" or v == 0 or v == "0":
            return None
        if isinstance(v, str):
            v = re.sub(r"[^\d.]", "", v.replace(",", ""))
            if not v:
                return None
        return Decimal(str(v)).quantize(Decimal("0.01"))

    @field_validator("store_tags", mode="before")
    @classmethod
    def _tags_from_str(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def listing_key(self) -> str:
        """Stable key for this seller listing (seller + url path)."""
        return f"{self.seller}:{self.url.path}"


class ScrapeStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class StoreReport(Base):
    seller: Slug
    status: ScrapeStatus
    offers: int = 0
    pages: int = 0
    duration_s: float = 0.0
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- catalog


class Product(Base):
    """Canonical, deduplicated product.

    `canonical_name`/`image` describe the *official* product; `raw_names` keeps every
    Egyptian-market spelling (incl. Arabic) so local names remain searchable."""

    id: Slug
    canonical_name: str = Field(min_length=2, max_length=200)
    raw_names: list[str] = Field(
        default_factory=list, description="local/seller names (searchable)"
    )
    tags: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=600)
    category: str | None = None
    brand: str | None = None
    image: HttpUrl | None = Field(default=None, description="representative product picture")
    sellers: list[Slug] = Field(default_factory=list)
    listings: dict[str, str] = Field(
        default_factory=dict, description="listing_key -> product URL for every known seller page"
    )
    group: str | None = Field(default=None, description="top-level taxonomy category")
    similar: list[Slug] = Field(default_factory=list, description="nearest neighbours, best first")
    enriched: bool = False
    extra_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tags", "raw_names", "sellers", mode="after")
    @classmethod
    def _dedupe_sorted(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class Enrichment(Base):
    """LLM output for one product – keep tiny, it multiplies across thousands of items."""

    key: str = Field(description="echo of the input key, unchanged")
    canonical_name: str = Field(
        max_length=120,
        description="Manufacturer's original product name (e.g. 'ESP32-WROOM-32 DevKit V1'), "
        "no marketing words, no seller names, English",
    )
    description: str = Field(
        max_length=240, description="1 sentence, technical, what it is and key specs"
    )
    tags: list[Annotated[str, StringConstraints(to_lower=True, max_length=30)]] = Field(
        max_length=8, description="lowercase search tags: family, interface, function, brand"
    )
    brand: str | None = Field(default=None, max_length=40)
    group: Group | None = Field(default=None, description="one of the fixed taxonomy groups")

    @field_validator("tags", mode="after")
    @classmethod
    def _norm_tags(cls, v: list[str]) -> list[str]:
        return sorted({re.sub(r"[^a-z0-9+.-]+", "-", t).strip("-") for t in v if t.strip()})


# --------------------------------------------------------------------------- history


class OfferRecord(Base):
    """An offer as stored in the history (one Parquet row), mapped to a product id."""

    product_id: Slug
    seller: Slug
    raw_name: str
    url: str
    price: Price | None = None
    currency: Currency = "EGP"
    availability: Availability = Availability.UNKNOWN
    sku: str | None = None
    category: str | None = None
    image: str | None = None
    flags: list[str] = Field(default_factory=list)


class Run(Base):
    """One pipeline execution."""

    run_id: str
    ts: datetime
    schema_version: int
    pipeline_version: str
    stores: list[StoreReport]
    offers: list[OfferRecord]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def digest(self) -> str:
        h = hashlib.sha256()
        for o in sorted(self.offers, key=lambda o: (o.product_id, o.seller, o.url)):
            h.update(f"{o.product_id}|{o.seller}|{o.url}|{o.price}|{o.availability}".encode())
        return h.hexdigest()[:16]


class RunSummary(Base):
    run_id: str
    ts: datetime
    digest: str
    products: int
    offers: int
    new_products: int
    stores_ok: int
    stores_failed: int


class FileInfo(Base):
    sha256: str
    bytes: int
    footer: int | None = Field(
        default=None,
        description="Parquet footer size incl. trailer; lets a reader fetch it exactly",
    )
    rows: int | None = None
    row_groups: int | None = None


class Manifest(Base):
    """Versioning metadata so diffs between runs are cheap to compute, plus per-file facts
    (size, footer, row groups) the frontend uses to plan range requests."""

    schema_version: int
    pipeline_version: str
    parquet_format: str
    generated_at: datetime
    products: int
    offers_total: int
    groups: dict[str, int] = Field(default_factory=dict, description="taxonomy group -> products")
    files: dict[str, FileInfo] = Field(default_factory=dict, description="relative path -> info")
    runs: list[RunSummary] = Field(default_factory=list)
