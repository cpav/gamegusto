"""Request bodies and JSON serializers for the API layer.

Serializers return plain dicts built from the domain models so the wire format
is explicit and decoupled from persistence: ``dedup_key`` and ``is_enriched``
are computed properties the client needs for row actions, not stored fields.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from models.game_record import GameRecord
from models.platform import OwnedPlatform
from models.recommendation import Recommendation


class AddGameRequest(BaseModel):
    """Add a manually-owned game to the library."""

    title: str = Field(min_length=1)
    platform: str | None = None


class SetPlatformRequest(BaseModel):
    """Set the platform on a library record (single platform, like the UI)."""

    platform: str = Field(min_length=1)


class PlatformRequest(BaseModel):
    """Add or rename an owned platform."""

    name: str = Field(min_length=1)


class FeedbackRequest(BaseModel):
    """Record a verdict on a recommended title; ``verdict: null`` clears it."""

    title: str = Field(min_length=1)
    verdict: Literal["loved", "not_for_me"] | None = None


class ChatRequest(BaseModel):
    """One user chat turn."""

    message: str = Field(min_length=1)


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
