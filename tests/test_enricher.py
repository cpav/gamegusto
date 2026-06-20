"""Unit tests for the LLM-assisted :class:`~agent.enricher.Enricher` (no network).

A fake Tavily client supplies snippets and a fake Bedrock returns (or fails to
return) a JSON classification. Covers a successful enrichment, cache-first
short-circuiting, and graceful degradation when Tavily is empty, the model
fails, or the reply is not parseable JSON.
"""

from __future__ import annotations

from typing import Any

from agent.enricher import Enricher
from models.game_record import GameRecord
from services.bedrock_service import BedrockServiceError

_GOOD_JSON = (
    '{"genre": "Run-and-gun shooter", "estimated_playtime_minutes": 240, '
    '"platform_availability": ["Nintendo Switch", "PlayStation 4"], '
    '"community_review": {"score": 8.7, "summary": "A frenetic arcade classic."}}'
)


class _FakeTavily:
    """A TavilyService whose web_search returns preset snippets (or none)."""

    def __init__(self, snippets: list[dict[str, str]]) -> None:
        self._snippets = snippets

    def web_search(self, query: str) -> list[dict[str, str]]:
        return list(self._snippets)


class _FakeBedrock:
    """Returns a preset reply, or raises a preset error, for invoke_conversational."""

    def __init__(self, reply: str = "", error: Exception | None = None) -> None:
        self._reply = reply
        self._error = error
        self.calls = 0

    def invoke_conversational(self, prompt: str, session_id: str) -> str:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._reply


_SNIPPETS = [{"title": "Metal Slug", "content": "Run-and-gun arcade shooter.", "url": "u"}]


def _enricher(bedrock: Any, snippets: list[dict[str, str]] | None = None) -> Enricher:
    tavily = _FakeTavily(_SNIPPETS if snippets is None else snippets)
    return Enricher(bedrock, tavily)  # type: ignore[arg-type]


def test_successful_enrichment_populates_fields() -> None:
    record = GameRecord(title="Metal Slug", platforms=["Nintendo Switch"], source="gmail")
    enriched = _enricher(_FakeBedrock(_GOOD_JSON)).enrich(record)

    assert enriched.genre == "Run-and-gun shooter"
    assert enriched.estimated_playtime == 240
    assert enriched.platform_availability == ["Nintendo Switch", "PlayStation 4"]
    assert enriched.community_review is not None
    assert enriched.community_review.score == 8.7
    assert enriched.community_review.source_count == len(_SNIPPETS)


def test_review_uses_model_aggregate_source_count_when_present() -> None:
    """The aggregated review prefers the model's own source_count (outlets averaged)."""
    reply = (
        '{"genre": "Action RPG", "estimated_playtime_minutes": 3000, '
        '"platform_availability": ["PC"], '
        '"community_review": {"score": 9.1, "summary": "Acclaimed.", "source_count": 27}}'
    )
    record = GameRecord(title="Elden Ring", platforms=["PC"], source="manual")
    enriched = _enricher(_FakeBedrock(reply)).enrich(record)

    assert enriched.community_review is not None
    assert enriched.community_review.score == 9.1
    assert enriched.community_review.source_count == 27  # model's count, not the snippet count


def test_cache_first_skips_already_enriched() -> None:
    bedrock = _FakeBedrock(_GOOD_JSON)
    record = GameRecord(
        title="Hades",
        source="manual",
        genre="Roguelike",
        platform_availability=["PC"],
    )
    result = _enricher(bedrock).enrich(record)

    assert result is record
    assert bedrock.calls == 0  # no model call when already enriched


def test_no_snippets_returns_record_unchanged() -> None:
    bedrock = _FakeBedrock(_GOOD_JSON)
    record = GameRecord(title="Obscure Game", source="manual")
    result = _enricher(bedrock, snippets=[]).enrich(record)

    assert result.genre is None
    assert bedrock.calls == 0  # Tavily empty -> never calls the model


def test_bad_json_degrades_gracefully() -> None:
    record = GameRecord(title="Metal Slug", source="gmail")
    result = _enricher(_FakeBedrock("sorry, I can't help with that")).enrich(record)

    assert result.genre is None
    assert result.community_review is None


def test_model_failure_degrades_gracefully() -> None:
    record = GameRecord(title="Metal Slug", source="gmail")
    result = _enricher(_FakeBedrock(error=BedrockServiceError("model down"))).enrich(record)

    assert result.genre is None


def test_partial_classification_fills_only_known_fields() -> None:
    record = GameRecord(title="Mystery", source="manual")
    partial = (
        '{"genre": "Adventure", "estimated_playtime_minutes": null, ' '"community_review": null}'
    )
    result = _enricher(_FakeBedrock(partial)).enrich(record)

    assert result.genre == "Adventure"
    assert result.estimated_playtime is None
    assert result.platform_availability == []
    assert result.community_review is None
