"""Unit tests for configuration loading (config.py).

Covers the error paths that were previously untested: missing/invalid required
variables, optional-int validation, ``.env`` parsing rules, and the guarantee
that secret values never appear in ``repr`` output (Req 10.1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config import Config, ConfigError, load_env_file

_REQUIRED = {
    "AWS_REGION": "eu-north-1",
    "BEDROCK_MODEL_ID": "eu.anthropic.claude-sonnet-4-6",
    "TAVILY_API_KEY": "tvly-secret-key",
    "DYNAMODB_TABLE_NAME": "gamegusto",
}


def _set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _REQUIRED.items():
        monkeypatch.setenv(key, value)


def test_from_env_builds_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.delenv("BEDROCK_REASONING_BUDGET_TOKENS", raising=False)
    monkeypatch.delenv("DEALS_REGION", raising=False)
    monkeypatch.delenv("GMAIL_TOKEN_PATH", raising=False)

    config = Config.from_env()

    assert config.aws_region == "eu-north-1"
    assert config.deals_region is None
    assert config.gmail_enabled is False


def test_missing_required_variable_names_it_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required(monkeypatch)
    monkeypatch.delenv("TAVILY_API_KEY")

    with pytest.raises(ConfigError, match="TAVILY_API_KEY"):
        Config.from_env()


def test_empty_required_variable_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "")

    with pytest.raises(ConfigError, match="AWS_REGION"):
        Config.from_env()


@pytest.mark.parametrize("bad", ["abc", "3.5", "0", "-100"])
def test_invalid_reasoning_budget_is_rejected(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("BEDROCK_REASONING_BUDGET_TOKENS", bad)

    with pytest.raises(ConfigError, match="BEDROCK_REASONING_BUDGET_TOKENS"):
        Config.from_env()


def test_optional_values_flow_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("BEDROCK_REASONING_BUDGET_TOKENS", "5000")
    monkeypatch.setenv("DEALS_REGION", "Denmark")
    monkeypatch.setenv("GMAIL_TOKEN_PATH", "/tmp/token.json")

    config = Config.from_env()

    assert config.bedrock_reasoning_budget_tokens == 5000
    assert config.deals_region == "Denmark"
    assert config.gmail_enabled is True


def test_repr_masks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    rendered = repr(Config.from_env())

    assert "tvly-secret-key" not in rendered  # the secret value never renders (Req 10.1)
    assert "tavily_api_key=***" in rendered
    assert "eu-north-1" in rendered  # non-secret fields render normally


def test_load_env_file_parses_and_never_clobbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment line\n\nFROM_FILE=file-value\nALREADY_SET=file-value\nnot a key value line\n"
    )
    monkeypatch.delenv("FROM_FILE", raising=False)
    monkeypatch.setenv("ALREADY_SET", "env-value")

    load_env_file(str(env_file))

    import os

    assert os.environ["FROM_FILE"] == "file-value"
    assert os.environ["ALREADY_SET"] == "env-value"  # existing env always wins
    monkeypatch.delenv("FROM_FILE")


def test_load_env_file_missing_file_is_a_noop() -> None:
    load_env_file("/nonexistent/path/.env")  # must not raise
