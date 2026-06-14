"""Persisted session record for personalization (Req 8.1).

``SessionData`` is the completed-session record written to memory after the agent
makes a recommendation. It feeds the no-repeat logic and cross-session
personalization (Req 8.2, 8.3). The agent now drives the conversation itself
(there is no fixed mood/time phase machine), so ``mood`` is a free-text summary
the agent supplies rather than a fixed set of numeric dimensions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models.recommendation import Recommendation


@dataclass
class SessionData:
    """Completed session persisted to memory for personalization (Req 8.1)."""

    user_id: str
    mood: str
    """Free-text summary of the player's mood/context for this session."""

    time_budget_minutes: int
    recommendation: Recommendation
    alternatives: list[Recommendation] = field(default_factory=list)
    user_feedback: str | None = None
