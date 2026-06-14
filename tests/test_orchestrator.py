"""Unit tests for :class:`AgentOrchestrator` (task 6.8).

Covers the conversation flow the design specifies (mood → time → platform gate →
recommendation), plus the empty-platform gate (Req 6.5), the no-match sentinel
(Req 7.1), session persistence (Req 8.1), and stateless degradation (Req 10.2).

Collaborators are replaced with in-process fakes so no network, AWS, or Tavily
call is ever made; each fake exposes only what the orchestrator touches.
"""

from __future__ import annotations

from agent.mood_interpreter import MoodDimensions, MoodInterpretation
from agent.orchestrator import AgentOrchestrator
from agent.time_parser import TimeParseResult
from models.platform import OwnedPlatform
from models.recommendation import Recommendation
from models.session import SessionData

USER_ID = "anonymous"

_MOOD = MoodDimensions(
    energy_level=0.5,
    stress_level=0.5,
    social_desire=0.5,
    challenge_appetite=0.5,
)


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
    """Library service whose ``refresh`` returns a preset record list."""

    def __init__(self, records: list[object] | None = None) -> None:
        self._records = records or []
        self.refreshed = False

    def refresh(self, user_id: str) -> list[object]:
        self.refreshed = True
        return list(self._records)


class FakeRecommender:
    """Recommender returning a preset :class:`Recommendation`."""

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

    def __init__(
        self, platforms: list[OwnedPlatform] | None = None, available: bool = True
    ) -> None:
        self._platforms = platforms or []
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


def _recommendation(title: str = "Hades") -> Recommendation:
    return Recommendation(
        game_title=title,
        genre="Action",
        estimated_playtime=30,
        reasoning=f"{title} is a great pick.",
        platform_availability=["Switch"],
    )


def _sentinel() -> Recommendation:
    return Recommendation(
        game_title="",
        genre=None,
        estimated_playtime=None,
        reasoning="I couldn't find a game that fits right now.",
    )


def _build(
    *,
    mood_result: MoodInterpretation,
    time_result: TimeParseResult,
    recommendation: Recommendation,
    platforms: list[OwnedPlatform] | None = None,
    available: bool = True,
) -> tuple[AgentOrchestrator, FakeMemory, FakeRecommender, FakeLibrary]:
    memory = FakeMemory(platforms=platforms, available=available)
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


def _interpreted() -> MoodInterpretation:
    return MoodInterpretation(
        mood_dimensions=_MOOD, needs_clarification=False, clarification_question=None
    )


def _parsed(minutes: int = 60) -> TimeParseResult:
    return TimeParseResult(minutes=minutes, needs_clarification=False, clarification_question=None)


# --- Mood phase -----------------------------------------------------------


def test_uninterpretable_mood_returns_clarification_and_stays_in_mood_phase() -> None:
    """Req 1.1/1.3: an uninterpretable mood keeps the session in mood gathering."""
    mood_result = MoodInterpretation(
        mood_dimensions=None,
        needs_clarification=True,
        clarification_question="How are you feeling?",
    )
    orchestrator, _, _, _ = _build(
        mood_result=mood_result, time_result=_parsed(), recommendation=_recommendation()
    )

    response = orchestrator.process_message("hmm")

    assert response.message == "How are you feeling?"
    assert response.recommendation is None
    assert orchestrator.session.current_phase == "mood_gathering"
    assert orchestrator.session.mood is None


def test_mood_clarification_falls_back_to_restart_prompt_when_question_missing() -> None:
    """Defensive: clarification with no supplied question falls back to a restart prompt."""
    mood_result = MoodInterpretation(
        mood_dimensions=None,
        needs_clarification=True,
        clarification_question=None,
    )
    orchestrator, _, _, _ = _build(
        mood_result=mood_result, time_result=_parsed(), recommendation=_recommendation()
    )

    response = orchestrator.process_message("???")

    assert "feeling" in response.message.lower()
    assert orchestrator.session.current_phase == "mood_gathering"


def test_interpreted_mood_advances_to_time_phase_and_asks_for_time() -> None:
    """Req 1.4: once the mood is interpreted, the agent asks how much time the user has."""
    orchestrator, _, _, _ = _build(
        mood_result=_interpreted(), time_result=_parsed(), recommendation=_recommendation()
    )

    response = orchestrator.process_message("relaxed and happy")

    assert orchestrator.session.mood == _MOOD
    assert orchestrator.session.current_phase == "time_gathering"
    assert "time" in response.message.lower()
    assert response.recommendation is None


