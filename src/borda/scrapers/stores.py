"""Default profile registry; runtime pipelines use their selected profile's stores."""

from ..profiles import default_profile

STORES = default_profile().configured_stores()
BY_SLUG = {store.slug: store for store in STORES}
