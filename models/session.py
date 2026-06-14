"""Session models for conversation state and persisted session records.

``SessionState`` is the in-memory state the orchestrator drives through the
conversation phases; ``SessionData`` is the persisted record written to memory
for personalization (Req 8.1).

To respect the layered architecture (models never import the agent layer at
runtime), ``MoodDimensions`` is referenced only under ``TYPE_CHECKING`` together
with ``from __future__ import annotations``, so the annotation stays a string
and no agent module is imported at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from models.recommendation import Recommendation

if TYPE_CHECKING:
    from agent.mood_interpreter import MoodDimensions


@dataclass
class SessionState:
    """Mutable conversation state for a single user session."""

    user_id: str = "anonymous"
    current_phase: str = "mood_gathering"
    """One of: mood_gathering, time_gathering, platform_setup, recommendation,
    alternatives."""

    mood: MoodDimensions | None = None
    time_budget_minutes: int | None = None
    primary_recommendation: Recommendation | None = None
    alternatives: list[Recommendation] = field(default_factory=list)


@dataclass
class SessionData:
    """Completed session persisted to memory for personalization (Req 8.1)."""

    user_id: str
    mood: MoodDimensions
    time_budget_minutes: int
    recommendation: Recommendation
    alternatives: list[Recommendation] = field(default_factory=list)
    user_feedback: str | None = None
