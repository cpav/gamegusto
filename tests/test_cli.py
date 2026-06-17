"""Tests for the headless CLI command dispatch (cli.py).

The interactive ``main`` loop is not exercised, but every command handler is,
over an ``AppContext`` wired with an in-memory memory store and a fake runtime so
no network is touched. Output is captured to assert what the user sees.
"""

from __future__ import annotations

from typing import Any

import cli
from agent.library_service import LibraryService
from agent.runtime import AgentReply
from bootstrap import AppContext
from config import Config
from models.game_record import GameRecord
from services.bedrock_service import BedrockServiceError
from services.memory_service import MemoryService
from services.sources.manual_source import ManualSource
from services.tavily_service import TavilyService

USER_ID = "cli-user"

CONFIG = Config(
    aws_region="eu-north-1",
    bedrock_model_id="m",
    tavily_api_key="x",
    dynamodb_table_name="t",
)


class _InMemoryClient:
    def __init__(self) -> None:
        self._docs: dict[tuple[str, str], dict[str, Any]] = {}

    def get_value(self, user_id: str, key: str) -> dict[str, Any] | None:
        return self._docs.get((user_id, key))

    def put_value(self, user_id: str, key: str, value: dict[str, Any]) -> None:
        self._docs[(user_id, key)] = value

    def append_event(self, user_id: str, key: str, event: dict[str, Any]) -> None:
        pass

    def list_events(self, user_id: str, key: str, limit: int) -> list[dict[str, Any]]:
        return []


class _NoopTavilyClient:
    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return {}


class _IdentityEnricher:
    """Returns records untouched (enrichment is not exercised by the CLI tests)."""

    def enrich(self, record: GameRecord) -> GameRecord:
        return record


class _FakeRuntime:
    """Stands in for AgentRuntime: returns a preset reply or raises."""

    def __init__(self, reply: AgentReply | None = None, error: Exception | None = None) -> None:
        self._reply = reply
        self._error = error
        self.reset_called = False
        self.sent: list[str] = []

    def send(self, text: str) -> AgentReply:
        self.sent.append(text)
        if self._error is not None:
            raise self._error
        assert self._reply is not None
        return self._reply

    def reset(self) -> None:
        self.reset_called = True


def _ctx(runtime: Any) -> tuple[AppContext, MemoryService]:
    memory = MemoryService(_InMemoryClient())
    tavily = TavilyService(api_key="x", client=_NoopTavilyClient())
    library = LibraryService([ManualSource(memory, USER_ID)], _IdentityEnricher(), memory)  # type: ignore[arg-type]
    ctx = AppContext(
        config=CONFIG,
        user_id=USER_ID,
        memory=memory,
        tavily=tavily,
        library=library,
        runtime=runtime,
        gmail=None,
    )
    return ctx, memory


def test_quit_returns_false() -> None:
    ctx, _ = _ctx(_FakeRuntime())
    assert cli._dispatch(ctx, "/quit") is False
    assert cli._dispatch(ctx, "/exit") is False


def test_help_prints_doc(capsys: Any) -> None:
    ctx, _ = _ctx(_FakeRuntime())
    assert cli._dispatch(ctx, "/help") is True
    assert "Commands:" in capsys.readouterr().out


def test_reset_clears_runtime() -> None:
    runtime = _FakeRuntime()
    ctx, _ = _ctx(runtime)
    cli._dispatch(ctx, "/reset")
    assert runtime.reset_called is True


def test_platform_add_list_rm(capsys: Any) -> None:
    ctx, memory = _ctx(_FakeRuntime())
    cli._dispatch(ctx, "/platform add Nintendo Switch")
    platforms = memory.get_platform_list(USER_ID)
    assert [p.name for p in platforms] == ["Nintendo Switch"]

    cli._dispatch(ctx, "/platform list")
    assert "Nintendo Switch" in capsys.readouterr().out

    cli._dispatch(ctx, f"/platform rm {platforms[0].platform_id}")
    assert memory.get_platform_list(USER_ID) == []

    cli._dispatch(ctx, "/platform rm missing-id")
    assert "No platform" in capsys.readouterr().out

    cli._dispatch(ctx, "/platform bogus")
    assert "Usage:" in capsys.readouterr().out


def test_platform_list_empty(capsys: Any) -> None:
    ctx, _ = _ctx(_FakeRuntime())
    cli._dispatch(ctx, "/platform list")
    assert "No platforms yet" in capsys.readouterr().out


def test_add_game_and_library(capsys: Any) -> None:
    ctx, memory = _ctx(_FakeRuntime())
    cli._dispatch(ctx, "/add Hades :: Switch")
    assert [r.title for r in memory.get_records(USER_ID)] == ["Hades"]

    cli._dispatch(ctx, "/library")
    assert "Hades" in capsys.readouterr().out

    cli._dispatch(ctx, "/add bad-format")
    assert "Usage:" in capsys.readouterr().out


def test_library_empty(capsys: Any) -> None:
    ctx, _ = _ctx(_FakeRuntime())
    cli._dispatch(ctx, "/library")
    assert "empty" in capsys.readouterr().out


def test_refresh(capsys: Any) -> None:
    ctx, memory = _ctx(_FakeRuntime())
    memory.upsert_record(USER_ID, GameRecord(title="Hades", platforms=["Switch"], source="manual"))
    cli._dispatch(ctx, "/refresh")
    assert "Library now has" in capsys.readouterr().out


def test_unknown_command(capsys: Any) -> None:
    ctx, _ = _ctx(_FakeRuntime())
    cli._dispatch(ctx, "/teleport")
    assert "Unknown command" in capsys.readouterr().out


def test_conversation_prints_reply_and_tools(capsys: Any) -> None:
    reply = AgentReply(message="Try Hades!", is_stateless_mode=True, tool_calls=["get_library"])
    ctx, _ = _ctx(_FakeRuntime(reply=reply))
    cli._dispatch(ctx, "what should I play?")
    out = capsys.readouterr().out
    assert "Try Hades!" in out
    assert "memory unavailable" in out
    assert "get_library" in out


def test_conversation_handles_bedrock_error(capsys: Any) -> None:
    ctx, _ = _ctx(_FakeRuntime(error=BedrockServiceError("engine down")))
    cli._dispatch(ctx, "recommend")
    assert "engine down" in capsys.readouterr().out
