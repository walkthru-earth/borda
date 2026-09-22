"""Fetcher retry policy against mocked HTTP (respx) – no network, no real sleeping."""

import httpx
import pytest
import respx

from borda import http as borda_http
from borda.http import Fetcher, FetchError, RateLimited, _RetryBudget


@pytest.fixture
def sleeps(monkeypatch):
    """Record backoff sleeps instead of waiting for them."""
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(borda_http.asyncio, "sleep", fake_sleep)
    return recorded


@pytest.fixture
def fetcher(tmp_path):
    return Fetcher(cache_dir=tmp_path / "cache", delay_s=0, retries=1, rate_limit_retries=3)


def _rate_limited(retry_after: str | None = None) -> RateLimited:
    headers = {"retry-after": retry_after} if retry_after else {}
    return RateLimited(
        httpx.Response(429, headers=headers, request=httpx.Request("GET", "https://s.test/p"))
    )


def test_rate_limit_budget_is_separate_from_transient_failures():
    budget = _RetryBudget(retries=1, rate_limit_retries=3, max_backoff_s=120)
    # 429s: 15 s, 30 s, 60 s, then give up – independent of the transient counter.
    assert [budget.next_backoff(_rate_limited()) for _ in range(4)] == [15.0, 30.0, 60.0, None]
    # A transient error still gets its own quick retry afterwards.
    assert budget.next_backoff(FetchError("HTTP 500")) == 1.5
    assert budget.next_backoff(FetchError("HTTP 500")) is None


def test_rate_limit_backoff_honours_retry_after_within_bounds():
    budget = _RetryBudget(retries=0, rate_limit_retries=5, max_backoff_s=120)
    assert budget.next_backoff(_rate_limited("90")) == 90.0  # server asks for more than 15 s
    assert budget.next_backoff(_rate_limited("2")) == 30.0  # but never less than our schedule
    assert budget.next_backoff(_rate_limited("86400")) == 240.0  # bogus header is capped
    budget = _RetryBudget(retries=0, rate_limit_retries=6, max_backoff_s=120)
    assert [budget.next_backoff(_rate_limited()) for _ in range(6)] == [15, 30, 60, 120, 120, 120]


def test_retries_zero_disables_rate_limit_retries_too(tmp_path):
    assert Fetcher(cache_dir=tmp_path, delay_s=0, retries=0).rate_limit_retries == 0
    assert Fetcher(cache_dir=tmp_path, delay_s=0, retries=3).rate_limit_retries > 0


@respx.mock
async def test_fetcher_recovers_from_a_long_429_window(fetcher, sleeps):
    route = respx.get("https://shop.test/products.json")
    route.side_effect = [
        httpx.Response(429),
        httpx.Response(429, headers={"retry-after": "45"}),
        httpx.Response(200, json={"products": [1]}),
    ]
    assert await fetcher.json("https://shop.test/products.json") == {"products": [1]}
    assert route.call_count == 3
    assert sleeps == [15.0, 45.0]  # schedule, then the server's Retry-After


@respx.mock
async def test_fetcher_gives_up_after_the_rate_limit_budget(fetcher, sleeps):
    route = respx.get("https://shop.test/products.json").mock(return_value=httpx.Response(429))
    with pytest.raises(FetchError, match="giving up .*HTTP 429"):
        await fetcher.text("https://shop.test/products.json")
    assert route.call_count == 4  # 1 + rate_limit_retries
    assert sleeps == [15.0, 30.0, 60.0]


@respx.mock
async def test_post_retries_and_names_empty_exceptions(fetcher, sleeps):
    route = respx.post("https://electra.test/livewire/update")
    route.side_effect = [httpx.ReadTimeout(""), httpx.Response(200, json={"ok": True})]
    assert await fetcher.post_json("https://electra.test/livewire/update", {}) == {"ok": True}
    assert sleeps == [1.5]
    route.side_effect = [httpx.ReadTimeout(""), httpx.ReadTimeout("")]
    with pytest.raises(FetchError, match="ReadTimeout"):
        await fetcher.post_json("https://electra.test/livewire/update", {})
