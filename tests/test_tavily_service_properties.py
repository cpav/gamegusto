"""Property-based tests for :class:`TavilyService` behavior (task 3.7).

Encodes two correctness properties from ``design.md``:

* **Property 3 -- Autocomplete activation threshold** (Req 3.4): a query shorter
  than 3 characters never triggers a Tavily request and yields ``[]``; a query of
  3+ characters triggers exactly one request and surfaces the extracted titles.
* **Property 13 -- Tavily rate-limit compliance** (Req 5.4): across any sequence
  of enrich/autocomplete calls within a single minute, the number of real API
  calls never exceeds the free-tier cap (60 RPM); excess calls degrade gracefully
  (return ``[]`` / the record unchanged) without hitting the API, and the budget
  is restored once the minute window rolls over.

A fake search client is injected so no test ever touches the real Tavily API.
Time is driven by a fake clock patched onto the service module so the sliding
window is fully deterministic.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

from models.game_record import GameRecord
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
EXPECTED_TITLES = ["Hades", "Celeste", "Hollow Knight"]


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
# Property 3: Autocomplete activation threshold (Req 3.4)
# ---------------------------------------------------------------------------


@given(query=st.text(max_size=2))
def test_autocomplete_below_threshold_never_searches(query: str) -> None:
    """Sub-3-character queries return ``[]`` and never call the API.

    **Validates: Requirements 3.4**
    """
    assert len(query) < 3  # generator invariant for the assertion below
    fake = FakeSearchClient(CANNED_RESPONSE)
    service = _make_service(fake)

    result = service.autocomplete(query)

    assert result == []
    assert fake.call_count == 0


@given(query=st.text(min_size=3, max_size=40))
def test_autocomplete_at_or_above_threshold_searches_and_returns_titles(query: str) -> None:
    """Queries of 3+ characters issue exactly one search and surface its titles.

    **Validates: Requirements 3.4**
    """
    assert len(query) >= 3  # generator invariant
    fake = FakeSearchClient(CANNED_RESPONSE)
    service = _make_service(fake)

    result = service.autocomplete(query)

    assert result == EXPECTED_TITLES
    assert fake.call_count == 1


@given(length=st.integers(min_value=0, max_value=2))
def test_autocomplete_boundary_lengths_below(length: int) -> None:
    """Explicit boundary lengths 0, 1, 2 stay below the activation threshold.

    **Validates: Requirements 3.4**
    """
    fake = FakeSearchClient(CANNED_RESPONSE)
    service = _make_service(fake)

    result = service.autocomplete("a" * length)

    assert result == []
    assert fake.call_count == 0


@given(length=st.integers(min_value=3, max_value=12))
def test_autocomplete_boundary_lengths_at_or_above(length: int) -> None:
    """Explicit boundary lengths 3..12 cross the activation threshold.

    **Validates: Requirements 3.4**
    """
    fake = FakeSearchClient(CANNED_RESPONSE)
    service = _make_service(fake)

    result = service.autocomplete("a" * length)

    assert result == EXPECTED_TITLES
    assert fake.call_count == 1


# ---------------------------------------------------------------------------
# Property 13: Tavily rate-limit compliance (Req 5.4)
# ---------------------------------------------------------------------------

_OPS = st.lists(st.sampled_from(["auto", "enrich"]), max_size=150)


@given(ops=_OPS)
@settings(deadline=None)
def test_rate_limit_never_exceeds_free_tier_within_window(ops: list[str]) -> None:
    """No sequence of calls in one minute exceeds the free-tier cap of 60.

    Within a single (frozen) minute the real API call count equals
    ``min(number_of_calls, 60)``; every call beyond the cap degrades to ``[]`` /
    the record unchanged without invoking the API. Advancing past the window
    restores the budget.

    **Validates: Requirements 5.4**
    """
    cap = TavilyService.FREE_TIER_RPM
    fake = FakeSearchClient(CANNED_RESPONSE)
    clock = FakeClock(start=1000.0)

    with mock.patch.object(tavily_service, "time", clock):
        service = _make_service(fake)

        for op in ops:
            if op == "auto":
                service.autocomplete("zelda")
            else:
                service.enrich(GameRecord(title="Some Game"))

        # The cap is never exceeded, and within one window we make exactly as
        # many real calls as are permitted.
        assert fake.call_count <= cap
        assert fake.call_count == min(len(ops), cap)

        # One more call at the same instant: it degrades iff we are at the cap.
        count_before = fake.call_count
        extra = service.autocomplete("portal")
        if count_before >= cap:
            assert extra == []
            assert fake.call_count == count_before  # no API call made
        else:
            assert extra == EXPECTED_TITLES
            assert fake.call_count == count_before + 1

        # Rolling the window over restores the budget: a search is permitted.
        count_before = fake.call_count
        clock.advance(60.0)
        recovered = service.autocomplete("metroid")
        assert recovered == EXPECTED_TITLES
        assert fake.call_count == count_before + 1


@given(extra_calls=st.integers(min_value=1, max_value=30))
@settings(deadline=None)
def test_calls_beyond_cap_degrade_without_api(extra_calls: int) -> None:
    """After the cap is reached, further calls degrade and never hit the API.

    autocomplete returns ``[]`` and enrich returns the record unchanged, all
    without incrementing the real API call count.

    **Validates: Requirements 5.4**
    """
    cap = TavilyService.FREE_TIER_RPM
    fake = FakeSearchClient(CANNED_RESPONSE)
    clock = FakeClock(start=5000.0)

    with mock.patch.object(tavily_service, "time", clock):
        service = _make_service(fake)

        # Saturate the window with exactly ``cap`` real calls.
        for _ in range(cap):
            service.autocomplete("hades")
        assert fake.call_count == cap

        # Every extra call within the same minute degrades gracefully.
        for _ in range(extra_calls):
            assert service.autocomplete("celeste") == []

            record = GameRecord(title="Stardew Valley")
            returned = service.enrich(record)
            assert returned is record  # unchanged, not re-fetched
            assert not record.is_enriched()

        # No degraded call ever reached the API.
        assert fake.call_count == cap
