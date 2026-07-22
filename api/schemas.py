"""Request bodies and JSON serializers for the API layer.

Serializers return plain dicts built from the domain models so the wire format
is explicit and decoupled from persistence: ``dedup_key`` and ``is_enriched``
are computed properties the client needs for row actions, not stored fields.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, StringConstraints, field_validator

from models.game_record import Course, GameRecord, TasteVerdict
from models.platform import OwnedPlatform
from models.recommendation import Recommendation
from services.igdb_service import GameSuggestion

#: Required text field: stripped BEFORE the length check, so whitespace-only
#: input is a 422 instead of quietly becoming an empty title/name downstream.
TrimmedStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AddGameRequest(BaseModel):
    """Add a manually-owned game to the library."""

    title: TrimmedStr
    platform: str | None = None

    @field_validator("platform")
    @classmethod
    def _blank_platform_is_absent(cls, value: str | None) -> str | None:
        """Treat a blank platform as 'not provided' rather than a platform named ''."""
        if value is None:
            return None
        return value.strip() or None


class RecordRequest(BaseModel):
    """Identifies a library record by its dedup key.

    The key is carried in the BODY, never the URL path. A dedup key is
    ``title|platform``, and platforms like "Xbox Series X/S" contain a slash:
    percent-encoded into a path, CloudFront decodes %2F back to a literal "/",
    which splits the path segment so the route stops matching and the API
    answers 404. A body is not touched by any of that.
    """

    dedup_key: TrimmedStr


class SetPlatformRequest(RecordRequest):
    """Set the platform on a library record (single platform, like the UI)."""

    platform: TrimmedStr


class SetTasteRequest(RecordRequest):
    """Rate a library game: the user's own verdict, course, and a short note.

    Every field is independently clearable (``null``), so a tap that only sets
    the course leaves the verdict alone. This is first-hand taste — the strongest
    signal the agent has — attached to a game the user actually played.
    """

    taste: TasteVerdict | None = None
    course: Course | None = None
    note: str | None = None

    @field_validator("note")
    @classmethod
    def _blank_note_is_absent(cls, value: str | None) -> str | None:
        """A whitespace-only note is 'no note', not a note made of spaces."""
        if value is None:
            return None
        return value.strip() or None


class PlatformRequest(BaseModel):
    """Add or rename an owned platform."""

    name: TrimmedStr


class ChatRequest(BaseModel):
    """One user chat turn."""

    message: TrimmedStr


def record_to_dict(record: GameRecord) -> dict[str, Any]:
    """Serialize a library record for the wire (contract fields + computed keys)."""
    review = record.community_review
    return {
        "title": record.title,
        "platforms": list(record.platforms),
        "source": record.source,
        "purchase_date": record.purchase_date.isoformat() if record.purchase_date else None,
        "genre": record.genre,
        "estimated_playtime_hours": record.estimated_playtime_hours,
        "community_review": review.as_dict() if review else None,
        "platform_availability": list(record.platform_availability),
        "external_ids": dict(record.external_ids),
        "cover_url": record.cover_url,
        "taste": record.taste,
        "course": record.course,
        "taste_note": record.taste_note,
        "dedup_key": record.dedup_key,
        "is_enriched": record.is_enriched(),
    }


def platform_to_dict(platform: OwnedPlatform) -> dict[str, str]:
    """Serialize an owned platform."""
    return {"platform_id": platform.platform_id, "name": platform.name}


def suggestion_to_dict(suggestion: GameSuggestion) -> dict[str, Any]:
    """Serialize an IGDB add-game suggestion (title + platforms + thumbnail)."""
    return {
        "name": suggestion.name,
        "platforms": list(suggestion.platforms),
        "cover_url": suggestion.cover_url,
    }


def pick_to_dict(rec: Recommendation, owned: bool) -> dict[str, Any]:
    """Serialize a recent pick and whether it's now in the library.

    Recent picks are history — what the agent suggested lately — not something to
    rate. Taste is rated on owned games instead (see ``SetTasteRequest``)."""
    return {
        "game_title": rec.game_title,
        "reasoning": rec.reasoning,
        "estimated_playtime": rec.estimated_playtime,
        "owned": owned,
    }
