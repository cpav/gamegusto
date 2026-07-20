"""Request bodies and JSON serializers for the API layer.

Serializers return plain dicts built from the domain models so the wire format
is explicit and decoupled from persistence: ``dedup_key`` and ``is_enriched``
are computed properties the client needs for row actions, not stored fields.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, StringConstraints, field_validator

from models.game_record import GameRecord
from models.platform import OwnedPlatform
from models.recommendation import Recommendation

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


class SetPlatformRequest(BaseModel):
    """Set the platform on a library record (single platform, like the UI)."""

    platform: TrimmedStr


class PlatformRequest(BaseModel):
    """Add or rename an owned platform."""

    name: TrimmedStr


class FeedbackRequest(BaseModel):
    """Record a verdict on a recommended title; ``verdict: null`` clears it."""

    title: TrimmedStr
    verdict: Literal["loved", "not_for_me"] | None = None


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
        "dedup_key": record.dedup_key,
        "is_enriched": record.is_enriched(),
    }


def platform_to_dict(platform: OwnedPlatform) -> dict[str, str]:
    """Serialize an owned platform."""
    return {"platform_id": platform.platform_id, "name": platform.name}


def pick_to_dict(rec: Recommendation, verdict: str | None, owned: bool) -> dict[str, Any]:
    """Serialize a recent pick with its feedback verdict and library status."""
    return {
        "game_title": rec.game_title,
        "reasoning": rec.reasoning,
        "estimated_playtime": rec.estimated_playtime,
        "verdict": verdict,
        "owned": owned,
    }
