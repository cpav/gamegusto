"""End-to-end agent conversation over the real graph (network edge faked).

Wires the real ``ToolRegistry``, ``LibraryService``, ``ManualSource``,
``MemoryService`` and ``AgentRuntime`` together, faking only the network edges
(Bedrock and Tavily). A scripted model drives a realistic multi-turn journey that
mirrors the brief's acceptance scenario: a taste-rich request yields a matching
owned title, and an "I already played it" follow-up offers the next best within
the same conversation — without re-asking what is already known.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.library_service import LibraryService
from agent.runtime import AgentRuntime
from agent.tools import ToolRegistry
from models.game_record import CommunityReview, GameRecord
from models.platform import OwnedPlatform
from services.bedrock_service import ConverseResult, ToolUse
from services.memory_service import MemoryService
from services.sources.manual_source import ManualSource
from services.tavily_service import TavilyService

USER_ID = "e2e-user"


class _ScriptedBedrock:
    """Replays a fixed list of Converse turns across the whole conversation."""

    def __init__(self, turns: list[ConverseResult]) -> None:
        self._turns = list(turns)

    def converse_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str
    ) -> ConverseResult:
        return self._turns.pop(0)


class _InMemoryClient:
    def __init__(self) -> None:
        self._docs: dict[tuple[str, str], dict[str, Any]] = {}
        self._events: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def get_value(self, user_id: str, key: str) -> dict[str, Any] | None:
        return self._docs.get((user_id, key))

    def put_value(self, user_id: str, key: str, value: dict[str, Any]) -> None:
        self._docs[(user_id, key)] = value

    def append_event(self, user_id: str, key: str, event: dict[str, Any]) -> None:
        self._events.setdefault((user_id, key), []).insert(0, event)

    def list_events(self, user_id: str, key: str, limit: int) -> list[dict[str, Any]]:
        return list(self._events.get((user_id, key), []))[:limit]


class _NoopTavilyClient:
    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return {}


def _tool(use: ToolUse) -> ConverseResult:
    return ConverseResult(
        stop_reason="tool_use",
        text="",
        tool_uses=[use],
        assistant_content=[
            {"toolUse": {"toolUseId": use.tool_use_id, "name": use.name, "input": use.input}}
        ],
    )


def _final(text: str) -> ConverseResult:
    return ConverseResult(stop_reason="end_turn", text=text, assistant_content=[{"text": text}])


@pytest.mark.e2e
def test_taste_match_then_already_played_followup() -> None:
    memory = MemoryService(_InMemoryClient())
    memory.add_platform(USER_ID, OwnedPlatform(name="Nintendo Switch"))
    memory.store_records(
        USER_ID,
        [
            GameRecord(
                title="Octopath Traveler",
                platforms=["Nintendo Switch"],
                source="manual",
                genre="RPG",
                estimated_playtime=3000,
                community_review=CommunityReview(8.5, "Acclaimed HD-2D job-system RPG.", 20),
                platform_availability=["Nintendo Switch"],
            ),
            GameRecord(
                title="Triangle Strategy",
                platforms=["Nintendo Switch"],
                source="manual",
                genre="Tactical RPG",
                estimated_playtime=3000,
                community_review=CommunityReview(8.0, "Deep HD-2D tactics.", 15),
                platform_availability=["Nintendo Switch"],
            ),
        ],
    )

    tavily = TavilyService(api_key="x", client=_NoopTavilyClient())
    library = LibraryService([ManualSource(memory, USER_ID)], tavily, memory)
    tools = ToolRegistry(memory, library, tavily, USER_ID)

    octopath_pick = (
        "I recommend Octopath Traveler — an HD-2D RPG with a job system, great solo, "
        "and a manageable story. Alternative: Triangle Strategy."
    )
    triangle_pick = (
        "Since you've played Octopath, go for Triangle Strategy — same HD-2D style with "
        "a deeper tactical job system."
    )
    bedrock = _ScriptedBedrock(
        [
            _tool(ToolUse("a", "get_owned_platforms", {})),
            _tool(ToolUse("b", "get_library", {})),
            _final(octopath_pick),
            _tool(
                ToolUse(
                    "c",
                    "save_recommendation",
                    {"game_title": "Octopath Traveler", "reasoning": "fit"},
                )
            ),
            _final(triangle_pick),
        ]
    )
    runtime = AgentRuntime(bedrock, tools, memory)  # type: ignore[arg-type]

    first = runtime.send(
        "I want an RPG with a cool job system, 2D HD graphics, solo, challenging, "
        "story not too complex, ~30h."
    )
    assert first.tool_calls == ["get_owned_platforms", "get_library"]
    assert "Octopath Traveler" in first.message

    second = runtime.send("I already played it")
    assert "Triangle Strategy" in second.message
    # The recommendation was persisted during the follow-up turn.
    assert tools.dispatch("get_recent_recommendations", {})["titles"] == ["Octopath Traveler"]
