"""Property-based tests for :class:`SearchService` (Brave-backed) behavior.

Encodes a correctness property from ``design.md``:

* **Property 13 -- rate-limit compliance** (Req 5.4): across any sequence of
  search calls within a single minute, the number of real API calls never
  exceeds the free-tier cap (60 RPM); excess calls degrade gracefully (return
  ``[]``) without hitting the API, and the budget is restored once the minute
  window rolls over.

A fake HTTP client is injected so no test ever touches the real Brave API. Time
is driven by a fake clock patched onto the service module so the sliding window
is fully deterministic.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

from services import search_service
from services.search_service import SearchService

# Canned Brave web-search envelope shared by every fake call.
BRAVE_RESPONSE: dict[str, Any] = {
    "web": {
        "results": [
            {"title": "Hades", "url": "https://ex/hades", "description": "Roguelike on PC/Switch."},
            {
                "title": "Celeste",
                "url": "https://ex/celeste",
                "description": "Platformer.",
                "extra_snippets": ["Also on PC.", "Tight controls."],
            },
        ]
    }
}


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self) -> Any:
        return self._payload


class FakeHttp:
    """Records every ``get`` invocation and returns a fixed canned response."""

    def __init__(self, payload: Any = BRAVE_RESPONSE, status: int = 200) -> None:
        self._payload = payload
        self._status = status
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, kwargs))
        return FakeResponse(self._payload, self._status)

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


def _service(http: FakeHttp) -> SearchService:
    """Build a service with the fake HTTP client injected (never hits the network)."""
    return SearchService(api_key="test-key", http=http)


# --- shape and options -----------------------------------------------------


def test_web_search_flattens_brave_results_to_snippets() -> None:
    snippets = _service(FakeHttp()).web_search("hades")

    assert snippets[0] == {
        "title": "Hades",
        "content": "Roguelike on PC/Switch.",
        "url": "https://ex/hades",
    }
    # Extra snippets are appended to the description so the model reads the fullest text.
    assert snippets[1]["content"] == "Platformer. Also on PC. Tight controls."


def test_include_domains_becomes_site_filters() -> None:
    http = FakeHttp()
    _service(http).web_search("switch deals", include_domains=["nintendo.com"])

    assert http.calls[0][1]["params"]["q"] == "switch deals site:nintendo.com"


def test_deep_requests_more_results_and_extra_snippets() -> None:
    http = FakeHttp()
    _service(http).web_search("elden ring price", deep=True)

    params = http.calls[0][1]["params"]
    assert params["count"] == 20
    assert params["extra_snippets"] == "true"


def test_no_key_is_unavailable_and_never_calls() -> None:
    http = FakeHttp()
    service = SearchService(api_key=None, http=http)

    assert service.is_available is False
    assert service.web_search("hades") == []
    assert http.call_count == 0


def test_blank_query_returns_nothing_without_calling() -> None:
    http = FakeHttp()
    assert _service(http).web_search("   ") == []
    assert http.call_count == 0


def test_web_search_degrades_on_failure() -> None:
    """A raising client degrades to [] and marks the service unavailable (Req 10.3)."""

    class _Boom:
        def get(self, url: str, **kwargs: Any) -> Any:
            raise RuntimeError("brave down")

    service = SearchService("key", http=_Boom())
    assert service.web_search("Hades") == []
    assert service.is_available is False


def test_malformed_payload_degrades_to_empty() -> None:
    assert _service(FakeHttp(payload={"web": "nope"})).web_search("hades") == []
    assert _service(FakeHttp(payload={})).web_search("hades") == []
    assert _service(FakeHttp(status=503)).web_search("hades") == []


# --- Property 13: rate-limit compliance (Req 5.4) --------------------------

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
    cap = SearchService.FREE_TIER_RPM
    http = FakeHttp()
    clock = FakeClock(start=1000.0)

    with mock.patch.object(search_service, "time", clock):
        service = _service(http)

        for op in ops:
            service.web_search("some game", deep=(op == "deep"))

        assert http.call_count <= cap
        assert http.call_count == min(len(ops), cap)

        count_before = http.call_count
        extra = service.web_search("portal")
        if count_before >= cap:
            assert extra == []
            assert http.call_count == count_before  # no API call made
        else:
            assert extra != []
            assert http.call_count == count_before + 1

        # Rolling the window over restores the budget: a search is permitted.
        count_before = http.call_count
        clock.advance(60.0)
        assert service.web_search("metroid") != []
        assert http.call_count == count_before + 1


@given(extra_calls=st.integers(min_value=1, max_value=30))
@settings(deadline=None)
def test_calls_beyond_cap_degrade_without_api(extra_calls: int) -> None:
    """After the cap is reached, further calls degrade and never hit the API.

    **Validates: Requirements 5.4**
    """
    cap = SearchService.FREE_TIER_RPM
    http = FakeHttp()
    clock = FakeClock(start=5000.0)

    with mock.patch.object(search_service, "time", clock):
        service = _service(http)

        for _ in range(cap):
            service.web_search("hades")
        assert http.call_count == cap

        for _ in range(extra_calls):
            assert service.web_search("celeste") == []
            assert service.web_search("stardew valley", deep=True) == []

        assert http.call_count == cap
