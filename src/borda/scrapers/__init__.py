"""Scraper registry: platform name -> scraper class."""

from __future__ import annotations

from ..http import Fetcher
from .base import BaseScraper, Store
from .easytest import EasyTestScraper
from .elghazawy import ElGhazawyScraper
from .locafy import LocafyScraper
from .odoo import OdooScraper
from .prestashop import PrestaShopScraper
from .shopify import ShopifyScraper
from .stores import BY_SLUG, STORES
from .wix import WixScraper
from .woocommerce import WooCommerceScraper

PLATFORMS: dict[str, type[BaseScraper]] = {
    cls.platform: cls
    for cls in (
        ShopifyScraper,
        WooCommerceScraper,
        PrestaShopScraper,
        OdooScraper,
        EasyTestScraper,
        ElGhazawyScraper,
        LocafyScraper,
        WixScraper,
    )
}


def build_scraper(store: Store, fetcher: Fetcher, *, max_pages: int) -> BaseScraper:
    try:
        cls = PLATFORMS[store.platform]
    except KeyError as exc:
        raise ValueError(f"{store.slug}: unsupported platform {store.platform!r}") from exc
    return cls(store, fetcher, max_pages=max_pages)


__all__ = ["BY_SLUG", "PLATFORMS", "STORES", "BaseScraper", "Store", "build_scraper"]
