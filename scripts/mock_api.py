#!/usr/bin/env python3
"""A dependency-free stand-in for the GameGusto API, for frontend work.

Serves the same routes and the same SSE event sequence as ``api/app.py`` from
in-memory fixtures, so the web client can be developed, demoed, and
screenshotted with **no AWS credentials and no Bedrock spend**. Chat replies are
canned and stream token-by-token, which is what makes the typing feel
verifiable offline.

    python scripts/mock_api.py            # http://127.0.0.1:8000
    cd web && npm run dev                 # proxies /api to it

This is a development tool only — it is never imported by the real service.
"""

from __future__ import annotations

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

PORT = 8000

PLATFORMS: list[dict[str, str]] = [
    {"platform_id": "p1", "name": "Nintendo Switch"},
    {"platform_id": "p2", "name": "PC"},
]

FIXTURES: list[tuple[str, str, str, float, float, int]] = [
    ("Hades", "Nintendo Switch", "Roguelike", 30, 9.2, 14),
    ("Hollow Knight", "Nintendo Switch", "Metroidvania", 40, 9.0, 11),
    ("Chained Echoes", "PC", "RPG", 35, 8.6, 7),
    ("Celeste", "Nintendo Switch", "Platformer", 12, 9.1, 12),
    ("Stardew Valley", "PC", "Cozy simulation", 90, 8.9, 16),
    ("Zelda: Tears of the Kingdom", "Nintendo Switch", "Adventure", 95, 9.6, 19),
    ("Dead Cells", "Nintendo Switch", "Roguelike", 25, 8.8, 9),
    ("Disco Elysium", "PC", "RPG", 28, 9.4, 15),
    # A slash in the platform: the shape that 404'd in production.
    ("Sunset Overdrive", "Xbox Series X/S", "Action", 15, 7.8, 8),
]


def _record(
    title: str, platform: str, genre: str, hours: float, score: float, sources: int
) -> dict[str, Any]:
    return {
        "title": title,
        "platforms": [platform],
        "source": "gmail",
        "purchase_date": "2026-03-12",
        "genre": genre,
        "estimated_playtime_hours": hours,
        "community_review": {
            "score": score,
            "sentiment_summary": "Widely praised for its feel and pacing.",
            "source_count": sources,
        },
        "platform_availability": [platform, "PC"],
        "external_ids": {},
        "cover_url": None,  # exercises the neon placeholder tile
        "dedup_key": f"{title.strip().casefold()}|{platform.strip().casefold()}",
        "is_enriched": True,
    }


STATE: dict[str, Any] = {
    "records": [_record(*fixture) for fixture in FIXTURES],
    "platforms": list(PLATFORMS),
    "picks": [
        {
            "game_title": "Death's Door",
            "reasoning": "Tight combat in short sessions.",
            "estimated_playtime": 9,
            "verdict": "loved",
            "owned": False,
        },
        {
            "game_title": "Sea of Stars",
            "reasoning": "Turn-based, gorgeous.",
            "estimated_playtime": 30,
            "verdict": None,
            "owned": False,
        },
    ],
    "conversation": [],
}

CANNED_REPLY = (
    "**Death's Door** — action-adventure, ~9 h, on Switch, and not in your library.\n\n"
    "Tight, readable combat with real bite — it scratches the same itch as the "
    "Hollow Knight in your library, but in 30-minute sessions that respect a "
    "school night. The boss design is the part people rave about.\n\n"
    "It's **79 kr.** on the Danish eShop right now (−60%, ends 28 Jul) — worth "
    "confirming on the store before you buy.\n\n"
    "Also worth a look: **Tunic**, **Cult of the Lamb**, **Blasphemous**."
)

