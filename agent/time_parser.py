"""Parse a user's stated available time into a Time_Budget in minutes.

Maps explicit hour/minute expressions to a positive integer and flags vague
input for clarification (Requirements 1.4, 1.5, 1.6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Match
from typing import Callable


@dataclass
class TimeParseResult:
    """Outcome of parsing a time expression: minutes, or a clarification ask."""

    minutes: int | None
    needs_clarification: bool
    clarification_question: str | None


class TimeParser:
    """Turn free-text time statements into a Time_Budget in minutes."""

    PATTERNS: list[tuple[str, Callable[[Match[str]], int]]] = [
        (
            r"(\d+(?:\.\d+)?)\s*h\w*\s*(?:and\s*)?(\d+)\s*m",
            lambda m: int(float(m.group(1)) * 60) + int(m.group(2)),
        ),
        (r"(\d+)\s*h\w*", lambda m: int(m.group(1)) * 60),
        (r"(\d+)\s*m\w*", lambda m: int(m.group(1))),
    ]
    AMBIGUOUS = ["a bit", "a little", "some time", "a while", "not long"]

    def parse(self, text: str) -> TimeParseResult:
        """Parse explicit hours/minutes to a positive int; clarify on vague input (Req 1.5, 1.6)."""
        t = text.lower().strip()
        if any(p in t for p in self.AMBIGUOUS):
            return TimeParseResult(
                None,
                True,
                "Could you give a rough estimate, like '30 minutes' or '2 hours'?",
            )
        for pattern, extract in self.PATTERNS:
            if m := re.search(pattern, t):
                minutes = extract(m)
                if minutes > 0:
                    return TimeParseResult(minutes, False, None)
                break
        return TimeParseResult(
            None,
            True,
            "How much time do you have? Try '45 minutes' or '1 hour'.",
        )
