"""API layer tests: the FastAPI adapter over the service graph.

Everything runs against fakes (no AWS, no Tavily): a dict-backed memory
client behind the real ``MemoryService``, a scripted ``BedrockService`` for
the agent loop, and a canned enricher/autocomplete. The chat tests parse the
actual SSE wire format, and the busy-turn test exercises the 409 guard with
a genuinely open stream.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.enricher import Enricher
from agent.library_service import LibraryService
from agent.runtime import AgentRuntime
from agent.tools import ToolRegistry
from api.app import TurnGuard, create_app
from bootstrap import AppContext
from config import Config
from models.game_record import GameRecord
from models.recommendation import Recommendation
from models.session import SessionData
from services.bedrock_service import (
    BedrockService,
    BedrockServiceError,
    ConverseResult,
    ToolUse,
)
from services.memory_service import MemoryService
from services.sources.base import RecordSource
from services.sources.manual_source import ManualSource
from services.tavily_service import TavilyService

USER = "default"


class FakeMemoryClient:
    """Dict-backed stand-in for the DynamoDB client (MemoryClient protocol)."""

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict[str, Any]] = {}
        self.events: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def get_value(self, user_id: str, key: str) -> dict[str, Any] | None:
        return self.docs.get((user_id, key))

    def put_value(self, user_id: str, key: str, value: dict[str, Any]) -> None:
        self.docs[(user_id, key)] = value

    def append_event(self, user_id: str, key: str, event: dict[str, Any]) -> None:
        self.events.setdefault((user_id, key), []).append(event)

    def list_events(self, user_id: str, key: str, limit: int) -> list[dict[str, Any]]:
        return list(reversed(self.events.get((user_id, key), [])))[:limit]

    def clear_events(self, user_id: str, key: str) -> None:
        self.events.pop((user_id, key), None)


class FakeBedrock(BedrockService):
    """Returns scripted ``ConverseResult``s (or raises a scripted error).

    The streaming variant re-plays each scripted round as two text deltas
    followed by the assembled result — the same contract the real
    ``converse_tools_stream`` provides.
    """

    def __init__(self, script: list[ConverseResult | BedrockServiceError]) -> None:
        self._script = list(script)

    def converse_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> ConverseResult:
        step = self._script.pop(0)
        if isinstance(step, BedrockServiceError):
            raise step
        return step

    def converse_tools_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> Iterator[str | ConverseResult]:
        result = self.converse_tools(messages, tools, system)
        if result.text:
            middle = max(1, len(result.text) // 2)
            yield result.text[:middle]
            yield result.text[middle:]
        yield result


class FakeEnricher(Enricher):
    """Fills the enrichment fields without any network calls."""

    def __init__(self) -> None:
        pass

    def enrich(self, record: GameRecord) -> GameRecord:
        record.genre = "Roguelike"
        record.platform_availability = ["Switch", "PC"]
        return record


class FakeTavily(TavilyService):
    """Serves canned autocomplete suggestions; nothing else is exercised."""

    def __init__(self) -> None:
        pass

    def autocomplete(self, query: str) -> list[str]:
        return ["Hades", "Hades II"]


def make_app(
    script: list[ConverseResult | BedrockServiceError] | None = None,
    bedrock: BedrockService | None = None,
) -> tuple[TestClient, AppContext, FakeMemoryClient]:
    """Wire the full graph over fakes and return a test client onto it."""
    store = FakeMemoryClient()
    memory = MemoryService(store)
    tavily = FakeTavily()
    enricher = FakeEnricher()
    sources: list[RecordSource] = [ManualSource(memory, USER)]
    library = LibraryService(sources=sources, enricher=enricher, memory=memory)
    tools = ToolRegistry(
        memory=memory, library=library, tavily=tavily, enricher=enricher, user_id=USER
    )
    runtime = AgentRuntime(
        bedrock=bedrock or FakeBedrock(script or []),
        tools=tools,
        memory=memory,
        system_prompt="test",
    )
    config = Config(
        aws_region="eu-west-1",
        bedrock_model_id="test-model",
        tavily_api_key="test-key",
        dynamodb_table_name="test-table",
    )
    ctx = AppContext(
        config=config,
        user_id=USER,
        memory=memory,
        tavily=tavily,
        library=library,
        enricher=enricher,
        runtime=runtime,
        gmail=None,
    )
    return TestClient(create_app(ctx)), ctx, store


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Split an SSE body into (event-name, decoded-payload) pairs."""
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.strip().split("\n\n"):
        lines = block.split("\n")
        name = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        events.append((name, json.loads(data)))
    return events


