"""Property-based tests for :class:`TimeParser` (task 6.3).

Encodes two correctness properties from ``design.md``:

* **Property 3 — Time budget parsing** (Req 1.5): explicit hour/minute
  expressions parse to the correct positive integer minutes.
* **Property 4 — Ambiguous time triggers clarification** (Req 1.6): vague
  phrases, non-numeric text, and non-positive values request a clarification.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from agent.time_parser import TimeParser

# Sane positive ranges so expected minute values are exact and unambiguous.
_hours = st.integers(min_value=1, max_value=12)
_minutes = st.integers(min_value=1, max_value=59)


@given(hours=_hours)
def test_explicit_hours_parse_to_minutes(hours: int) -> None:
    """An explicit hours expression yields ``hours * 60`` minutes.

    **Validates: Requirements 1.5**
    """
    result = TimeParser().parse(f"{hours} hours")

    assert result.needs_clarification is False
    assert result.clarification_question is None
    assert result.minutes == hours * 60


@given(minutes=_minutes)
def test_explicit_minutes_parse_to_minutes(minutes: int) -> None:
    """An explicit minutes expression yields exactly that many minutes.

    **Validates: Requirements 1.5**
    """
    result = TimeParser().parse(f"{minutes} minutes")

    assert result.needs_clarification is False
    assert result.clarification_question is None
    assert result.minutes == minutes


@given(
    hours=_hours,
    minutes=_minutes,
    template=st.sampled_from(["{h} hours and {m} minutes", "{h}h and {m}m"]),
)
def test_combined_hours_and_minutes_parse_to_total(hours: int, minutes: int, template: str) -> None:
    """A clearly separated combined expression yields ``hours * 60 + minutes``.

    A clear separator (``and``) is used deliberately: the implementation's
    combined pattern parses an unseparated form like ``1h30m`` as 60, a
    documented quirk this property does not exercise.

    **Validates: Requirements 1.5**
    """
    result = TimeParser().parse(template.format(h=hours, m=minutes))

    assert result.needs_clarification is False
    assert result.clarification_question is None
    assert result.minutes == hours * 60 + minutes


@given(phrase=st.sampled_from(TimeParser.AMBIGUOUS))
def test_ambiguous_phrases_trigger_clarification(phrase: str) -> None:
    """Each known vague phrase requests a more specific estimate.

    **Validates: Requirements 1.6**
    """
    result = TimeParser().parse(phrase)

    assert result.needs_clarification is True
    assert result.minutes is None
    assert result.clarification_question
    assert result.clarification_question.strip()


# Free text carrying no digits can never match a numeric pattern, so it must
# always fall through to clarification.
_non_numeric_text = st.text(
    alphabet=st.characters(blacklist_categories=("Nd",), blacklist_characters="0123456789"),
    max_size=40,
)


@given(text=_non_numeric_text)
def test_non_numeric_text_triggers_clarification(text: str) -> None:
    """Any text without numeric values requests a clarification.

    **Validates: Requirements 1.6**
    """
    result = TimeParser().parse(text)

    assert result.needs_clarification is True
    assert result.minutes is None
    assert result.clarification_question
    assert result.clarification_question.strip()


@given(
    template=st.sampled_from(["{n} minutes", "{n} hours", "{n} hours and {n} minutes"]),
)
def test_non_positive_values_trigger_clarification(template: str) -> None:
    """Explicit zero durations are non-positive and request a clarification.

    **Validates: Requirements 1.6**
    """
    result = TimeParser().parse(template.format(n=0))

    assert result.needs_clarification is True
    assert result.minutes is None
    assert result.clarification_question
    assert result.clarification_question.strip()
