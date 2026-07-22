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
import logging
import os
import threading
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from api.auth import TOKEN_HEADER, AuthError, build_verifier
from api.schemas import (
    AddGameRequest,
    ChatRequest,
    PlatformRequest,
    RecordRequest,
    SetPlatformRequest,
    SetTasteRequest,
    pick_to_dict,
    platform_to_dict,
    record_to_dict,
    suggestion_to_dict,
)
from bootstrap import AppContext
from models.game_record import GameRecord
from models.platform import OwnedPlatform
from services.bedrock_service import BedrockServiceError

logger = logging.getLogger(__name__)

#: Comma-separated allowed CORS origins; defaults cover the local Vite dev server.
_CORS_ENV = "GAMEGUSTO_CORS_ORIGINS"
_DEFAULT_CORS = "http://localhost:5173,http://127.0.0.1:5173"

#: Minimum query length before the catalog search hits IGDB (matches the UI).
_CATALOG_MIN_CHARS = 3

#: Records enriched per bulk request. Each costs a web search plus a model
#: call, and CloudFront allows the origin 60 seconds — a whole library in one
#: request would be cut off mid-flight, having spent the tokens anyway.
_ENRICH_BATCH = 5


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

    verifier = build_verifier()
    if verifier is None:
        # Loud, because the deployed function must never reach this branch.
        # Absent configuration disables auth so local development and the mock
        # API stay credential-free; see api/auth.py.
        logger.warning("No COGNITO_USER_POOL_ID set — API is UNAUTHENTICATED.")

    def current_user(request: Request) -> str:
        """Authenticate the request, then return the storage identity.

        Note the deliberate split: the token proves *who is asking*, but what
        comes back is ``ctx.user_id``, not the Cognito ``sub``. The library
        predates authentication and lives under its own key — returning the
        subject here would not migrate that data, it would hide it behind an
        empty account.
        """
        if verifier is None:
            return ctx.user_id

        token = request.headers.get(TOKEN_HEADER, "")
        if not token:
            raise HTTPException(status_code=401, detail="Not signed in.")

        try:
            verifier.subject(token)
        except AuthError as exc:
            # 401 rather than 403: the client should re-authenticate, which is
            # what the web app keys its silent-refresh on.
            raise HTTPException(status_code=401, detail=str(exc)) from exc

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

    # The dedup key travels in the body on every one of these, never the path.
    # It is "title|platform", and platforms such as "Xbox Series X/S" contain a
    # slash: percent-encoded into a path segment, CloudFront decodes %2F back
    # into a real "/", the segment splits, the route stops matching, and the
    # API answers 404 for a record that exists. Half the library was
    # unreachable this way.
    @app.put("/api/library/platform")
    def set_game_platform(
        body: SetPlatformRequest, user: str = Depends(current_user)
    ) -> dict[str, Any]:
        records = ctx.memory.get_records(user)
        record = _find_record(records, body.dedup_key)
        record.platforms = [body.platform]
        # Persist the WHOLE list: the edit changes the dedup key, and a full
        # store is what keeps the old-keyed duplicate from lingering.
        ctx.memory.store_records(user, records)
        return {"record": record_to_dict(record)}

    @app.put("/api/library/taste")
    def set_game_taste(body: SetTasteRequest, user: str = Depends(current_user)) -> dict[str, Any]:
        """Record the user's own rating of a game they own.

        Verdict, course and note are each set independently to exactly what the
        body carries (``null`` clears one) — the client always sends the full
        intended state, so a tap that changes only the verdict leaves the rest.
        This is first-hand taste, and it rides along whenever the agent reads the
        library, which is how it reaches the next recommendation.
        """
        records = ctx.memory.get_records(user)
        record = _find_record(records, body.dedup_key)
        record.taste = body.taste
        record.course = body.course
        record.taste_note = body.note
        ctx.memory.store_records(user, records)
        return {"record": record_to_dict(record)}

    @app.post("/api/library/enrich")
    def enrich_game(body: RecordRequest, user: str = Depends(current_user)) -> dict[str, Any]:
        records = ctx.memory.get_records(user)
        record = _find_record(records, body.dedup_key)
        # Asked for explicitly, so refresh the cover too: this is the only way
        # to replace art the image search got wrong.
        enriched = ctx.enricher.enrich(record, refresh_cover=True)
        ctx.memory.store_records(user, [enriched if r is record else r for r in records])
        return {"record": record_to_dict(enriched)}

    @app.post("/api/library/enrich-all")
    def enrich_all(
        user: str = Depends(current_user),
        refresh: Annotated[bool, Query()] = False,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Enrich every record that still needs it, a batch at a time.

        Adding a dozen games by hand leaves a dozen records with no genre, no
        length, no score and no art; filling them in one at a time through the
        detail sheet is the tedious path this replaces.

        ``refresh=true`` re-does the whole library instead, which is how it
        moves to a better art source without opening each game in turn.

        **Bounded on purpose.** Each record costs a search plus a model call,
        and CloudFront gives the origin 60 seconds. So this processes at most
        ``_ENRICH_BATCH`` records and reports how many remain; the client calls
        again until nothing is left, which keeps one click bounded work and
        gives honest progress instead of a request that dies at the edge.

        One read and one write per batch, rather than the per-record route's
        read-modify-write of the entire list N times over.

        Failures are per-record: a title the model cannot classify must not
        stop the rest of the batch.
        """
        records = ctx.memory.get_records(user)

        if refresh:
            # A refresh has no shrinking backlog to work through — every record
            # already qualifies — so batches are walked by an explicit cursor.
            # Without one the server would hand back the same first five on
            # every call, and "redo all" would quietly redo only those.
            batch = records[offset : offset + _ENRICH_BATCH]
            remaining = max(0, len(records) - (offset + len(batch)))
        else:
            needs_work = [
                record for record in records if not record.is_enriched() or record.cover_url is None
            ]
            batch = needs_work[:_ENRICH_BATCH]

        for record in batch:
            try:
                ctx.enricher.enrich(record, refresh_cover=refresh)
            except BedrockServiceError:
                continue

        if batch:
            ctx.memory.store_records(user, records)

        if not refresh:
            # Recomputed against the freshly enriched records, so "remaining"
            # counts what genuinely still needs work — not what this batch
            # failed to fix, which would loop forever on a title nothing can
            # classify.
            remaining = sum(
                1 for record in records if not record.is_enriched() or record.cover_url is None
            )

        return {
            "enriched": len(batch),
            "remaining": remaining,
            "records": [record_to_dict(record) for record in records],
        }

    # POST rather than DELETE: a DELETE carrying a body is legal but poorly
    # supported by intermediaries, and the key cannot go in the path.
    @app.post("/api/library/remove", status_code=204)
    def remove_game(body: RecordRequest, user: str = Depends(current_user)) -> Response:
        records = ctx.memory.get_records(user)
        remaining = [r for r in records if r.dedup_key != body.dedup_key]
        if len(remaining) == len(records):
            raise HTTPException(status_code=404, detail="game not found")
        ctx.memory.store_records(user, remaining)
        return Response(status_code=204)

    @app.get("/api/catalog/search")
    def catalog_search(q: str = "") -> dict[str, Any]:
        """Live game-title suggestions from IGDB for the add-game box.

        Each result carries the platforms the game shipped on and its box art,
        so the picker gets the title right and then chooses the platform they
        own it on. IGDB is the games industry's own catalogue — strictly better
        for this than the general web search the field used to lean on.
        """
        query = q.strip()
        if len(query) < _CATALOG_MIN_CHARS:
            return {"results": []}
        return {"results": [suggestion_to_dict(s) for s in ctx.igdb.search_games(query)]}

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

    # --- recent picks ---

    @app.get("/api/picks")
    def get_picks(
        user: str = Depends(current_user),
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> dict[str, Any]:
        owned = {r.title.strip().casefold() for r in ctx.memory.get_records(user)}
        picks: list[dict[str, Any]] = []
        seen: set[str] = set()
        # The same game may be recommended across sessions; show it once (newest first).
        for rec in ctx.memory.get_recent_recommendations(user, limit):
            key = rec.game_title.strip().casefold()
            if key in seen:
                continue
            seen.add(key)
            picks.append(pick_to_dict(rec, key in owned))
        return {"picks": picks}

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
