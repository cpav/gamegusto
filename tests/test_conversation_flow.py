"""End-to-end conversation flow over the real agent graph (network edge faked).

Wires the real ``MoodInterpreter``, ``TimeParser``, ``LibraryService``,
``Recommender``, ``ManualSource``, ``MemoryService`` and ``AgentOrchestrator``
together, faking only the three external boundaries (Bedrock, Tavily, the memory
client). This guards the headless runnable path: mood -> time -> platform gate ->
recommendation, including alternatives and the no-platform gate.
"""

from __future__ import annotations

from typing import Any

from agent.library_service import LibraryService
from agent.mood_interpreter import MoodInterpreter
from agent.orchestrator import AgentOrchestrator
from agent.recommender import Recommender
from agent.time_parser import TimeParser
from models.game_record import CommunityReview, GameRecord
from models.platform import OwnedPlatform
from services.bedrock_service import BedrockService
from services.memory_service import MemoryService
from services.sources.manual_source import ManualSource
from services.tavily_service import TavilyService

USER_ID = "flow-user"


class _FakeBedrock(BedrockService):
    """Bedrock stand-in: structured mood JSON + canned reasoning (no network)."""

    def __init__(self) -> None:
        pass

    def invoke_with_schema(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "energy_level": 0.3,
            "stress_level": 0.2,
            "social_desire": 0.3,
            "challenge_appetite": 0.3,
            "interpretable": True,
        }

    def invoke_conversational(self, prompt: str, session_id: str) -> str:
        return "Have fun!"


class _InMemoryClient:
    """Dict-backed MemoryClient (no network)."""

    def __init__(self) -> None:
        self._docs: dict[tuple[str, str], dict[str, Any]] = {}
        self._events: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def get_value(self, user_id: str, key: str) -> dict[str, Any] | None:
        return self._docs.get((user_id, key))

    def put_value(self, user_id: str, key: str, value: dict[str, Any]) -> None:
        self._docs[(user_id, key)] = value

    def append_event(self, user_id: str, key: str, event: dict[str, Any]) -> None:
        self._events.setdefault((user_id, key), []).append(event)

    def list_events(self, user_id: str, key: str, limit: int) -> list[dict[str, Any]]:
        return list(reversed(self._events.get((user_id, key), [])))[:limit]


class _NoopTavilyClient:
    """Tavily client returning nothing; seeded records are pre-enriched."""

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return {}


def _build() -> AgentOrchestrator:
    memory = MemoryService(_InMemoryClient())
    memory.add_platform(USER_ID, OwnedPlatform(name="Nintendo Switch"))
    memory.store_records(
        USER_ID,
        [
            GameRecord(
                title="Hades",
                platforms=["Nintendo Switch"],
                source="manual",
                genre="Roguelike",
                estimated_playtime=40,
                community_review=CommunityReview(9.3, "Beloved for its combat.", 42),
                platform_availability=["Nintendo Switch", "PC"],
            ),
            GameRecord(
                title="Stardew Valley",
                platforms=["Nintendo Switch"],
                source="manual",
                genre="Simulation",
                estimated_playtime=50,
                community_review=CommunityReview(9.1, "Cozy and relaxing.", 30),
                platform_availability=["Nintendo Switch"],
            ),
        ],
    )
    bedrock = _FakeBedrock()
    tavily = TavilyService(api_key="x", client=_NoopTavilyClient())
    library = LibraryService([ManualSource(memory, USER_ID)], tavily, memory)
    orchestrator = AgentOrchestrator(
        MoodInterpreter(bedrock), TimeParser(), Recommender(bedrock, memory), library, memory
    )
    orchestrator.session.user_id = USER_ID
    return orchestrator


def test_full_conversation_yields_recommendation_with_alternatives() -> None:
    """Mood -> time -> recommendation produces a playable, well-reasoned pick."""
    orchestrator = _build()

    first = orchestrator.process_message("I'm feeling relaxed")
    assert "time" in first.message.lower()
    assert first.recommendation is None

    second = orchestrator.process_message("about an hour, say 60 minutes")
    assert second.recommendation is not None
    rec = second.recommendation
    assert rec.game_title == "Hades"  # higher review than Stardew within budget
    assert "Nintendo Switch" in rec.platform_availability
    assert "Beloved for its combat." in rec.reasoning  # review summary present
    assert second.alternatives  # Stardew offered as an alternative
    assert all("Nintendo Switch" in alt.platform_availability for alt in second.alternatives)


def test_recommendation_blocked_without_platforms() -> None:
    """With no owned platforms, the recommendation is gated (Req 6.5)."""
    memory = MemoryService(_InMemoryClient())
    bedrock = _FakeBedrock()
    tavily = TavilyService(api_key="x", client=_NoopTavilyClient())
    library = LibraryService([ManualSource(memory, USER_ID)], tavily, memory)
    orchestrator = AgentOrchestrator(
        MoodInterpreter(bedrock), TimeParser(), Recommender(bedrock, memory), library, memory
    )
    orchestrator.session.user_id = USER_ID

    orchestrator.process_message("relaxed")
    response = orchestrator.process_message("60 minutes")

    assert response.needs_platforms is True
    assert response.recommendation is None
