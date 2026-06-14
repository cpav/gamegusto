"""Unit tests for the canonical data models (task 2.4).

Covers ``GameRecord.dedup_key`` normalization and the ``is_enriched()`` truth
table (Req 2.2, 2.3) and ``OwnedPlatform`` id generation (Req 6.4).
"""

from __future__ import annotations

import uuid

import pytest

from models.game_record import CommunityReview, GameRecord
from models.platform import OwnedPlatform


class TestDedupKey:
    """``dedup_key`` collapses cross-source duplicates via normalized title+platform."""

    def test_casefold_makes_title_case_insensitive(self) -> None:
        upper = GameRecord(title="HADES", platforms=["PC"])
        title_case = GameRecord(title="Hades", platforms=["PC"])
        lower = GameRecord(title="hades", platforms=["PC"])

        assert upper.dedup_key == title_case.dedup_key == lower.dedup_key

    def test_casefold_makes_platform_case_insensitive(self) -> None:
        upper = GameRecord(title="Hades", platforms=["PC"])
        lower = GameRecord(title="Hades", platforms=["pc"])

        assert upper.dedup_key == lower.dedup_key

    def test_strips_surrounding_whitespace_on_title_and_platform(self) -> None:
        padded = GameRecord(title="  Hades ", platforms=["  PC "])
        clean = GameRecord(title="Hades", platforms=["PC"])

        assert padded.dedup_key == clean.dedup_key

    def test_key_format_is_title_pipe_platform(self) -> None:
        record = GameRecord(title="Hades", platforms=["PC"])

        assert record.dedup_key == "hades|pc"

    def test_platform_taken_from_first_entry(self) -> None:
        record = GameRecord(title="Hades", platforms=["Switch", "PC"])

        assert record.dedup_key == "hades|switch"

    def test_empty_platforms_yields_trailing_pipe_with_empty_segment(self) -> None:
        record = GameRecord(title="Hades", platforms=[])

        assert record.dedup_key == "hades|"

    def test_unicode_aware_casefold(self) -> None:
        # The German "ß" casefolds to "ss"; plain str.lower() would not collapse these.
        sharp_s = GameRecord(title="STRAßE", platforms=["PC"])
        double_s = GameRecord(title="strasse", platforms=["PC"])

        assert sharp_s.dedup_key == double_s.dedup_key


class TestIsEnriched:
    """``is_enriched()`` is true only when both genre and availability are present."""

    def test_genre_and_availability_present_is_enriched(self) -> None:
        record = GameRecord(
            title="Hades",
            genre="Roguelike",
            platform_availability=["PC"],
        )

        assert record.is_enriched() is True

    def test_genre_present_but_no_availability_is_not_enriched(self) -> None:
        record = GameRecord(title="Hades", genre="Roguelike", platform_availability=[])

        assert record.is_enriched() is False

    def test_availability_present_but_no_genre_is_not_enriched(self) -> None:
        record = GameRecord(title="Hades", genre=None, platform_availability=["PC"])

        assert record.is_enriched() is False

    def test_neither_genre_nor_availability_is_not_enriched(self) -> None:
        record = GameRecord(title="Hades", genre=None, platform_availability=[])

        assert record.is_enriched() is False

    @pytest.mark.parametrize(
        ("genre", "availability", "expected"),
        [
            (None, [], False),
            (None, ["PC"], False),
            ("Roguelike", [], False),
            ("Roguelike", ["PC"], True),
        ],
    )
    def test_truth_table(self, genre: str | None, availability: list[str], expected: bool) -> None:
        record = GameRecord(title="Hades", genre=genre, platform_availability=availability)

        assert record.is_enriched() is expected

    def test_default_record_is_not_enriched(self) -> None:
        # A freshly-imported record (no enrichment yet) must not pass the gate.
        assert GameRecord(title="Hades").is_enriched() is False


class TestOwnedPlatformId:
    """``OwnedPlatform`` auto-generates a stable, unique string id (Req 6.4)."""

    def test_platform_id_is_generated_when_not_provided(self) -> None:
        platform = OwnedPlatform(name="PC")

        assert isinstance(platform.platform_id, str)
        assert platform.platform_id

    def test_generated_id_is_a_valid_uuid_string(self) -> None:
        platform = OwnedPlatform(name="PC")

        # Round-tripping through uuid.UUID validates the format without raising.
        assert str(uuid.UUID(platform.platform_id)) == platform.platform_id

    def test_generated_ids_are_unique_across_instances(self) -> None:
        ids = {OwnedPlatform(name="PC").platform_id for _ in range(100)}

        assert len(ids) == 100

    def test_explicitly_passed_platform_id_is_respected(self) -> None:
        platform = OwnedPlatform(name="PC", platform_id="fixed-id")

        assert platform.platform_id == "fixed-id"


def test_community_review_is_constructible() -> None:
    # Sanity check the nested enrichment model used by GameRecord.
    review = CommunityReview(score=9.1, sentiment_summary="Beloved", source_count=42)

    assert review.score == 9.1
    assert review.sentiment_summary == "Beloved"
    assert review.source_count == 42
