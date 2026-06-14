"""Property-based tests for :class:`MemoryService` round-trips (task 3.5).

Encodes three correctness properties from ``design.md``:

* **Property 11** — Game_Record store round-trip (Validates: Requirements 5.2, 9.5)
* **Property 12** — Platform_List CRUD round-trip (Validates: Requirements 6.1, 6.2, 6.3, 6.4)
* **Property 13** — Session persistence round-trip (Validates: Requirements 8.1, 8.2)

The service is exercised against an in-memory fake :class:`MemoryClient`; no real
AWS/AgentCore service is contacted.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from models.game_record import CommunityReview, GameRecord
from models.platform import OwnedPlatform
from models.recommendation import Recommendation
from models.session import SessionData
from services.memory_service import MemoryService

USER_ID = "test-user"
MISSING_ID = "__definitely-not-a-real-platform-id__"


@dataclass
class MoodDimensions:
    """Local stand-in for ``agent.mood_interpreter.MoodDimensions``.

    ``MemoryService._session_to_dict`` calls ``dataclasses.asdict`` on the mood,
    so the test's mood must be a dataclass instance with the four dimensions.
    """

    energy_level: float
    stress_level: float
    social_desire: float
    challenge_appetite: float


class FakeMemoryClient:
    """Dict-backed in-memory implementation of the ``MemoryClient`` protocol.

    Keyed documents live in ``_values``; the append-only session log lives in
    ``_events`` and is kept newest-first. Values are deep-copied on the way in
    and out so callers cannot mutate stored state by aliasing.
    """

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], dict[str, Any]] = {}
        self._events: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def get_value(self, user_id: str, key: str) -> dict[str, Any] | None:
        stored = self._values.get((user_id, key))
        return copy.deepcopy(stored) if stored is not None else None

    def put_value(self, user_id: str, key: str, value: dict[str, Any]) -> None:
        self._values[(user_id, key)] = copy.deepcopy(value)

    def append_event(self, user_id: str, key: str, event: dict[str, Any]) -> None:
        self._events.setdefault((user_id, key), []).insert(0, copy.deepcopy(event))

    def list_events(self, user_id: str, key: str, limit: int) -> list[dict[str, Any]]:
        events = self._events.get((user_id, key), [])
        return copy.deepcopy(events[:limit])


# --- Hypothesis strategies ---

_text = st.text(max_size=20)
_nonempty_text = st.text(min_size=1, max_size=20)
_finite_float = st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6)

_community_reviews = st.builds(
    CommunityReview,
    score=st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    sentiment_summary=_text,
    source_count=st.integers(min_value=0, max_value=1000),
)

_game_records = st.builds(
    GameRecord,
    title=_nonempty_text,
    platforms=st.lists(_nonempty_text, max_size=3),
    source=st.sampled_from(["gmail", "manual", "enrichment"]),
    purchase_date=st.one_of(st.none(), st.dates()),
    genre=st.one_of(st.none(), _text),
    estimated_playtime=st.one_of(st.none(), st.integers(min_value=0, max_value=100_000)),
    community_review=st.one_of(st.none(), _community_reviews),
    platform_availability=st.lists(_nonempty_text, max_size=3),
    external_ids=st.dictionaries(_nonempty_text, _text, max_size=3),
)

_platforms = st.builds(OwnedPlatform, name=_text)

_recommendations = st.builds(
    Recommendation,
    game_title=_nonempty_text,
    genre=st.one_of(st.none(), _text),
    estimated_playtime=st.one_of(st.none(), st.integers(min_value=0, max_value=100_000)),
    reasoning=_text,
    brief_reasoning=_text,
    platform_availability=st.lists(_nonempty_text, max_size=3),
    community_review=st.one_of(st.none(), _community_reviews),
)

_moods = st.builds(
    MoodDimensions,
    energy_level=_finite_float,
    stress_level=_finite_float,
    social_desire=_finite_float,
    challenge_appetite=_finite_float,
)

_sessions = st.builds(
    SessionData,
    user_id=st.just(USER_ID),
    mood=_moods,
    time_budget_minutes=st.integers(min_value=0, max_value=100_000),
    recommendation=_recommendations,
    alternatives=st.lists(_recommendations, max_size=3),
    user_feedback=st.one_of(st.none(), _text),
)

_CONTRACT_FIELDS = frozenset(
    {
        "title",
        "platforms",
        "source",
        "purchase_date",
        "genre",
        "estimated_playtime",
        "community_review",
        "platform_availability",
        "external_ids",
    }
)


def _dedup_first_wins(records: list[GameRecord]) -> list[GameRecord]:
    """Mirror the service's dedup: first occurrence per dedup key wins."""
    seen: set[str] = set()
    result: list[GameRecord] = []
    for record in records:
        if record.dedup_key in seen:
            continue
        seen.add(record.dedup_key)
        result.append(record)
    return result


