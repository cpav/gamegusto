"""Storefront-aware deal lookup for recommendation candidates.

The agent may use this (its call, never a forced step) to check current prices
and discounts for a candidate game on the official store tied to each platform —
PlayStation Store, Microsoft/Xbox Store, Nintendo eShop, or Steam — scoped to the
user's region. It reuses the rate-limited, gracefully-degrading Tavily
``web_search`` rather than any paid price API: results are web snippets the model
reads to judge whether a discount is worth tipping a close recommendation.
Console eShop/PSN/Xbox prices are exactly where structured price APIs are weakest,
so reading store snippets is the pragmatic source here.

Platform -> store resolution reuses :func:`agent.platform_match.platform_family`,
so "PS5", "PlayStation 4", and "PlayStation" all collapse to a single PlayStation
Store query (deduped), and unrecognized or legacy-handheld platforms are skipped.
Like the services it builds on, this never raises: an empty/failed search simply
yields empty snippets so the model proceeds without price info.
"""

from __future__ import annotations

from typing import Any, Protocol

from agent.platform_match import platform_family


class _Searcher(Protocol):
    """Minimal view of the search dependency used here (Tavily satisfies it)."""

    def web_search(self, query: str) -> list[dict[str, str]]: ...


#: Default region folded into deal queries; natural language for Tavily.
DEFAULT_DEALS_REGION = "Denmark"

#: Canonical platform family -> official storefront name used in the query.
_STORE_BY_FAMILY: dict[str, str] = {
    "playstation": "PlayStation Store",
    "xbox": "Microsoft Xbox Store",
    "nintendo": "Nintendo eShop",
    "pc": "Steam",
}


def store_for_platform(name: str) -> str | None:
    """Return the official storefront for ``name``, or ``None`` when none applies.

    Resolves through the shared platform-family logic, so platform aliases collapse
    to one store. Legacy handhelds (PSP, PS Vita) and unrecognized platforms return
    ``None`` — there is no current digital-deal surface worth searching for them.
    """
    family = platform_family(name)
    if family is None:
        return None
    return _STORE_BY_FAMILY.get(family)


def build_deal_query(title: str, store: str, region: str) -> str:
    """Build a natural-language Tavily query for a title's deal on ``store``."""
    return f"{title} {store} price discount sale {region}".strip()


def find_deals(
    tavily: _Searcher,
    title: str,
    platforms: list[str] | None = None,
    region: str = DEFAULT_DEALS_REGION,
) -> dict[str, Any]:
    """Search official-store deals for ``title`` across ``platforms``, by region.

    Each distinct store is searched once (so owning both "PS4" and "PS5" yields a
    single PlayStation Store query) and results are grouped per store. When no
    platform resolves to a known store (or none is given) a single store-agnostic
    search is run instead, so the model still gets some price signal. Returns
    ``{"ok": False, "error": ...}`` only for a missing title; otherwise a
    ``{"title", "region", "deals": [{"store", "platforms", "snippets"}]}`` payload.
    """
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "title is required"}
    region = (region or "").strip() or DEFAULT_DEALS_REGION

    deals: list[dict[str, Any]] = []
    grouped = _stores_for_platforms(platforms)
    if grouped:
        for store, names in grouped:
            snippets = tavily.web_search(build_deal_query(title, store, region))
            deals.append({"store": store, "platforms": names, "snippets": snippets})
    else:
        snippets = tavily.web_search(f"{title} game price discount sale {region}")
        deals.append({"store": None, "platforms": [], "snippets": snippets})
    return {"title": title, "region": region, "deals": deals}


def _stores_for_platforms(platforms: list[str] | None) -> list[tuple[str, list[str]]]:
    """Group the given platforms by their resolved store, preserving first-seen order.

    Returns ``[(store, [platform names that mapped to it])]`` with unmapped platforms
    dropped, so each store is queried exactly once.
    """
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for name in platforms or []:
        store = store_for_platform(name)
        if store is None:
            continue
        if store not in grouped:
            grouped[store] = []
            order.append(store)
        grouped[store].append(name)
    return [(store, grouped[store]) for store in order]
