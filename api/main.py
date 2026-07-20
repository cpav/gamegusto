"""Runnable API entry point.

Local run (same env vars as the CLI / Streamlit app, ``.env`` supported):

    uvicorn --factory api.main:build --reload --port 8000

Kept as a factory (not a module-level ``app``) so importing this module never
builds AWS clients as a side effect.
"""

from __future__ import annotations

from fastapi import FastAPI

from api.app import create_app
from bootstrap import build_app
from config import Config, load_env_file


def build() -> FastAPI:
    """Build the wired application from the process environment."""
    load_env_file()
    return create_app(build_app(Config.from_env()))
