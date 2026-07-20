"""HTTP API over the GameGusto agent (frontend re-platform, Phase 1).

A thin FastAPI layer over the same service graph the Streamlit UI uses
(``bootstrap.AppContext``): JSON endpoints for the library, platforms, and
recommendation history, plus a Server-Sent-Events chat stream over
``AgentRuntime.stream``. Client-agnostic by design — the future PWA (and any
later native app) talks to this instead of importing Python.

Single-user for now: identity comes from one dependency (``current_user``)
that returns the context's fixed user id. Swapping that dependency for
JWT-derived identity (Cognito ``sub``) is the whole Phase 3 auth change —
no route touches a user id any other way.
"""
