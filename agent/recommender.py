"""Recommendation selection over the canonical Game_Record library.

The :class:`Recommender` operates entirely on :class:`~models.game_record.GameRecord`
values produced by the library assembly. It filters the library to games playable
on an owned platform with confirmed availability (Req 5.3, 7.1, 7.5), fitting the
caller's time budget (Req 7.1), and not recommended in the last five sessions
(Req 8.3); ranks the survivors by community review quality (Req 7.2); and turns
the winner into a display :class:`~models.recommendation.Recommendation` with
reasoning that summarizes the community review (Req 7.3). Up to three alternatives
are offered, each likewise playable on an owned platform (Req 7.4).

When nothing matches, ``recommend`` returns a sentinel recommendation with an empty
``game_title`` and an explanatory ``reasoning`` so the orchestrator can surface a
"no recommendation" message rather than handling an exception.
"""

from __future__ import annotations

from agent.mood_interpreter import MoodDimensions
from models.game_record import GameRecord
from models.platform import OwnedPlatform
from models.recommendation import Recommendation
from services.bedrock_service import BedrockService, BedrockServiceError
from services.memory_service import MemoryService

#: Number of recent sessions whose primaries are excluded from new picks (Req 8.3).
_RECENT_SESSIONS = 5

#: Maximum number of alternatives offered alongside the primary (Req 7.4).
_MAX_ALTERNATIVES = 3

#: Reasoning returned by the sentinel when no candidate matches (Req 7.1).
_NO_MATCH_REASONING = (
    "I couldn't find a game that fits your mood, available time, and the platforms "
    "you own right now. Try adding more platforms or games to your library."
)


