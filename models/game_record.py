"""Canonical owned-game record contract (data contract v1.0.0).

This module realizes the locked ``GameRecord`` schema documented in
``docs/data-contract.md``. Every record source (Gmail, manual) produces
records conforming to this contract, and every consumer (LibraryService,
Recommender, MemoryService, UI) reads it. There are no per-source record types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

CONTRACT_VERSION = "3.2.0"

Source = Literal["gmail", "manual", "enrichment"]

#: The user's own verdict on a game they've played — a cooking metaphor that
#: captures how it landed for THEM, independent of the critics: loved it despite
#: the reviews, an underrated gem, a guilty pleasure, bland (good on paper, left
#: them cold), or bounced off entirely. This is the strongest taste signal there
#: is, because it's first-hand. (contract v3.2)
TasteVerdict = Literal["chefs_kiss", "hidden_gem", "guilty_pleasure", "bland", "sent_back"]

#: What kind of experience a game is / when the user reaches for it — a quick
#: bite, the big main event, or a cozy wind-down. Orthogonal to the verdict, and
#: what lets "I've got 30 minutes tonight" pull from the right shelf. (contract v3.2)
Course = Literal["starter", "main", "dessert"]


@dataclass
class CommunityReview:
    """Aggregated community review signal populated by enrichment (web search)."""

    score: float
    """Normalized review score on a 0.0-10.0 scale."""

    sentiment_summary: str
    """Short summary used in recommendation reasoning (Req 7.3)."""

    source_count: int
    """Number of aggregated sources backing the score (a confidence signal)."""

    def as_dict(self) -> dict[str, Any]:
        """Serialize to the persisted/contract shape (single source of truth)."""
        return {
            "score": self.score,
            "sentiment_summary": self.sentiment_summary,
            "source_count": self.source_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CommunityReview | None:
        """Rebuild from a persisted dict, or ``None`` when absent."""
        if not data:
            return None
        return cls(
            score=data["score"],
            sentiment_summary=data["sentiment_summary"],
            source_count=data["source_count"],
        )


@dataclass
class GameRecord:
    """A single owned game, normalized across all sources (data contract v3.1.0)."""

    title: str
    platforms: list[str] = field(default_factory=list)
    source: Source = "manual"
    purchase_date: date | None = None
    genre: str | None = None
    estimated_playtime_hours: float | None = None
    """Approximate main-story completion time in HOURS (e.g. 12.5). Was minutes
    under contract v2 (``estimated_playtime``); the store converts legacy values
    on read. Hours are the unit people actually think in for game lengths."""
    community_review: CommunityReview | None = None
    platform_availability: list[str] = field(default_factory=list)
    external_ids: dict[str, str] = field(default_factory=dict)
    cover_url: str | None = None
    """Cover/key art URL for the v2 card grid (contract v3.1). Presentation-only:
    it never affects dedup, matching, or recommendation reasoning, and a record
    without one renders a styled placeholder instead."""
    taste: TasteVerdict | None = None
    """The user's own verdict on this game (contract v3.2). Set by hand from the
    library, never by enrichment — it is first-hand taste, not a critic score.
    The agent leans toward what loved games share and away from rejected ones."""
    course: Course | None = None
    """What kind of experience this is / when the user reaches for it (v3.2):
    a quick starter, a main event, or a dessert wind-down. Pairs with the time
    the user has tonight to decide what actually fits."""
    taste_note: str | None = None
    """The user's own short comment on the game (v3.2) — 'combat sings in short
    bursts', 'gorgeous but hollow'. Handed to the agent verbatim; the richest,
    most specific taste signal of all."""

    @property
    def dedup_key(self) -> str:
        """Normalized title+platform key that collapses cross-source duplicates.

        Uses ``casefold`` for Unicode-aware case folding and strips surrounding
        whitespace so ``"  Hades "`` and ``"hades"`` map to the same key (Req 2.3).
        """
        platform = self.platforms[0] if self.platforms else ""
        return f"{self.title.strip().casefold()}|{platform.strip().casefold()}"

    def is_enriched(self) -> bool:
        """Whether enrichment has populated the fields needed for recommendation.

        Gates cache-first enrichment (Req 5.1): a record is enriched once it has a
        genre and at least one known availability platform.
        """
        return self.genre is not None and bool(self.platform_availability)
