"""Unit tests for the agent tool-use loop (agent.runtime).

A scripted ``BedrockService`` returns preset Converse turns so the loop can be
exercised deterministically with no network: final answers, multi-round tool
dispatch, the iteration cap, the stateless flag, and history reset.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agent.enricher import Enricher
from agent.library_service import LibraryService
from agent.runtime import (
    _ITERATION_LIMIT_MESSAGE,
    _MAX_TOOL_ITERATIONS,
    _MAX_WRAPUP_ROUNDS,
    AgentReply,
    AgentRuntime,
)
from agent.tools import ToolRegistry
from services.bedrock_service import BedrockServiceError, ConverseResult, ToolUse
from services.memory_service import MemoryService
from services.sources.manual_source import ManualSource
from services.tavily_service import TavilyService

USER_ID = "runtime-user"


class _ScriptedBedrock:
    """Returns preset ConverseResults in order; records the history it receives.

    An entry may also be an ``Exception`` instance, which is raised instead of
    returned (to script transient model failures).
    """

    def __init__(self, turns: list[Any], loop_last: bool = False) -> None:
        self._turns = list(turns)
        self._loop_last = loop_last
        self.calls: list[list[dict[str, Any]]] = []
        self.tool_args: list[list[dict[str, Any]]] = []
        self.systems: list[str] = []

    def converse_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str
    ) -> ConverseResult:
        self.calls.append(copy.deepcopy(messages))
        self.tool_args.append(tools)
        self.systems.append(system)
        if not self._turns:
            raise AssertionError("script exhausted")
        turn = self._turns[0]
        if not (self._loop_last and len(self._turns) == 1):
            self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        assert isinstance(turn, ConverseResult)
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
    enricher = Enricher(bedrock, tavily)
    library = LibraryService([ManualSource(memory, USER_ID)], enricher, memory)
    tools = ToolRegistry(memory, library, tavily, enricher, USER_ID)
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

    # Only the final turn's text is the persisted answer; the tool-turn note ("Adding
    # that.") is transient "thinking" and is not baked into the reply.
    assert reply.message == "Done — try Hades."
    assert "Adding that." not in reply.message
    assert reply.tool_calls == ["add_platform"]
    # The tool actually ran against memory.
    assert [p.name for p in memory.get_platform_list(USER_ID)] == ["Nintendo Switch"]
    # The second model call saw the assistant turn and the toolResult appended.
    second_call_history = bedrock.calls[1]
    assert second_call_history[-1]["content"][0]["toolResult"]["toolUseId"] == "t1"


def test_tool_turn_text_is_thinking_not_persisted() -> None:
    """Text written in a research tool-calling turn is transient 'thinking'; only the
    final turn's text is persisted as the answer (the prompt steers the model to write
    its full reply in that final turn)."""
    lookup = ToolUse("t1", "get_library", {})
    bedrock = _ScriptedBedrock(
        [_tool_call(lookup, "Let me note that."), _final("I recommend Hades — a fast roguelike.")]
    )
    runtime, _ = _runtime(bedrock)

    reply = runtime.send("recommend a roguelike")

    assert reply.message == "I recommend Hades — a fast roguelike."
    assert "Let me note that" not in reply.message  # the tool-turn note is not kept


def test_save_turn_text_is_the_answer() -> None:
    """The model often presents the recommendation and calls save_recommendation in
    ONE turn. That (substantial) text is the answer — it must be kept, with the final
    turn's short follow-up appended, not discarded as thinking (which kept only the
    follow-up). Short save narration is covered by the wrap-up test below."""
    presented = (
        "🎯 Play Hades — a superb roguelike.\n\n"
        "It matches your love of fast, skill-based action: every run is short enough "
        "for a weeknight, the combat is razor sharp, and the story actually rewards "
        "dying. Reception is stellar (93 on Metacritic), it's on the Switch you own, "
        "and it isn't in your library.\n\nAlternatives: Dead Cells, Rogue Legacy 2."
    )
    save = ToolUse("s1", "save_recommendation", {"game_title": "Hades", "reasoning": "fits"})
    bedrock = _ScriptedBedrock(
        [
            _tool_call(save, presented),
            _final("A demo is also available if you want to try first!"),
        ]
    )
    runtime, _ = _runtime(bedrock)

    reply = runtime.send("recommend a roguelike")

    assert reply.message == (presented + "\n\nA demo is also available if you want to try first!")


def test_falls_back_to_thinking_when_final_turn_is_silent() -> None:
    """If the final turn produces no text, the working notes are used so the reply is
    never empty (safety net for a model that answered mid-loop)."""
    lookup = ToolUse("t1", "get_library", {})
    bedrock = _ScriptedBedrock([_tool_call(lookup, "I recommend Hades."), _final("")])
    runtime, _ = _runtime(bedrock)

    reply = runtime.send("recommend a roguelike")

    assert "I recommend Hades" in reply.message


def test_stream_yields_thinking_tool_and_answer_events_in_order() -> None:
    """stream() marks tool-turn text as 'thinking' and the final text as 'text'."""
    add = ToolUse("t1", "add_platform", {"name": "Nintendo Switch"})
    bedrock = _ScriptedBedrock([_tool_call(add, "Adding that."), _final("Done — try a new RPG.")])
    runtime, _ = _runtime(bedrock)

    events = list(runtime.stream("I own a Switch"))

    assert [e.kind for e in events] == ["thinking", "tool", "text"]
    assert events[0].text == "Adding that."  # transient working note
    assert events[1].tool == "add_platform"
    assert events[2].text == "Done — try a new RPG."  # final answer


def test_iteration_cap_returns_fallback() -> None:
    forever = _tool_call(ToolUse("t", "get_owned_platforms", {}))
    bedrock = _ScriptedBedrock([forever], loop_last=True)
    runtime, _ = _runtime(bedrock)

    reply = runtime.send("loop please")

    assert reply.message == _ITERATION_LIMIT_MESSAGE
    # Main cap rounds plus the bounded closing rounds, since the wrap-up now dispatches
    # tools too (so the model can finish e.g. a save before answering).
    assert len(reply.tool_calls) == _MAX_TOOL_ITERATIONS + _MAX_WRAPUP_ROUNDS
    # Every wrap-up call must STILL carry tool specs: once the history holds
    # toolUse/toolResult blocks, Converse rejects a request with no toolConfig
    # ("toolConfig field must be defined"). Regression guard for that engine crash.
    assert bedrock.tool_args[-1], "wrap-up call dropped tools -> Converse ValidationException"


def test_wrap_up_completes_pending_save_then_answers() -> None:
    """On cap-hit, if the model responds to the wrap-up by saving (a tool call) plus
    narrating, the closing round dispatches the save and the NEXT round's text is the
    real answer — the 'let me save…' narration is never mistaken for the reply."""
    ping = _tool_call(ToolUse("t", "get_owned_platforms", {}))
    save = _tool_call(
        ToolUse("s", "save_recommendation", {"game_title": "Hades", "reasoning": "fits"}),
        preface="Now I have everything — let me save this.",
    )
    turns = [ping] * _MAX_TOOL_ITERATIONS + [save, _final("🎮 Play Hades — a superb roguelike.")]
    bedrock = _ScriptedBedrock(turns)
    runtime, _ = _runtime(bedrock)

    reply = runtime.send("recommend under pressure")

    assert reply.message == "🎮 Play Hades — a superb roguelike."
    assert "let me save" not in reply.message.lower()


def test_failed_turn_rolls_back_history_so_next_turn_works() -> None:
    """A transient model failure must not leave an orphaned user message behind:
    Converse rejects two consecutive user messages, so without rollback one failure
    would brick every subsequent turn of the conversation."""
    bedrock = _ScriptedBedrock([BedrockServiceError("model down"), _final("Play Hades!")])
    runtime, _ = _runtime(bedrock)

    with pytest.raises(BedrockServiceError):
        runtime.send("first try")
    reply = runtime.send("second try")

    assert reply.message == "Play Hades!"
    # The failed turn left no trace: the successful call saw exactly one user message.
    assert len(bedrock.calls[-1]) == 1
    assert bedrock.calls[-1][0]["content"][0]["text"] == "second try"


def test_failure_mid_tool_round_rolls_back_dangling_tool_use() -> None:
    """A failure AFTER a tool round must roll back the toolUse/toolResult messages too
    (a dangling toolUse without toolConfig also fails Converse validation)."""
    add = ToolUse("t1", "add_platform", {"name": "Switch"})
    bedrock = _ScriptedBedrock(
        [_tool_call(add), BedrockServiceError("model down"), _final("All good now.")]
    )
    runtime, _ = _runtime(bedrock)

    with pytest.raises(BedrockServiceError):
        runtime.send("first try")
    reply = runtime.send("second try")

    assert reply.message == "All good now."
    history = bedrock.calls[-1]
    assert len(history) == 1  # no leftover assistant/toolResult messages
    assert "toolUse" not in str(history)


def test_callable_system_prompt_is_resolved_fresh_each_turn() -> None:
    """A callable system prompt is re-evaluated per turn (keeps the embedded date
    fresh in a long-lived session)."""
    stamps = iter(["day one", "day two"])
    bedrock = _ScriptedBedrock([_final("one"), _final("two")])
    tavily = TavilyService(api_key="x", client=_NoopTavilyClient())
    memory = MemoryService(_InMemoryClient())
    enricher = Enricher(bedrock, tavily)  # type: ignore[arg-type]
    library = LibraryService([ManualSource(memory, USER_ID)], enricher, memory)
    tools = ToolRegistry(memory, library, tavily, enricher, USER_ID)
    runtime = AgentRuntime(
        bedrock,  # type: ignore[arg-type]
        tools,
        memory,
        system_prompt=lambda: next(stamps),
    )

    runtime.send("hi")
    runtime.send("again")

    assert bedrock.systems == ["day one", "day two"]


def test_history_is_windowed_at_turn_boundaries() -> None:
    """Long sessions drop the oldest whole turns: each Bedrock round re-sends the
    entire history, so an unbounded session grows cost quadratically."""
    from agent.runtime import _MAX_HISTORY_TURNS

    turns = _MAX_HISTORY_TURNS + 2
    bedrock = _ScriptedBedrock([_final(f"reply {n}") for n in range(turns)])
    runtime, _ = _runtime(bedrock)

    for n in range(turns):
        runtime.send(f"turn {n}")

    last_history = bedrock.calls[-1]
    first_texts = [m["content"][0]["text"] for m in last_history if m["role"] == "user"]
    # The last request holds exactly the window: the newest turn plus the
    # (_MAX_HISTORY_TURNS - 1) before it; "turn 0" and "turn 1" fell off.
    assert first_texts == [f"turn {n}" for n in range(2, turns)]
    assert len(first_texts) == _MAX_HISTORY_TURNS


def test_windowing_never_splits_tool_pairs() -> None:
    """The window cut lands on a turn boundary even when old turns contain
    toolUse/toolResult pairs (splitting one would fail Converse validation)."""
    from agent.runtime import _MAX_HISTORY_TURNS

    add = ToolUse("t1", "add_platform", {"name": "Switch"})
    script: list[Any] = []
    for _ in range(_MAX_HISTORY_TURNS + 1):
        script.extend([_tool_call(add), _final("done")])
    bedrock = _ScriptedBedrock(script)
    runtime, _ = _runtime(bedrock)

    for n in range(_MAX_HISTORY_TURNS + 1):
        runtime.send(f"turn {n}")

    last_history = bedrock.calls[-1]
    assert last_history[0]["role"] == "user"
    assert "toolResult" not in str(last_history[0])  # window opens on a plain user turn
    # Every toolUse in the window still has its toolResult in the next message.
    for index, message in enumerate(last_history):
        if message["role"] == "assistant" and "toolUse" in str(message):
            assert "toolResult" in str(last_history[index + 1])


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
    enricher = Enricher(bedrock, tavily)  # type: ignore[arg-type]
    library = LibraryService([ManualSource(memory, USER_ID)], enricher, memory)
    tools = ToolRegistry(memory, library, tavily, enricher, USER_ID)
    runtime = AgentRuntime(bedrock, tools, _StatelessMemory())  # type: ignore[arg-type]

    reply = runtime.send("hi")

    assert reply.is_stateless_mode is True
