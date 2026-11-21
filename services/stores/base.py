"""The ``Store`` protocol — official prices from one platform's own storefront.

A store reads what its storefront shows a shopper in one market: the store's own
numbers, in that market's currency. That is the point of this package. Search
snippets and price trackers can carry another country's listing or a currency
conversion; a store answer cannot.

None of these are documented public APIs. They are the data the stores' own
pages load, so they can change without notice (Microsoft has already retired one
deals endpoint that older guides still describe). A store therefore raises
``StoreError`` on anything it does not recognise rather than guessing, and the
agent falls back to web search with the price labelled unconfirmed.

Requests identify themselves honestly (``USER_AGENT``) rather than posing as a
browser: the stores answer such requests, and a store that stopped would be a
reason to drop it, not to disguise the client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

USER_AGENT = "GameGusto/1.0 (personal game recommender)"
TIMEOUT = 15


class StoreError(Exception):
    """The store could not answer reliably: unreachable, changed shape, or wrong market."""


class Http(Protocol):
    """The slice of ``requests`` used here, so tests need no network."""

    def get(self, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class StorePrice:
    """One product's current price as its store shows it in one market."""

    title: str
    """The store's exact product name, edition included, so a wrong match is visible."""
    platforms: tuple[str, ...]
    price: float
    """What anyone pays today: member-only prices (Game Pass and the like) are excluded."""
    regular_price: float
    currency: str
    sale_ends: str | None
    """ISO date the current sale ends, or ``None`` when not on sale or undated."""
    url: str

    @property
    def discount_percent(self) -> int:
        """The discount off the regular price, rounded; 0 when not on sale."""
        if self.regular_price <= 0 or self.price >= self.regular_price:
            return 0
        return round((1 - self.price / self.regular_price) * 100)

    def to_dict(self) -> dict[str, Any]:
        """The compact form handed to the model."""
        out: dict[str, Any] = {
            "title": self.title,
            "platforms": list(self.platforms),
            "price": self.price,
            "regular_price": self.regular_price,
            "currency": self.currency,
            "url": self.url,
        }
        if self.discount_percent:
            out["discount_percent"] = self.discount_percent
            out["sale_ends"] = self.sale_ends
        return out


class Store(Protocol):
    """Official prices for one platform family, in a ``da-DK``-style locale's market.

    Both methods raise ``StoreError`` when the store cannot answer reliably. An
    empty list means the store answered and found nothing.
    """

    name: str
    """Shopper-facing store name, e.g. ``"Nintendo eShop"``."""

    def deals(self, locale: str, limit: int) -> list[StorePrice]:
        """Games on sale now, in the store's own order."""
        ...

    def lookup(self, title: str, locale: str, limit: int) -> list[StorePrice]:
        """Products matching ``title``, best match first, with their current price."""
        ...
