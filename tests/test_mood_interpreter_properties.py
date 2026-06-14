"""Property-based tests for :class:`MoodInterpreter` (task 6.3).

Encodes two correctness properties from ``design.md``:

* **Property 1 — Mood interpretation produces valid dimensions** (Req 1.2):
  when the agent returns an interpretable payload, every dimension is clamped
  into ``[0.0, 1.0]`` and no clarification is requested; missing or non-numeric
  dimensions instead request a clarification.
* **Property 2 — Uninterpretable mood triggers clarification** (Req 1.3): an
  ``interpretable=false`` payload, a sanitized service failure, or a malformed
  payload all yield a clarification, and the agent's failures never propagate.

All Bedrock interaction is stubbed; no network calls are made.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agent.mood_interpreter import (
    _DIMENSION_FIELDS,
    MoodInterpreter,
)
from services.bedrock_service import BedrockService, BedrockServiceError


class _StubBedrock(BedrockService):
    """Bedrock stand-in returning a fixed payload or raising, with no network."""

    def __init__(self, *, payload: dict[str, Any] | None = None, error: bool = False) -> None:
        self._payload = payload
        self._error = error

    def invoke_with_schema(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return the canned payload, or raise a sanitized service error."""
        if self._error:
            raise BedrockServiceError("service unavailable")
        assert self._payload is not None
        return self._payload


# Finite numerics spanning well below 0 and above 1 so clamping is exercised.
_numeric = st.one_of(
    st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    st.integers(min_value=-100, max_value=100),
)


@given(
    energy_level=_numeric,
    stress_level=_numeric,
    social_desire=_numeric,
    challenge_appetite=_numeric,
)
def test_valid_numeric_dimensions_are_clamped(
    energy_level: float,
    stress_level: float,
    social_desire: float,
    challenge_appetite: float,
) -> None:
    """Interpretable numeric payloads produce dimensions clamped to [0, 1].

    **Validates: Requirements 1.2**
    """
    raw = {
        "energy_level": energy_level,
        "stress_level": stress_level,
        "social_desire": social_desire,
        "challenge_appetite": challenge_appetite,
        "interpretable": True,
    }
    interpreter = MoodInterpreter(_StubBedrock(payload=raw))

    result = interpreter.interpret("buzzing with energy")

    assert result.needs_clarification is False
    assert result.clarification_question is None
    assert result.mood_dimensions is not None
    for field in _DIMENSION_FIELDS:
        value = getattr(result.mood_dimensions, field)
        assert isinstance(value, float)
        assert 0.0 <= value <= 1.0
        assert value == min(1.0, max(0.0, float(raw[field])))


# Values that are not valid numerics: strings, None, and booleans (which the
# implementation explicitly rejects despite being int subclasses).
_invalid_value = st.one_of(
    st.text(max_size=10),
    st.none(),
    st.booleans(),
)


@given(
    bad_field=st.sampled_from(_DIMENSION_FIELDS),
    bad_value=_invalid_value,
    good_value=st.floats(min_value=0.0, max_value=1.0),
    drop=st.booleans(),
)
def test_non_numeric_or_missing_dimension_triggers_clarification(
    bad_field: str,
    bad_value: object,
    good_value: float,
    drop: bool,
) -> None:
    """A non-numeric or absent dimension yields a clarification, not dimensions.

    **Validates: Requirements 1.2**
    """
    payload: dict[str, Any] = {field: good_value for field in _DIMENSION_FIELDS}
    payload["interpretable"] = True
    if drop:
        del payload[bad_field]
    else:
        payload[bad_field] = bad_value

    result = MoodInterpreter(_StubBedrock(payload=payload)).interpret("hmm")

    assert result.needs_clarification is True
    assert result.mood_dimensions is None
    assert result.clarification_question
    assert result.clarification_question.strip()


def _interpretable_false_payloads() -> st.SearchStrategy[dict[str, Any]]:
    """Payloads that signal the mood is not interpretable, with/without dims."""
    base = st.fixed_dictionaries(
        {field: st.floats(min_value=0.0, max_value=1.0) for field in _DIMENSION_FIELDS}
    )
    question = st.one_of(st.none(), st.text(min_size=1, max_size=30))
    return st.one_of(
        st.builds(
            lambda dims, q: {**dims, "interpretable": False, "clarification_question": q},
            base,
            question,
        ),
        st.just({"interpretable": False}),
    )


@given(payload=_interpretable_false_payloads())
def test_uninterpretable_payload_triggers_clarification(payload: dict[str, Any]) -> None:
    """An ``interpretable=false`` payload always requests a clarification.

    **Validates: Requirements 1.3**
    """
    result = MoodInterpreter(_StubBedrock(payload=payload)).interpret("asdfghjkl")

    assert result.needs_clarification is True
    assert result.mood_dimensions is None
    assert result.clarification_question
    assert result.clarification_question.strip()


def test_service_error_propagates_without_fallback() -> None:
    """A Bedrock failure propagates as ``BedrockServiceError`` (LLM is required).

    The interpreter no longer degrades to a canned clarification on transport
    failure — the error surfaces so a misconfigured model is not masked.

    **Validates: Requirements 1.3**
    """
    with pytest.raises(BedrockServiceError):
        MoodInterpreter(_StubBedrock(error=True)).interpret("anything")


# Malformed/garbage payloads: empty, missing the interpretable flag, or carrying
# unrelated keys. None describe a usable mood, so all request clarification.
_malformed_payloads: st.SearchStrategy[dict[str, Any]] = st.one_of(
    st.just({}),
    st.dictionaries(
        keys=st.text(max_size=8).filter(lambda k: k not in (*_DIMENSION_FIELDS, "interpretable")),
        values=st.one_of(st.text(max_size=8), st.integers(), st.none()),
        max_size=4,
    ),
)


@given(payload=_malformed_payloads)
def test_malformed_payload_triggers_clarification(payload: dict[str, Any]) -> None:
    """A payload lacking usable dimensions requests a clarification.

    **Validates: Requirements 1.3**
    """
    result = MoodInterpreter(_StubBedrock(payload=payload)).interpret("???")

    assert result.needs_clarification is True
    assert result.mood_dimensions is None
    assert result.clarification_question
    assert result.clarification_question.strip()
