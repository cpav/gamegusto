"""Conversation orchestration for GameGusto.

The :class:`AgentOrchestrator` drives a single recommendation session through its
phases — mood gathering, time gathering, an owned-platform gate, and finally the
recommendation (Req 1.1, 1.4, 6.5, 7.1). It composes the mood interpreter, time
parser, library assembly, recommender, and memory store via constructor
injection and exposes the public ``session`` so the UI can render current state.

When the backing memory store is unavailable the session still completes; it
simply runs without persistence and flags ``is_stateless_mode`` so the UI can
tell the user that personalization is temporarily limited (Req 10.2).
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.library_service import LibraryService
from agent.mood_interpreter import MoodDimensions, MoodInterpreter
from agent.recommender import Recommender
from agent.time_parser import TimeParser
from models.recommendation import Recommendation
from models.session import SessionData, SessionState
from services.memory_service import MemoryService

#: Prompt asking for the time budget once a mood has been interpreted (Req 1.4).
_TIME_PROMPT = "Got it. How much time do you have to play? For example, '45 minutes' or '2 hours'."

#: Prompt shown when the Platform_List is empty and blocks a recommendation (Req 6.5).
_PLATFORM_PROMPT = (
    "Tell me which platforms you own before I recommend — add at least one in the Library view."
)

#: Prompt used to restart intake if conversation state is somehow incomplete.
_RESTART_PROMPT = "Let's start over — how are you feeling right now?"


@dataclass
class AgentResponse:
    """A single agent turn rendered by the UI.

    ``needs_platforms`` is set when an empty Platform_List blocks a recommendation
    (Req 6.5); ``is_stateless_mode`` reflects an unavailable memory store (Req 10.2).
    """

    message: str
    recommendation: Recommendation | None = None
    alternatives: list[Recommendation] | None = None
    error: str | None = None
    is_stateless_mode: bool = False
    needs_platforms: bool = False


class AgentOrchestrator:
    """Drives mood → time → platform gate → recommendation for one session."""

    def __init__(
        self,
        mood_interpreter: MoodInterpreter,
        time_parser: TimeParser,
        recommender: Recommender,
        library_service: LibraryService,
        memory_service: MemoryService,
    ) -> None:
        """Build the orchestrator from its injected collaborators (Req 7.1)."""
        self._mood = mood_interpreter
        self._time = time_parser
        self._recommender = recommender
        self._library = library_service
        self._memory = memory_service
        self.session = SessionState()

    def process_message(self, user_input: str) -> AgentResponse:
        """Route input by the current conversation phase (Req 1.1, 1.4, 6.5, 7.1)."""
        phase = self.session.current_phase
        if phase == "mood_gathering":
            return self._handle_mood(user_input)
        if phase == "time_gathering":
            return self._handle_time(user_input)
        return self._generate_recommendation()

    def _handle_mood(self, user_input: str) -> AgentResponse:
        """Interpret the Mood_Input; clarify when unclear, else ask for time (Req 1.1, 1.4)."""
        result = self._mood.interpret(user_input)
        if result.needs_clarification or result.mood_dimensions is None:
            return self._respond(result.clarification_question or _RESTART_PROMPT)
        self.session.mood = result.mood_dimensions
        self.session.current_phase = "time_gathering"
        return self._respond(_TIME_PROMPT)

    def _handle_time(self, user_input: str) -> AgentResponse:
        """Parse the Time_Budget; clarify when ambiguous, else recommend (Req 1.4)."""
        result = self._time.parse(user_input)
        if result.needs_clarification or result.minutes is None:
            return self._respond(result.clarification_question or _TIME_PROMPT)
        self.session.time_budget_minutes = result.minutes
        self.session.current_phase = "recommendation"
        return self._generate_recommendation()

    def _generate_recommendation(self) -> AgentResponse:
        """Gate on the Platform_List, then recommend and persist the session (Req 6.5, 7.1)."""
        user_id = self.session.user_id
        platforms = self._memory.get_platform_list(user_id)
        if not platforms:
            self.session.current_phase = "platform_setup"
            return self._respond(_PLATFORM_PROMPT, needs_platforms=True)

        mood = self.session.mood
        time_budget = self.session.time_budget_minutes
        if mood is None or time_budget is None:  # incomplete state: restart intake
            self.session.current_phase = "mood_gathering"
            return self._respond(_RESTART_PROMPT)

        library = self._library.refresh(user_id)
        rec = self._recommender.recommend(
            mood=mood,
            time_budget_minutes=time_budget,
            library=library,
            owned_platforms=platforms,
            user_id=user_id,
        )
        if not rec.game_title:  # sentinel: no candidate matched (Req 7.1)
            return self._respond(rec.reasoning)

        self.session.primary_recommendation = rec
        self._persist_session(user_id, mood, time_budget, rec)
        return self._respond(rec.reasoning, recommendation=rec)

    def _persist_session(
        self,
        user_id: str,
        mood: MoodDimensions,
        time_budget: int,
        recommendation: Recommendation,
    ) -> None:
        """Persist the completed session when memory is available (Req 8.1, 10.2)."""
        if not self._memory.is_available:
            return
        self._memory.store_session(
            user_id,
            SessionData(
                user_id=user_id,
                mood=mood,
                time_budget_minutes=time_budget,
                recommendation=recommendation,
            ),
        )

    def _respond(
        self,
        message: str,
        recommendation: Recommendation | None = None,
        needs_platforms: bool = False,
    ) -> AgentResponse:
        """Build a response, reflecting stateless mode from memory health (Req 10.2)."""
        return AgentResponse(
            message=message,
            recommendation=recommendation,
            is_stateless_mode=not self._memory.is_available,
            needs_platforms=needs_platforms,
        )
