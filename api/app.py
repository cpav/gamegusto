"""FastAPI application factory and routes.

Everything is a thin adapter: routes call the same ``MemoryService`` /
``AgentRuntime`` / ``Enricher`` operations the Streamlit views call, and the
chat endpoint streams ``AgentRuntime.stream`` events as Server-Sent Events.
Endpoints are sync ``def``s on purpose — the underlying graph is synchronous
(boto3), and Starlette runs sync routes on its threadpool.

Transcript compatibility: the conversation is persisted in exactly the shape
the Streamlit UI reads (``{"role", "content"}`` + optional ``"notes"``), so
both frontends resume the same conversation during the migration window.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from api.schemas import (
    AddGameRequest,
    ChatRequest,
    FeedbackRequest,
    PlatformRequest,
    SetPlatformRequest,
    pick_to_dict,
    platform_to_dict,
    record_to_dict,
)
from bootstrap import AppContext
from models.game_record import GameRecord
from models.platform import OwnedPlatform
from services.bedrock_service import BedrockServiceError

#: Comma-separated allowed CORS origins; defaults cover the local Vite dev server.
_CORS_ENV = "GAMEGUSTO_CORS_ORIGINS"
_DEFAULT_CORS = "http://localhost:5173,http://127.0.0.1:5173"

#: Minimum query length before autocomplete hits the search service (matches the UI).
_AUTOCOMPLETE_MIN_CHARS = 3


def _sse(event: str, payload: dict[str, Any]) -> str:
    """Frame one Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


class TurnGuard:
    """One in-flight chat turn per user.

    The runtime's conversation history is mutable per-user state, so a second
    concurrent turn must be rejected. Release is token-matched and is invoked
    from BOTH the stream generator's ``finally`` and a response
    BackgroundTask: the double release covers the disconnect edge where a
    never-started generator's ``finally`` never runs, and the token check
    keeps the late BackgroundTask of turn N from freeing the slot that turn
    N+1 has since claimed.
    """

    def __init__(self) -> None:
        self._in_flight: dict[str, object] = {}
        self._lock = threading.Lock()

    def begin(self, user_id: str) -> object | None:
        """Claim the user's turn slot; ``None`` when a turn is already running."""
        with self._lock:
            if user_id in self._in_flight:
                return None
            token = object()
            self._in_flight[user_id] = token
            return token

    def end(self, user_id: str, token: object) -> None:
        """Free the slot — only if ``token`` is the claim currently holding it."""
        with self._lock:
            if self._in_flight.get(user_id) is token:
                del self._in_flight[user_id]


