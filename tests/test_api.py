"""API layer tests: the FastAPI adapter over the service graph.

Everything runs against fakes (no AWS, no Tavily, no IGDB): a dict-backed
memory client behind the real ``MemoryService``, a scripted ``BedrockService``
for the agent loop, a canned enricher, and a canned IGDB catalog search. The
chat tests parse the actual SSE wire format, and the busy-turn test exercises
the 409 guard with a genuinely open stream.
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
from services.igdb_service import GameSuggestion, IgdbService
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
        #: Titles for which the image search finds nothing, mirroring a real
        #: Tavily miss or rate limit. Per instance, not shared on the class.
        self.no_art: set[str] = set()

    def enrich(self, record: GameRecord, *, refresh_cover: bool = False) -> GameRecord:
        record.genre = "Roguelike"
        record.platform_availability = ["Switch", "PC"]
        # Matches the real enricher: art is fetched before the is_enriched()
        # early return, a miss leaves what was already there, and an explicit
        # refresh replaces an existing cover.
        if record.cover_url is None or refresh_cover:
            found = (
                None if record.title in self.no_art else f"https://img.example/{record.title}.jpg"
            )
            if found or record.cover_url is None:
                record.cover_url = found
        return record


class FakeTavily(TavilyService):
    """A stand-in Tavily; the agent's web search is scripted via Bedrock, not here."""

    def __init__(self) -> None:
        pass


class FakeIgdb(IgdbService):
    """Serves canned catalog suggestions; the query is echoed so the gate is testable."""

    def __init__(self) -> None:
        pass

    @property
    def is_available(self) -> bool:
        return True

    def search_games(self, query: str, limit: int = 8) -> list[GameSuggestion]:
        return [
            GameSuggestion(query.title(), ("Nintendo Switch", "PC"), "https://img.example/a.jpg"),
            GameSuggestion(f"{query.title()} II", ("PC",), None),
        ]


def make_app(
    script: list[ConverseResult | BedrockServiceError] | None = None,
    bedrock: BedrockService | None = None,
) -> tuple[TestClient, AppContext, FakeMemoryClient]:
    """Wire the full graph over fakes and return a test client onto it."""
    store = FakeMemoryClient()
    memory = MemoryService(store)
    tavily = FakeTavily()
    igdb = FakeIgdb()
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
        igdb=igdb,
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

    assert client.post("/api/library/remove", json={"dedup_key": "hades|switch"}).status_code == 204
    assert client.get("/api/library").json()["records"] == []
    assert client.post("/api/library/remove", json={"dedup_key": "hades|switch"}).status_code == 404


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
    blank = client.put("/api/library/platform", json={"dedup_key": "hades|", "platform": " "})
    assert blank.status_code == 422
    assert client.post("/api/platforms", json={"name": "  "}).status_code == 422


def test_set_platform_persists_and_rekeys() -> None:
    client, _, _ = make_app()
    client.post("/api/library", json={"title": "Celeste"})
    updated = client.put(
        "/api/library/platform", json={"dedup_key": "celeste|", "platform": "Switch"}
    )
    assert updated.status_code == 200
    assert updated.json()["record"]["platforms"] == ["Switch"]
    records = client.get("/api/library").json()["records"]
    assert records[0]["dedup_key"] == "celeste|switch"


def test_enrich_fills_fields_and_persists() -> None:
    client, _, _ = make_app()
    client.post("/api/library", json={"title": "Hades", "platform": "Switch"})
    enriched = client.post("/api/library/enrich", json={"dedup_key": "hades|switch"})
    assert enriched.status_code == 200
    assert enriched.json()["record"]["is_enriched"] is True
    assert client.get("/api/library").json()["records"][0]["genre"] == "Roguelike"


def test_enrich_unknown_game_is_404() -> None:
    client, _, _ = make_app()
    assert client.post("/api/library/nope|/enrich").status_code == 404


def test_catalog_search_min_length_gate_and_shape() -> None:
    client, _, _ = make_app()
    # Below the threshold IGDB is never hit.
    assert client.get("/api/catalog/search", params={"q": "ha"}).json() == {"results": []}
    # At the threshold, structured suggestions come back (title + platforms + art).
    results = client.get("/api/catalog/search", params={"q": "had"}).json()["results"]
    assert results == [
        {
            "name": "Had",
            "platforms": ["Nintendo Switch", "PC"],
            "cover_url": "https://img.example/a.jpg",
        },
        {"name": "Had II", "platforms": ["PC"], "cover_url": None},
    ]


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
        igdb=ctx.igdb,
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


# --- artwork backfill ------------------------------------------------------


def test_enrich_all_persists_across_requests() -> None:
    """The point of the endpoint is that the covers survive; assert the write."""
    client, ctx, _ = make_app()
    ctx.memory.upsert_record(USER, GameRecord(title="Celeste", platforms=["PC"], source="manual"))

    client.post("/api/library/enrich-all")

    reread = client.get("/api/library").json()["records"]
    assert reread[0]["cover_url"] == "https://img.example/Celeste.jpg"


def test_enrich_replaces_an_existing_cover() -> None:
    """The button exists to fix bad art; filling only gaps could never do that."""
    client, ctx, _ = make_app()
    ctx.memory.upsert_record(
        USER,
        GameRecord(
            title="Batman",
            platforms=["PC"],
            source="manual",
            cover_url="https://img.example/wrong-and-ugly.jpg",
        ),
    )
    key = ctx.memory.get_records(USER)[0].dedup_key

    body = client.post("/api/library/enrich", json={"dedup_key": key}).json()
    assert body["record"]["cover_url"] == "https://img.example/Batman.jpg"


