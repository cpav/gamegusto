"""Official Nintendo eShop prices for one European market.

Two services behind Nintendo's own European site, used together:

* the **catalogue search** finds games (title, platforms, product page, eShop id).
  It runs on the UK catalogue, which lists the European range; its own price
  fields are UK prices and are never used.
* the **eShop price service** returns the market's price for those ids: regular
  price, any sale price, and when the sale ends. It is the authority. A game it
  does not sell in the market is dropped, and a "deal" counts only if this
  service shows a sale price, whatever the UK catalogue says.
"""

from __future__ import annotations

import re
from typing import Any

import requests

from services.stores.base import TIMEOUT, USER_AGENT, Http, StoreError, StorePrice

_SEARCH = "https://searching.nintendo-europe.com/en/select"
_PRICE = "https://api.ec.nintendo.com/v1/price"
_SITE = "https://www.nintendo.com"

#: The price service answers at most 50 ids per call, which also bounds how many
#: discounted candidates are checked for a market's deals.
_PRICE_BATCH = 50

#: Games with an eShop id, i.e. ones the price service can price. The catalogue
#: also lists retired systems (SNES, Wii, 3DS) with no id, and for a title like
#: "Mario Kart" those filled every result slot.
_PRICEABLE_GAMES = "type:GAME AND nsuid_txt:*"

#: Characters with meaning in Solr's query syntax.
_QUERY_SYNTAX = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


class NintendoStore:
    """Reads Nintendo eShop deals and prices for one European market."""

    name = "Nintendo eShop"

    def __init__(self, http: Http | None = None) -> None:
        self._http = http or requests

    def deals(self, locale: str, limit: int) -> list[StorePrice]:
        """Popular games on sale in the market, most popular first."""
        docs = self._search(
            {
                "q": "*",
                "fq": f"{_PRICEABLE_GAMES} AND price_has_discount_b:true",
                "sort": "popularity asc",
                "rows": str(_PRICE_BATCH),
            }
        )
        return [p for p in self._priced(docs, locale) if p.discount_percent][:limit]

    def lookup(self, title: str, locale: str, limit: int) -> list[StorePrice]:
        """Games matching ``title`` that the market sells, in search relevance order."""
        # Solr query syntax in a title ("Ratchet & Clank: Rift Apart!") can make the
        # search fail outright; the words alone match just as well.
        query = " ".join(_QUERY_SYNTAX.sub(" ", title).split())
        if not query:
            return []
        docs = self._search({"q": query, "fq": _PRICEABLE_GAMES, "rows": str(limit)})
        return self._priced(docs, locale)

    def _search(self, params: dict[str, str]) -> list[dict[str, Any]]:
        """Query the European catalogue and return its documents."""
        data = self._get_json(_SEARCH, {**params, "wt": "json"})
        docs = (data.get("response") or {}).get("docs") if isinstance(data, dict) else None
        if not isinstance(docs, list):
            raise StoreError("Nintendo catalogue answered without results")
        return [d for d in docs if isinstance(d, dict)]

    def _priced(self, docs: list[dict[str, Any]], locale: str) -> list[StorePrice]:
        """Price ``docs`` in the locale's market, keeping only games it sells."""
        by_id: dict[str, dict[str, Any]] = {}
        for doc in docs:
            ids = doc.get("nsuid_txt")
            if isinstance(ids, list) and ids:
                by_id.setdefault(str(ids[0]), doc)
        if not by_id:
            return []

        language, country = locale.split("-")
        data = self._get_json(
            _PRICE,
            {"country": country.upper(), "lang": language.lower(), "ids": ",".join(by_id)},
        )
        entries = data.get("prices") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise StoreError("Nintendo price service answered without prices")
        by_price = {str(e.get("title_id")): e for e in entries if isinstance(e, dict)}

        prices: list[StorePrice] = []
        for nsuid, doc in by_id.items():  # catalogue order: popularity or relevance
            entry = by_price.get(nsuid)
            if entry is None or entry.get("sales_status") != "onsale":
                continue  # not sold in this market (not found, unreleased, withdrawn)
            regular = _amount(entry.get("regular_price"))
            if regular is None:
                continue
            sale = _amount(entry.get("discount_price"))
            ends = str((entry.get("discount_price") or {}).get("end_datetime", ""))[:10]
            prices.append(
                StorePrice(
                    title=str(doc.get("title", "")),
                    platforms=tuple(str(s) for s in doc.get("system_names_txt") or []),
                    price=sale[0] if sale else regular[0],
                    regular_price=regular[0],
                    currency=regular[1],
                    sale_ends=(ends or None) if sale else None,
                    url=_SITE + str(doc["url"]) if doc.get("url") else _SITE,
                )
            )
        return prices

    def _get_json(self, url: str, params: dict[str, str]) -> Any:
        response = self._http.get(
            url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise StoreError("Nintendo answered with something other than JSON") from exc


def _amount(price: Any) -> tuple[float, str] | None:
    """``{"raw_value": "51.75", "currency": "DKK"}`` as ``(51.75, "DKK")``."""
    if not isinstance(price, dict):
        return None
    try:
        return float(price["raw_value"]), str(price["currency"])
    except (KeyError, TypeError, ValueError):
        return None
