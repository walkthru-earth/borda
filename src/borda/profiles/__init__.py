"""Validated country profiles; packaged JSON is the authoritative default configuration."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

ProfileSlug = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)
]
CurrencyCode = Annotated[str, StringConstraints(to_upper=True, pattern=r"^[A-Z]{3}$")]


class StoreConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: ProfileSlug
    name: str = Field(min_length=1)
    base_url: HttpUrl
    platform: str = Field(min_length=1)
    currency: CurrencyCode | None = None
    enabled: bool = True
    note: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class PublicProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    id: ProfileSlug
    country_code: Annotated[str, StringConstraints(to_upper=True, pattern=r"^[A-Z]{2}$")]
    country_name: str = Field(min_length=1)
    country_name_ar: str = Field(min_length=1)
    currency: CurrencyCode
    locale: str = Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    locale_ar: str = Field(pattern=r"^ar(?:-[A-Za-z0-9]{2,8})*$")
    timezone: str
    languages: list[str] = Field(min_length=1)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("languages")
    @classmethod
    def valid_languages(cls, values: list[str]) -> list[str]:
        if (
            "en" not in values
            or len(set(values)) != len(values)
            or any(v not in {"en", "ar"} for v in values)
        ):
            raise ValueError("languages must include en, with optional ar and no duplicates")
        return values


class CountryProfile(PublicProfile):
    stores: list[StoreConfig] = Field(default_factory=list)
    minimum_price: Decimal = Field(default=Decimal("0.05"), ge=0)

    @model_validator(mode="after")
    def unique_stores(self) -> CountryProfile:
        if len({store.slug for store in self.stores}) != len(self.stores):
            raise ValueError("store slugs must be unique within a profile")
        return self

    def public(self) -> PublicProfile:
        return PublicProfile.model_validate(
            self.model_dump(include=set(PublicProfile.model_fields))
        )

    def configured_stores(self) -> list[StoreConfig]:
        return [
            store.model_copy(update={"currency": store.currency or self.currency})
            for store in self.stores
        ]

    @property
    def fingerprint(self) -> str:
        normalized = self.model_copy(update={"stores": self.configured_stores()})
        return hashlib.sha256(normalized.model_dump_json().encode()).hexdigest()[:12]


@lru_cache(maxsize=1)
def default_profile() -> CountryProfile:
    return CountryProfile.model_validate_json(files(__package__).joinpath("egypt.json").read_text())


def load_profile(value: str | Path | CountryProfile = "egypt") -> CountryProfile:
    if isinstance(value, CountryProfile):
        return value
    if str(value) == "egypt":
        return default_profile()
    return CountryProfile.model_validate_json(Path(value).expanduser().read_text())


active_profile: ContextVar[CountryProfile | None] = ContextVar("borda_profile", default=None)


@contextmanager
def use_profile(profile: CountryProfile):
    """Scope implicit model/normalization defaults without leaking between async runs."""
    token = active_profile.set(profile)
    try:
        yield profile
    finally:
        active_profile.reset(token)


def assert_profile_directory(path: Path, profile: CountryProfile) -> None:
    """Legacy manifests belong to the default profile; never mix distinct markets."""
    import json

    identity = (profile.id, profile.country_code, profile.currency)
    ownership = path / ".borda-profile.json"
    manifest = path / "manifest.json"
    observed_profiles = []
    if ownership.exists():
        observed_profiles.append(PublicProfile.model_validate_json(ownership.read_text()))
    if manifest.exists():
        raw = json.loads(manifest.read_text())
        observed_profiles.append(
            PublicProfile.model_validate(
                raw.get("profile") or default_profile().public().model_dump()
            )
        )
    if not observed_profiles and path.exists() and next(path.rglob("*.parquet"), None):
        legacy = default_profile()
        if identity != (legacy.id, legacy.country_code, legacy.currency):
            raise ValueError(
                f"Data directory {path} contains unlabelled legacy data; choose a separate data directory for {profile.id}"
            )
    for observed in observed_profiles:
        if (observed.id, observed.country_code, observed.currency) != identity:
            raise ValueError(
                f"Data directory {path} belongs to profile {observed.id} ({observed.currency}), "
                f"not {profile.id} ({profile.currency}); choose a separate data directory"
            )


def claim_profile_directory(path: Path, profile: CountryProfile) -> None:
    """Record ownership before the first data write, so interrupted runs can resume."""
    assert_profile_directory(path, profile)
    ownership = path / ".borda-profile.json"
    if not ownership.exists():
        path.mkdir(parents=True, exist_ok=True)
        pending = ownership.with_suffix(".tmp")
        pending.write_text(profile.public().model_dump_json(indent=2) + "\n")
        pending.replace(ownership)
