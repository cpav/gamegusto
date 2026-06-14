"""Application configuration loaded exclusively from environment variables.

No secrets are hardcoded; every value is read from the process environment.
Secret values are never included in ``repr`` output or log messages so they
cannot leak through stack traces or debug logging.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

# Default extended-thinking budget (in tokens) for the Bedrock model when the
# environment does not override it.
_DEFAULT_REASONING_BUDGET_TOKENS = 3000


class ConfigError(RuntimeError):
    """Raised when a required configuration value is missing or invalid."""


def load_env_file(path: str = ".env") -> None:
    """Populate ``os.environ`` from a ``.env`` file without overriding existing vars.

    A convenience for the CLI and helper scripts so they work without manually
    exporting the file. Lines are ``KEY=VALUE``; blanks and ``#`` comments are
    ignored. Already-set environment variables always win, so this never clobbers
    an explicitly provided value.
    """
    if not os.path.isfile(path):
        return
    with open(path) as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _require(name: str) -> str:
    """Return a required environment variable or raise ``ConfigError``.

    The error reports only the variable name, never any value.
    """
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _optional(name: str) -> str | None:
    """Return an optional environment variable, or ``None`` when unset."""
    value = os.environ.get(name)
    return value or None


def _optional_int(name: str, default: int) -> int:
    """Return an optional integer environment variable, or ``default`` when unset.

    Raises ``ConfigError`` (naming only the variable) when the value is set but
    not a valid positive integer.
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer") from exc
    if value <= 0:
        raise ConfigError(f"Environment variable {name} must be a positive integer")
    return value


# Names of fields whose values are sensitive and must never be rendered.
_SECRET_FIELDS = frozenset({"tavily_api_key"})


@dataclass(frozen=True)
class Config:
    """Typed, immutable view of the application's environment configuration."""

    # Required.
    aws_region: str
    bedrock_model_id: str
    """Bedrock model id or cross-Region inference-profile id for the base model
    (e.g. a ``global.anthropic.claude-sonnet-4-6-...`` inference profile)."""

    tavily_api_key: str
    dynamodb_table_name: str
    """Name of the DynamoDB table backing AgentCore-style memory."""

    # Optional with defaults.
    bedrock_reasoning_budget_tokens: int = _DEFAULT_REASONING_BUDGET_TOKENS
    """Extended-thinking token budget passed to the Bedrock Converse API."""

    # Optional (feature-gated integrations).
    gmail_token_path: str | None = None

    @classmethod
    def from_env(cls) -> Config:
        """Build a ``Config`` from the current process environment."""
        return cls(
            aws_region=_require("AWS_REGION"),
            bedrock_model_id=_require("BEDROCK_MODEL_ID"),
            tavily_api_key=_require("TAVILY_API_KEY"),
            dynamodb_table_name=_require("DYNAMODB_TABLE_NAME"),
            bedrock_reasoning_budget_tokens=_optional_int(
                "BEDROCK_REASONING_BUDGET_TOKENS", _DEFAULT_REASONING_BUDGET_TOKENS
            ),
            gmail_token_path=_optional("GMAIL_TOKEN_PATH"),
        )

    @property
    def gmail_enabled(self) -> bool:
        """True when a cached Gmail token is configured (what the source needs)."""
        return bool(self.gmail_token_path)

    def __repr__(self) -> str:
        """Render the config with secret values masked."""
        parts: list[str] = []
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in _SECRET_FIELDS:
                rendered = "***" if value else "None"
            else:
                rendered = repr(value)
            parts.append(f"{field.name}={rendered}")
        return f"Config({', '.join(parts)})"
