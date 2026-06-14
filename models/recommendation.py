"""Display-facing recommendation model.

A ``Recommendation`` is what the UI renders and what the agent persists with a
session (via the ``save_recommendation`` tool); it is derived from a canonical
:class:`~models.game_record.GameRecord`. The record uses ``title`` internally,
while this display surface exposes ``game_title`` (Req 7.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models.game_record import CommunityReview


@dataclass
class Recommendation:
    """A single recommendation rendered in the chat view (Req 7.1, 7.3, 7.4)."""

    game_title: str
    genre: str | None
    estimated_playtime: int | None
    """Estimated playtime in minutes."""

    reasoning: str
    """Detailed reasoning for the primary recommendation (Req 7.3)."""

    brief_reasoning: str = ""
    """Short reasoning used for alternatives (Req 7.4)."""

    platform_availability: list[str] = field(default_factory=list)
    community_review: CommunityReview | None = None