# --- health ---


def test_health_reports_memory_state() -> None:
    client, _, _ = make_app()
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "memory_available": True}


# --- library ---


def test_add_list_and_remove_game() -> None:
    client, _, _ = make_app()
    created = client.post("/api/library", json={"title": "  Hades ", "platform": "Switch"})
    assert created.status_code == 201
    record = created.json()["record"]
    assert record["title"] == "Hades"
    assert record["dedup_key"] == "hades|switch"
    assert record["is_enriched"] is False

    listed = client.get("/api/library").json()
    assert [r["title"] for r in listed["records"]] == ["Hades"]

    assert client.delete("/api/library/hades|switch").status_code == 204
    assert client.get("/api/library").json()["records"] == []
    assert client.delete("/api/library/hades|switch").status_code == 404


def test_add_game_rejects_blank_title() -> None:
    client, _, _ = make_app()
    assert client.post("/api/library", json={"title": ""}).status_code == 422
    # Whitespace-only must not slip past validation as an empty-title record.
    assert client.post("/api/library", json={"title": "   "}).status_code == 422


def test_blank_platform_means_no_platform() -> None:
    client, _, _ = make_app()
    created = client.post("/api/library", json={"title": "Hades", "platform": "  "})
    assert created.status_code == 201
    assert created.json()["record"]["platforms"] == []
    assert client.put("/api/library/hades|/platform", json={"platform": " "}).status_code == 422
    assert client.post("/api/platforms", json={"name": "  "}).status_code == 422


def test_set_platform_persists_and_rekeys() -> None:
    client, _, _ = make_app()
    client.post("/api/library", json={"title": "Celeste"})
    updated = client.put("/api/library/celeste|/platform", json={"platform": "Switch"})
    assert updated.status_code == 200
    assert updated.json()["record"]["platforms"] == ["Switch"]
    records = client.get("/api/library").json()["records"]
    assert records[0]["dedup_key"] == "celeste|switch"


def test_enrich_fills_fields_and_persists() -> None:
    client, _, _ = make_app()
    client.post("/api/library", json={"title": "Hades", "platform": "Switch"})
    enriched = client.post("/api/library/hades|switch/enrich")
    assert enriched.status_code == 200
    assert enriched.json()["record"]["is_enriched"] is True
    assert client.get("/api/library").json()["records"][0]["genre"] == "Roguelike"


def test_enrich_unknown_game_is_404() -> None:
    client, _, _ = make_app()
    assert client.post("/api/library/nope|/enrich").status_code == 404


def test_autocomplete_min_length_gate() -> None:
    client, _, _ = make_app()
    assert client.get("/api/autocomplete", params={"q": "ha"}).json() == {"suggestions": []}
    long_enough = client.get("/api/autocomplete", params={"q": "had"}).json()
    assert long_enough == {"suggestions": ["Hades", "Hades II"]}


# --- platforms ---


def test_platform_crud_roundtrip() -> None:
    client, _, _ = make_app()
    created = client.post("/api/platforms", json={"name": "Nintendo Switch"})
    assert created.status_code == 201
    platform_id = created.json()["platform"]["platform_id"]

    assert client.put(f"/api/platforms/{platform_id}", json={"name": "Switch"}).status_code == 200
    names = [p["name"] for p in client.get("/api/platforms").json()["platforms"]]
    assert names == ["Switch"]

    assert client.delete(f"/api/platforms/{platform_id}").status_code == 204
    assert client.get("/api/platforms").json()["platforms"] == []
    assert client.put(f"/api/platforms/{platform_id}", json={"name": "X"}).status_code == 404
    assert client.delete(f"/api/platforms/{platform_id}").status_code == 404


# --- picks & feedback ---


def _seed_pick(ctx: AppContext, title: str) -> None:
    ctx.memory.store_session(
        USER,
        SessionData(
            user_id=USER,
            mood="cozy",
            time_budget_minutes=120,
            recommendation=Recommendation(game_title=title, reasoning="fits", estimated_playtime=9),
        ),
    )