TOOL_SEQUENCE = ["get_owned_platforms", "get_library", "web_search", "save_recommendation"]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"mock-api: {fmt % args}")

    # --- plumbing ---

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _empty(self, status: int = 204) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            parsed = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _sse_start(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # Close when the turn ends: with HTTP/1.1 keep-alive and no
        # Content-Length the client's reader would never see the body finish,
        # so the UI would sit on a blinking cursor forever.
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()

    def _sse(self, event: str, payload: dict[str, Any]) -> None:
        self.wfile.write(f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode())
        self.wfile.flush()

    # --- routes ---

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        url = urlparse(self.path)
        path = url.path
        if path == "/api/health":
            self._json({"status": "ok", "memory_available": True})
        elif path == "/api/library":
            self._json({"records": STATE["records"], "memory_available": True})
        elif path == "/api/platforms":
            self._json({"platforms": STATE["platforms"]})
        elif path == "/api/picks":
            self._json({"picks": STATE["picks"]})
        elif path == "/api/conversation":
            self._json({"messages": STATE["conversation"]})
        elif path == "/api/autocomplete":
            query = (parse_qs(url.query).get("q") or [""])[0].strip()
            if len(query) < 3:
                self._json({"suggestions": []})
            else:
                self._json(
                    {
                        "suggestions": [
                            f"{query.title()}",
                            f"{query.title()} II",
                            f"{query.title()} DX",
                        ]
                    }
                )
        else:
            self._empty(404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._body()
        if path == "/api/chat":
            self._chat(str(body.get("message", "")))
        elif path == "/api/library/remove":
            key = str(body.get("dedup_key", ""))
            before = len(STATE["records"])
            STATE["records"] = [r for r in STATE["records"] if r["dedup_key"] != key]
            self._empty(204 if len(STATE["records"]) < before else 404)
        elif path.startswith("/api/library/enrich-all"):
            # One title stays without art on purpose, so the client's
            # "couldn't find N" path is exercised rather than assumed.
            refresh = "refresh=true" in urlparse(self.path).query
            enriched = 0
            for record in STATE["records"]:
                needs = refresh or not record["is_enriched"] or record["cover_url"] is None
                if not needs:
                    continue
                record["is_enriched"] = True
                record["genre"] = record["genre"] or "Action"
                # One title never gets art, so the client's "some are still
                # without" path is exercised rather than assumed.
                if record["title"] != "Disco Elysium":
                    slug = record["title"].lower().replace(" ", "-").replace(":", "")
                    record["cover_url"] = f"https://placehold.co/460x215/181b2e/2de2e6?text={slug}"
                enriched += 1
            remaining = (
                0
                if refresh
                else sum(
                    1 for r in STATE["records"] if not r["is_enriched"] or r["cover_url"] is None
                )
            )
            self._json({"enriched": enriched, "remaining": remaining, "records": STATE["records"]})
        elif path == "/api/library":
            title = str(body.get("title", "")).strip()
            platform = (body.get("platform") or "").strip()
            record = {
                **_record(title, platform, "", 0, 0, 0),
                "genre": None,
                "estimated_playtime_hours": None,
                "community_review": None,
                "platform_availability": [],
                "platforms": [platform] if platform else [],
                "source": "manual",
                "is_enriched": False,
                "dedup_key": f"{title.casefold()}|{platform.casefold()}",
            }
            STATE["records"].append(record)
            self._json({"record": record}, 201)
        elif path == "/api/platforms":
            platform = {
                "platform_id": f"p{len(STATE['platforms']) + 1}",
                "name": str(body.get("name", "")).strip(),
            }
            STATE["platforms"].append(platform)
            self._json({"platform": platform}, 201)
        elif path == "/api/picks/feedback":
            title = str(body.get("title", ""))
            for pick in STATE["picks"]:
                if pick["game_title"] == title:
                    pick["verdict"] = body.get("verdict")
            self._json({"title": title, "verdict": body.get("verdict")})
        elif path == "/api/library/enrich":
            key = str(body.get("dedup_key", ""))
            for record in STATE["records"]:
                if record["dedup_key"] == key:
                    record.update(
                        genre=record["genre"] or "Action",
                        estimated_playtime_hours=record["estimated_playtime_hours"] or 12,
                        is_enriched=True,
                    )
                    self._json({"record": record})
                    break
            else:
                self._json({"detail": "game not found"}, 404)

    def do_PUT(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._body()
        if match := re.fullmatch(r"/api/library/(.+)/platform", path):
            key = unquote(match.group(1))
            platform = str(body.get("platform", "")).strip()
            for record in STATE["records"]:
                if record["dedup_key"] == key:
                    record["platforms"] = [platform]
                    record["dedup_key"] = f"{record['title'].casefold()}|{platform.casefold()}"
                    self._json({"record": record})
                    return
        self._empty(404)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/conversation":
            STATE["conversation"] = []
            self._empty()
        elif path == "/api/picks":
            STATE["picks"] = []
            self._empty()
        else:
            self._empty(404)

    def _chat(self, message: str) -> None:
        """Replay a realistic turn: working notes, tool chips, then a typed answer."""
        self._sse_start()
        self._sse("thinking", {"text": "Checking what you own and what's on sale…"})
        for tool in TOOL_SEQUENCE:
            self._sse("tool", {"tool": tool})
            time.sleep(0.45)
        for token in re.findall(r"\S+\s*", CANNED_REPLY):
            self._sse("delta", {"text": token})
            time.sleep(0.012)
        self._sse("text", {"text": CANNED_REPLY})
        STATE["conversation"].extend(
            (
                {"role": "user", "content": message},
                {
                    "role": "assistant",
                    "content": CANNED_REPLY,
                    "notes": ["Checking what you own and what's on sale…"],
                },
            )
        )
        self._sse(
            "done",
            {
                "usage": {
                    "inputTokens": 3100,
                    "outputTokens": 412,
                    "cacheReadInputTokens": 28000,
                },
                "memory_available": True,
            },
        )


if __name__ == "__main__":
    print(f"mock GameGusto API on http://127.0.0.1:{PORT} (no AWS, no spend)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
