"""Registry of Egyptian stores. Platform detection was done by hand (see README).

To add a store: append a `Store`. If its platform is already supported nothing else
is needed."""

from __future__ import annotations

from .base import Store

STORES: list[Store] = [
    Store(
        slug="fut-electronics",
        name="Future Electronics (FUT)",
        base_url="https://store.fut-electronics.com",
        platform="shopify",
    ),
    Store(
        slug="devboardsmarket",
        name="DevBoards Market",
        base_url="https://devboardsmarket.com",
        platform="shopify",
    ),
    Store(
        slug="circuits-elec",
        name="Circuits Electronics",
        base_url="https://circuits-elec.com",
        platform="shopify",
    ),
    Store(
        slug="uge-one",
        name="UGE Electronics",
        base_url="https://uge-one.com",
        platform="woocommerce",
        note="HTML front is behind Cloudflare, but the WooCommerce Store API is open.",
    ),
    Store(
        slug="microohm",
        name="Micro Ohm Electronics",
        base_url="https://microohm-eg.com",
        platform="woocommerce",
    ),
    Store(
        slug="makerselectronics",
        name="Makers Electronics",
        base_url="https://makerselectronics.com",
        platform="woocommerce",
    ),
    Store(
        slug="mostelectronic",
        name="Most Electronic",
        base_url="https://mostelectronic.com",
        platform="woocommerce",
    ),
    Store(
        slug="fares-pcb",
        name="Fares PCB",
        base_url="https://fares-pcb.com",
        platform="woocommerce",
    ),
    Store(
        slug="ic-hat",
        name="IC Hat",
        base_url="https://ic-hat.com/en",
        platform="prestashop",
        params={"listing": "2-home"},
    ),
    Store(
        slug="ram-e-shop",
        name="RAM Electronics",
        base_url="https://www.ram-e-shop.com",
        platform="odoo",
    ),
    Store(
        slug="easytest",
        name="EasyTest",
        base_url="https://www.easytest.com.eg/en",
        platform="easytest",
    ),
    Store(
        slug="maamoon",
        name="Maamoon Est.",
        base_url="https://www.maamoon.com",
        platform="wix",
        note="Storefront GraphQL (anonymous instance token); sitemap+JSON-LD fallback.",
    ),
    Store(
        slug="eshopmas",
        name="eShop MAS",
        base_url="https://eshopmas.com",
        platform="woocommerce",
        enabled=False,
        note="Cloudflare challenge blocks both HTML and the Store API from CI runners.",
    ),
    Store(
        slug="rsdelivers",
        name="RS Components Egypt",
        base_url="https://eg.rsdelivers.com",
        platform="unsupported",
        enabled=False,
        note="Prices are rendered client-side (JSON-LD reports 0); catalog is >100k SKUs.",
    ),
    Store(
        slug="amazon-eg",
        name="Amazon Egypt",
        base_url="https://www.amazon.eg",
        platform="unsupported",
        enabled=False,
        note="Optional per brief; requires anti-bot handling – out of scope for now.",
    ),
]

BY_SLUG: dict[str, Store] = {s.slug: s for s in STORES}
