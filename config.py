"""Application configuration loaded exclusively from environment variables.

No secrets are hardcoded; every value is read from the process environment.
Secret values are never included in ``repr`` output or log messages so they
cannot leak through stack traces or debug logging.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields


class ConfigError(RuntimeError):
    """Raised when a required configuration value is missing."""


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


# Names of fields whose values are sensitive and must never be rendered.
_SECRET_FIELDS = frozenset({"tavily_api_key", "xbox_client_secret"})


@dataclass(frozen=True)
class Config:
    """Typed, immutable view of the application's environment configuration."""

    # Required.
    aws_region: str
    bedrock_agent_id: str
    bedrock_agent_alias_id: str
    tavily_api_key: str

    # Optional (feature-gated integrations).
    xbox_client_id: str | None = None
    xbox_client_secret: str | None = None
    gmail_credentials_path: str | None = None
    gmail_token_path: str | None = None
    gmail_redirect_uri: str | None = None

    @classmethod
    def from_env(cls) -> Config:
        """Build a ``Config`` from the current process environment."""
        return cls(
            aws_region=_require("AWS_REGION"),
            bedrock_agent_id=_require("BEDROCK_AGENT_ID"),
            bedrock_agent_alias_id=_require("BEDROCK_AGENT_ALIAS_ID"),
            tavily_api_key=_require("TAVILY_API_KEY"),
            xbox_client_id=_optional("XBOX_CLIENT_ID"),
            xbox_client_secret=_optional("XBOX_CLIENT_SECRET"),
            gmail_credentials_path=_optional("GMAIL_CREDENTIALS_PATH"),
            gmail_token_path=_optional("GMAIL_TOKEN_PATH"),
            gmail_redirect_uri=_optional("GMAIL_REDIRECT_URI"),
        )

    @property
    def xbox_enabled(self) -> bool:
        """True when both Xbox OAuth credentials are configured."""
        return bool(self.xbox_client_id and self.xbox_client_secret)

    @property
    def gmail_enabled(self) -> bool:
        """True when the Gmail credentials path is configured."""
        return bool(self.gmail_credentials_path)

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
