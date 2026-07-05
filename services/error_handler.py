"""User-facing error sanitization.

Maps internal exceptions to generic, detail-free messages so that no technical
details (stack traces, exception text, API keys, endpoints, internal codes) ever
reach the user (Req 10.1, 10.4). All external-service failures are routed through
this sanitizer before being surfaced in the UI.

Sanitized does not mean silent: the real exception (with traceback) is logged
server-side here, at the single choke point every failure passes through, so a
deployed failure is diagnosable from the app logs (Streamlit Cloud captures
stdout/stderr) while the user still only ever sees the generic message.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ErrorHandler:
    """Translates exceptions into safe, user-friendly messages."""

    GENERIC_MESSAGES: dict[str, str] = {
        "memory_unavailable": (
            "Personalization is temporarily limited. Recommendations still work."
        ),
        "tavily_unavailable": (
            "Game lookup is temporarily unavailable. Using available information."
        ),
        "gmail_unavailable": (
            "Couldn't read your Gmail purchases right now. The rest of the app still works."
        ),
        "llm_unavailable": (
            "The recommendation engine is temporarily unavailable. Please try again."
        ),
        "unknown": "Something went wrong. Let's try again.",
    }

    @staticmethod
    def sanitize_error(error: Exception, service: str) -> str:
        """Return a generic message for ``service`` that leaks no technical details.

        The ``error`` is never interpolated into the RETURNED message: known services
        map to a friendly message, and any unrecognized service falls back to the
        generic ``unknown`` message (Req 10.1, 10.4). The real exception is logged
        with its traceback (server logs only) so failures stay diagnosable.
        """
        logger.error("%s service failure: %s", service, error, exc_info=error)
        return ErrorHandler.GENERIC_MESSAGES.get(
            f"{service}_unavailable", ErrorHandler.GENERIC_MESSAGES["unknown"]
        )
