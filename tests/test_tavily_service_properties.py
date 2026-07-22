"""Property-based tests for :class:`TavilyService` behavior (task 3.7).

Encodes a correctness property from ``design.md``:

* **Property 13 -- Tavily rate-limit compliance** (Req 5.4): across any sequence
  of search calls within a single minute, the number of real API calls never
  exceeds the free-tier cap (60 RPM); excess calls degrade gracefully (return
  ``[]``) without hitting the API, and the budget is restored once the minute
  window rolls over.

(Manual-entry autocomplete moved to IGDB; its activation-threshold property now
lives against the ``/api/catalog/search`` route in ``test_api.py``.)

A fake search client is injected so no test ever touches the real Tavily API.
Time is driven by a fake clock patched onto the service module so the sliding
window is fully deterministic.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

from services import tavily_service
from services.tavily_service import TavilyService

# Canned Tavily response shared by every fake call. The titles are already
# clean and unique, so ``_extract_titles`` returns them verbatim.
CANNED_RESPONSE: dict[str, Any] = {
    "answer": "Hades is a roguelike action game, around 30 hours, on PC and Switch, scored 93/100.",
    "results": [
        {"title": "Hades", "content": "Roguelike action game on PC and Nintendo Switch."},
        {"title": "Celeste", "content": "Platformer available on PC."},
        {"title": "Hollow Knight", "content": "Metroidvania on PC and Switch."},
    ],
}


class FakeSearchClient:
    """Records every ``search`` invocation and returns a fixed canned response."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((query, kwargs))
        return dict(self._response)

    @property
    def call_count(self) -> int:
        return len(self.calls)


class FakeClock:
    """A controllable monotonic clock standing in for the ``time`` module."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_service(fake: FakeSearchClient) -> TavilyService:
    """Build a service with the fake client injected (never hits the network)."""
    return TavilyService(api_key="test-key", client=fake)


# ---------------------------------------------------------------------------
# Property 13: Tavily rate-limit compliance (Req 5.4)
# ---------------------------------------------------------------------------

_OPS = st.lists(st.sampled_from(["deep", "web"]), max_size=150)


@given(ops=_OPS)
@settings(deadline=None)
def test_rate_limit_never_exceeds_free_tier_within_window(ops: list[str]) -> None:
    """No sequence of calls in one minute exceeds the free-tier cap of 60.

    Within a single (frozen) minute the real API call count equals
    ``min(number_of_calls, 60)``; every call beyond the cap degrades to ``[]``
    without invoking the API. Advancing past the window restores the budget.

    **Validates: Requirements 5.4**
    """
    cap = TavilyService.FREE_TIER_RPM
    fake = FakeSearchClient(CANNED_RESPONSE)
    clock = FakeClock(start=1000.0)

    with mock.patch.object(tavily_service, "time", clock):
        service = _make_service(fake)

        for op in ops:
            service.web_search("some game", deep=(op == "deep"))

        # The cap is never exceeded, and within one window we make exactly as
        # many real calls as are permitted.
        assert fake.call_count <= cap
        assert fake.call_count == min(len(ops), cap)

        # One more call at the same instant: it degrades iff we are at the cap.
        count_before = fake.call_count
        extra = service.web_search("portal")
        if count_before >= cap:
            assert extra == []
            assert fake.call_count == count_before  # no API call made
        else:
            assert extra != []
            assert fake.call_count == count_before + 1

        # Rolling the window over restores the budget: a search is permitted.
        count_before = fake.call_count
        clock.advance(60.0)
        assert service.web_search("metroid") != []
        assert fake.call_count == count_before + 1


@given(extra_calls=st.integers(min_value=1, max_value=30))
@settings(deadline=None)
def test_calls_beyond_cap_degrade_without_api(extra_calls: int) -> None:
    """After the cap is reached, further calls degrade and never hit the API.

    Both web_search and find_image return their empty value without incrementing
    the real API call count.

    **Validates: Requirements 5.4**
    """
    cap = TavilyService.FREE_TIER_RPM
    fake = FakeSearchClient(CANNED_RESPONSE)
    clock = FakeClock(start=5000.0)

    with mock.patch.object(tavily_service, "time", clock):
        service = _make_service(fake)

        # Saturate the window with exactly ``cap`` real calls.
        for _ in range(cap):
            service.web_search("hades")
        assert fake.call_count == cap

        # Every extra call within the same minute degrades gracefully.
        for _ in range(extra_calls):
            assert service.web_search("celeste") == []
            assert service.find_image("stardew valley") is None

        # No degraded call ever reached the API.
        assert fake.call_count == cap


# --- find_image (cover art, contract v3.1) ---


def test_find_image_returns_first_valid_url() -> None:
    client = FakeSearchClient(
        {"images": ["not-a-url", {"url": "https://img.example/cover.jpg"}, "https://later.jpg"]}
    )
    service = TavilyService("key", client=client)

    assert service.find_image("Hades cover art") == "https://img.example/cover.jpg"
    # Images are requested explicitly; the default search never pays for them.
    assert client.calls[0][1]["include_images"] is True


def test_find_image_accepts_bare_url_strings() -> None:
    client = FakeSearchClient({"images": ["https://img.example/bare.png"]})
    assert TavilyService("key", client=client).find_image("Hades") == "https://img.example/bare.png"


def test_find_image_returns_none_without_usable_images() -> None:
    payloads: list[dict[str, Any]] = [
        {"images": []},
        {"images": "nope"},
        {},
        {"images": [{"no_url": 1}]},
    ]
    for payload in payloads:
        client = FakeSearchClient(payload)
        assert TavilyService("key", client=client).find_image("Hades") is None


def test_find_image_degrades_on_failure() -> None:
    class _Boom:
        def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("tavily down")

    service = TavilyService("key", client=_Boom())
    assert service.find_image("Hades") is None
    assert service.is_available is False


def test_find_image_skips_blank_query_without_calling() -> None:
    client = FakeSearchClient({"images": ["https://img.example/x.jpg"]})
    assert TavilyService("key", client=client).find_image("   ") is None
    assert client.call_count == 0
