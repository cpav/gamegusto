"""Property-based tests for :class:`ErrorHandler` sanitization (task 3.2).

Encodes Property 22 from ``design.md``: for any exception and any service name,
the sanitized message must never leak technical details and must always be one
of the finite set of known-safe generic messages.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from services.error_handler import ErrorHandler

# A finite, known-safe set of outputs. Property 22 requires every sanitized
# message to be drawn from exactly this set.
SAFE_MESSAGES = frozenset(ErrorHandler.GENERIC_MESSAGES.values())

# Exception types whose constructors accept an arbitrary message string.
_EXCEPTION_TYPES = (
    Exception,
    ValueError,
    RuntimeError,
    KeyError,
    ConnectionError,
    TimeoutError,
    OSError,
)

# Message fragments that resemble genuinely sensitive technical detail, mixed in
# to make leakage detectable if the implementation ever interpolated the error.
_SENSITIVE_SNIPPETS = st.sampled_from(
    [
        "sk-live-0123456789abcdefSECRET",
        "Traceback (most recent call last): File 'x.py', line 42",
        "https://api.internal.example.com/v1/secret?token=abc123",
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG",
        "ECONNREFUSED 10.0.0.5:5432",
        "ERR_CODE_5150 internal database failure",
        "password=hunter2",
    ]
)

# Arbitrary message text, including sensitive-looking strings and edge cases.
_sensitive_text = st.one_of(
    st.text(),
    _SENSITIVE_SNIPPETS,
    st.text(min_size=1).flatmap(lambda s: _SENSITIVE_SNIPPETS.map(lambda secret: f"{s} {secret}")),
)


@st.composite
def _exceptions(draw: st.DrawFn) -> tuple[Exception, str]:
    """Build an exception of an arbitrary type carrying ``message`` text."""
    message = draw(_sensitive_text)
    exc_type = draw(st.sampled_from(_EXCEPTION_TYPES))
    return exc_type(message), message


@given(
    exc_and_message=_exceptions(),
    service=st.text(),
)
def test_sanitized_message_never_leaks_and_is_always_safe(
    exc_and_message: tuple[Exception, str], service: str
) -> None:
    """Property 22: sanitized output leaks no detail and is always a safe message.

    **Validates: Requirements 10.1, 10.4**
    """
    error, _message = exc_and_message

    result = ErrorHandler.sanitize_error(error, service)

    # Always a non-empty string.
    assert isinstance(result, str)
    assert result

    # Always drawn from the finite set of known-safe messages.
    assert result in SAFE_MESSAGES

    # The sanitized output is determined SOLELY by the service name: sanitizing a
    # benign exception for the same service yields an identical message. This is
    # the precise statement of "never leaks" — the exception's message, args, and
    # repr cannot influence the output, so no technical detail can ever reach the
    # user (Req 10.1, 10.4). (A naive substring check is unsound here: a finite
    # safe message can coincidentally contain a one-character error text such as
    # "S", which is not a leak.)
    assert result == ErrorHandler.sanitize_error(ValueError("benign placeholder"), service)


@given(service=st.sampled_from(["memory", "tavily", "gmail", "llm"]))
def test_known_services_map_to_their_specific_message(service: str) -> None:
    """Known services resolve to their dedicated (non-fallback) safe message.

    **Validates: Requirements 10.1, 10.4**
    """
    result = ErrorHandler.sanitize_error(ValueError("sk-live-leak"), service)

    assert result == ErrorHandler.GENERIC_MESSAGES[f"{service}_unavailable"]


@given(
    service=st.text().filter(lambda s: f"{s}_unavailable" not in ErrorHandler.GENERIC_MESSAGES),
)
def test_unknown_services_fall_back_to_generic_message(service: str) -> None:
    """Any unrecognized service falls back to the generic ``unknown`` message.

    **Validates: Requirements 10.1, 10.4**
    """
    result = ErrorHandler.sanitize_error(RuntimeError("Traceback secret"), service)

    assert result == ErrorHandler.GENERIC_MESSAGES["unknown"]
