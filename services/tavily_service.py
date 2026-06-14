"""Tavily enrichment and autocomplete service.

Boundary to the Tavily Search API. Enriches any ``GameRecord`` with genre,
estimated playtime, platform availability, and a ``CommunityReview`` regardless
of which source produced it (Req 5.1), and powers manual-entry autocomplete that
activates only at >= 3 characters (Req 3.4).

The service is free-tier rate limited (Req 5.4) and cache-first: an
already-enriched record is returned untouched. Every failure degrades
gracefully -- ``enrich`` returns the record unchanged and ``autocomplete``
returns ``[]`` -- so a Tavily outage never raises to callers (Req 5.5, 10.3).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from models.game_record import CommunityReview, GameRecord
from services.error_handler import ErrorHandler


class _SearchClient(Protocol):
    """Minimal view of the Tavily client used by this service (untyped upstream)."""

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...


@dataclass
class RateLimitState:
    """Sliding one-minute window tracking requests against the free-tier cap."""

    requests_this_minute: int = 0
    minute_start: float = 0.0


# Canonical genre tokens scanned for in Tavily answer/snippet text (Req 5.1).
_GENRE_KEYWORDS: list[tuple[str, str]] = [
    ("role-playing", "RPG"),
    ("role playing", "RPG"),
    ("rpg", "RPG"),
    ("first-person shooter", "Shooter"),
    ("shooter", "Shooter"),
    ("platformer", "Platformer"),
    ("metroidvania", "Metroidvania"),
    ("roguelike", "Roguelike"),
    ("roguelite", "Roguelike"),
    ("fighting", "Fighting"),
    ("racing", "Racing"),
    ("survival horror", "Survival Horror"),
    ("horror", "Horror"),
    ("survival", "Survival"),
    ("strategy", "Strategy"),
    ("simulation", "Simulation"),
    ("sandbox", "Sandbox"),
    ("open world", "Open World"),
    ("puzzle", "Puzzle"),
    ("sports", "Sports"),
    ("stealth", "Stealth"),
    ("rhythm", "Rhythm"),
    ("visual novel", "Visual Novel"),
    ("adventure", "Adventure"),
    ("action", "Action"),
    ("indie", "Indie"),
]

# Platform keyword -> (canonical name, family, is_generic). Specific variants are
# scanned before generic family tokens so "Xbox Series X" wins over bare "Xbox".
_PLATFORM_KEYWORDS: list[tuple[str, str, str, bool]] = [
    ("nintendo switch", "Nintendo Switch", "nintendo", False),
    ("switch", "Nintendo Switch", "nintendo", True),
    ("playstation 5", "PlayStation 5", "playstation", False),
    ("ps5", "PlayStation 5", "playstation", False),
    ("playstation 4", "PlayStation 4", "playstation", False),
    ("ps4", "PlayStation 4", "playstation", False),
    ("playstation", "PlayStation", "playstation", True),
    ("xbox series x", "Xbox Series X", "xbox", False),
    ("xbox series s", "Xbox Series S", "xbox", False),
    ("xbox one", "Xbox One", "xbox", False),
    ("xbox", "Xbox", "xbox", True),
    ("steam", "PC", "pc", False),
    ("windows", "PC", "pc", False),
    ("pc", "PC", "pc", True),
]

# Playtime expressions, richest pattern first; each yields a duration in minutes.
_PLAYTIME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|to|–)\s*(\d+(?:\.\d+)?)\s*hour", re.I), "hour_range"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*hour", re.I), "hours"),
    (re.compile(r"(\d+)\s*min", re.I), "minutes"),
]

# Review-score expressions normalized onto a 0-10 scale.
_SCORE_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"(\d+(?:\.\d+)?)\s*/\s*100"), 0.1),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:/|out of)\s*10\b"), 1.0),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:/|out of)\s*5\b"), 2.0),
    (re.compile(r"(\d+(?:\.\d+)?)\s*%"), 0.1),
]

_SUMMARY_MAX_CHARS = 280


class TavilyService:
    """Enriches ``GameRecord``s and serves autocomplete via the Tavily API."""

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

    def enrich(self, record: GameRecord) -> GameRecord:
        """Populate genre, playtime, availability, and review for ``record``.

        Cache-first and idempotent: an already-enriched record is returned as-is.
        Works regardless of the record's source (Req 5.1). Any field Tavily cannot
        provide is left unset, leaving the record incomplete (Req 5.5). On a
        rate-limit miss or any failure the record is returned unchanged rather than
        raising (Req 5.4, 10.3).
        """
        if record.is_enriched():
            return record
        if not self._available or not self._check_rate_limit():
            return record
        try:
            data = self._search(f"{record.title} video game genre playtime platforms review")
        except Exception as exc:  # noqa: BLE001 - degrade on any Tavily failure (Req 10.3)
            self._degrade(exc)
            return record
        return self._apply(record, data)

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

    def _apply(self, record: GameRecord, data: dict[str, Any]) -> GameRecord:
        """Fill any unset enrichment fields of ``record`` from a Tavily response.

        Parses ``answer`` and ``results[].content`` per data-contract section 5.
        Existing values are preserved so enrichment is non-destructive; fields that
        cannot be derived are left unset (Req 5.5).
        """
        text = self._response_text(data)
        if record.genre is None:
            record.genre = self._parse_genre(text)
        if record.estimated_playtime is None:
            record.estimated_playtime = self._parse_playtime(text)
        if not record.platform_availability:
            record.platform_availability = self._parse_platforms(text)
        if record.community_review is None:
            record.community_review = self._parse_review(data, text)
        return record

    @staticmethod
    def _response_text(data: dict[str, Any]) -> str:
        """Concatenate the answer and result snippets into one lowercased blob."""
        parts: list[str] = []
        answer = data.get("answer")
        if isinstance(answer, str):
            parts.append(answer)
        for result in TavilyService._results(data):
            content = result.get("content")
            if isinstance(content, str):
                parts.append(content)
        return " ".join(parts).lower()

    @staticmethod
    def _results(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the ``results`` list from a response, tolerating bad shapes."""
        results = data.get("results")
        if not isinstance(results, list):
            return []
        return [r for r in results if isinstance(r, dict)]

    @staticmethod
    def _parse_genre(text: str) -> str | None:
        """Return the first recognized genre mentioned in ``text``, else ``None``."""
        for keyword, canonical in _GENRE_KEYWORDS:
            if keyword in text:
                return canonical
        return None

    @staticmethod
    def _parse_playtime(text: str) -> int | None:
        """Parse an estimated playtime in minutes from ``text``, else ``None``.

        A range ("20-30 hours") is averaged; bare hours/minutes are converted
        directly. The result is always normalized to whole minutes (Req 5.1).
        """
        for pattern, kind in _PLAYTIME_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            if kind == "hour_range":
                low, high = float(match.group(1)), float(match.group(2))
                return round((low + high) / 2 * 60)
            if kind == "hours":
                return round(float(match.group(1)) * 60)
            return int(match.group(1))
        return None

    @staticmethod
    def _parse_platforms(text: str) -> list[str]:
        """Extract a deduplicated list of available platforms from ``text`` (Req 5.3).

        Specific console variants take precedence over generic family tokens, so a
        page mentioning "Xbox Series X" yields that variant rather than bare "Xbox".
        """
        found: list[str] = []
        families_with_specific: set[str] = set()
        for keyword, canonical, family, is_generic in _PLATFORM_KEYWORDS:
            if keyword not in text:
                continue
            if is_generic and family in families_with_specific:
                continue
            if not is_generic:
                families_with_specific.add(family)
            if canonical not in found:
                found.append(canonical)
        return found

    def _parse_review(self, data: dict[str, Any], text: str) -> CommunityReview | None:
        """Build a ``CommunityReview`` when a score can be derived, else ``None``.

        The score is normalized to 0.0-10.0; the sentiment summary comes from the
        Tavily answer (or first snippet) and ``source_count`` reflects the number of
        aggregated results (Req 7.2).
        """
        score = self._parse_score(text)
        if score is None:
            return None
        return CommunityReview(
            score=score,
            sentiment_summary=self._summary(data),
            source_count=len(self._results(data)),
        )

    @staticmethod
    def _parse_score(text: str) -> float | None:
        """Parse a community review score normalized to 0.0-10.0, else ``None``."""
        for pattern, factor in _SCORE_PATTERNS:
            match = pattern.search(text)
            if match:
                score = float(match.group(1)) * factor
                return max(0.0, min(10.0, score))
        return None

    @staticmethod
    def _summary(data: dict[str, Any]) -> str:
        """Return a short sentiment summary from the answer or first snippet."""
        answer = data.get("answer")
        if isinstance(answer, str) and answer.strip():
            return answer.strip()[:_SUMMARY_MAX_CHARS]
        for result in TavilyService._results(data):
            content = result.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()[:_SUMMARY_MAX_CHARS]
        return "Community sentiment summary unavailable."

    @staticmethod
    def _extract_titles(data: dict[str, Any]) -> list[str]:
        """Return deduplicated result titles for autocomplete suggestions (Req 3.4)."""
        titles: list[str] = []
        for result in TavilyService._results(data):
            title = result.get("title")
            if isinstance(title, str) and title.strip() and title not in titles:
                titles.append(title.strip())
        return titles
