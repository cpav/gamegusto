"""Unit tests for :class:`XboxStore` against pages shaped like xbox.com's.

The fake pages carry the same ``__PRELOADED_STATE__`` structure the live site
embeds (verified against xbox.com/da-DK on 2026-11-21): a market, one ordered
product channel, and a summary per product with its prices.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from services.stores.base import USER_AGENT, StoreError
from services.stores.xbox_store import XboxStore


def _offer(price: float, msrp: float, eligibility: str = "None", ends: str = "") -> dict:
    return {
        "eligibilityInfo": {"eligibility": eligibility, "type": "Unknown"},
        "availabilityActions": ["Details", "Purchase"],
        "endDateUtc": ends,
        "listPrice": price,
        "msrp": msrp,
        "currency": "DKK",
    }


def _summary(title: str, available_on: list[str], offers: list[dict]) -> dict:
    return {"title": title, "availableOn": available_on, "specificPrices": {"purchaseable": offers}}


# Display order differs from the summaries' dict order on purpose: the channel's
# list is the store's order and must win.
_SUMMARIES = {
    "PCONLY": _summary("Desktop Sim", ["PC"], [_offer(50, 100)]),
    "MEMBER": _summary(
        "Member Deal",
        ["XboxSeriesX", "XboxOne"],
        [_offer(415.2, 519, ends="12/03/2026 09:59:59"), _offer(51.9, 519, "GamePass")],
    ),
    "SALE": _summary(
        "Forza Horizon 6", ["XboxSeriesX", "PC"], [_offer(411.75, 549, ends="01/07/2027 07:59:59")]
    ),
    "FULL": _summary("Hades II", ["XboxSeriesX"], [_offer(219, 219, ends="12/31/9998 23:59:59")]),
    "PASSONLY": _summary("Pass Game", ["XboxSeriesX"], [_offer(0, 0, "GamePass")]),
}
_ORDER = ["SALE", "PCONLY", "MEMBER", "PASSONLY", "FULL"]


def _page(locale: str = "da-DK", order: list[str] | None = None) -> str:
    state = {
        "appContext": {"marketInfo": {"locale": locale, "market": locale[-2:]}},
        "core2": {
            "channels": {
                "channelData": {
                    "BROWSE": {"data": {"products": [{"productId": p} for p in order or _ORDER]}}
                }
            },
            "products": {"productSummaries": _SUMMARIES},
        },
    }
    return f"<script>window.__PRELOADED_STATE__ = {json.dumps(state)};</script><div></div>"


class _Response:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class _Http:
    def __init__(self, text: str, status: int = 200) -> None:
        self._response = _Response(text, status)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append((url, kwargs))
        return self._response


def test_deals_are_console_games_in_store_order_at_the_public_price() -> None:
    http = _Http(_page())
    deals = XboxStore(http).deals("da-DK", 10)

    assert [d.title for d in deals] == ["Forza Horizon 6", "Member Deal", "Hades II"]
    forza = deals[0]
    assert (forza.price, forza.regular_price, forza.currency) == (411.75, 549, "DKK")
    assert forza.discount_percent == 25
    assert forza.sale_ends == "2027-01-07"
    assert forza.url == "https://www.xbox.com/da-DK/games/store/_/SALE"
    assert http.calls[0][0] == "https://www.xbox.com/da-DK/games/browse/game-deals"
    assert http.calls[0][1]["headers"]["User-Agent"] == USER_AGENT


def test_a_member_only_price_is_never_quoted() -> None:
    """Game Pass discounts sit beside the public price; quoting one misleads."""
    deals = XboxStore(_Http(_page())).deals("da-DK", 10)
    member = next(d for d in deals if d.title == "Member Deal")

    assert member.price == 415.2
    assert member.discount_percent == 20


def test_a_full_price_game_has_no_sale_end() -> None:
    deals = XboxStore(_Http(_page())).deals("da-DK", 10)
    hades = next(d for d in deals if d.title == "Hades II")

    assert hades.discount_percent == 0 and hades.sale_ends is None


def test_lookup_uses_the_store_search_and_respects_the_limit() -> None:
    http = _Http(_page())
    found = XboxStore(http).lookup("Forza", "da-DK", 1)

    assert [f.title for f in found] == ["Forza Horizon 6"]
    url, kwargs = http.calls[0]
    assert url == "https://www.xbox.com/da-DK/search/results/games"
    assert kwargs["params"] == {"q": "Forza"}


def test_lookup_lists_console_versions_first_then_pc_only_ones() -> None:
    """A PC-only listing is the answer to "is it on Xbox?", so a lookup keeps it,
    last and with its platforms. Dropped, the agent read Microsoft's PC listing for
    Forspoken off the web as an Xbox release. Deals stay console-only."""
    found = XboxStore(_Http(_page())).lookup("anything", "da-DK", 10)

    assert [f.title for f in found] == ["Forza Horizon 6", "Member Deal", "Hades II", "Desktop Sim"]
    assert found[-1].platforms == ("PC",)


def test_a_page_from_another_market_is_an_error_not_a_foreign_price() -> None:
    with pytest.raises(StoreError, match="en-US"):
        XboxStore(_Http(_page(locale="en-US"))).deals("da-DK", 10)


@pytest.mark.parametrize(
    "html",
    [
        "<html>no store data here</html>",
        "<script>window.__PRELOADED_STATE__ = {broken</script>",
        "<script>window.__PRELOADED_STATE__ = [1, 2];</script>",
    ],
)
def test_a_page_without_readable_store_data_is_an_error(html: str) -> None:
    with pytest.raises(StoreError):
        XboxStore(_Http(html)).deals("da-DK", 10)


def test_a_page_without_a_product_list_is_an_error() -> None:
    state = {"appContext": {"marketInfo": {"locale": "da-DK"}}, "core2": {}}
    html = f"<script>window.__PRELOADED_STATE__ = {json.dumps(state)};</script>"

    with pytest.raises(StoreError, match="product list"):
        XboxStore(_Http(html)).deals("da-DK", 10)


def test_an_http_failure_propagates_for_the_tool_to_handle() -> None:
    with pytest.raises(RuntimeError):
        XboxStore(_Http("", status=503)).deals("da-DK", 10)
