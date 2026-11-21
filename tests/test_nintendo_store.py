"""Unit tests for :class:`NintendoStore` against responses shaped like Nintendo's.

The fakes mirror the live services (verified 2026-11-21): the European catalogue
search returns Solr documents, and the eShop price service returns one entry per
id with a ``sales_status`` and ``raw_value`` amounts.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.stores.base import USER_AGENT, StoreError
from services.stores.nintendo_store import NintendoStore

_DOCS = [
    {
        "title": "Hogwarts Legacy",
        "nsuid_txt": ["70070000019472"],
        "system_names_txt": ["Nintendo Switch"],
        "url": "/en-gb/Games/Hogwarts-Legacy-1.html",
    },
    {
        # Discounted in the UK catalogue, full price in Denmark: not a Danish deal.
        "title": "UK Only Deal",
        "nsuid_txt": ["70010000000002"],
        "system_names_txt": ["Nintendo Switch 2"],
        "url": "/en-gb/Games/UK-Only-Deal-2.html",
    },
    {"title": "Not Sold Here", "nsuid_txt": ["70010000000003"], "system_names_txt": []},
    {"title": "No Id", "system_names_txt": ["Nintendo Switch"]},
]

_PRICES = {
    "country": "DK",
    "prices": [
        {
            "title_id": 70010000000003,
            "sales_status": "not_found",
        },
        {
            "title_id": 70070000019472,
            "sales_status": "onsale",
            "regular_price": {"amount": "519,00 kr.", "currency": "DKK", "raw_value": "519"},
            "discount_price": {
                "amount": "51,75 kr.",
                "currency": "DKK",
                "raw_value": "51.75",
                "end_datetime": "2026-11-30T22:59:59Z",
            },
        },
        {
            "title_id": 70010000000002,
            "sales_status": "onsale",
            "regular_price": {"amount": "299,00 kr.", "currency": "DKK", "raw_value": "299"},
        },
    ],
}


class _Response:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Http:
    """Routes the catalogue search and the price service to canned answers."""

    def __init__(self, docs: Any = None, prices: Any = None) -> None:
        self._docs = {"response": {"docs": _DOCS}} if docs is None else docs
        self._prices = _PRICES if prices is None else prices
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append((url, kwargs))
        return _Response(self._docs if "searching" in url else self._prices)


def test_deals_are_games_the_danish_store_actually_discounts() -> None:
    http = _Http()
    deals = NintendoStore(http).deals("da-DK", 10)

    assert [d.title for d in deals] == ["Hogwarts Legacy"]
    hogwarts = deals[0]
    assert (hogwarts.price, hogwarts.regular_price, hogwarts.currency) == (51.75, 519, "DKK")
    assert hogwarts.discount_percent == 90
    assert hogwarts.sale_ends == "2026-11-30"
    assert hogwarts.url == "https://www.nintendo.com/en-gb/Games/Hogwarts-Legacy-1.html"


def test_deals_ask_the_catalogue_for_discounted_games_and_price_them_in_the_market() -> None:
    http = _Http()
    NintendoStore(http).deals("da-DK", 10)

    search_url, search = http.calls[0]
    assert "searching.nintendo-europe.com" in search_url
    assert "price_has_discount_b:true" in search["params"]["fq"]
    price_url, price = http.calls[1]
    assert price_url == "https://api.ec.nintendo.com/v1/price"
    assert price["params"]["country"] == "DK" and price["params"]["lang"] == "da"
    assert price["params"]["ids"] == "70070000019472,70010000000002,70010000000003"
    assert price["headers"]["User-Agent"] == USER_AGENT


def test_lookup_returns_full_price_games_and_drops_ones_not_sold_here() -> None:
    found = NintendoStore(_Http()).lookup("Hogwarts", "da-DK", 5)

    assert [f.title for f in found] == ["Hogwarts Legacy", "UK Only Deal"]
    full = found[1]
    assert (full.price, full.regular_price, full.discount_percent) == (299, 299, 0)
    assert full.sale_ends is None and full.platforms == ("Nintendo Switch 2",)


def test_lookup_strips_query_syntax_from_the_title() -> None:
    http = _Http()
    NintendoStore(http).lookup('Ratchet & Clank: "Rift" (Apart)!', "da-DK", 3)

    assert http.calls[0][1]["params"]["q"] == "Ratchet Clank Rift Apart"


def test_searches_only_games_the_eshop_can_price() -> None:
    """Retired systems' entries carry no eShop id; they once filled every result slot."""
    http = _Http()
    NintendoStore(http).lookup("Mario Kart", "da-DK", 3)
    NintendoStore(http).deals("da-DK", 10)

    filters = [kwargs["params"]["fq"] for url, kwargs in http.calls if "searching" in url]
    assert len(filters) == 2 and all("nsuid_txt:*" in fq for fq in filters)


def test_a_title_of_only_syntax_searches_nothing() -> None:
    http = _Http()

    assert NintendoStore(http).lookup("!!!", "da-DK", 3) == []
    assert http.calls == []


def test_no_priceable_ids_means_no_price_call() -> None:
    http = _Http(docs={"response": {"docs": [{"title": "No Id"}]}})

    assert NintendoStore(http).lookup("No Id", "da-DK", 3) == []
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    ("docs", "prices"),
    [
        ({"error": "bad query"}, None),
        (None, {"error": "unknown country"}),
        (None, ValueError("not json")),
    ],
)
def test_unrecognised_answers_are_errors(docs: Any, prices: Any) -> None:
    with pytest.raises(StoreError):
        NintendoStore(_Http(docs=docs, prices=prices)).deals("da-DK", 10)
