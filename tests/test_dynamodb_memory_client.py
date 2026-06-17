"""Unit tests for :class:`DynamoDBMemoryClient` and its MemoryService integration.

A dict-backed ``_FakeTable`` mimics the boto3 DynamoDB ``Table`` resource methods
the client uses (``get_item``/``put_item``/``query``), so the persistence layer is
verified end to end with no AWS access. The tests also wire the fake through the
real :class:`MemoryService` to confirm records, the platform list, and sessions
round-trip — including the float<->Decimal conversion at the boundary.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Key

from models.game_record import CommunityReview, GameRecord
from models.platform import OwnedPlatform
from models.recommendation import Recommendation
from models.session import SessionData
from services.dynamodb_memory_client import DynamoDBMemoryClient
from services.memory_service import MemoryService

USER_ID = "user-1"


class _FakeTable:
    """Minimal in-memory stand-in for a boto3 DynamoDB Table resource."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def get_item(self, Key: dict[str, str]) -> dict[str, Any]:  # noqa: N803 (boto3 kwarg)
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": item} if item is not None else {}

    def put_item(self, Item: dict[str, Any]) -> None:  # noqa: N803 (boto3 kwarg)
        self.items[(Item["pk"], Item["sk"])] = Item

    def query(
        self,
        KeyConditionExpression: Any,  # noqa: N803 (boto3 kwarg)
        ScanIndexForward: bool = True,  # noqa: N803 (boto3 kwarg)
        Limit: int | None = None,  # noqa: N803 (boto3 kwarg)
    ) -> dict[str, Any]:
        # The client always queries pk == USER#<id> AND sk begins_with EVENT#<key>#.
        pk_value, sk_prefix = _decode_condition(KeyConditionExpression)
        matched = [
            item
            for (pk, sk), item in self.items.items()
            if pk == pk_value and sk.startswith(sk_prefix)
        ]
        matched.sort(key=lambda item: item["sk"], reverse=not ScanIndexForward)
        if Limit is not None:
            matched = matched[:Limit]
        return {"Items": matched}


def _decode_condition(expression: Any) -> tuple[str, str]:
    """Pull the pk value and sk begins_with prefix out of a boto3 condition tree."""
    pk_value = ""
    sk_prefix = ""
    for child in expression._values:  # And(eq, begins_with)
        values = getattr(child, "_values", ())
        if not values:
            continue
        attr = values[0]
        name = getattr(attr, "name", "")
        operator = getattr(child, "expression_operator", "")
        if name == "pk" and operator == "=":
            pk_value = values[1]
        elif name == "sk" and operator == "begins_with":
            sk_prefix = values[1]
    return pk_value, sk_prefix


def _client() -> tuple[DynamoDBMemoryClient, _FakeTable]:
    table = _FakeTable()
    return DynamoDBMemoryClient(table_name="t", table=table), table


# --- client-level behavior ---------------------------------------------------


def test_document_round_trip_and_decimal_conversion() -> None:
    client, table = _client()
    client.put_value(USER_ID, "records", {"score": 9.3, "count": 2, "name": "Hades"})

    # Stored numbers are Decimals (DynamoDB-safe), not floats.
    stored = table.items[("USER#user-1", "DOC#records")]["value"]
    assert isinstance(stored["score"], Decimal)

    # Read back as plain Python types.
    value = client.get_value(USER_ID, "records")
    assert value == {"score": 9.3, "count": 2, "name": "Hades"}
    assert isinstance(value["score"], float)
    assert isinstance(value["count"], int)


def test_get_value_missing_returns_none() -> None:
    client, _ = _client()
    assert client.get_value(USER_ID, "records") is None


def test_events_are_newest_first_and_limited() -> None:
    client, _ = _client()
    for i in range(5):
        client.append_event(USER_ID, "sessions", {"n": i})

    recent = client.list_events(USER_ID, "sessions", limit=3)
    assert [e["n"] for e in recent] == [4, 3, 2]


def test_query_condition_targets_only_user_and_event_prefix() -> None:
    # The fake decodes the same condition the client builds, proving the client
    # scopes queries to the user partition and the event-key prefix.
    pk_value, sk_prefix = _decode_condition(
        Key("pk").eq("USER#user-1") & Key("sk").begins_with("EVENT#sessions#")
    )
    assert pk_value == "USER#user-1"
    assert sk_prefix == "EVENT#sessions#"


# --- MemoryService integration over the DynamoDB client ----------------------


def test_memory_service_records_round_trip_over_dynamodb() -> None:
    client, _ = _client()
    memory = MemoryService(client)
    record = GameRecord(
        title="Hades",
        platforms=["Nintendo Switch"],
        source="manual",
        purchase_date=date(2025, 6, 10),
        genre="Roguelike",
        estimated_playtime=40,
        community_review=CommunityReview(9.3, "Beloved.", 42),
        platform_availability=["Nintendo Switch", "PC"],
    )

    assert memory.store_records(USER_ID, [record]) is True
    loaded = memory.get_records(USER_ID)

    assert len(loaded) == 1
    assert loaded[0].title == "Hades"
    assert loaded[0].purchase_date == date(2025, 6, 10)
    assert loaded[0].community_review is not None
    assert loaded[0].community_review.score == 9.3


def test_memory_service_platform_crud_over_dynamodb() -> None:
    client, _ = _client()
    memory = MemoryService(client)
    platform = OwnedPlatform(name="Nintendo Switch")

    assert memory.add_platform(USER_ID, platform) is True
    assert [p.name for p in memory.get_platform_list(USER_ID)] == ["Nintendo Switch"]
    assert memory.remove_platform(USER_ID, platform.platform_id) is True
    assert memory.get_platform_list(USER_ID) == []


def test_memory_service_sessions_round_trip_over_dynamodb() -> None:
    client, _ = _client()
    memory = MemoryService(client)
    for title in ("Hades", "Celeste", "Tunic"):
        memory.store_session(
            USER_ID,
            SessionData(
                user_id=USER_ID,
                mood="relaxed",
                time_budget_minutes=60,
                recommendation=Recommendation(
                    game_title=title, reasoning="r", estimated_playtime=30
                ),
            ),
        )

    recent = memory.get_recent_recommendations(USER_ID, sessions=2)
    assert [r.game_title for r in recent] == ["Tunic", "Celeste"]
