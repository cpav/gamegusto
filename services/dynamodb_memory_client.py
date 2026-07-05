"""DynamoDB-backed implementation of the ``MemoryClient`` protocol.

This is the concrete persistence layer behind :class:`~services.memory_service.MemoryService`.
It stores everything for one user under a single-table design so the manual-fill
UI path, the Gmail import, and the agent all read and write the same store:

* ``PK = USER#<user_id>``
* ``SK = DOC#<key>``            -> keyed documents (records, platform list)
* ``SK = EVENT#<key>#<ts>#<id>`` -> append-only session log (sortable, newest-first)

DynamoDB's document API rejects ``float`` and requires ``Decimal`` for numbers,
so values are converted to ``Decimal`` on write and back to ``int``/``float`` on
read at this boundary; callers keep working with plain Python types.

Scale note: each keyed document (e.g. the whole game library under
``DOC#records``) is ONE DynamoDB item, and items are capped at 400KB — roughly
400-800 enriched game records. Fine for a personal library; if the library ever
approaches that, split records across per-game items (``SK = REC#<dedup_key>``)
behind this same client interface.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

DOC_PREFIX = "DOC#"
EVENT_PREFIX = "EVENT#"


def _pk(user_id: str) -> str:
    """Partition key for a user."""
    return f"USER#{user_id}"


def _to_dynamo(value: Any) -> Any:
    """Recursively convert Python values to DynamoDB-safe types (float -> Decimal)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _to_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_dynamo(item) for item in value]
    return value


def _from_dynamo(value: Any) -> Any:
    """Recursively convert DynamoDB types back to plain Python (Decimal -> int/float)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _from_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(item) for item in value]
    return value


class DynamoDBMemoryClient:
    """Stores GameGusto memory in a single DynamoDB table (no network in tests).

    A boto3 ``Table`` resource can be injected for testing; otherwise one is
    created lazily for ``table_name`` in ``region_name``.
    """

    def __init__(
        self,
        table_name: str,
        region_name: str | None = None,
        table: Any | None = None,
    ) -> None:
        """Build the client around ``table_name``; inject ``table`` to avoid AWS."""
        if table is None:
            import boto3

            table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)
        self._table = table

    def get_value(self, user_id: str, key: str) -> dict[str, Any] | None:
        """Return the document stored under ``key`` for ``user_id``, or ``None``."""
        response = self._table.get_item(Key={"pk": _pk(user_id), "sk": f"{DOC_PREFIX}{key}"})
        item = response.get("Item")
        if not item or "value" not in item:
            return None
        converted = _from_dynamo(item["value"])
        return converted if isinstance(converted, dict) else None

    def put_value(self, user_id: str, key: str, value: dict[str, Any]) -> None:
        """Persist ``value`` under ``key`` for ``user_id``, replacing any prior value."""
        self._table.put_item(
            Item={
                "pk": _pk(user_id),
                "sk": f"{DOC_PREFIX}{key}",
                "value": _to_dynamo(value),
            }
        )

    def append_event(self, user_id: str, key: str, event: dict[str, Any]) -> None:
        """Append ``event`` to the log under ``key`` for ``user_id``.

        The sort key embeds a UTC timestamp plus a uuid suffix so events order
        chronologically and never collide within the same millisecond.
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        self._table.put_item(
            Item={
                "pk": _pk(user_id),
                "sk": f"{EVENT_PREFIX}{key}#{timestamp}#{uuid.uuid4().hex}",
                "event": _to_dynamo(event),
            }
        )

    def list_events(self, user_id: str, key: str, limit: int) -> list[dict[str, Any]]:
        """Return up to ``limit`` most-recent events for ``key``, newest first."""
        from boto3.dynamodb.conditions import Key

        response = self._table.query(
            KeyConditionExpression=(
                Key("pk").eq(_pk(user_id)) & Key("sk").begins_with(f"{EVENT_PREFIX}{key}#")
            ),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [
            _from_dynamo(item["event"]) for item in response.get("Items", []) if "event" in item
        ]