# --- Property 11: Game_Record store round-trip ---


@given(records=st.lists(_game_records, max_size=8))
def test_game_record_store_round_trip(records: list[GameRecord]) -> None:
    """Property 11: stored records survive the round-trip, deduped, contract-only.

    **Validates: Requirements 5.2, 9.5**
    """
    client = FakeMemoryClient()
    service = MemoryService(client)

    assert service.store_records(USER_ID, records) is True

    expected = _dedup_first_wins(records)
    retrieved = service.get_records(USER_ID)

    # Every contract field survives the round-trip, deduped by dedup_key.
    assert retrieved == expected

    # Only the nine contract fields are persisted — no source-specific/raw data.
    stored = client.get_value(USER_ID, MemoryService.RECORDS_KEY)
    assert stored is not None
    for item in stored["records"]:
        assert set(item.keys()) == _CONTRACT_FIELDS


@given(
    initial=st.lists(_game_records, max_size=6),
    record=_game_records,
)
def test_upsert_record_adds_then_replaces(initial: list[GameRecord], record: GameRecord) -> None:
    """Property 11: upsert_record adds a new record and replaces by dedup key (Req 9.5).

    **Validates: Requirements 5.2, 9.5**
    """
    client = FakeMemoryClient()
    service = MemoryService(client)
    service.store_records(USER_ID, initial)

    assert service.upsert_record(USER_ID, record) is True

    retrieved = service.get_records(USER_ID)
    # Exactly one record carries the upserted key, and it equals the upserted one.
    matches = [r for r in retrieved if r.dedup_key == record.dedup_key]
    assert matches == [record]

    # Replacing the same key keeps the library size stable and swaps the value.
    replacement = GameRecord(
        title=record.title,
        platforms=list(record.platforms),
        source="enrichment",
        genre="replaced-genre",
    )
    assert replacement.dedup_key == record.dedup_key
    size_before = len(retrieved)
    assert service.upsert_record(USER_ID, replacement) is True

    after = service.get_records(USER_ID)
    assert len(after) == size_before
    assert [r for r in after if r.dedup_key == record.dedup_key] == [replacement]


# --- Property 12: Platform_List CRUD round-trip ---


@given(
    platforms=st.lists(_platforms, min_size=1, max_size=6, unique_by=lambda p: p.platform_id),
    new_name=_text,
)
def test_platform_list_crud_round_trip(platforms: list[OwnedPlatform], new_name: str) -> None:
    """Property 12: add/get/update/remove round-trip with free-text names.

    **Validates: Requirements 6.1, 6.2, 6.3, 6.4**
    """
    client = FakeMemoryClient()
    service = MemoryService(client)

    # add -> get returns exactly what was added, in order, free-text names intact.
    for platform in platforms:
        assert service.add_platform(USER_ID, platform) is True
    assert service.get_platform_list(USER_ID) == platforms

    target = platforms[0]

    # update_platform: existing id renames and returns True; missing id returns False.
    assert service.update_platform(USER_ID, target.platform_id, new_name) is True
    renamed = service.get_platform_list(USER_ID)
    assert {p.platform_id: p.name for p in renamed}[target.platform_id] == new_name
    assert service.update_platform(USER_ID, MISSING_ID, new_name) is False

    # remove_platform: existing id removes and returns True; missing id returns False.
    assert service.remove_platform(USER_ID, target.platform_id) is True
    remaining = service.get_platform_list(USER_ID)
    assert target.platform_id not in {p.platform_id for p in remaining}
    assert len(remaining) == len(platforms) - 1
    assert service.remove_platform(USER_ID, MISSING_ID) is False


# --- Property 13: Session persistence round-trip ---


@given(
    sessions=st.lists(_sessions, max_size=8),
    limit=st.integers(min_value=1, max_value=10),
)
def test_session_persistence_round_trip(sessions: list[SessionData], limit: int) -> None:
    """Property 13: stored sessions' primary recommendations round-trip newest-first.

    **Validates: Requirements 8.1, 8.2**
    """
    client = FakeMemoryClient()
    service = MemoryService(client)

    for session in sessions:
        assert service.store_session(USER_ID, session) is True

    expected = [s.recommendation for s in reversed(sessions)][:limit]
    retrieved = service.get_recent_recommendations(USER_ID, limit)

    assert retrieved == expected
