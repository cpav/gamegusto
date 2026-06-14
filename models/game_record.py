"""Canonical owned-game record contract (data contract v1.0.0).

This module realizes the locked ``GameRecord`` schema documented in
``docs/data-contract.md``. Every record source (Xbox, Gmail, manual) produces
records conforming to this contract, and every consumer (LibraryService,
Recommender, MemoryService, UI) reads it. There are no per-source record types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

CONTRACT_VERSION = "1.0.0"

Source = Literal["xbox", "gmail", "manual", "enrichment"]


@dataclass
class CommunityReview:
    """Aggregated community review signal populated by enrichment (Tavily)."""

    score: float
    """Normalized review score on a 0.0-10.0 scale."""

    sentiment_summary: str
    """Short summary used in recommendation reasoning (Req 7.3)."""

    source_count: int
    """Number of aggregated sources backing the score (a confidence signal)."""


@dataclass
class GameRecord:
    """A single owned game, normalized across all sources (data contract v1.0.0)."""

    title: str
    platforms: list[str] = field(default_factory=list)
    source: Source = "manual"
    purchase_date: date | None = None
    genre: str | None = None
    estimated_playtime: int | None = None
    community_review: CommunityReview | None = None
    platform_availability: list[str] = field(default_factory=list)
    external_ids: dict[str, str] = field(default_factory=dict)

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
