"""Unit tests for the agent tool registry (agent.tools).

Each tool is exercised over the real service graph with the network edge faked:
an in-memory ``MemoryClient``, a fake Tavily search client, the real
``LibraryService`` and ``ManualSource``. Verifies dispatch wiring, input
validation, family-aware library filtering, and graceful error results.
"""

from __future__ import annotations

from typing import Any

from agent.enricher import Enricher
from agent.library_service import LibraryService
from agent.tools import ToolRegistry
from models.game_record import GameRecord
from services.memory_service import MemoryService
from services.sources.manual_source import ManualSource
from services.tavily_service import TavilyService

USER_ID = "tools-user"

# What the fake model returns when the enricher asks it to classify a title.
_ENRICH_JSON = (
    '{"genre": "Run-and-gun shooter", "estimated_playtime_minutes": 120, '
    '"platform_availability": ["Nintendo Switch", "PlayStation 4"], '
    '"community_review": {"score": 8.5, "summary": "Frenetic and acclaimed."}}'
)


class _FakeBedrock:
    """Returns a preset classification reply for the enricher (no network)."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    def invoke_conversational(self, prompt: str, session_id: str) -> str:
        return self._reply


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
        self._events.setdefault((user_id, key), []).insert(0, event)

    def list_events(self, user_id: str, key: str, limit: int) -> list[dict[str, Any]]:
        return list(self._events.get((user_id, key), []))[:limit]

    def clear_events(self, user_id: str, key: str) -> None:
        self._events.pop((user_id, key), None)


class _FakeTavilyClient:
    """Returns canned search data so enrichment/web_search are deterministic."""

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "answer": "A great role-playing game. 90/100.",
            "results": [
                {"title": "Review", "content": "Available on Nintendo Switch. RPG.", "url": "u"}
            ],
        }


def _registry() -> tuple[ToolRegistry, MemoryService]:
    memory = MemoryService(_InMemoryClient())
    tavily = TavilyService(api_key="x", client=_FakeTavilyClient())
    enricher = Enricher(_FakeBedrock(_ENRICH_JSON), tavily)  # type: ignore[arg-type]
    library = LibraryService([ManualSource(memory, USER_ID)], enricher, memory)
    return ToolRegistry(memory, library, tavily, enricher, USER_ID), memory


def test_platform_tools_round_trip() -> None:
    reg, _ = _registry()
    assert reg.dispatch("get_owned_platforms", {}) == {"platforms": []}

    added = reg.dispatch("add_platform", {"name": "Nintendo Switch"})
    assert added["ok"] is True

    listed = reg.dispatch("get_owned_platforms", {})
    assert [p["name"] for p in listed["platforms"]] == ["Nintendo Switch"]
    platform_id = listed["platforms"][0]["id"]

    assert reg.dispatch("remove_platform", {"platform_id": platform_id}) == {"ok": True}
    assert reg.dispatch("get_owned_platforms", {}) == {"platforms": []}


def test_add_platform_requires_name() -> None:
    reg, _ = _registry()
    result = reg.dispatch("add_platform", {"name": "   "})
    assert result["ok"] is False and "required" in result["error"]


def test_add_manual_game_and_library_filters() -> None:
    reg, _ = _registry()
    reg.dispatch(
        "add_manual_game",
        {"title": "Hades", "platform": "Switch", "genre": "Roguelike", "estimated_playtime": 40},
    )
    reg.dispatch("add_manual_game", {"title": "Celeste", "platform": "PC"})

    all_games = reg.dispatch("get_library", {})
    assert {g["title"] for g in all_games["games"]} == {"Hades", "Celeste"}

    # Family-aware platform filter: owned "Xbox" would miss, "Switch" hits Hades.
    on_switch = reg.dispatch("get_library", {"platform": "Nintendo Switch"})
    assert [g["title"] for g in on_switch["games"]] == ["Hades"]

    by_genre = reg.dispatch("get_library", {"genre": "rogue"})
    assert [g["title"] for g in by_genre["games"]] == ["Hades"]

    with_playtime = reg.dispatch("get_library", {"has_playtime": True})
    assert [g["title"] for g in with_playtime["games"]] == ["Hades"]


def test_set_game_fields_fills_playtime() -> None:
    reg, _ = _registry()
    reg.dispatch("add_manual_game", {"title": "Tunic", "platform": "PC"})

    missing = reg.dispatch("set_game_fields", {"title": "Nope", "estimated_playtime": 10})
    assert missing["ok"] is False

    ok = reg.dispatch("set_game_fields", {"title": "tunic", "estimated_playtime": 600})
    assert ok["ok"] is True
    assert ok["game"]["estimated_playtime"] == 600


def test_enrich_and_web_search() -> None:
    reg, _ = _registry()
    reg.dispatch("add_manual_game", {"title": "Hades", "platform": "Switch"})

    enriched = reg.dispatch("enrich_game", {"title": "Hades"})
    assert enriched["ok"] is True
    assert enriched["game"]["genre"] == "Run-and-gun shooter"  # from LLM classification
    assert "Nintendo Switch" in enriched["game"]["platform_availability"]

    assert reg.dispatch("enrich_game", {"title": "ghost"})["ok"] is False

    searched = reg.dispatch("web_search", {"query": "Hades review"})
    assert searched["results"]  # snippets returned
    assert reg.dispatch("web_search", {"query": "  "})["ok"] is False


def test_web_search_deep_and_site() -> None:
    reg, _ = _registry()
    # The deep + site options (used to read a store's deals page) flow through to a
    # successful search; the fake client ignores the kwargs and returns its snippet.
    out = reg.dispatch(
        "web_search", {"query": "Xbox deals Denmark", "deep": True, "site": "microsoft.com"}
    )
    assert out["results"]


def test_import_gmail_reports_delta() -> None:
    reg, memory = _registry()
    memory.upsert_record(USER_ID, GameRecord(title="Hades", platforms=["Switch"], source="manual"))
    result = reg.dispatch("import_gmail", {})
    assert result["library_size"] == 1


def test_recommendation_persistence_and_recency() -> None:
    reg, _ = _registry()
    saved = reg.dispatch(
        "save_recommendation",
        {
            "game_title": "Hades",
            "reasoning": "Fast roguelike that fits short sessions.",
            "mood": "relaxed",
            "time_budget_minutes": 60,
            "alternatives": ["Celeste"],
        },
    )
    assert saved == {"ok": True}

    recent = reg.dispatch("get_recent_recommendations", {"n": 5})
    assert recent == {
        "recommendations": [{"title": "Hades", "feedback": None}],
        "older_feedback": [],
    }


def test_unknown_tool_is_reported_not_raised() -> None:
    reg, _ = _registry()
    result = reg.dispatch("teleport", {})
    assert result["ok"] is False and "unknown tool" in result["error"]


def test_crashing_handler_is_reported_not_raised() -> None:
    """An unexpected bug inside a handler surfaces to the model as an error result,
    never as an exception that would kill the whole agent turn."""

    def _boom(_: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("unexpected bug in a handler")

    reg, _ = _registry()
    reg._handlers["get_library"] = _boom  # noqa: SLF001 - inject a broken handler

    result = reg.dispatch("get_library", {})

    assert result["ok"] is False
    assert "get_library" in result["error"]


def test_specs_cover_all_handlers() -> None:
    reg, _ = _registry()
    spec_names = {s["toolSpec"]["name"] for s in reg.specs()}
    handler_names = set(reg._handlers)  # noqa: SLF001 - registry internals under test
    assert spec_names == handler_names


def test_recent_recommendations_carry_feedback() -> None:
    """The agent reads the user's 👍/👎 verdicts alongside recent picks, plus
    feedback left on older (out-of-window) recommendations."""
    reg, memory = _registry()
    reg.dispatch("save_recommendation", {"game_title": "Hades", "reasoning": "fits"})
    memory.set_feedback(USER_ID, "Hades", "loved")
    memory.set_feedback(USER_ID, "Older Pick", "not_for_me")

    out = reg.dispatch("get_recent_recommendations", {"n": 5})

    assert out["recommendations"] == [{"title": "Hades", "feedback": "loved"}]
    assert out["older_feedback"] == [{"title": "Older Pick", "feedback": "not_for_me"}]
