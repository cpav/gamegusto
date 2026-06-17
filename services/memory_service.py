"""Persistent store backed by a DynamoDB memory client.

The single store for the canonical ``GameRecord`` library, the user's
``Platform_List``, and completed sessions (Req 6, 8, 10.2). It is the only
component that talks to the memory client, which is injected via the
constructor so the service stays testable.

Two invariants shape this module:

* **Contract-fields-only persistence.** Records are serialized to exactly the
  nine ``GameRecord`` contract fields and nothing else, so no source-specific or
  raw data (e.g. Gmail email bodies) is ever written (Req 4.2).
* **Graceful degradation.** Any backing-store failure flips the service to
  unavailable, routes the error through :class:`ErrorHandler`, and returns an
  empty / ``False`` result instead of raising, so callers can degrade to a
  stateless session (Req 10.2). No raw exception ever reaches a caller.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from models.game_record import CommunityReview, GameRecord
from models.platform import OwnedPlatform
from models.recommendation import Recommendation
from models.session import SessionData
from services.error_handler import ErrorHandler


class MemoryClient(Protocol):
    """Minimal document/event store contract the service depends on.

    Keyed documents (records, platform list) use ``get_value``/``put_value``;
    the append-only session log uses ``append_event``/``list_events``. The
    concrete DynamoDB client is injected at construction.
    """

    def get_value(self, user_id: str, key: str) -> dict[str, Any] | None:
        """Return the document stored under ``key`` for ``user_id``, or ``None``."""
        ...

    def put_value(self, user_id: str, key: str, value: dict[str, Any]) -> None:
        """Persist ``value`` under ``key`` for ``user_id``, replacing any prior value."""
        ...

    def append_event(self, user_id: str, key: str, event: dict[str, Any]) -> None:
        """Append ``event`` to the log stored under ``key`` for ``user_id``."""
        ...

    def list_events(self, user_id: str, key: str, limit: int) -> list[dict[str, Any]]:
        """Return up to ``limit`` most-recent events for ``key``, newest first."""
        ...


class MemoryService:
    """Stores Game_Records, the Platform_List, and sessions in DynamoDB."""

    RECORDS_KEY = "records"
    PLATFORMS_KEY = "platforms"
    SESSIONS_KEY = "sessions"

    def __init__(self, client: MemoryClient) -> None:
        """Build the service around an injected memory ``client`` (DynamoDB-backed)."""
        self._client = client
        self._available = True
        self._last_error: str | None = None

    # --- Game_Records (single store for ALL sources + UI) ---

    def get_records(self, user_id: str) -> list[GameRecord]:
        """Return the user's stored library, or ``[]`` if memory is unreachable."""
        try:
            document = self._client.get_value(user_id, self.RECORDS_KEY)
            self._mark_available()
            if not document:
                return []
            raw_records = document.get("records", [])
            return [self._record_from_dict(item) for item in raw_records]
        except Exception as exc:  # backing-store failure (Req 10.2)
            self._mark_unavailable(exc)
            return []

    def store_records(self, user_id: str, records: list[GameRecord]) -> bool:
        """Persist ``records``, defensively de-duplicating and keeping only contract fields.

        Duplicates (by :attr:`GameRecord.dedup_key`) are never stored — the first
        occurrence wins — and each record is serialized to its contract fields
        only (Req 3.5, 4.2).
        """
        try:
            deduped = self._dedup(records)
            payload = {"records": [self._record_to_dict(r) for r in deduped]}
            self._client.put_value(user_id, self.RECORDS_KEY, payload)
            self._mark_available()
            return True
        except Exception as exc:
            self._mark_unavailable(exc)
            return False

    def upsert_record(self, user_id: str, record: GameRecord) -> bool:
        """Add or replace a single record by dedup key (used by the library view, Req 9.5)."""
        existing = self.get_records(user_id)
        merged = [r for r in existing if r.dedup_key != record.dedup_key]
        merged.append(record)
        return self.store_records(user_id, merged)

    # --- Platform_List (Req 6.1-6.4) ---

    def get_platform_list(self, user_id: str) -> list[OwnedPlatform]:
        """Return the user's owned platforms, or ``[]`` if memory is unreachable (Req 6.3)."""
        try:
            document = self._client.get_value(user_id, self.PLATFORMS_KEY)
            self._mark_available()
            if not document:
                return []
            return [
                OwnedPlatform(name=item["name"], platform_id=item["platform_id"])
                for item in document.get("platform_list", [])
            ]
        except Exception as exc:
            self._mark_unavailable(exc)
            return []

    def add_platform(self, user_id: str, platform: OwnedPlatform) -> bool:
        """Append ``platform`` to the Platform_List (free-text names allowed, Req 6.1, 6.4)."""
        platforms = self.get_platform_list(user_id)
        platforms.append(platform)
        return self._store_platforms(user_id, platforms)

    def update_platform(self, user_id: str, platform_id: str, new_name: str) -> bool:
        """Rename the platform ``platform_id``; ``False`` if it is absent (Req 6.2)."""
        platforms = self.get_platform_list(user_id)
        updated = False
        for platform in platforms:
            if platform.platform_id == platform_id:
                platform.name = new_name
                updated = True
                break
        if not updated:
            return False
        return self._store_platforms(user_id, platforms)

    def remove_platform(self, user_id: str, platform_id: str) -> bool:
        """Remove the platform ``platform_id``; ``False`` if it is absent (Req 6.1)."""
        platforms = self.get_platform_list(user_id)
        remaining = [p for p in platforms if p.platform_id != platform_id]
        if len(remaining) == len(platforms):
            return False
        return self._store_platforms(user_id, remaining)

    # --- sessions / personalization (Req 8) ---

    def store_session(self, user_id: str, session: SessionData) -> bool:
        """Persist a completed session for personalization and no-repeat logic (Req 8.1)."""
        try:
            self._client.append_event(user_id, self.SESSIONS_KEY, self._session_to_dict(session))
            self._mark_available()
            return True
        except Exception as exc:
            self._mark_unavailable(exc)
            return False

    def get_recent_recommendations(self, user_id: str, sessions: int = 5) -> list[Recommendation]:
        """Return the primary recommendations from the most recent ``sessions`` (Req 8.3)."""
        try:
            events = self._client.list_events(user_id, self.SESSIONS_KEY, sessions)
            self._mark_available()
            recommendations: list[Recommendation] = []
            for event in events:
                data = event.get("recommendation")
                if data:
                    recommendations.append(self._recommendation_from_dict(data))
            return recommendations
        except Exception as exc:
            self._mark_unavailable(exc)
            return []

    @property
    def is_available(self) -> bool:
        """Whether the backing store is currently reachable (drives stateless mode, Req 10.2)."""
        return self._available

    @property
    def last_error(self) -> str | None:
        """The most recent sanitized error message, for surfacing in the UI (Req 10.1)."""
        return self._last_error

    # --- internal helpers ---

    def _store_platforms(self, user_id: str, platforms: list[OwnedPlatform]) -> bool:
        """Persist the full Platform_List, replacing any prior value (Req 6.2)."""
        try:
            payload = {
                "platform_list": [{"platform_id": p.platform_id, "name": p.name} for p in platforms]
            }
            self._client.put_value(user_id, self.PLATFORMS_KEY, payload)
            self._mark_available()
            return True
        except Exception as exc:
            self._mark_unavailable(exc)
            return False

    def _mark_available(self) -> None:
        """Record that the last backing-store interaction succeeded."""
        self._available = True
        self._last_error = None

    def _mark_unavailable(self, exc: Exception) -> None:
        """Flip to unavailable and capture a sanitized message (Req 10.1, 10.2)."""
        self._available = False
        self._last_error = ErrorHandler.sanitize_error(exc, "memory")

    @staticmethod
    def _dedup(records: list[GameRecord]) -> list[GameRecord]:
        """Drop duplicates by dedup key, keeping the first occurrence (Req 3.5)."""
        seen: set[str] = set()
        deduped: list[GameRecord] = []
        for record in records:
            if record.dedup_key in seen:
                continue
            seen.add(record.dedup_key)
            deduped.append(record)
        return deduped

    @staticmethod
    def _record_to_dict(record: GameRecord) -> dict[str, Any]:
        """Serialize a record to its nine contract fields only (Req 4.2)."""
        return {
            "title": record.title,
            "platforms": list(record.platforms),
            "source": record.source,
            "purchase_date": record.purchase_date.isoformat() if record.purchase_date else None,
            "genre": record.genre,
            "estimated_playtime": record.estimated_playtime,
            "community_review": record.community_review.as_dict()
            if record.community_review
            else None,
            "platform_availability": list(record.platform_availability),
            "external_ids": dict(record.external_ids),
        }

    @staticmethod
    def _record_from_dict(data: dict[str, Any]) -> GameRecord:
        """Rebuild a ``GameRecord`` from its persisted contract-field representation."""
        raw_date = data.get("purchase_date")
        return GameRecord(
            title=data["title"],
            platforms=list(data.get("platforms", [])),
            source=data.get("source", "manual"),
            purchase_date=date.fromisoformat(raw_date) if raw_date else None,
            genre=data.get("genre"),
            estimated_playtime=data.get("estimated_playtime"),
            community_review=CommunityReview.from_dict(data.get("community_review")),
            platform_availability=list(data.get("platform_availability", [])),
            external_ids=dict(data.get("external_ids", {})),
        )

    @staticmethod
    def _recommendation_to_dict(rec: Recommendation) -> dict[str, Any]:
        """Serialize a recommendation for session persistence (Req 8.1)."""
        return {
            "game_title": rec.game_title,
            "reasoning": rec.reasoning,
            "estimated_playtime": rec.estimated_playtime,
        }

    @staticmethod
    def _recommendation_from_dict(data: dict[str, Any]) -> Recommendation:
        """Rebuild a recommendation from its persisted representation (Req 8.2)."""
        return Recommendation(
            game_title=data["game_title"],
            reasoning=data.get("reasoning", ""),
            estimated_playtime=data.get("estimated_playtime"),
        )

    @staticmethod
    def _session_to_dict(session: SessionData) -> dict[str, Any]:
        """Serialize a completed session to the persisted schema (Req 8.1)."""
        return {
            "user_id": session.user_id,
            "mood": session.mood,
            "time_budget_minutes": session.time_budget_minutes,
            "recommendation": MemoryService._recommendation_to_dict(session.recommendation),
            "alternatives": [
                MemoryService._recommendation_to_dict(alt) for alt in session.alternatives
            ],
        }
