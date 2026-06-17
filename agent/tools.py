"""Tool registry exposed to the Bedrock agent.

Each tool is a thin function plus a Converse ``toolSpec`` (JSON schema) that wraps
existing services — :class:`MemoryService`, :class:`LibraryService`, and
:class:`TavilyService`. The model decides which tools to call and when; the
registry only declares the surface and dispatches calls. Selection of the actual
game is the model's job, not a tool — it reads the library, applies the user's
stated taste/mood/time/owned platforms, and may enrich or web-search to fill gaps.

Tools never raise to the caller: expected failures (e.g. a title not in the
library) are returned as ``{"ok": False, "error": ...}`` so the model can react,
and the underlying services already degrade gracefully rather than raising.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.enricher import Enricher
from agent.library_service import LibraryService
from agent.platform_match import owned_intersects, platforms_match
from models.game_record import GameRecord
from models.platform import OwnedPlatform
from models.recommendation import Recommendation
from models.session import SessionData
from services.memory_service import MemoryService
from services.tavily_service import TavilyService

#: One registered tool: its Converse spec plus the handler that executes it.
ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _spec(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict:
    """Build a Converse ``toolSpec`` from a name, description, and JSON schema."""
    return {
        "toolSpec": {
            "name": name,
            "description": description,
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }
            },
        }
    }


class ToolRegistry:
    """Declares the agent's tools and dispatches calls to the wrapped services."""

    def __init__(
        self,
        memory: MemoryService,
        library: LibraryService,
        tavily: TavilyService,
        enricher: Enricher,
        user_id: str,
    ) -> None:
        """Build the registry around the shared service graph for ``user_id``."""
        self._memory = memory
        self._library = library
        self._tavily = tavily
        self._enricher = enricher
        self._user_id = user_id
        self._handlers: dict[str, ToolHandler] = {
            "get_owned_platforms": self._get_owned_platforms,
            "add_platform": self._add_platform,
            "remove_platform": self._remove_platform,
            "get_library": self._get_library,
            "add_manual_game": self._add_manual_game,
            "set_game_fields": self._set_game_fields,
            "import_gmail": self._import_gmail,
            "enrich_game": self._enrich_game,
            "web_search": self._web_search,
            "get_recent_recommendations": self._get_recent_recommendations,
            "save_recommendation": self._save_recommendation,
        }

    def specs(self) -> list[dict[str, Any]]:
        """Return the Converse ``toolSpec`` list advertised to the model."""
        return _TOOL_SPECS

    def dispatch(self, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool ``name`` with ``tool_input``; never raises a KeyError.

        An unknown tool name returns an error result so a hallucinated tool name
        is surfaced to the model rather than crashing the loop.
        """
        handler = self._handlers.get(name)
        if handler is None:
            return {"ok": False, "error": f"unknown tool: {name}"}
        return handler(tool_input)

    # --- platform tools (Req 6) ---

    def _get_owned_platforms(self, _: dict[str, Any]) -> dict[str, Any]:
        platforms = self._memory.get_platform_list(self._user_id)
        return {"platforms": [{"id": p.platform_id, "name": p.name} for p in platforms]}

    def _add_platform(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        name = str(tool_input.get("name", "")).strip()
        if not name:
            return {"ok": False, "error": "name is required"}
        ok = self._memory.add_platform(self._user_id, OwnedPlatform(name=name))
        return {"ok": ok, "name": name}

    def _remove_platform(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        platform_id = str(tool_input.get("platform_id", "")).strip()
        if not platform_id:
            return {"ok": False, "error": "platform_id is required"}
        ok = self._memory.remove_platform(self._user_id, platform_id)
        return {"ok": ok}

    # --- library tools (Req 3, 5, 9.5) ---

    def _get_library(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        records = self._memory.get_records(self._user_id)
        platform = tool_input.get("platform")
        genre = tool_input.get("genre")
        has_playtime = tool_input.get("has_playtime")
        if isinstance(platform, str) and platform.strip():
            records = [r for r in records if _record_on_platform(r, platform)]
        if isinstance(genre, str) and genre.strip():
            needle = genre.strip().casefold()
            records = [r for r in records if r.genre and needle in r.genre.casefold()]
        if has_playtime is True:
            records = [r for r in records if r.estimated_playtime is not None]
        return {"games": [_record_to_dict(r) for r in records]}

    def _add_manual_game(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        title = str(tool_input.get("title", "")).strip()
        platform = str(tool_input.get("platform", "")).strip()
        if not title or not platform:
            return {"ok": False, "error": "title and platform are required"}
        record = GameRecord(
            title=title,
            platforms=[platform],
            source="manual",
            genre=_opt_str(tool_input.get("genre")),
            estimated_playtime=_opt_int(tool_input.get("estimated_playtime")),
        )
        ok = self._memory.upsert_record(self._user_id, record)
        return {"ok": ok, "title": title}

    def _set_game_fields(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        title = str(tool_input.get("title", "")).strip()
        if not title:
            return {"ok": False, "error": "title is required"}
        # Load the library once and persist it once (no re-read via upsert_record).
        records = self._memory.get_records(self._user_id)
        record = _find(records, title)
        if record is None:
            return {"ok": False, "error": f"no game titled {title!r} in the library"}
        if "estimated_playtime" in tool_input:
            record.estimated_playtime = _opt_int(tool_input.get("estimated_playtime"))
        if "genre" in tool_input:
            record.genre = _opt_str(tool_input.get("genre"))
        ok = self._memory.store_records(self._user_id, records)
        return {"ok": ok, "game": _record_to_dict(record)}

    def _import_gmail(self, _: dict[str, Any]) -> dict[str, Any]:
        before = len(self._memory.get_records(self._user_id))
        records = self._library.refresh(self._user_id)
        after = len(records)
        return {"imported": max(0, after - before), "library_size": after}

    def _enrich_game(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        title = str(tool_input.get("title", "")).strip()
        if not title:
            return {"ok": False, "error": "title is required"}
        records = self._memory.get_records(self._user_id)
        record = _find(records, title)
        if record is None:
            return {"ok": False, "error": f"no game titled {title!r} in the library"}
        self._enricher.enrich(record)  # mutates the record in place, cache-first
        ok = self._memory.store_records(self._user_id, records)
        return {"ok": ok, "game": _record_to_dict(record)}

    def _web_search(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        query = str(tool_input.get("query", "")).strip()
        if not query:
            return {"ok": False, "error": "query is required"}
        return {"results": self._tavily.web_search(query)}

    # --- personalization tools (Req 8) ---

    def _get_recent_recommendations(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        n = _opt_int(tool_input.get("n")) or 5
        recs = self._memory.get_recent_recommendations(self._user_id, n)
        return {"titles": [r.game_title for r in recs]}

    def _save_recommendation(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        title = str(tool_input.get("game_title", "")).strip()
        if not title:
            return {"ok": False, "error": "game_title is required"}
        recommendation = Recommendation(
            game_title=title,
            reasoning=str(tool_input.get("reasoning", "")),
            estimated_playtime=_opt_int(tool_input.get("time_budget_minutes")),
        )
        alternatives = [
            Recommendation(game_title=str(t), reasoning="")
            for t in tool_input.get("alternatives", [])
            if str(t).strip()
        ]
        session = SessionData(
            user_id=self._user_id,
            mood=str(tool_input.get("mood", "")),
            time_budget_minutes=_opt_int(tool_input.get("time_budget_minutes")) or 0,
            recommendation=recommendation,
            alternatives=alternatives,
        )
        ok = self._memory.store_session(self._user_id, session)
        return {"ok": ok}


def _find(records: list[GameRecord], title: str) -> GameRecord | None:
    """Return the record whose title matches ``title`` (case-insensitive), else None."""
    needle = title.strip().casefold()
    for record in records:
        if record.title.strip().casefold() == needle:
            return record
    return None


def _record_on_platform(record: GameRecord, platform: str) -> bool:
    """True when ``record`` is owned on or available on ``platform`` (family-aware)."""
    if owned_intersects([platform], record.platform_availability):
        return True
    return any(platforms_match(platform, owned) for owned in record.platforms)


def _record_to_dict(record: GameRecord) -> dict[str, Any]:
    """Serialize a record for tool output (the shape the model reasons over)."""
    review = record.community_review
    return {
        "title": record.title,
        "platforms": list(record.platforms),
        "source": record.source,
        "genre": record.genre,
        "estimated_playtime": record.estimated_playtime,
        "platform_availability": list(record.platform_availability),
        "community_review": review.as_dict() if review is not None else None,
    }


def _opt_str(value: Any) -> str | None:
    """Coerce ``value`` to a non-empty stripped string, or ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_int(value: Any) -> int | None:
    """Coerce ``value`` to an int, or ``None`` when absent/invalid."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_TOOL_SPECS: list[dict[str, Any]] = [
    _spec("get_owned_platforms", "List the gaming platforms the user owns.", {}, []),
    _spec(
        "add_platform",
        "Add a platform to the user's owned-platform list.",
        {"name": {"type": "string", "description": "Platform name, e.g. 'Nintendo Switch'."}},
        ["name"],
    ),
    _spec(
        "remove_platform",
        "Remove an owned platform by its id (from get_owned_platforms).",
        {"platform_id": {"type": "string"}},
        ["platform_id"],
    ),
    _spec(
        "get_library",
        "List the user's owned games, optionally filtered. Use this to find candidates.",
        {
            "platform": {"type": "string", "description": "Filter to a platform (family-aware)."},
            "genre": {"type": "string", "description": "Case-insensitive genre substring filter."},
            "has_playtime": {
                "type": "boolean",
                "description": "When true, only games with a known estimated playtime.",
            },
        },
        [],
    ),
    _spec(
        "add_manual_game",
        "Add a game the user owns by hand (source='manual').",
        {
            "title": {"type": "string"},
            "platform": {"type": "string"},
            "estimated_playtime": {"type": "integer", "description": "Minutes, optional."},
            "genre": {"type": "string"},
        },
        ["title", "platform"],
    ),
    _spec(
        "set_game_fields",
        "Update fields on an existing owned game (e.g. fill in estimated_playtime or genre).",
        {
            "title": {"type": "string"},
            "estimated_playtime": {"type": "integer", "description": "Minutes."},
            "genre": {"type": "string"},
        },
        ["title"],
    ),
    _spec(
        "import_gmail",
        "Import owned games from the user's purchase-confirmation emails, then enrich.",
        {},
        [],
    ),
    _spec(
        "enrich_game",
        "Look up genre, playtime, platform availability, and reviews for an owned game.",
        {"title": {"type": "string"}},
        ["title"],
    ),
    _spec(
        "web_search",
        "Search the web for game info (genre, playtime, availability, reviews).",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _spec(
        "get_recent_recommendations",
        "List titles recommended in recent sessions, to avoid repeats.",
        {"n": {"type": "integer", "description": "How many recent sessions to look back."}},
        [],
    ),
    _spec(
        "save_recommendation",
        "Persist the chosen recommendation once you present it to the user.",
        {
            "game_title": {"type": "string"},
            "reasoning": {"type": "string"},
            "mood": {"type": "string", "description": "Short summary of the user's mood/context."},
            "time_budget_minutes": {"type": "integer"},
            "alternatives": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Alternative game titles offered.",
            },
        },
        ["game_title", "reasoning"],
    ),
]
