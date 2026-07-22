"""Web search via the Brave Search API for the agent and enrichment.

Boundary to Brave's independent search index. It powers :meth:`web_search` — raw
web snippets used by the agent's ``web_search`` tool and by the LLM-assisted
:class:`~agent.enricher.Enricher` to populate game metadata (genre, playtime,
platform availability, community review), with a ``deep`` mode that pulls more
results and Brave's extra snippets for reading store deals pages.

Brave was chosen over a Google reseller for the same reasons it fits a privacy-
minded app: an independent index (not scraped Google), no tracking, and a real
free tier. Anthropic's own Claude web search runs on Brave too.

The service is free-tier rate limited (Req 5.4). Every failure degrades
gracefully — searches return ``[]`` — so a search outage never raises to callers
(Req 5.5, 10.3). Interpreting the results into structured fields is the
enricher's job, not this service's.

Title autocomplete and cover art do NOT live here: both moved to IGDB
(:class:`~services.igdb_service.IgdbService`), the games industry's own
catalogue.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from services.error_handler import ErrorHandler

_API_URL = "https://api.search.brave.com/res/v1/web/search"
_TIMEOUT = 8

#: Results per request. Deep pulls the max and asks for extra snippets, which is
#: what surfaces the price buried in a store deals page a bare snippet misses.
_SHALLOW_COUNT = 5
_DEEP_COUNT = 20


class _Http(Protocol):
    """The slice of ``requests`` used here, so tests need no network."""

    def get(self, url: str, **kwargs: Any) -> Any: ...


@dataclass
class RateLimitState:
    """Sliding one-minute window tracking requests against the free-tier cap."""

    requests_this_minute: int = 0
    minute_start: float = 0.0


class SearchService:
    """Searches the web via the Brave Search API. Degrades to ``[]`` on any failure."""

    #: Brave's free "Data for AI" tier is ~1 query/second; a 60-per-minute cap
    #: keeps sequential agent/enrichment calls comfortably inside it (Req 5.4).
    FREE_TIER_RPM = 60

    def __init__(self, api_key: str | None, http: _Http | None = None) -> None:
        """Build the service from ``api_key``; inject ``http`` for testing.

        A missing key means the whole feature is off — ``web_search`` returns
        ``[]`` rather than making an unauthenticated call that would only fail.
        """
        self._api_key = api_key
        self._http = http or requests
        self._rate = RateLimitState()
        self._available = bool(api_key)
        self._last_error: str | None = None

    def web_search(
        self, query: str, include_domains: list[str] | None = None, deep: bool = False
    ) -> list[dict[str, str]]:
        """Return web snippets for ``query`` to inform agent reasoning.

        Each snippet is ``{"title", "content", "url"}``. ``include_domains`` (when
        given) restricts results to those domains via ``site:`` operators — e.g. a
        single official store domain, so the agent reads the storefront rather than
        price-aggregator/grey-market sites. ``deep`` pulls the maximum results plus
        Brave's extra snippets, which is what surfaces the actual prices on a
        store's deals page a basic snippet misses. A rate-limit miss or any failure
        degrades to ``[]`` rather than raising (Req 5.4, 10.3).
        """
        if not query.strip() or not self._available or not self._check_rate_limit():
            return []
        term = query
        if include_domains:
            term = f"{query} " + " ".join(f"site:{domain}" for domain in include_domains)
        try:
            data = self._search(term, deep)
        except Exception as exc:  # noqa: BLE001 - degrade on any Brave failure (Req 10.3)
            self._degrade(exc)
            return []
        return self._snippets(data)

    @property
    def is_available(self) -> bool:
        """False without a key, or once a Brave call has failed; enables degradation."""
        return self._available

    @property
    def last_error(self) -> str | None:
        """Sanitized message for the most recent failure, or ``None`` (Req 10.3)."""
        return self._last_error

    def _check_rate_limit(self) -> bool:
        """Consume one request from the current minute window, staying within the cap.

        Returns ``False`` (without consuming) when the free-tier RPM is exhausted
        for the current minute (Req 5.4).
        """
        now = time.time()
        if now - self._rate.minute_start >= 60:
            self._rate = RateLimitState(requests_this_minute=0, minute_start=now)
        if self._rate.requests_this_minute >= self.FREE_TIER_RPM:
            return False
        self._rate.requests_this_minute += 1
        return True

    def _search(self, query: str, deep: bool) -> dict[str, Any]:
        """Issue one Brave web search and return the parsed JSON envelope."""
        params: dict[str, Any] = {"q": query, "count": _DEEP_COUNT if deep else _SHALLOW_COUNT}
        if deep:
            # Extra snippets carry more of the page body; harmless if the plan
            # does not return them.
            params["extra_snippets"] = "true"
        response = self._http.get(
            _API_URL,
            headers={"Accept": "application/json", "X-Subscription-Token": str(self._api_key)},
            params=params,
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _degrade(self, exc: Exception) -> None:
        """Mark the service unavailable and record a sanitized error (Req 10.3)."""
        self._available = False
        self._last_error = ErrorHandler.sanitize_error(exc, "search")

    @staticmethod
    def _snippets(data: dict[str, Any]) -> list[dict[str, str]]:
        """Flatten Brave's ``web.results`` into ``{title, content, url}`` snippets.

        ``content`` is the result description joined with any extra snippets, so
        the model reads the fullest text Brave returned for the page. Bad shapes
        are tolerated — a missing or malformed field yields an empty string, never
        a raise.
        """
        web = data.get("web")
        results = web.get("results") if isinstance(web, dict) else None
        if not isinstance(results, list):
            return []
        snippets: list[dict[str, str]] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            content = str(result.get("description", ""))
            extra = result.get("extra_snippets")
            if isinstance(extra, list):
                content = " ".join(
                    part for part in [content, *[str(item) for item in extra]] if part
                )
            snippets.append(
                {
                    "title": str(result.get("title", "")),
                    "content": content,
                    "url": str(result.get("url", "")),
                }
            )
        return snippets
