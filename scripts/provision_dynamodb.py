"""Provision the GameGusto DynamoDB table on AWS.

Creates the single table used by :class:`~services.dynamodb_memory_client.DynamoDBMemoryClient`
with a composite primary key (``pk`` string, ``sk`` string) and on-demand
billing. Idempotent: if the table already exists, it reports and exits 0.

Table name and region come from the environment via ``config.Config``
(``DYNAMODB_TABLE_NAME``, ``AWS_REGION``). No secrets are printed.

Usage (inside the venv, with AWS credentials configured):
    python scripts/provision_dynamodb.py
"""

from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config, ConfigError, load_env_file  # noqa: E402


def main() -> None:
    """Create the DynamoDB table if it does not already exist."""
    load_env_file()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        sys.exit(1)

    client = boto3.client("dynamodb", region_name=config.aws_region)
    table_name = config.dynamodb_table_name

    try:
        client.describe_table(TableName=table_name)
        print(f"Table '{table_name}' already exists in {config.aws_region}. Nothing to do.")
        return
    except client.exceptions.ResourceNotFoundException:
        pass
    except ClientError as exc:
        print(f"Failed to check table existence ({type(exc).__name__}).")
        sys.exit(1)

    print(f"Creating table '{table_name}' in {config.aws_region} (on-demand billing)...")
    client.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=table_name)
    print(f"Table '{table_name}' is ACTIVE.")


if __name__ == "__main__":
    main()
