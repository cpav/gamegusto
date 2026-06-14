"""Platform-family normalization and matching.

Owned platforms and enrichment-reported availability rarely use identical
strings: a user owns ``"Xbox"`` while availability says ``"Xbox Series X"``, or
owns ``"Switch"`` while a page says ``"Nintendo Switch"``. Exact case-folded
comparison misses these, so matching is done at the *family* level (Xbox,
PlayStation, Nintendo, PC) with an exact-name fallback for anything outside the
known families (the Platform_List is free-text and extensible, Req 6.4).
"""

from __future__ import annotations

# Substring keyword -> canonical family. Order does not matter: a name maps to a
# family if any keyword is contained in its case-folded form.
_FAMILY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("nintendo", "nintendo"),
    ("switch", "nintendo"),
    ("playstation", "playstation"),
    ("ps5", "playstation"),
    ("ps4", "playstation"),
    ("ps3", "playstation"),
    ("psp", "playstation"),
    ("vita", "playstation"),
    ("xbox", "xbox"),
    ("steam", "pc"),
    ("windows", "pc"),
    ("pc", "pc"),
    ("mac", "pc"),
    ("linux", "pc"),
)


def platform_family(name: str) -> str | None:
    """Return the canonical family for ``name``, or ``None`` if unrecognized."""
    folded = name.casefold()
    for keyword, family in _FAMILY_KEYWORDS:
        if keyword in folded:
            return family
    return None


def platforms_match(owned: str, available: str) -> bool:
    """True when ``owned`` and ``available`` name the same platform.

    Matches when both resolve to the same known family, or — for names outside
    the known families — when they are equal after case folding and trimming.
    """
    owned_family = platform_family(owned)
    available_family = platform_family(available)
    if owned_family is not None and available_family is not None:
        return owned_family == available_family
    return owned.strip().casefold() == available.strip().casefold()


def owned_intersects(owned_names: list[str], availability: list[str]) -> bool:
    """True when any owned platform matches any available platform (Req 5.3, 7.1)."""
    return any(
        platforms_match(owned, available) for owned in owned_names for available in availability
    )
