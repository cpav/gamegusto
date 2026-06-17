"""Recommendation record persisted with a session.

A ``Recommendation`` is what the agent persists via the ``save_recommendation``
tool and what the recommendation history reads back. The agent produces its
reasoning as free text, so this carries just the title, that reasoning, and the
playtime it fit to; ``game_title`` mirrors a ``GameRecord.title`` (Req 7.1, 8.1).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Recommendation:
    """A recommendation persisted for history and no-repeat logic (Req 7.1, 8.1)."""

    game_title: str
    reasoning: str
    estimated_playtime: int | None = None
    """Estimated playtime in minutes, when known."""
