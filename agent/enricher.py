"""LLM-assisted enrichment of game records.

Replaces brittle keyword matching: for a record missing metadata, the enricher
runs a Tavily web search and asks the Bedrock model to read the snippets (plus
its own knowledge of the title) and return structured fields — genre, main-story
completion time, platform availability, and a community-review score/summary.
This reliably classifies titles that keyword matching mislabels (e.g. Metal Slug
as a run-and-gun shooter rather than "Puzzle").

Enrichment is **cache-first** (an already-enriched record is returned untouched)
and **degrades gracefully**: if Tavily returns nothing, or the model call fails,
or the reply is not parseable JSON, the record is returned unchanged rather than
blocking library assembly. (Conversational reasoning remains a hard LLM
dependency in the runtime; auxiliary enrichment does not.)
"""

from __future__ import annotations

import json
from typing import Any

from models.game_record import CommunityReview, GameRecord
from services.bedrock_service import BedrockService, BedrockServiceError
from services.tavily_service import TavilyService

#: Cap on snippets fed to the model, keeping the prompt bounded.
_MAX_SNIPPETS = 6


class Enricher:
    """Populates missing ``GameRecord`` metadata via Tavily search + the LLM."""

    def __init__(self, bedrock: BedrockService, tavily: TavilyService) -> None:
        """Build the enricher around the Bedrock model and the Tavily search."""
        self._bedrock = bedrock
        self._tavily = tavily

    def enrich(self, record: GameRecord) -> GameRecord:
        """Fill any unset enrichment fields of ``record`` (cache-first, degrading).

        Returns the record unchanged when it is already enriched, when Tavily
        yields no snippets, or when the model call/JSON parse fails (Req 5.5, 10.3).
        """
        if record.is_enriched():
            return record
        snippets = self._tavily.web_search(
            f"{record.title} video game genre length to beat platforms review score"
        )
        if not snippets:
            return record
        try:
            data = self._classify(record.title, snippets)
        except (BedrockServiceError, ValueError):
            return record
        return self._apply(record, data, source_count=len(snippets))

    def _classify(self, title: str, snippets: list[dict[str, str]]) -> dict[str, Any]:
        """Ask the model to classify the title into structured fields.

        Raises ``BedrockServiceError`` on a transport failure or ``ValueError``
        when the reply contains no parseable JSON object.
        """
        reply = self._bedrock.invoke_conversational(self._prompt(title, snippets), "enrich")
        return _parse_json_object(reply)

    @staticmethod
    def _prompt(title: str, snippets: list[dict[str, str]]) -> str:
        """Build the enrichment classification prompt from the title and snippets."""
        lines = "\n".join(
            f"- {s.get('content', '').strip()}"
            for s in snippets[:_MAX_SNIPPETS]
            if s.get("content")
        )
        return (
            "You enrich a video game record. Using the web snippets below plus your "
            "own knowledge of the title, reply with ONLY a JSON object (no prose), "
            "with exactly this shape:\n"
            '{"genre": "<concise primary genre, e.g. \'Run-and-gun shooter\', '
            "'Action RPG', 'Cozy simulation'>\", "
            '"estimated_playtime_minutes": <integer approximate main-story '
            "completion time in minutes, or null>, "
            '"platform_availability": ["<platforms it is available on>"], '
            '"community_review": {"score": <number 0-10>, "summary": "<one sentence>", '
            '"source_count": <integer>} or null}\n'
            "For community_review, give an AGGREGATE score AVERAGED across multiple "
            "outlets/critics and player reviews (a consensus rating, like a Metacritic "
            "average) normalized to 0-10 — NOT a single review — and set source_count to "
            "the number of distinct review sources you considered. The summary should "
            "describe the overall consensus.\n"
            "Do not invent platforms or ratings you cannot support; use null / [] when "
            "unknown. Prefer accuracy from your own knowledge over the snippets when they "
            "conflict.\n\n"
            f"Game title: {title}\n\nSnippets:\n{lines or '(none)'}"
        )

    @staticmethod
    def _apply(record: GameRecord, data: dict[str, Any], source_count: int) -> GameRecord:
        """Fill unset fields of ``record`` from the parsed classification."""
        genre = data.get("genre")
        if record.genre is None and isinstance(genre, str) and genre.strip():
            record.genre = genre.strip()

        playtime = data.get("estimated_playtime_minutes")
        if record.estimated_playtime is None and isinstance(playtime, (int, float)):
            minutes = int(playtime)
            if minutes > 0:
                record.estimated_playtime = minutes

        platforms = data.get("platform_availability")
        if not record.platform_availability and isinstance(platforms, list):
            record.platform_availability = [str(p).strip() for p in platforms if str(p).strip()]

        if record.community_review is None:
            record.community_review = _parse_review(data.get("community_review"), source_count)
        return record


def _parse_review(value: Any, source_count: int) -> CommunityReview | None:
    """Build an aggregated ``CommunityReview`` from the model's object.

    The score is an outlet-averaged consensus rating (see the prompt). The model's
    own ``source_count`` (distinct outlets considered) is preferred; ``source_count``
    (the snippet count) is the fallback when the model omits it.
    """
    if not isinstance(value, dict):
        return None
    score = value.get("score")
    if not isinstance(score, (int, float)):
        return None
    model_sources = value.get("source_count")
    sources = int(model_sources) if isinstance(model_sources, (int, float)) else source_count
    summary = value.get("summary")
    return CommunityReview(
        score=max(0.0, min(10.0, float(score))),
        sentiment_summary=str(summary).strip() if summary else "",
        source_count=max(sources, 0),
    )


def _parse_json_object(reply: str) -> dict[str, Any]:
    """Extract and parse the first JSON object from ``reply``.

    Raises ``ValueError`` when no valid JSON object is present, so a malformed
    reply degrades to "leave the record unenriched" rather than surfacing text.
    """
    start = reply.find("{")
    end = reply.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in reply")
    parsed = json.loads(reply[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("reply JSON is not an object")
    return parsed