# --- Time phase -----------------------------------------------------------


def test_ambiguous_time_returns_clarification_and_stays_in_time_phase() -> None:
    """Req 1.6: ambiguous time keeps the session in time gathering."""
    time_result = TimeParseResult(
        minutes=None,
        needs_clarification=True,
        clarification_question="Roughly how long?",
    )
    orchestrator, _, _, _ = _build(
        mood_result=_interpreted(),
        time_result=time_result,
        recommendation=_recommendation(),
        platforms=[OwnedPlatform(name="Switch")],
    )
    orchestrator.process_message("relaxed")  # advance into time phase

    response = orchestrator.process_message("a bit")

    assert response.message == "Roughly how long?"
    assert orchestrator.session.current_phase == "time_gathering"
    assert orchestrator.session.time_budget_minutes is None


def test_parsed_time_proceeds_to_recommendation() -> None:
    """Req 1.4/7.1: a parsed time budget proceeds straight to a recommendation."""
    orchestrator, memory, recommender, library = _build(
        mood_result=_interpreted(),
        time_result=_parsed(90),
        recommendation=_recommendation(),
        platforms=[OwnedPlatform(name="Switch")],
    )
    orchestrator.process_message("relaxed")

    response = orchestrator.process_message("90 minutes")

    assert orchestrator.session.time_budget_minutes == 90
    assert response.recommendation is not None
    assert response.recommendation.game_title == "Hades"
    assert response.message == "Hades is a great pick."
    assert library.refreshed is True
    assert recommender.calls[0]["time_budget_minutes"] == 90
    assert memory.stored_sessions  # session persisted (Req 8.1)


# --- Platform gate (Req 6.5) ----------------------------------------------


def test_empty_platform_list_blocks_recommendation() -> None:
    """Req 6.5: an empty Platform_List blocks the recommendation and flags needs_platforms."""
    orchestrator, _, recommender, library = _build(
        mood_result=_interpreted(),
        time_result=_parsed(),
        recommendation=_recommendation(),
        platforms=[],
    )
    orchestrator.process_message("relaxed")

    response = orchestrator.process_message("1 hour")

    assert response.needs_platforms is True
    assert response.recommendation is None
    assert orchestrator.session.current_phase == "platform_setup"
    assert library.refreshed is False
    assert recommender.calls == []


# --- No-match sentinel (Req 7.1) ------------------------------------------


def test_no_match_sentinel_returns_friendly_message_without_recommendation() -> None:
    """Req 7.1: the empty-title sentinel surfaces a no-match message, no recommendation set."""
    orchestrator, memory, _, _ = _build(
        mood_result=_interpreted(),
        time_result=_parsed(),
        recommendation=_sentinel(),
        platforms=[OwnedPlatform(name="Switch")],
    )
    orchestrator.process_message("relaxed")

    response = orchestrator.process_message("1 hour")

    assert response.recommendation is None
    assert response.message == "I couldn't find a game that fits right now."
    assert orchestrator.session.primary_recommendation is None
    assert memory.stored_sessions == []  # nothing persisted for a no-match


# --- Stateless degradation (Req 10.2) -------------------------------------


def test_stateless_mode_reflected_and_session_not_persisted_when_memory_unavailable() -> None:
    """Req 10.2: when memory is unavailable, responses flag stateless mode and skip persistence."""
    orchestrator, memory, _, _ = _build(
        mood_result=_interpreted(),
        time_result=_parsed(),
        recommendation=_recommendation(),
        platforms=[OwnedPlatform(name="Switch")],
        available=False,
    )
    orchestrator.process_message("relaxed")

    response = orchestrator.process_message("1 hour")

    assert response.is_stateless_mode is True
    assert response.recommendation is not None
    assert memory.stored_sessions == []  # no persistence in stateless mode


def test_recommendation_phase_with_incomplete_state_restarts_intake() -> None:
    """Defensive: reaching the recommendation phase without mood/time restarts intake."""
    orchestrator, _, recommender, library = _build(
        mood_result=_interpreted(),
        time_result=_parsed(),
        recommendation=_recommendation(),
        platforms=[OwnedPlatform(name="Switch")],
    )
    # Jump straight to the recommendation phase without gathering mood or time.
    orchestrator.session.current_phase = "recommendation"

    response = orchestrator.process_message("recommend")

    assert orchestrator.session.current_phase == "mood_gathering"
    assert response.recommendation is None
    assert library.refreshed is False
    assert recommender.calls == []
