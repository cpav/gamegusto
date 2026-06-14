"""Unit tests for the agent tool-use loop (agent.runtime).

A scripted ``BedrockService`` returns preset Converse turns so the loop can be
exercised deterministically with no network: final answers, multi-round tool
dispatch, the iteration cap, the stateless flag, and history reset.
"""

from __future__ import annotations

import copy
from typing import Any

from agent.library_service import LibraryService
from agent.runtime import _ITERATION_LIMIT_MESSAGE, AgentReply, AgentRuntime
from agent.tools import ToolRegistry
from services.bedrock_service import ConverseResult, ToolUse
from services.memory_service import MemoryService
from services.sources.manual_source import ManualSource
from services.tavily_service import TavilyService

USER_ID = "runtime-user"


class _ScriptedBedrock:
    """Returns preset ConverseResults in order; records the history it receives."""

    def __init__(self, turns: list[ConverseResult], loop_last: bool = False) -> None:
        self._turns = list(turns)
        self._loop_last = loop_last
        self.calls: list[list[dict[str, Any]]] = []

    def converse_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str
    ) -> ConverseResult:
        self.calls.append(copy.deepcopy(messages))
        if not self._turns:
            raise AssertionError("script exhausted")
        turn = self._turns[0]
        if not (self._loop_last and len(self._turns) == 1):
            self._turns.pop(0)
        return turn


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


def _final(text: str) -> ConverseResult:
    return ConverseResult(stop_reason="end_turn", text=text, assistant_content=[{"text": text}])


def _tool_call(use: ToolUse, preface: str = "") -> ConverseResult:
    content: list[dict[str, Any]] = []
    if preface:
        content.append({"text": preface})
    content.append(
        {"toolUse": {"toolUseId": use.tool_use_id, "name": use.name, "input": use.input}}
    )
    return ConverseResult(
        stop_reason="tool_use", text=preface, tool_uses=[use], assistant_content=content
    )


def _runtime(bedrock: Any) -> tuple[AgentRuntime, MemoryService]:
    memory = MemoryService(_InMemoryClient())
    tavily = TavilyService(api_key="x", client=_NoopTavilyClient())
    library = LibraryService([ManualSource(memory, USER_ID)], tavily, memory)
    tools = ToolRegistry(memory, library, tavily, USER_ID)
    return AgentRuntime(bedrock, tools, memory), memory


def test_immediate_final_answer() -> None:
    bedrock = _ScriptedBedrock([_final("Play Hades!")])
    runtime, _ = _runtime(bedrock)

    reply = runtime.send("recommend something")

    assert isinstance(reply, AgentReply)
    assert reply.message == "Play Hades!"
    assert reply.tool_calls == []
    assert reply.is_stateless_mode is False


def test_tool_round_trip_executes_tool_and_returns_answer() -> None:
    add = ToolUse("t1", "add_platform", {"name": "Nintendo Switch"})
    bedrock = _ScriptedBedrock([_tool_call(add, "Adding that."), _final("Done — try Hades.")])
    runtime, memory = _runtime(bedrock)

    reply = runtime.send("I own a Switch")

    assert reply.message == "Done — try Hades."
    assert reply.tool_calls == ["add_platform"]
    # The tool actually ran against memory.
    assert [p.name for p in memory.get_platform_list(USER_ID)] == ["Nintendo Switch"]
    # The second model call saw the assistant turn and the toolResult appended.
    second_call_history = bedrock.calls[1]
    assert second_call_history[-1]["content"][0]["toolResult"]["toolUseId"] == "t1"


def test_iteration_cap_returns_fallback() -> None:
    forever = _tool_call(ToolUse("t", "get_owned_platforms", {}))
    bedrock = _ScriptedBedrock([forever], loop_last=True)
    runtime, _ = _runtime(bedrock)

    reply = runtime.send("loop please")

    assert reply.message == _ITERATION_LIMIT_MESSAGE
    assert len(reply.tool_calls) == 8  # _MAX_TOOL_ITERATIONS rounds


def test_reset_clears_history() -> None:
    bedrock = _ScriptedBedrock([_final("one"), _final("two")])
    runtime, _ = _runtime(bedrock)

    runtime.send("first")
    runtime.reset()
    runtime.send("second")

    # Second send starts fresh: exactly one (user) message before the model call.
    assert len(bedrock.calls[1]) == 1
    assert bedrock.calls[1][0]["content"][0]["text"] == "second"


class _StatelessMemory:
    """Minimal memory stand-in reporting an unavailable backing store."""

    is_available = False


def test_stateless_flag_reflects_memory_health() -> None:
    bedrock = _ScriptedBedrock([_final("hi")])
    tavily = TavilyService(api_key="x", client=_NoopTavilyClient())
    memory = MemoryService(_InMemoryClient())
    library = LibraryService([ManualSource(memory, USER_ID)], tavily, memory)
    tools = ToolRegistry(memory, library, tavily, USER_ID)
    runtime = AgentRuntime(bedrock, tools, _StatelessMemory())  # type: ignore[arg-type]

    reply = runtime.send("hi")

    assert reply.is_stateless_mode is True