def test_enrich_keeps_the_old_cover_when_the_search_finds_nothing() -> None:
    """A refresh that comes back empty must not strip art the user could see."""
    client, ctx, _ = make_app()
    assert isinstance(ctx.enricher, FakeEnricher)
    ctx.enricher.no_art = {"Batman"}
    ctx.memory.upsert_record(
        USER,
        GameRecord(
            title="Batman",
            platforms=["PC"],
            source="manual",
            cover_url="https://img.example/keep.jpg",
        ),
    )
    key = ctx.memory.get_records(USER)[0].dedup_key

    body = client.post("/api/library/enrich", json={"dedup_key": key}).json()
    assert body["record"]["cover_url"] == "https://img.example/keep.jpg"


def test_a_platform_containing_a_slash_is_reachable() -> None:
    """The production bug: "Xbox Series X/S" makes the dedup key contain "/".

    Percent-encoded into a URL path, CloudFront decodes %2F back into a real
    separator, the path segment splits, the route stops matching, and the API
    404s on a game that exists. Keys travel in the body precisely so that a
    platform's punctuation cannot decide whether a record is reachable.
    """
    client, ctx, _ = make_app()
    ctx.memory.upsert_record(
        USER,
        GameRecord(title="Sunset Overdrive", platforms=["Xbox Series X/S"], source="manual"),
    )
    key = ctx.memory.get_records(USER)[0].dedup_key
    assert "/" in key

    assert client.post("/api/library/enrich", json={"dedup_key": key}).status_code == 200
    assert (
        client.put("/api/library/platform", json={"dedup_key": key, "platform": "PC"}).status_code
        == 200
    )

    moved = ctx.memory.get_records(USER)[0].dedup_key
    assert client.post("/api/library/remove", json={"dedup_key": moved}).status_code == 204
    assert ctx.memory.get_records(USER) == []


# --- bulk enrichment -------------------------------------------------------


def test_enrich_all_fills_records_that_need_it() -> None:
    """The manual-entry case: a batch of bare titles gains metadata and art."""
    client, ctx, _ = make_app()
    for title in ("Batman", "Spyro"):
        ctx.memory.upsert_record(USER, GameRecord(title=title, platforms=["PC"], source="manual"))

    body = client.post("/api/library/enrich-all").json()

    assert body["enriched"] == 2
    assert body["remaining"] == 0
    for record in body["records"]:
        assert record["genre"] == "Roguelike"
        assert record["cover_url"] == f"https://img.example/{record['title']}.jpg"


def test_enrich_all_leaves_complete_records_alone() -> None:
    client, ctx, _ = make_app()
    ctx.memory.upsert_record(
        USER,
        GameRecord(
            title="Done",
            platforms=["PC"],
            source="manual",
            genre="RPG",
            platform_availability=["PC"],
            cover_url="https://img.example/keep.jpg",
        ),
    )

    body = client.post("/api/library/enrich-all").json()

    assert body["enriched"] == 0
    assert body["records"][0]["cover_url"] == "https://img.example/keep.jpg"
    assert body["records"][0]["genre"] == "RPG"


def test_enrich_all_works_in_bounded_batches() -> None:
    """CloudFront allows the origin 60s; a whole library would be cut off."""
    client, ctx, _ = make_app()
    for index in range(8):
        ctx.memory.upsert_record(
            USER, GameRecord(title=f"Game {index}", platforms=["PC"], source="manual")
        )

    first = client.post("/api/library/enrich-all").json()
    assert first["enriched"] == 5  # _ENRICH_BATCH
    assert first["remaining"] == 3

    second = client.post("/api/library/enrich-all").json()
    assert second["enriched"] == 3
    assert second["remaining"] == 0


def test_enrich_all_refresh_walks_the_whole_library() -> None:
    """A refresh has no shrinking backlog, so it is paged by cursor.

    The first version hardcoded remaining=0 for refresh and always took the
    first five records, so "redo all" silently redid five games and stopped.
    """
    client, ctx, _ = make_app()
    for index in range(12):
        ctx.memory.upsert_record(
            USER,
            GameRecord(
                title=f"Game {index}",
                platforms=["PC"],
                source="manual",
                genre="RPG",
                platform_availability=["PC"],
                cover_url="https://img.example/old.jpg",
            ),
        )

    seen = 0
    offset = 0
    for _ in range(10):
        body = client.post(f"/api/library/enrich-all?refresh=true&offset={offset}").json()
        seen += body["enriched"]
        offset += body["enriched"]
        if body["remaining"] == 0:
            break
    assert seen == 12, "every record must be reached, not just the first batch"

    covers = {r["title"]: r["cover_url"] for r in body["records"]}
    assert all(url.startswith("https://img.example/Game") for url in covers.values())


def test_enrich_all_refresh_redoes_everything() -> None:
    """Moving to a better art source must not mean opening each game."""
    client, ctx, _ = make_app()
    ctx.memory.upsert_record(
        USER,
        GameRecord(
            title="Hades",
            platforms=["PC"],
            source="manual",
            genre="RPG",
            platform_availability=["PC"],
            cover_url="https://img.example/old.jpg",
        ),
    )

    assert client.post("/api/library/enrich-all").json()["enriched"] == 0

    body = client.post("/api/library/enrich-all?refresh=true").json()
    assert body["enriched"] == 1
    assert body["records"][0]["cover_url"] == "https://img.example/Hades.jpg"


def test_enrich_all_requires_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "eu-north-1_test")
    monkeypatch.setenv("AWS_REGION", "eu-north-1")
    client, _, _ = make_app()

    assert client.post("/api/library/enrich-all").status_code == 401
