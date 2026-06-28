"""Streamlit UI bootstrap: secrets bridging, cached service graph, accessors.

Bridges Streamlit's secrets manager into the process environment so the existing
env-only :class:`~config.Config` works identically locally and on Streamlit
Community Cloud (Req 12.3), then builds the service graph once per session via
:func:`bootstrap.build_app` and exposes small accessors the views use.

Gmail is omitted on the hosted deployment (its vars are simply unset), so the
library is the existing DynamoDB store plus manual entry (Req 12.4).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import streamlit as st

from bootstrap import AppContext, build_app
from config import Config, ConfigError, load_env_file

#: Single-user app: all data lives under one user id (the app is private to the owner).
USER_ID = "default"

#: IANA timezone -> store-region country name. The browser's timezone reflects where
#: the user physically *is* (unlike language, which a Dane may set to en-GB, and
#: unlike a server's IP/AWS region, which is the datacenter). Best-effort and
#: overridable: an explicit DEALS_REGION always wins, and unknown zones fall back to
#: the default. Extend as needed.
_COUNTRY_BY_TIMEZONE = {
    "Europe/Copenhagen": "Denmark",
    "Europe/Stockholm": "Sweden",
    "Europe/Oslo": "Norway",
    "Europe/Helsinki": "Finland",
    "Atlantic/Reykjavik": "Iceland",
    "Europe/Berlin": "Germany",
    "Europe/Amsterdam": "Netherlands",
    "Europe/Brussels": "Belgium",
    "Europe/Paris": "France",
    "Europe/Madrid": "Spain",
    "Europe/Rome": "Italy",
    "Europe/Lisbon": "Portugal",
    "Europe/Dublin": "Ireland",
    "Europe/Vienna": "Austria",
    "Europe/Zurich": "Switzerland",
    "Europe/Warsaw": "Poland",
    "Europe/Prague": "Czechia",
    "Europe/London": "United Kingdom",
    "America/New_York": "United States",
    "America/Chicago": "United States",
    "America/Denver": "United States",
    "America/Los_Angeles": "United States",
    "America/Toronto": "Canada",
    "Australia/Sydney": "Australia",
    "Pacific/Auckland": "New Zealand",
}


def _region_from_timezone(timezone: str | None) -> str | None:
    """Map an IANA timezone (e.g. ``"Europe/Copenhagen"``) to a country, or ``None``."""
    if not timezone:
        return None
    return _COUNTRY_BY_TIMEZONE.get(timezone.strip())


def _detect_region() -> str | None:
    """Best-effort store region from the browser's timezone (via a JS round-trip).

    Returns ``None`` on the first run (the component resolves on the next rerun) and
    for unmapped/unavailable timezones, so the configured default applies until/unless
    a known timezone resolves. Degrades quietly if the component can't run.
    """
    try:
        from streamlit_js_eval import streamlit_js_eval

        timezone = streamlit_js_eval(
            js_expressions="Intl.DateTimeFormat().resolvedOptions().timeZone",
            key="user_timezone",
        )
    except Exception:  # noqa: BLE001 - component unavailable; fall back to the default
        return None
    return _region_from_timezone(timezone if isinstance(timezone, str) else None)


def _secrets_to_env(secrets: Mapping[str, Any]) -> dict[str, str]:
    """Return the string-valued secret entries to export as environment variables.

    Pure helper (no Streamlit/OS state) so it can be unit-tested; nested/non-string
    secret values are skipped since config reads flat string env vars.
    """
    return {key: value for key, value in secrets.items() if isinstance(value, str)}


def _bridge_secrets() -> None:
    """Populate ``os.environ`` from a local ``.env`` and Streamlit secrets.

    Existing environment values always win (``setdefault``), so an explicit env
    var is never clobbered. Accessing ``st.secrets`` raises when no secrets file
    is configured (normal in local dev), which is caught and ignored.
    """
    load_env_file()
    try:
        secrets = dict(st.secrets)
    except Exception:  # noqa: BLE001 - no secrets.toml in local dev is fine
        return
    for key, value in _secrets_to_env(secrets).items():
        os.environ.setdefault(key, value)


def detect_and_store_region() -> None:
    """Run the browser-timezone detection once per script run, caching any result.

    Call this exactly once near the top of the app: it instantiates a JS component,
    so invoking it from every ``get_context`` accessor would collide on the widget
    key. The timezone resolves on the rerun after first paint; ``get_context`` then
    rebuilds the graph with the new region. ``None`` results leave the cache (and so
    the configured default) untouched.
    """
    region = _detect_region()
    if region is not None:
        st.session_state["_detected_region"] = region


def get_context() -> AppContext:
    """Return the per-session :class:`AppContext`, or stop with a friendly error.

    Cached in ``st.session_state`` (not ``st.cache_resource``): a session is recreated
    after a redeploy/reconnect, so the graph is rebuilt with the current code rather
    than a stale object from a previous version (which caused ``AttributeError`` on a
    newly-added field). It is also rebuilt when the detected region changes (the
    browser timezone resolves a rerun after first paint). A missing required secret
    surfaces as a sanitized message — the variable name only, never a value (Req 10.1).
    """
    region = st.session_state.get("_detected_region")
    if "_ctx" not in st.session_state or region != st.session_state.get("_ctx_region"):
        _bridge_secrets()
        try:
            st.session_state["_ctx"] = build_app(
                Config.from_env(), user_id=USER_ID, detected_region=region
            )
            st.session_state["_ctx_region"] = region
        except ConfigError as exc:
            st.error(f"⚙️ Configuration problem: {exc}. Set it in the app's secrets and reload.")
            st.stop()
    ctx: AppContext = st.session_state["_ctx"]
    return ctx


def get_runtime() -> Any:
    """Return the agent runtime driving the conversation."""
    return get_context().runtime


def get_memory_service() -> Any:
    """Return the shared memory service (records, platforms, sessions)."""
    return get_context().memory


def get_user_id() -> str:
    """Return the active user id."""
    return get_context().user_id


def get_autocomplete(query: str) -> list[str]:
    """Return manual-entry title suggestions for ``query`` (>= 3 chars, Req 3.4)."""
    return get_context().tavily.autocomplete(query)


def get_enricher() -> Any:
    """Return the enricher (Tavily search + LLM) for on-demand enrichment."""
    return get_context().enricher