def test_picks_dedupe_verdicts_and_owned_flag() -> None:
    client, ctx, _ = make_app()
    _seed_pick(ctx, "Death's Door")
    _seed_pick(ctx, "Death's Door")  # recommended twice; shown once
    _seed_pick(ctx, "Hades")
    client.post("/api/library", json={"title": "Hades"})
    client.post("/api/picks/feedback", json={"title": "Death's Door", "verdict": "loved"})

    picks = client.get("/api/picks").json()["picks"]
    assert [p["game_title"] for p in picks] == ["Hades", "Death's Door"]
    by_title = {p["game_title"]: p for p in picks}
    assert by_title["Death's Door"]["verdict"] == "loved"
    assert by_title["Hades"]["owned"] is True
    assert by_title["Death's Door"]["owned"] is False


def test_clear_picks_keeps_feedback() -> None:
    client, ctx, _ = make_app()
    _seed_pick(ctx, "Tunic")
    client.post("/api/picks/feedback", json={"title": "Tunic", "verdict": "not_for_me"})
    assert client.delete("/api/picks").status_code == 204
    assert client.get("/api/picks").json()["picks"] == []
    # Verdicts are taste, not recency — they survive a history clear.
    assert ctx.memory.get_feedback(USER)["tunic"]["verdict"] == "not_for_me"


def test_feedback_clears_with_null_verdict() -> None:
    client, ctx, _ = make_app()
    client.post("/api/picks/feedback", json={"title": "Tunic", "verdict": "loved"})
    client.post("/api/picks/feedback", json={"title": "Tunic", "verdict": None})
    assert ctx.memory.get_feedback(USER) == {}


# --- conversation & chat ---


def _tool_round() -> ConverseResult:
    return ConverseResult(
        stop_reason="tool_use",
        text="let me check your platforms",
        tool_uses=[ToolUse(tool_use_id="t1", name="get_owned_platforms", input={})],
        assistant_content=[{"text": "let me check your platforms"}, {"toolUse": {"id": "t1"}}],
        usage={"inputTokens": 100, "outputTokens": 20},
    )


def _answer_round(text: str = "🎯 Play Death's Door tonight — it fits.") -> ConverseResult:
    return ConverseResult(
        stop_reason="end_turn",
        text=text,
        assistant_content=[{"text": text}],
        usage={"inputTokens": 150, "outputTokens": 60},
    )


