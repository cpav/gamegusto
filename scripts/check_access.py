"""Preflight access-check for GameGusto's external services.

Validates connectivity and configuration for the services GameGusto depends on
and prints a clear PASS / FAIL / SKIPPED report:

* Required services (AWS / Bedrock, Tavily, DynamoDB) report PASS or FAIL.
* Optional services (Gmail) report SKIPPED when their environment variables
  are unset, otherwise PASS or FAIL.

The script never crashes on a service failure and never echoes secret values:
configuration is referenced by variable name only, and failures are reduced to
a sanitized reason. It exits non-zero when any required service fails; SKIPPED
optional services never cause a non-zero exit.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import StrEnum

import boto3
from botocore.config import Config as BotoConfig
from tavily import TavilyClient

# Ensure the repo root is importable when this script is run directly
# (e.g. ``python scripts/check_access.py``) so ``config`` can be reused.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config, ConfigError, load_env_file  # noqa: E402

# Short, bounded timeouts so the preflight check can never hang on a slow or
# unreachable endpoint.
_BOTO_CONFIG = BotoConfig(
    connect_timeout=5,
    read_timeout=5,
    retries={"max_attempts": 1},
)


class Status(StrEnum):
    """Outcome of a single service check."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class CheckResult:
    """The sanitized result of checking one service."""

    service: str
    status: Status
    detail: str
    required: bool


def _sanitize_failure(exc: Exception) -> str:
    """Reduce an exception to a safe, user-friendly reason.

    Only the exception type name is surfaced so that secrets, endpoints, and
    stack traces never reach the report.
    """
    return f"reachability check failed ({type(exc).__name__})"


def _load_config() -> tuple[Config | None, str | None]:
    """Load configuration, returning ``(config, error)``.

    A missing required variable yields ``(None, message)`` where the message
    names only the missing variable, never a value.
    """
    try:
        return Config.from_env(), None
    except ConfigError as exc:
        return None, str(exc)


def check_aws(config: Config | None, config_error: str | None) -> CheckResult:
    """Check AWS credentials and Bedrock client availability."""
    service = "AWS / Bedrock"
    if config is None:
        return CheckResult(
            service,
            Status.FAIL,
            f"configuration incomplete ({config_error})",
            required=True,
        )
    try:
        sts = boto3.client("sts", region_name=config.aws_region, config=_BOTO_CONFIG)
        sts.get_caller_identity()
        # Confirm the Bedrock runtime is reachable in the configured region.
        boto3.client(
            "bedrock-runtime",
            region_name=config.aws_region,
            config=_BOTO_CONFIG,
        )
    except Exception as exc:  # noqa: BLE001 - any failure is reported, never raised
        return CheckResult(service, Status.FAIL, _sanitize_failure(exc), required=True)
    return CheckResult(
        service,
        Status.PASS,
        f"credentials valid; region {config.aws_region}",
        required=True,
    )


def check_tavily(config: Config | None, config_error: str | None) -> CheckResult:
    """Check that the Tavily API key is present and accepted."""
    service = "Tavily"
    if config is None:
        return CheckResult(
            service,
            Status.FAIL,
            f"configuration incomplete ({config_error})",
            required=True,
        )
    try:
        client = TavilyClient(api_key=config.tavily_api_key)
        # Minimal reachability check; verifies the key is accepted.
        client.search("gamegusto preflight", max_results=1)
    except Exception as exc:  # noqa: BLE001 - any failure is reported, never raised
        return CheckResult(service, Status.FAIL, _sanitize_failure(exc), required=True)
    return CheckResult(service, Status.PASS, "API key accepted", required=True)


def check_dynamodb(config: Config | None, config_error: str | None) -> CheckResult:
    """Check that the DynamoDB table is reachable and ACTIVE."""
    service = "DynamoDB"
    if config is None:
        return CheckResult(
            service,
            Status.FAIL,
            f"configuration incomplete ({config_error})",
            required=True,
        )
    try:
        client = boto3.client("dynamodb", region_name=config.aws_region, config=_BOTO_CONFIG)
        described = client.describe_table(TableName=config.dynamodb_table_name)
        status = described["Table"]["TableStatus"]
    except Exception as exc:  # noqa: BLE001 - any failure is reported, never raised
        return CheckResult(service, Status.FAIL, _sanitize_failure(exc), required=True)
    if status != "ACTIVE":
        return CheckResult(service, Status.FAIL, f"table status is {status}", required=True)
    return CheckResult(service, Status.PASS, f"table '{config.dynamodb_table_name}' ACTIVE", True)


def check_gmail(config: Config | None) -> CheckResult:
    """Check optional Gmail configuration (skipped when the token is unset)."""
    service = "Gmail"
    if config is not None:
        token_path = config.gmail_token_path
    else:
        token_path = os.environ.get("GMAIL_TOKEN_PATH") or None
    if not token_path:
        return CheckResult(service, Status.SKIPPED, "GMAIL_TOKEN_PATH not set", required=False)
    if not os.path.isfile(token_path):
        return CheckResult(
            service,
            Status.FAIL,
            "cached token not found at GMAIL_TOKEN_PATH (run scripts/gmail_authorize.py)",
            required=False,
        )
    return CheckResult(service, Status.PASS, "cached token present", required=False)


def _print_report(results: list[CheckResult]) -> None:
    """Print a readable PASS / FAIL / SKIPPED report."""
    print("GameGusto access check")
    print("=" * 60)
    for group_required, heading in ((True, "Required"), (False, "Optional")):
        print(f"\n{heading}:")
        for result in (r for r in results if r.required is group_required):
            print(f"  [{result.status.value:<7}] {result.service:<14} {result.detail}")
    print("=" * 60)


def main() -> None:
    """Run all access checks, print the report, and set the exit code."""
    load_env_file()
    config, config_error = _load_config()
    results = [
        check_aws(config, config_error),
        check_tavily(config, config_error),
        check_dynamodb(config, config_error),
        check_gmail(config),
    ]
    _print_report(results)
    failed_required = [r for r in results if r.required and r.status is Status.FAIL]
    if failed_required:
        print(f"\nFAIL: {len(failed_required)} required service(s) unavailable.")
        sys.exit(1)
    print("\nOK: all required services available.")
    sys.exit(0)


if __name__ == "__main__":
    main()
