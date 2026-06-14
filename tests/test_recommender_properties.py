"""Property-based tests for :class:`Recommender` (task 6.7).

Encodes correctness properties 15–20 from ``design.md``:

* **Property 15 — Every recommendation is playable on an owned platform**
  (Req 5.3, 7.1, 7.4): the primary (when present) and every alternative are
  playable on an owned platform, with non-empty reasoning.
* **Property 16 — Unconfirmed availability is never the primary** (Req 7.5): a
  record with empty ``platform_availability`` is never selected as primary.
* **Property 17 — Community review quality drives ranking** (Req 7.2): the
  primary has the maximum community review score among eligible candidates and
  alternatives are ordered non-increasing in score (missing review ranks last).
* **Property 18 — Primary reasoning includes a community review summary**
  (Req 7.3): the reasoning contains the review's sentiment summary, or notes
  that review data is unavailable.
* **Property 19 — Time budget constraint** (Req 7.1): the primary's playtime is
  not ``None`` and is within the time budget.
* **Property 20 — No repeat recommendations in recent history** (Req 8.3): a
  title in the last five sessions is never the primary.

All Bedrock and memory interaction is stubbed; no network calls are made. The
Bedrock stub returns a fixed narrative, and the recommender composes its
reasoning from a deterministic factual core (which carries the review summary)
plus that narrative, so the Property 18 assertions are deterministic.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agent.mood_interpreter import MoodDimensions
from agent.recommender import Recommender
from models.game_record import CommunityReview, GameRecord
from models.platform import OwnedPlatform
from models.recommendation import Recommendation
from services.bedrock_service import BedrockService, BedrockServiceError
from services.memory_service import MemoryService

# Platform names the user may own, plus extras a user never owns. Recasing in
# the strategies exercises the casefold-based matching in the Recommender.
_OWNABLE_PLATFORMS = ["PC", "Xbox", "PlayStation", "Switch", "Mobile"]
_UNOWNED_PLATFORMS = ["RetroBox", "CloudArcade"]

# A small title pool so libraries and recent history collide, exercising the
# no-repeat filter and review-score ties.
_TITLE_POOL = ["Hades", "Celeste", "Stardew", "Doom", "Tetris", "Hollow Knight"]

_USER_ID = "user-1"


class _StubBedrock(BedrockService):
    """Bedrock stand-in whose conversational call returns canned text (no network)."""

    NARRATIVE = "A great fit for tonight."

    def __init__(self) -> None:
        """Skip the real client setup; this stub never reaches a network."""

    def invoke_conversational(self, prompt: str, session_id: str) -> str:
        """Return a fixed narrative so reasoning is deterministic in tests."""
        return self.NARRATIVE


class _FailingBedrock(BedrockService):
    """Bedrock stand-in whose conversational call always fails (no network)."""

    def __init__(self) -> None:
        """Skip the real client setup; this stub never reaches a network."""

    def invoke_conversational(self, prompt: str, session_id: str) -> str:
        """Always fail so the LLM-required reasoning path raises."""
        raise BedrockServiceError("service unavailable")


class _RecentMemory(MemoryService):
    """Memory stand-in returning a fixed recent-recommendation list (no network)."""

    def __init__(self, recent: list[Recommendation]) -> None:
        """Store the canned recent recommendations; other methods are unused."""
        self._recent = recent

    def get_recent_recommendations(self, user_id: str, sessions: int = 5) -> list[Recommendation]:
        """Return the configured recent recommendations regardless of args."""
        return list(self._recent)


# --- generators ---------------------------------------------------------------

_unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

_mood_strategy = st.builds(
    MoodDimensions,
    energy_level=_unit,
    stress_level=_unit,
    social_desire=_unit,
    challenge_appetite=_unit,
)

_review_strategy = st.one_of(
    st.none(),
    st.builds(
        CommunityReview,
        score=st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
        sentiment_summary=st.text(min_size=1, max_size=40),
        source_count=st.integers(min_value=1, max_value=50),
    ),
)


@st.composite
def _owned_platforms(draw: st.DrawFn) -> list[OwnedPlatform]:
    """A non-empty Platform_List drawn from the ownable pool, with mixed casing."""
    names = draw(
        st.lists(
            st.sampled_from(_OWNABLE_PLATFORMS),
            min_size=1,
            max_size=len(_OWNABLE_PLATFORMS),
            unique=True,
        )
    )
    return [OwnedPlatform(name=draw(st.sampled_from([n, n.upper(), n.lower()]))) for n in names]


@st.composite
def _game(draw: st.DrawFn) -> GameRecord:
    """A library record spanning confirmed/unconfirmed availability and playtime."""
    raw_avail = draw(
        st.lists(
            st.sampled_from(_OWNABLE_PLATFORMS + _UNOWNED_PLATFORMS),
            max_size=4,
            unique=True,
        )
    )
    availability = [draw(st.sampled_from([n, n.upper(), n.lower()])) for n in raw_avail]
    return GameRecord(
        title=draw(st.sampled_from(_TITLE_POOL)),
        genre=draw(st.one_of(st.none(), st.sampled_from(["Action", "Puzzle", "RPG"]))),
        estimated_playtime=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=600))),
        community_review=draw(_review_strategy),
        platform_availability=availability,
    )


_library_strategy = st.lists(_game(), max_size=8)
_budget_strategy = st.integers(min_value=1, max_value=480)
_recent_strategy = st.lists(st.sampled_from(_TITLE_POOL), max_size=5, unique=True)


# --- independent reference helpers (mirror the Recommender contract) ----------


def _owned_set(owned_platforms: list[OwnedPlatform]) -> set[str]:
    return {p.name.casefold() for p in owned_platforms}


def _is_playable(availability: list[str], owned: set[str]) -> bool:
    if not availability:
        return False
    return any(p.casefold() in owned for p in availability)


def _score(review: CommunityReview | None) -> float:
    return review.score if review is not None else -1.0


def _eligible(
    library: list[GameRecord],
    owned_platforms: list[OwnedPlatform],
    budget: int,
    recent_titles: set[str],
) -> list[GameRecord]:
    owned = _owned_set(owned_platforms)
    return [
        game
        for game in library
        if game.title not in recent_titles
        and _is_playable(game.platform_availability, owned)
        and game.estimated_playtime is not None
        and game.estimated_playtime <= budget
    ]


def _make_recommender(recent: list[Recommendation] | None = None) -> Recommender:
    return Recommender(_StubBedrock(), _RecentMemory(recent or []))


# --- properties ---------------------------------------------------------------


@given(
    mood=_mood_strategy,
    budget=_budget_strategy,
    library=_library_strategy,
    owned=_owned_platforms(),
)
def test_recommendations_are_playable_on_owned_platform(
    mood: MoodDimensions,
    budget: int,
    library: list[GameRecord],
    owned: list[OwnedPlatform],
) -> None:
    """Primary and alternatives are playable on an owned platform, with reasoning.

    **Validates: Requirements 5.3, 7.1, 7.4**
    """
    owned_set = _owned_set(owned)
    recommender = _make_recommender()

    primary = recommender.recommend(mood, budget, library, owned, _USER_ID)
    if primary.game_title:
        assert _is_playable(primary.platform_availability, owned_set)
        assert primary.reasoning.strip()

    alternatives = recommender.alternatives(library, owned)
    assert len(alternatives) <= 3
    for alt in alternatives:
        assert alt.game_title
        assert _is_playable(alt.platform_availability, owned_set)
        assert alt.brief_reasoning.strip()


@given(
    mood=_mood_strategy,
    budget=_budget_strategy,
    library=_library_strategy,
    owned=_owned_platforms(),
)
def test_unconfirmed_availability_never_primary(
    mood: MoodDimensions,
    budget: int,
    library: list[GameRecord],
    owned: list[OwnedPlatform],
) -> None:
    """A record with empty availability is never selected as the primary.

    **Validates: Requirements 7.5**
    """
    primary = _make_recommender().recommend(mood, budget, library, owned, _USER_ID)
    if primary.game_title:
        assert primary.platform_availability
        assert _is_playable(primary.platform_availability, _owned_set(owned))


@given(
    mood=_mood_strategy,
    budget=_budget_strategy,
    library=_library_strategy,
    owned=_owned_platforms(),
)
def test_community_review_quality_drives_ranking(
    mood: MoodDimensions,
    budget: int,
    library: list[GameRecord],
    owned: list[OwnedPlatform],
) -> None:
    """The primary has the maximum eligible review score; alternatives non-increasing.

    **Validates: Requirements 7.2**
    """
    recommender = _make_recommender()
    eligible = _eligible(library, owned, budget, recent_titles=set())

    primary = recommender.recommend(mood, budget, library, owned, _USER_ID)

    if not eligible:
        assert primary.game_title == ""
        return

    assert primary.game_title
    best_score = max(_score(game.community_review) for game in eligible)
    assert _score(primary.community_review) == best_score
    assert primary.game_title in {game.title for game in eligible}

    alternatives = recommender.alternatives(library, owned)
    scores = [_score(alt.community_review) for alt in alternatives]
    assert scores == sorted(scores, reverse=True)


@given(
    mood=_mood_strategy,
    budget=_budget_strategy,
    library=_library_strategy,
    owned=_owned_platforms(),
)
def test_primary_reasoning_reflects_community_review(
    mood: MoodDimensions,
    budget: int,
    library: list[GameRecord],
    owned: list[OwnedPlatform],
) -> None:
    """Reasoning includes the review summary, or notes review data is unavailable.

    **Validates: Requirements 7.3**
    """
    primary = _make_recommender().recommend(mood, budget, library, owned, _USER_ID)
    if not primary.game_title:
        return
    if primary.community_review is not None:
        assert primary.community_review.sentiment_summary in primary.reasoning
    else:
        assert "unavailable" in primary.reasoning.lower()


@given(
    mood=_mood_strategy,
    budget=_budget_strategy,
    library=_library_strategy,
    owned=_owned_platforms(),
)
def test_primary_respects_time_budget(
    mood: MoodDimensions,
    budget: int,
    library: list[GameRecord],
    owned: list[OwnedPlatform],
) -> None:
    """The primary's estimated playtime is present and within the time budget.

    **Validates: Requirements 7.1**
    """
    primary = _make_recommender().recommend(mood, budget, library, owned, _USER_ID)
    if primary.game_title:
        assert primary.estimated_playtime is not None
        assert primary.estimated_playtime <= budget


@given(
    mood=_mood_strategy,
    budget=_budget_strategy,
    library=_library_strategy,
    owned=_owned_platforms(),
    recent_titles=_recent_strategy,
)
def test_no_repeat_from_recent_history(
    mood: MoodDimensions,
    budget: int,
    library: list[GameRecord],
    owned: list[OwnedPlatform],
    recent_titles: list[str],
) -> None:
    """A title from the most recent sessions is never the primary recommendation.

    **Validates: Requirements 8.3**
    """
    recent = [
        Recommendation(game_title=title, genre=None, estimated_playtime=None, reasoning="")
        for title in recent_titles
    ]
    primary = _make_recommender(recent).recommend(mood, budget, library, owned, _USER_ID)
    if primary.game_title:
        assert primary.game_title not in set(recent_titles)


def test_llm_failure_propagates_when_a_primary_is_produced() -> None:
    """A Bedrock failure during reasoning raises rather than degrading (LLM required).

    The recommender no longer falls back to deterministic-only reasoning on LLM
    failure — the error surfaces so a misconfigured model is not masked.

    **Validates: Requirements 7.2, 7.3**
    """
    mood = MoodDimensions(0.5, 0.5, 0.5, 0.5)
    owned = [OwnedPlatform(name="PC")]
    library = [
        GameRecord(
            title="Hades",
            genre="Roguelike",
            estimated_playtime=30,
            community_review=CommunityReview(9.0, "Beloved.", 10),
            platform_availability=["PC"],
        )
    ]
    recommender = Recommender(_FailingBedrock(), _RecentMemory([]))

    with pytest.raises(BedrockServiceError):
        recommender.recommend(mood, 60, library, owned, _USER_ID)