def create_app(ctx: AppContext) -> FastAPI:
    """Build the API around an already-wired application graph."""
    app = FastAPI(title="GameGusto API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get(_CORS_ENV, _DEFAULT_CORS).split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def current_user() -> str:
        """Resolve the requesting user.

        Single-user for now. Phase 3 replaces this dependency with JWT
        validation that returns the Cognito ``sub`` — nothing else changes.
        """
        return ctx.user_id

    turns = TurnGuard()

    # --- health ---

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "memory_available": ctx.memory.is_available}

    # --- library ---

    @app.get("/api/library")
    def get_library(user: str = Depends(current_user)) -> dict[str, Any]:
        records = sorted(ctx.memory.get_records(user), key=lambda r: r.title.casefold())
        return {
            "records": [record_to_dict(r) for r in records],
            "memory_available": ctx.memory.is_available,
        }

    @app.post("/api/library", status_code=201)
    def add_game(body: AddGameRequest, user: str = Depends(current_user)) -> dict[str, Any]:
        platforms = [body.platform] if body.platform else []
        record = GameRecord(title=body.title, platforms=platforms, source="manual")
        ctx.memory.upsert_record(user, record)
        return {"record": record_to_dict(record)}

    def _find_record(records: list[GameRecord], dedup_key: str) -> GameRecord:
        for record in records:
            if record.dedup_key == dedup_key:
                return record
        raise HTTPException(status_code=404, detail="game not found")

    @app.put("/api/library/{dedup_key}/platform")
    def set_game_platform(
        dedup_key: str, body: SetPlatformRequest, user: str = Depends(current_user)
    ) -> dict[str, Any]:
        records = ctx.memory.get_records(user)
        record = _find_record(records, dedup_key)
        record.platforms = [body.platform]
        # Persist the WHOLE list: the edit changes the dedup key, and a full
        # store is what keeps the old-keyed duplicate from lingering.
        ctx.memory.store_records(user, records)
        return {"record": record_to_dict(record)}

    @app.post("/api/library/{dedup_key}/enrich")
    def enrich_game(dedup_key: str, user: str = Depends(current_user)) -> dict[str, Any]:
        records = ctx.memory.get_records(user)
        record = _find_record(records, dedup_key)
        enriched = ctx.enricher.enrich(record)
        ctx.memory.store_records(user, [enriched if r is record else r for r in records])
        return {"record": record_to_dict(enriched)}

    @app.delete("/api/library/{dedup_key}", status_code=204)
    def remove_game(dedup_key: str, user: str = Depends(current_user)) -> Response:
        records = ctx.memory.get_records(user)
        remaining = [r for r in records if r.dedup_key != dedup_key]
        if len(remaining) == len(records):
            raise HTTPException(status_code=404, detail="game not found")
        ctx.memory.store_records(user, remaining)
        return Response(status_code=204)

    @app.get("/api/autocomplete")
    def autocomplete(q: str = "") -> dict[str, Any]:
        query = q.strip()
        if len(query) < _AUTOCOMPLETE_MIN_CHARS:
            return {"suggestions": []}
        return {"suggestions": ctx.tavily.autocomplete(query)}

    # --- platforms ---

    @app.get("/api/platforms")
    def get_platforms(user: str = Depends(current_user)) -> dict[str, Any]:
        return {"platforms": [platform_to_dict(p) for p in ctx.memory.get_platform_list(user)]}

    @app.post("/api/platforms", status_code=201)
    def add_platform(body: PlatformRequest, user: str = Depends(current_user)) -> dict[str, Any]:
        platform = OwnedPlatform(name=body.name)
        ctx.memory.add_platform(user, platform)
        return {"platform": platform_to_dict(platform)}

    @app.put("/api/platforms/{platform_id}")
    def rename_platform(
        platform_id: str, body: PlatformRequest, user: str = Depends(current_user)
    ) -> dict[str, Any]:
        if not ctx.memory.update_platform(user, platform_id, body.name):
            raise HTTPException(status_code=404, detail="platform not found")
        return {"platform": {"platform_id": platform_id, "name": body.name}}

    @app.delete("/api/platforms/{platform_id}", status_code=204)
    def remove_platform(platform_id: str, user: str = Depends(current_user)) -> Response:
        if not ctx.memory.remove_platform(user, platform_id):
            raise HTTPException(status_code=404, detail="platform not found")
        return Response(status_code=204)

    # --- recent picks & feedback ---

    @app.get("/api/picks")
    def get_picks(
        user: str = Depends(current_user),
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> dict[str, Any]:
        feedback = ctx.memory.get_feedback(user)
        owned = {r.title.strip().casefold() for r in ctx.memory.get_records(user)}
        picks: list[dict[str, Any]] = []
        seen: set[str] = set()
        # The same game may be recommended across sessions; show it once (newest first).
        for rec in ctx.memory.get_recent_recommendations(user, limit):
            key = rec.game_title.strip().casefold()
            if key in seen:
                continue
            seen.add(key)
            verdict = (feedback.get(key) or {}).get("verdict")
            picks.append(pick_to_dict(rec, verdict, key in owned))
        return {"picks": picks}

    @app.post("/api/picks/feedback")
    def set_feedback(body: FeedbackRequest, user: str = Depends(current_user)) -> dict[str, Any]:
        ctx.memory.set_feedback(user, body.title, body.verdict)
        return {"title": body.title, "verdict": body.verdict}

    @app.delete("/api/picks", status_code=204)
    def clear_picks(user: str = Depends(current_user)) -> Response:
        ctx.memory.clear_recent_recommendations(user)
        return Response(status_code=204)

    # --- conversation ---

    @app.get("/api/conversation")
    def get_conversation(user: str = Depends(current_user)) -> dict[str, Any]:
        return {"messages": ctx.memory.get_conversation(user)}

    @app.delete("/api/conversation", status_code=204)
    def reset_conversation(user: str = Depends(current_user)) -> Response:
        ctx.runtime.reset()
        ctx.memory.store_conversation(user, [])
        return Response(status_code=204)

    # --- chat (SSE) ---

    def _chat_events(user: str, message: str, token: object) -> Iterator[str]:
        """Stream one turn as SSE, then persist the transcript.

        Persistence mirrors the Streamlit flow: the user+assistant pair is
        stored only after a successful turn — on failure the runtime rolls its
        history back, so the unsaved transcript keeps the two in step.
        """
        try:
            history = ctx.memory.get_conversation(user)
            ctx.runtime.seed_transcript(history)  # no-op mid-conversation
            answer: list[str] = []
            notes: list[str] = []
            try:
                for event in ctx.runtime.stream(message, deltas=True):
                    if event.kind == "delta":
                        # Token-level fragment of the round in progress; the
                        # round's closing thinking/text event is authoritative.
                        yield _sse("delta", {"text": event.text})
                    elif event.kind == "tool":
                        yield _sse("tool", {"tool": event.tool})
                    elif event.kind == "thinking":
                        notes.append(event.text)
                        yield _sse("thinking", {"text": event.text})
                    else:
                        answer.append(event.text)
                        yield _sse("text", {"text": event.text})
            except BedrockServiceError as exc:
                yield _sse("error", {"message": str(exc)})
                return
            reply = "\n\n".join(answer) or "\n\n".join(notes)
            entry: dict[str, Any] = {"role": "assistant", "content": reply}
            if notes:
                entry["notes"] = notes
            history.append({"role": "user", "content": message})
            history.append(entry)
            ctx.memory.store_conversation(user, history)
            yield _sse(
                "done",
                {
                    "usage": ctx.runtime.last_turn_usage,
                    "memory_available": ctx.memory.is_available,
                },
            )
        finally:
            turns.end(user, token)

    @app.post("/api/chat")
    def chat(body: ChatRequest, user: str = Depends(current_user)) -> StreamingResponse:
        token = turns.begin(user)
        if token is None:
            raise HTTPException(status_code=409, detail="a turn is already in progress")
        return StreamingResponse(
            _chat_events(user, body.message, token),
            media_type="text/event-stream",
            # Token-matched backstop for the disconnect edge (see TurnGuard);
            # normal turns already ended in the generator's ``finally``.
            background=BackgroundTask(turns.end, user, token),
        )

    return app