class Recommender:
    """Selects a primary game recommendation and alternatives from the library."""

    def __init__(self, bedrock: BedrockService, memory: MemoryService) -> None:
        """Build the recommender around the Bedrock agent and the memory store."""
        self._bedrock = bedrock
        self._memory = memory

    def recommend(
        self,
        mood: MoodDimensions,
        time_budget_minutes: int,
        library: list[GameRecord],
        owned_platforms: list[OwnedPlatform],
        user_id: str,
    ) -> Recommendation:
        """Return one primary recommendation: playable, within budget, well-reviewed.

        Returns an empty sentinel recommendation (empty ``game_title`` with an
        explanatory ``reasoning``) when no candidate satisfies the constraints, so
        the orchestrator can communicate the no-match case without exceptions.
        """
        eligible = self._eligible_candidates(library, owned_platforms, time_budget_minutes, user_id)
        if not eligible:
            return self._no_recommendation()
        primary_record = self._select_primary(eligible)
        recommendation = self._to_recommendation(primary_record)
        recommendation.reasoning = self._build_reasoning(
            primary_record, mood, time_budget_minutes, owned_platforms, user_id
        )
        return recommendation

    def alternatives(
        self,
        eligible: list[GameRecord],
        owned_platforms: list[OwnedPlatform],
        exclude_title: str | None = None,
        max_count: int = _MAX_ALTERNATIVES,
    ) -> list[Recommendation]:
        """Return up to ``max_count`` alternatives, each playable on an owned platform.

        Re-applies the owned-platform filter so every alternative has confirmed
        availability (Req 7.4), ranks the survivors by community review, excludes
        ``exclude_title`` (typically the primary), and notes any missing review data
        in ``brief_reasoning`` (Req 7.5).
        """
        owned = self._owned_set(owned_platforms)
        ranked = sorted(
            (
                game
                for game in eligible
                if self._is_playable(game, owned) and game.title != exclude_title
            ),
            key=self._review_score,
            reverse=True,
        )
        results: list[Recommendation] = []
        for record in ranked[:max_count]:
            recommendation = self._to_recommendation(record)
            recommendation.brief_reasoning = self._build_brief_reasoning(record)
            results.append(recommendation)
        return results

    def _eligible_candidates(
        self,
        library: list[GameRecord],
        owned_platforms: list[OwnedPlatform],
        time_budget_minutes: int,
        user_id: str,
    ) -> list[GameRecord]:
        """Filter the library to eligible candidates, ranked by review (Req 7.1, 7.2, 8.3)."""
        recent = {
            rec.game_title
            for rec in self._memory.get_recent_recommendations(user_id, _RECENT_SESSIONS)
        }
        owned = self._owned_set(owned_platforms)
        eligible = [
            game
            for game in library
            if game.title not in recent  # no repeats from recent sessions (Req 8.3)
            and self._is_playable(game, owned)  # confirmed owned-platform availability
            and game.estimated_playtime is not None
            and game.estimated_playtime <= time_budget_minutes  # fits time budget (Req 7.1)
        ]
        # Stable sort keeps input order among equal scores (Req 7.2, ranking monotonicity).
        eligible.sort(key=self._review_score, reverse=True)
        return eligible

    @staticmethod
    def _select_primary(eligible: list[GameRecord]) -> GameRecord:
        """Pick the top-ranked candidate (highest community review score, Req 7.2)."""
        return eligible[0]

    def _build_reasoning(
        self,
        record: GameRecord,
        mood: MoodDimensions,
        minutes: int,
        owned_platforms: list[OwnedPlatform],
        user_id: str,
    ) -> str:
        """Build detailed primary reasoning including the community-review summary.

        Always produces deterministic text covering the review summary (or its
        absence, Req 7.3, 7.5), the time budget, and the playable owned platforms,
        then optionally appends a friendly note from the agent. Any agent failure
        degrades gracefully to the deterministic text alone.
        """
        base = self._deterministic_reasoning(record, mood, minutes, owned_platforms)
        extra = self._enhance_reasoning(base, user_id)
        return f"{base} {extra}" if extra else base

    def _enhance_reasoning(self, base: str, user_id: str) -> str | None:
        """Optionally enrich reasoning via the agent; ``None`` when unavailable (Req 7.2)."""
        try:
            response = self._bedrock.invoke_conversational(self._reasoning_prompt(base), user_id)
        except BedrockServiceError:
            return None
        if isinstance(response, str) and response.strip():
            return response.strip()
        return None

    @staticmethod
    def _deterministic_reasoning(
        record: GameRecord,
        mood: MoodDimensions,
        minutes: int,
        owned_platforms: list[OwnedPlatform],
    ) -> str:
        """Compose the deterministic reasoning string (Req 7.3, 7.5)."""
        owned = Recommender._owned_set(owned_platforms)
        playable_on = [p for p in record.platform_availability if p.casefold() in owned]
        platforms_text = ", ".join(playable_on) if playable_on else "your owned platforms"
        genre_clause = f"a {record.genre} game that " if record.genre else "a game that "
        review = record.community_review
        if review is not None:
            review_text = (
                f"The community rates it {review.score:.1f}/10 across "
                f"{review.source_count} sources: {review.sentiment_summary}"
            )
        else:
            review_text = "Community review data is unavailable for this title."
        return (
            f"{record.title} is {genre_clause}suits how you're feeling right now "
            f"({Recommender._mood_descriptor(mood)}). It runs about "
            f"{record.estimated_playtime} minutes, comfortably within your "
            f"{minutes}-minute window, and you can play it on {platforms_text}. "
            f"{review_text}"
        )

    @staticmethod
    def _build_brief_reasoning(record: GameRecord) -> str:
        """Short reasoning for an alternative, noting missing review data (Req 7.4, 7.5)."""
        genre_text = record.genre if record.genre else "Unknown genre"
        playtime_text = (
            f"~{record.estimated_playtime} min"
            if record.estimated_playtime is not None
            else "playtime unknown"
        )
        review = record.community_review
        review_text = (
            f"rated {review.score:.1f}/10" if review is not None else "community review unavailable"
        )
        return f"{genre_text}, {playtime_text}, {review_text}."

    @staticmethod
    def _reasoning_prompt(base: str) -> str:
        """Prompt the agent for a short, friendly embellishment of the reasoning."""
        return (
            "In one or two upbeat sentences, add a friendly note encouraging the player "
            "to try this game. Do not contradict or merely repeat these facts:\n"
            f"{base}"
        )

    @staticmethod
    def _mood_descriptor(mood: MoodDimensions) -> str:
        """Summarize the mood dimensions into a short human phrase for reasoning text."""
        energy = "high-energy" if mood.energy_level >= 0.5 else "low-key"
        challenge = "challenge-seeking" if mood.challenge_appetite >= 0.5 else "relaxed"
        social = "social" if mood.social_desire >= 0.5 else "solo"
        return f"{energy}, {challenge}, {social}"

    @staticmethod
    def _owned_set(owned_platforms: list[OwnedPlatform]) -> set[str]:
        """Case-folded set of owned platform names for membership checks (Req 7.1)."""
        return {platform.name.casefold() for platform in owned_platforms}

    @staticmethod
    def _is_playable(game: GameRecord, owned: set[str]) -> bool:
        """True only when confirmed availability intersects owned platforms (Req 5.3, 7.5)."""
        if not game.platform_availability:  # unconfirmed availability -> excluded
            return False
        return any(platform.casefold() in owned for platform in game.platform_availability)

    @staticmethod
    def _review_score(game: GameRecord) -> float:
        """Ranking key; a missing review sorts below any reviewed record (Req 7.2)."""
        return game.community_review.score if game.community_review else -1.0

    @staticmethod
    def _to_recommendation(record: GameRecord) -> Recommendation:
        """Map a GameRecord onto a display Recommendation (record.title -> game_title)."""
        return Recommendation(
            game_title=record.title,
            genre=record.genre,
            estimated_playtime=record.estimated_playtime,
            reasoning="",
            platform_availability=list(record.platform_availability),
            community_review=record.community_review,
        )

    @staticmethod
    def _no_recommendation() -> Recommendation:
        """Sentinel returned when no candidate matches; empty title signals no match."""
        return Recommendation(
            game_title="",
            genre=None,
            estimated_playtime=None,
            reasoning=_NO_MATCH_REASONING,
        )
