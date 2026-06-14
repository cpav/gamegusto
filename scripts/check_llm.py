"""Live check that the Bedrock base model (Claude Sonnet) is wired and reachable.

Makes ONE real Converse call through :class:`~services.bedrock_service.BedrockService`
using the configured ``BEDROCK_MODEL_ID`` / ``AWS_REGION`` and the extended-thinking
budget. There is no mock and no fallback: if the model is misconfigured or
unreachable, this prints the sanitized error and exits non-zero.

Usage (inside the venv, with AWS credentials + BEDROCK_MODEL_ID configured):
    python scripts/check_llm.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config, ConfigError, load_env_file  # noqa: E402
from services.bedrock_service import BedrockService, BedrockServiceError  # noqa: E402


def main() -> None:
    """Invoke the model once and report whether it is live."""
    load_env_file()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        sys.exit(1)

    print(f"Model:  {config.bedrock_model_id}")
    print(f"Region: {config.aws_region}")
    print(f"Thinking budget: {config.bedrock_reasoning_budget_tokens} tokens")
    print("-" * 60)

    service = BedrockService(config)
    try:
        reply = service.invoke_conversational(
            "Reply with a single short sentence confirming you are reachable.",
            session_id="llm-healthcheck",
        )
    except BedrockServiceError as exc:
        print(f"LLM CHECK FAILED: {exc}")
        sys.exit(1)

    print("LLM CHECK OK. Model replied:")
    print(f"  {reply.strip()}")
    sys.exit(0)


if __name__ == "__main__":
    main()
