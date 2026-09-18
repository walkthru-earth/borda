from __future__ import annotations

import pytest
from pydantic_ai import models

from borda.models import Availability, RawOffer

models.ALLOW_MODEL_REQUESTS = False  # never hit a real LLM from tests


def offer(seller: str, name: str, price: float | None = 100, **kw) -> RawOffer:
    slug = name.lower().replace(" ", "-")[:40]
    return RawOffer(
        seller=seller,
        raw_name=name,
        url=kw.pop("url", f"https://{seller}.example/p/{slug}"),
        price=price,
        availability=kw.pop("availability", Availability.IN_STOCK),
        **kw,
    )


@pytest.fixture
def make_offer():
    return offer
