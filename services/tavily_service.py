"""Tavily search service: web search for the agent and enrichment.

Boundary to the Tavily Search API, powering :meth:`web_search` — raw web
snippets used by the agent's ``web_search`` tool and by the LLM-assisted
:class:`~agent.enricher.Enricher` to populate game metadata (genre, playtime,
platform availability, community review), with a ``deep`` mode that reads full
page content for store deals pages.

The service is free-tier rate limited (Req 5.4). Every failure degrades
gracefully — searches return ``[]`` — so a Tavily outage never raises to callers
(Req 5.5, 10.3). Interpreting the search results into structured fields is the
enricher's job, not this service's.

Two things that used to live here have moved to IGDB
(:class:`~services.igdb_service.IgdbService`), the games industry's own
catalogue: manual-entry title autocomplete, and cover art. IGDB returns real
titles with platforms and the actual box art, rather than web-page headings and
whatever a general image search turned up for "cover art".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from services.error_handler import ErrorHandler


class _SearchClient(Protocol):
    """Minimal view of the Tavily client used by this service (untyped upstream)."""

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...


@dataclass
class RateLimitState:
    """Sliding one-minute window tracking requests against the free-tier cap."""

    requests_this_minute: int = 0
    minute_start: float = 0.0


#: Per-result cap on a deep search's page text, so full ``raw_content`` (store deal
#: pages run ~12k chars) is bounded before it reaches the model's context.
_DEEP_CONTENT_CHARS = 3000


class TavilyService:
    """Searches the web via the Tavily API for the agent and enrichment."""

    FREE_TIER_RPM = 60

    def __init__(self, api_key: str, client: _SearchClient | None = None) -> None:
        """Build the service from ``api_key``; inject ``client`` for testing.

        When ``client`` is omitted a real ``tavily.TavilyClient`` is constructed.
        The import is local so the dependency is only required at runtime.
        """
        if client is None:
            from tavily import TavilyClient

            client = TavilyClient(api_key=api_key)
        self._client = client
        self._rate = RateLimitState()
        self._available = True
        self._last_error: str | None = None

    def web_search(
        self, query: str, include_domains: list[str] | None = None, deep: bool = False
    ) -> list[dict[str, str]]:
        """Return raw web snippets for ``query`` to inform agent reasoning.

        Each snippet is ``{"title", "content", "url"}``. ``include_domains`` (when
        given) restricts results to those domains — e.g. a single official store
        domain, so the agent reads the storefront rather than price-aggregator/
        grey-market sites. ``deep`` switches on advanced extraction + full page
        ``raw_content`` (bounded), which is what surfaces the actual prices on a
        store's deals page that a basic snippet misses. A rate-limit miss or any
        failure degrades to ``[]`` rather than raising (Req 5.4, 10.3).
        """
        if not query.strip() or not self._available or not self._check_rate_limit():
            return []
        try:
            data = self._search(query, include_domains, deep)
        except Exception as exc:  # noqa: BLE001 - degrade on any Tavily failure (Req 10.3)
            self._degrade(exc)
            return []
        snippets: list[dict[str, str]] = []
        answer = data.get("answer")
        if isinstance(answer, str) and answer.strip():
            snippets.append({"title": "summary", "content": answer.strip(), "url": ""})
        for result in self._results(data):
            content = str(result.get("content", ""))
            if deep:
                raw = str(result.get("raw_content") or "")
                if raw:  # the page body holds store prices; bound it so it fits context
                    content = f"{content}\n{raw}"[:_DEEP_CONTENT_CHARS]
            snippets.append(
                {
                    "title": str(result.get("title", "")),
                    "content": content,
                    "url": str(result.get("url", "")),
                }
            )
        return snippets

    @property
    def is_available(self) -> bool:
        """False once a Tavily call has failed; enables graceful degradation."""
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

    def _search(
        self, query: str, include_domains: list[str] | None = None, deep: bool = False
    ) -> dict[str, Any]:
        """Issue a Tavily search including the synthesized answer envelope.

        ``include_domains`` is forwarded only when non-empty so the default behaviour
        (search the whole web) is unchanged for the enricher and basic searches.
        ``deep`` switches on advanced extraction + full page ``raw_content`` — used when
        reading store deals pages, where prices live in the page body the snippet misses.
        """
        kwargs: dict[str, Any] = {"include_answer": True, "max_results": 5}
        if include_domains:
            kwargs["include_domains"] = include_domains
        if deep:
            kwargs["search_depth"] = "advanced"
            kwargs["include_raw_content"] = True
        return self._client.search(query, **kwargs)

    def _degrade(self, exc: Exception) -> None:
        """Mark the service unavailable and record a sanitized error (Req 10.3)."""
        self._available = False
        self._last_error = ErrorHandler.sanitize_error(exc, "tavily")

    @staticmethod
    def _results(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the ``results`` list from a response, tolerating bad shapes."""
        results = data.get("results")
        if not isinstance(results, list):
            return []
        return [r for r in results if isinstance(r, dict)]
