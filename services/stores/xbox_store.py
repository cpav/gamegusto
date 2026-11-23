"""Official Microsoft Store prices, read from xbox.com's own pages.

xbox.com renders each page with its data embedded as ``window.__PRELOADED_STATE__``:
the product list in display order plus a summary per product carrying the
market's price, the regular price, the sale end date and who the price is for.
The deals page and the search page share that shape, so one parser serves both.

Two checks keep an answer honest. The page states the market it served, and a
mismatch (xbox.com falling back to another country's store) is an error, never a
foreign price passed off as local. And only prices open to everyone count: a
Game Pass member discount sits alongside the public price and is dropped.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import requests

from services.stores.base import TIMEOUT, USER_AGENT, Http, StoreError, StorePrice

_BASE = "https://www.xbox.com"
_STATE_MARKER = "__PRELOADED_STATE__"

#: Products playable on an Xbox console. PC-only listings appear in the same
#: lists: noise in a list of deals, but the answer when a game is looked up.
_CONSOLES = frozenset({"XboxSeriesX", "XboxOne"})


class XboxStore:
    """Reads Microsoft Store deals and prices from xbox.com for one market."""

    name = "Microsoft Store"

    def __init__(self, http: Http | None = None) -> None:
        self._http = http or requests

    def deals(self, locale: str, limit: int) -> list[StorePrice]:
        """The store's deals page, first page, console games, in the store's own order."""
        state = self._page(f"/{locale}/games/browse/game-deals", locale, {})
        return _prices(state, locale, consoles_only=True)[:limit]

    def lookup(self, title: str, locale: str, limit: int) -> list[StorePrice]:
        """The store's own search for ``title``: console versions first, then PC-only.

        A PC-only listing is returned, platforms and all, rather than dropped.
        Dropped, it read as "not in the store", and the agent went to web search,
        found Microsoft's PC listing for Forspoken and called it an Xbox release.
        """
        state = self._page(f"/{locale}/search/results/games", locale, {"q": title})
        found = _prices(state, locale, consoles_only=False)
        found.sort(key=lambda price: not _CONSOLES.intersection(price.platforms))  # stable
        return found[:limit]

    def _page(self, path: str, locale: str, params: dict[str, str]) -> dict[str, Any]:
        """Fetch one xbox.com page and return its embedded state for ``locale``."""
        response = self._http.get(
            _BASE + path,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        state = _preloaded_state(str(response.text))
        served = str(_dig(state, "appContext", "marketInfo", "locale") or "")
        if served.lower() != locale.lower():
            raise StoreError(f"xbox.com served {served or 'no'} market for {locale}")
        return state


def _preloaded_state(html: str) -> dict[str, Any]:
    """Decode the JSON object assigned to ``window.__PRELOADED_STATE__``."""
    try:
        start = html.index("{", html.index(_STATE_MARKER))
        state, _ = json.JSONDecoder().raw_decode(html, start)
    except ValueError as exc:  # marker missing or JSON malformed
        raise StoreError("xbox.com page carries no readable store data") from exc
    if not isinstance(state, dict):
        raise StoreError("xbox.com store data is not an object")
    return state


def _prices(state: dict[str, Any], locale: str, *, consoles_only: bool) -> list[StorePrice]:
    """Products from the page's one product list, priced, in display order."""
    channels = _dig(state, "core2", "channels", "channelData")
    summaries = _dig(state, "core2", "products", "productSummaries")
    if not isinstance(channels, dict) or not isinstance(summaries, dict):
        raise StoreError("xbox.com page has no product list")
    ordered: list[str] = []
    for channel in channels.values():
        for product in _dig(channel, "data", "products") or []:
            if isinstance(product, dict) and product.get("productId"):
                ordered.append(str(product["productId"]))

    prices: list[StorePrice] = []
    for product_id in ordered:
        summary = summaries.get(product_id)
        if not isinstance(summary, dict):
            continue
        platforms = tuple(str(p) for p in summary.get("availableOn") or [])
        if consoles_only and not _CONSOLES.intersection(platforms):
            continue
        price = _public_price(summary)
        if price is None:
            continue  # not for sale to everyone (subscription-only, or no price yet)
        list_price, msrp, currency, ends = price
        prices.append(
            StorePrice(
                title=str(summary.get("title", "")),
                platforms=platforms,
                price=list_price,
                regular_price=msrp,
                currency=currency,
                sale_ends=ends if list_price < msrp else None,
                url=f"{_BASE}/{locale}/games/store/_/{product_id}",
            )
        )
    return prices


def _public_price(summary: dict[str, Any]) -> tuple[float, float, str, str | None] | None:
    """The lowest purchase price open to everyone: (price, regular, currency, end date).

    Each price carries an ``eligibilityInfo``; ``"None"`` means no membership is
    needed. Member discounts are listed alongside and must not be quoted as the
    price.
    """
    best: tuple[float, float, str, str | None] | None = None
    for offer in _dig(summary, "specificPrices", "purchaseable") or []:
        if not isinstance(offer, dict):
            continue
        if _dig(offer, "eligibilityInfo", "eligibility") != "None":
            continue
        if "Purchase" not in (offer.get("availabilityActions") or []):
            continue
        try:
            list_price = float(offer["listPrice"])
            msrp = float(offer["msrp"])
            currency = str(offer["currency"])
        except (KeyError, TypeError, ValueError):
            continue
        if best is None or list_price < best[0]:
            best = (list_price, msrp, currency, _iso_date(offer.get("endDateUtc")))
    return best


def _iso_date(value: Any) -> str | None:
    """``"12/03/2026 09:59:59"`` (xbox.com's US-style UTC stamp) as ``"2026-12-03"``."""
    try:
        return datetime.strptime(str(value), "%m/%d/%Y %H:%M:%S").date().isoformat()
    except ValueError:
        return None


def _dig(data: Any, *keys: str) -> Any:
    """Walk nested dicts, returning ``None`` at the first missing or non-dict step."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data
