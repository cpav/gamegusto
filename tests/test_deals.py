"""Unit tests for storefront-aware deal lookup (agent.deals).

The Tavily edge is faked with a recorder so the platform->store mapping, query
construction, per-store de-duplication, the no-store fallback, and graceful
degradation are all checked without any network.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from agent.deals import build_deal_query, find_deals, store_for_platform


class _RecordingTavily:
    """Records every query and returns one canned snippet per call."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def web_search(self, query: str) -> list[dict[str, str]]:
        self.queries.append(query)
        return [{"title": "Deal", "content": f"snippet for {query}", "url": "u"}]


class _EmptyTavily:
    """Mimics a degraded Tavily that always returns no snippets."""

    def web_search(self, query: str) -> list[dict[str, str]]:
        return []


def test_store_for_platform_resolves_families() -> None:
    assert store_for_platform("PS5") == "PlayStation Store"
    assert store_for_platform("PlayStation 4") == "PlayStation Store"
    assert store_for_platform("Xbox Series X") == "Microsoft Xbox Store"
    assert store_for_platform("Nintendo Switch") == "Nintendo eShop"
    assert store_for_platform("Steam") == "Steam"
    assert store_for_platform("Windows") == "Steam"


def test_store_for_platform_skips_handhelds_and_unknown() -> None:
    assert store_for_platform("PSP") is None
    assert store_for_platform("PS Vita") is None
    assert store_for_platform("Atari 2600") is None


def test_build_deal_query_includes_title_store_region() -> None:
    query = build_deal_query("Hades", "Steam", "Denmark")
    assert "Hades" in query and "Steam" in query and "Denmark" in query


def test_find_deals_groups_and_dedups_by_store() -> None:
    tavily = _RecordingTavily()
    out = find_deals(tavily, "Hades", ["PS4", "PS5", "Steam"], "Denmark")

    assert out["title"] == "Hades"
    assert out["region"] == "Denmark"
    assert out["today"] == date.today().isoformat()  # date carried for staleness checks
    assert [d["store"] for d in out["deals"]] == ["PlayStation Store", "Steam"]
    assert out["deals"][0]["platforms"] == ["PS4", "PS5"]  # both folded into one store
    assert len(tavily.queries) == 2  # one query per distinct store
    assert all(d["snippets"] for d in out["deals"])


def test_find_deals_defaults_region_to_denmark() -> None:
    tavily = _RecordingTavily()
    out = find_deals(tavily, "Hades", ["Steam"])
    assert out["region"] == "Denmark"
    assert "Denmark" in tavily.queries[0]


def test_find_deals_falls_back_to_generic_search_when_no_store_resolves() -> None:
    tavily = _RecordingTavily()
    out = find_deals(tavily, "Hades", ["PSP"], "Denmark")

    assert len(out["deals"]) == 1
    assert out["deals"][0]["store"] is None
    assert out["deals"][0]["platforms"] == []
    assert len(tavily.queries) == 1  # a single store-agnostic search still runs


def test_find_deals_falls_back_when_no_platforms_given() -> None:
    tavily = _RecordingTavily()
    out = find_deals(tavily, "Hades")
    assert [d["store"] for d in out["deals"]] == [None]


def test_find_deals_requires_a_title() -> None:
    result: dict[str, Any] = find_deals(_RecordingTavily(), "   ", ["Steam"])
    assert result["ok"] is False and "required" in result["error"]


def test_find_deals_degrades_to_empty_snippets() -> None:
    out = find_deals(_EmptyTavily(), "Hades", ["Steam"], "Denmark")
    assert out["deals"][0]["snippets"] == []