def test_chat_streams_events_and_persists_transcript() -> None:
    client, _, _ = make_app(script=[_tool_round(), _answer_round()])
    response = client.post("/api/chat", json={"message": "cozy tonight"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    kinds = [name for name, _ in events]
    # Each round streams two deltas ahead of its closing thinking/text event.
    assert kinds == ["delta", "delta", "thinking", "tool", "delta", "delta", "text", "done"]
    assert events[3][1] == {"tool": "get_owned_platforms"}
    final_text = events[6][1]["text"]
    assert events[4][1]["text"] + events[5][1]["text"] == final_text
    done = events[7][1]
    assert done["usage"] == {"inputTokens": 250, "outputTokens": 80}
    assert done["memory_available"] is True

    messages = client.get("/api/conversation").json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "cozy tonight"
    assert "Death's Door" in messages[1]["content"]
    assert messages[1]["notes"] == ["let me check your platforms"]


def test_chat_error_is_streamed_and_not_persisted() -> None:
    client, _, _ = make_app(script=[BedrockServiceError("The recommendation service is busy")])
    events = parse_sse(client.post("/api/chat", json={"message": "hi"}).text)
    assert events == [("error", {"message": "The recommendation service is busy"})]
    assert client.get("/api/conversation").json()["messages"] == []


class BlockingBedrock(BedrockService):
    """Parks the model round on an event so a turn stays in flight."""

    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self._started = started
        self._release = release

    def converse_tools_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> Iterator[str | ConverseResult]:
        self._started.set()
        assert self._release.wait(timeout=5), "test never released the blocked turn"
        yield _answer_round()


def test_chat_second_turn_while_one_is_in_flight_is_409() -> None:
    started, release = threading.Event(), threading.Event()
    client, ctx, _ = make_app(bedrock=BlockingBedrock(started, release))
    first: list[int] = []
    turn = threading.Thread(
        target=lambda: first.append(client.post("/api/chat", json={"message": "first"}).status_code)
    )
    turn.start()
    try:
        assert started.wait(timeout=5), "first turn never reached the model"
        # A second client (same app) while the first turn is mid-model-call:
        busy = TestClient(client.app).post("/api/chat", json={"message": "second"})
        assert busy.status_code == 409
    finally:
        release.set()
        turn.join(timeout=5)
    assert first == [200]
    # The slot frees once the stream completes: the SAME app accepts a new turn
    # (the released event lets the fake answer immediately this time).
    follow_up = client.post("/api/chat", json={"message": "again"})
    assert follow_up.status_code == 200


def test_turn_guard_stale_release_cannot_free_a_newer_turn() -> None:
    """Turn N's late backstop release must not free the slot turn N+1 holds."""
    guard = TurnGuard()
    token1 = guard.begin(USER)
    assert token1 is not None
    guard.end(USER, token1)  # turn 1's normal release

    token2 = guard.begin(USER)
    assert token2 is not None
    guard.end(USER, token1)  # turn 1's LATE backstop fires after turn 2 claimed
    assert guard.begin(USER) is None  # turn 2 still holds the slot

    guard.end(USER, token2)
    assert guard.begin(USER) is not None  # a matching release frees it


def test_reset_conversation_clears_agent_and_store() -> None:
    client, ctx, _ = make_app(script=[_answer_round()])
    client.post("/api/chat", json={"message": "hello"})
    assert client.get("/api/conversation").json()["messages"] != []
    assert client.delete("/api/conversation").status_code == 204
    assert client.get("/api/conversation").json()["messages"] == []
    assert ctx.runtime._messages == []  # noqa: SLF001 — asserting the reset reached the agent


def test_conversation_restores_agent_context_after_restart() -> None:
    """A new process (fresh runtime) seeded from the stored transcript keeps context."""
    client, ctx, store = make_app(script=[_answer_round()])
    client.post("/api/chat", json={"message": "hello"})

    # Same store, brand-new app/runtime — as after a server restart.
    memory = MemoryService(store)
    tavily = FakeTavily()
    enricher = FakeEnricher()
    library = LibraryService(sources=[ManualSource(memory, USER)], enricher=enricher, memory=memory)
    tools = ToolRegistry(
        memory=memory, library=library, tavily=tavily, enricher=enricher, user_id=USER
    )
    runtime = AgentRuntime(
        bedrock=FakeBedrock([_answer_round("A follow-up pick.")]),
        tools=tools,
        memory=memory,
        system_prompt="test",
    )
    ctx2 = AppContext(
        config=ctx.config,
        user_id=USER,
        memory=memory,
        tavily=tavily,
        library=library,
        enricher=enricher,
        runtime=runtime,
        gmail=None,
    )
    client2 = TestClient(create_app(ctx2))
    events = parse_sse(client2.post("/api/chat", json={"message": "shorter?"}).text)
    assert events[-1][0] == "done"
    # The restored history (2 messages) precedes the new turn's pair.
    assert len(client2.get("/api/conversation").json()["messages"]) == 4


# --- entry point ---


def test_main_build_wires_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "AWS_REGION": "eu-west-1",
        "BEDROCK_MODEL_ID": "test-model",
        "TAVILY_API_KEY": "test-key",
        "DYNAMODB_TABLE_NAME": "test-table",
    }.items():
        monkeypatch.setenv(key, value)
    from api.main import build

    assert isinstance(build(), FastAPI)


# --- authentication --------------------------------------------------------
#
# create_app() reads COGNITO_USER_POOL_ID at build time, so these tests set it
# before constructing the app. Every other test in this file runs with it
# absent, which is the local-development path.


def test_routes_reject_requests_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a pool configured, an unauthenticated request must not reach data."""
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "eu-north-1_test")
    monkeypatch.setenv("AWS_REGION", "eu-north-1")
    client, _, _ = make_app()

    for method, path in [
        ("get", "/api/library"),
        ("get", "/api/picks"),
        ("get", "/api/platforms"),
        ("get", "/api/conversation"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 401, f"{method.upper()} {path} was not protected"


def test_chat_rejects_requests_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "eu-north-1_test")
    monkeypatch.setenv("AWS_REGION", "eu-north-1")
    client, _, _ = make_app()

    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_health_stays_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Health is the origin's readiness probe — it must not require a token."""
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "eu-north-1_test")
    monkeypatch.setenv("AWS_REGION", "eu-north-1")
    client, _, _ = make_app()

    assert client.get("/api/health").status_code == 200


def test_a_rejected_token_yields_401_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "eu-north-1_test")
    monkeypatch.setenv("AWS_REGION", "eu-north-1")
    client, _, _ = make_app()

    response = client.get("/api/library", headers={"X-Id-Token": "not-a-real-token"})
    assert response.status_code == 401


def test_without_a_pool_the_api_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """The local path: no pool means no auth, so frontend work needs no AWS."""
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    client, _, _ = make_app()

    assert client.get("/api/library").status_code == 200
