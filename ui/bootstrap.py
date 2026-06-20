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


@st.cache_resource(show_spinner=False)
def _build_context() -> AppContext:
    """Build and cache the wired service graph for the session (Req 3.5)."""
    _bridge_secrets()
    return build_app(Config.from_env(), user_id=USER_ID)


def get_context() -> AppContext:
    """Return the cached :class:`AppContext`, or stop with a friendly error.

    A missing required secret surfaces as a sanitized message (the variable name
    only, never a value) rather than a stack trace (Req 10.1).
    """
    try:
        return _build_context()
    except ConfigError as exc:
        st.error(f"⚙️ Configuration problem: {exc}. Set it in the app's secrets and reload.")
        st.stop()


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
