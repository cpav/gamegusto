"""Unit tests for family-aware platform matching (agent.platform_match).

Covers the brief's platform-granularity fix: owning a family name like "Xbox"
must match availability variants like "Xbox Series X", while names outside the
known families fall back to exact (case-insensitive) comparison.
"""

from __future__ import annotations

import pytest

from agent.platform_match import owned_intersects, platform_family, platforms_match


@pytest.mark.parametrize(
    "name, family",
    [
        ("Nintendo Switch", "nintendo"),
        ("Switch", "nintendo"),
        ("PlayStation 5", "playstation"),
        ("PS4", "playstation"),
        ("Xbox Series X", "xbox"),
        ("Xbox", "xbox"),
        ("Steam", "pc"),
        ("Windows", "pc"),
        ("PC", "pc"),
        ("Stadia", None),
    ],
)
def test_platform_family(name: str, family: str | None) -> None:
    assert platform_family(name) == family


@pytest.mark.parametrize(
    "owned, available",
    [
        ("Xbox", "Xbox Series X"),
        ("Xbox Series S", "Xbox One"),
        ("Switch", "Nintendo Switch"),
        ("PC", "Steam"),
        ("playstation", "PlayStation 5"),
    ],
)
def test_same_family_matches(owned: str, available: str) -> None:
    assert platforms_match(owned, available) is True


def test_different_family_does_not_match() -> None:
    assert platforms_match("Xbox", "PlayStation 5") is False
    assert platforms_match("Nintendo Switch", "Steam") is False


def test_unknown_platforms_fall_back_to_exact_casefold() -> None:
    assert platforms_match("Stadia", "stadia") is True
    assert platforms_match(" Stadia ", "STADIA") is True
    assert platforms_match("Stadia", "Ouya") is False


def test_owned_intersects() -> None:
    assert owned_intersects(["Xbox", "Switch"], ["Xbox Series X"]) is True
    assert owned_intersects(["Switch"], ["PlayStation 5", "PC"]) is False
    assert owned_intersects([], ["Xbox"]) is False
    assert owned_intersects(["Xbox"], []) is False
