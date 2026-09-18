"""Runtime settings (env-driven) and repo paths."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EGMARKET_", env_file=".env", extra="ignore")

    repo_root: Path = Field(default_factory=lambda: Path.cwd())
    data_dir: Path | None = None
    diagnostics_dir: Path | None = None
    cache_dir: Path | None = None

    # scraping
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 egmarket/0.1 (+price-history bot)"
    )
    request_timeout_s: float = 30.0
    request_delay_s: float = 0.6
    max_retries: int = 3
    max_pages_per_store: int = 400
    http_cache_ttl_h: float = 20.0  # re-use fetched pages within a run window / retry

    # enrichment (Pydantic AI). `hetzner:<model>` uses the OpenAI-compatible Hetzner
    # Inference API; any `provider:model` string Pydantic AI understands also works.
    ai_model: str = "hetzner:Qwen/Qwen3.6-35B-A3B-FP8"
    ai_enabled: bool = True
    ai_batch_size: int = 30
    ai_max_items_per_run: int = 1500
    ai_requests_per_minute: int = 8  # Hetzner limit is 10/min – keep headroom for retries
    hetzner_base_url: str = "https://inference.hetzner.com/api/v1"
    hetzner_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("HETZNER_INFERENCE_TOKEN", "EGMARKET_HETZNER_TOKEN"),
    )

    # dedupe / validation
    fuzzy_threshold: int = 93
    outlier_factor: float = 8.0  # flag if price differs from product median by this factor

    @property
    def data(self) -> Path:
        return self.data_dir or self.repo_root / "data"

    @property
    def diagnostics(self) -> Path:
        return self.diagnostics_dir or self.repo_root / "diagnostics"

    @property
    def cache(self) -> Path:
        return self.cache_dir or self.repo_root / ".cache" / "http"


settings = Settings()
