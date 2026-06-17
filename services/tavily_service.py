"""Tavily search service: web search and manual-entry autocomplete.

Boundary to the Tavily Search API. It powers two things:

* :meth:`web_search` — raw web snippets used by the agent's ``web_search`` tool
  and by the LLM-assisted :class:`~agent.enricher.Enricher` to populate game
  metadata (genre, playtime, platform availability, community review).
* :meth:`autocomplete` — manual-entry suggestions, active only at >= 3
  characters (Req 3.4).

The service is free-tier rate limited (Req 5.4). Every failure degrades
gracefully — searches return ``[]`` — so a Tavily outage never raises to callers
(Req 5.5, 10.3). Interpreting the search results into structured fields is the
enricher's job, not this service's.
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


class TavilyService:
    """Searches the web and serves autocomplete via the Tavily API."""

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

    def web_search(self, query: str) -> list[dict[str, str]]:
        """Return raw web snippets for ``query`` to inform agent/enricher reasoning.

        Each snippet is ``{"title", "content", "url"}``. A rate-limit miss or any
        failure degrades to ``[]`` rather than raising (Req 5.4, 10.3).
        """
        if not query.strip() or not self._available or not self._check_rate_limit():
            return []
        try:
            data = self._search(query)
        except Exception as exc:  # noqa: BLE001 - degrade on any Tavily failure (Req 10.3)
            self._degrade(exc)
            return []
        snippets: list[dict[str, str]] = []
        answer = data.get("answer")
        if isinstance(answer, str) and answer.strip():
            snippets.append({"title": "summary", "content": answer.strip(), "url": ""})
        for result in self._results(data):
            snippets.append(
                {
                    "title": str(result.get("title", "")),
                    "content": str(result.get("content", "")),
                    "url": str(result.get("url", "")),
                }
            )
        return snippets

    def autocomplete(self, query: str) -> list[str]:
        """Return title suggestions for manual entry, active only at >= 3 chars.

        Below the threshold no request is made and ``[]`` is returned (Req 3.4).
        A rate-limit miss or any failure also degrades to ``[]`` (Req 5.4, 10.3).
        """
        if len(query) < 3 or not self._available or not self._check_rate_limit():
            return []
        try:
            data = self._search(f"{query} video game")
        except Exception as exc:  # noqa: BLE001 - degrade on any Tavily failure (Req 10.3)
            self._degrade(exc)
            return []
        return self._extract_titles(data)

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

    def _search(self, query: str) -> dict[str, Any]:
        """Issue a Tavily search including the synthesized answer envelope."""
        return self._client.search(query, include_answer=True, max_results=5)

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

    @staticmethod
    def _extract_titles(data: dict[str, Any]) -> list[str]:
        """Return deduplicated result titles for autocomplete suggestions (Req 3.4)."""
        titles: list[str] = []
        for result in TavilyService._results(data):
            title = result.get("title")
            if isinstance(title, str) and title.strip() and title not in titles:
                titles.append(title.strip())
        return titles
