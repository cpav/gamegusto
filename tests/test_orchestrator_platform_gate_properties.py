"""Property-based test for the orchestrator's owned-platform gate (task 6.9).

Property 14: Empty platform list blocks recommendation (Req 6.5). For *any*
interpreted mood and *any* parsed time budget, an empty Platform_List must block
the recommendation — the orchestrator returns ``needs_platforms=True`` with no
recommendation, parks the session in ``platform_setup``, and never calls the
recommender or refreshes the library. When the Platform_List is non-empty the
gate passes and a recommendation is produced (the recommender is called).

Collaborators are in-process fakes so no network, AWS, or Tavily call is made;
each fake exposes only what the orchestrator touches.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.mood_interpreter import MoodDimensions, MoodInterpretation
from agent.orchestrator import AgentOrchestrator
from agent.time_parser import TimeParseResult
from models.platform import OwnedPlatform
from models.recommendation import Recommendation
from models.session import SessionData

USER_ID = "anonymous"


# --- Fakes (no network) ---------------------------------------------------


class FakeMood:
    """Mood interpreter returning a scripted :class:`MoodInterpretation`."""

    def __init__(self, result: MoodInterpretation) -> None:
        self._result = result
        self.calls: list[str] = []

    def interpret(self, text: str) -> MoodInterpretation:
        self.calls.append(text)
        return self._result


class FakeTime:
    """Time parser returning a scripted :class:`TimeParseResult`."""

    def __init__(self, result: TimeParseResult) -> None:
        self._result = result
        self.calls: list[str] = []

    def parse(self, text: str) -> TimeParseResult:
        self.calls.append(text)
        return self._result


class FakeLibrary:
    """Library service whose ``refresh`` records whether it was called."""

    def __init__(self) -> None:
        self.refreshed = False

    def refresh(self, user_id: str) -> list[object]:
        self.refreshed = True
        return []


class FakeRecommender:
    """Recommender returning a preset :class:`Recommendation`; records calls."""

    def __init__(self, recommendation: Recommendation) -> None:
        self._recommendation = recommendation
        self.calls: list[dict[str, object]] = []

    def recommend(
        self,
        mood: MoodDimensions,
        time_budget_minutes: int,
        library: list[object],
        owned_platforms: list[OwnedPlatform],
        user_id: str,
    ) -> Recommendation:
        self.calls.append(
            {
                "mood": mood,
                "time_budget_minutes": time_budget_minutes,
                "owned_platforms": owned_platforms,
                "user_id": user_id,
            }
        )
        return self._recommendation


class FakeMemory:
    """Memory store: serves a preset Platform_List and captures stored sessions."""

    def __init__(self, platforms: list[OwnedPlatform], available: bool = True) -> None:
        self._platforms = platforms
        self._available = available
        self.stored_sessions: list[SessionData] = []

    def get_platform_list(self, user_id: str) -> list[OwnedPlatform]:
        return list(self._platforms)

    def store_session(self, user_id: str, session: SessionData) -> bool:
        self.stored_sessions.append(session)
        return True

    @property
    def is_available(self) -> bool:
        return self._available


# --- Builders -------------------------------------------------------------


def _build(
    *,
    mood: MoodDimensions,
    minutes: int,
    platforms: list[OwnedPlatform],
) -> tuple[AgentOrchestrator, FakeMemory, FakeRecommender, FakeLibrary]:
    """Wire an orchestrator that, on the second message, reaches the platform gate."""
    mood_result = MoodInterpretation(
        mood_dimensions=mood, needs_clarification=False, clarification_question=None
    )
    time_result = TimeParseResult(
        minutes=minutes, needs_clarification=False, clarification_question=None
    )
    # The recommender's platform_availability intersects an owned platform when present.
    owned_name = platforms[0].name if platforms else "Switch"
    recommendation = Recommendation(
        game_title="Hades",
        genre="Action",
        estimated_playtime=30,
        reasoning="Hades is a great pick.",
        platform_availability=[owned_name],
    )
    memory = FakeMemory(platforms=platforms)
    recommender = FakeRecommender(recommendation)
    library = FakeLibrary()
    orchestrator = AgentOrchestrator(
        mood_interpreter=FakeMood(mood_result),  # type: ignore[arg-type]
        time_parser=FakeTime(time_result),  # type: ignore[arg-type]
        recommender=recommender,  # type: ignore[arg-type]
        library_service=library,  # type: ignore[arg-type]
        memory_service=memory,  # type: ignore[arg-type]
    )
    return orchestrator, memory, recommender, library


# --- Strategies -----------------------------------------------------------

_dimension = st.floats(min_value=0.0, max_value=1.0)

_moods = st.builds(
    MoodDimensions,
    energy_level=_dimension,
    stress_level=_dimension,
    social_desire=_dimension,
    challenge_appetite=_dimension,
)

_time_budgets = st.integers(min_value=1, max_value=24 * 60)

_platform_names = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126), min_size=1, max_size=20
)

_platform_lists = st.lists(st.builds(OwnedPlatform, name=_platform_names), min_size=0, max_size=5)


# --- Property 14 ----------------------------------------------------------


@settings(max_examples=200)
@given(mood=_moods, minutes=_time_budgets, platforms=_platform_lists)
def test_property_14_platform_gate(
    mood: MoodDimensions, minutes: int, platforms: list[OwnedPlatform]
) -> None:
    """Property 14: empty Platform_List blocks the recommendation, non-empty passes.

    **Validates: Requirements 6.5**
    """
    orchestrator, _, recommender, library = _build(mood=mood, minutes=minutes, platforms=platforms)

    orchestrator.process_message("some mood")  # advance mood -> time phase
    response = orchestrator.process_message("some time")  # parse time -> platform gate

    if not platforms:
        # Empty list: the gate blocks the recommendation entirely.
        assert response.needs_platforms is True
        assert response.recommendation is None
        assert orchestrator.session.current_phase == "platform_setup"
        assert recommender.calls == []
        assert library.refreshed is False
    else:
        # Non-empty list: the gate passes and a recommendation is produced.
        assert response.needs_platforms is False
        assert response.recommendation is not None
        assert len(recommender.calls) == 1
        assert library.refreshed is True
