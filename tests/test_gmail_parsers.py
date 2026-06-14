"""Unit tests for the Gmail per-retailer parsers and source skipping (task 4.6).

Covers the Nintendo eShop and Microsoft Store parsers turning representative raw
Gmail message dicts (matching the real receipt formats) into ``GameRecord``s,
robustness on malformed mail (returns ``[]``, never raises), and the source-level
skip contract: an unavailable ``GmailSource`` returns ``[]`` with a sanitized
``last_error`` rather than raising (Req 3.2, 3.5).

These are fast, network-free unit tests: raw message dicts are constructed
in-test with the same base64url body encoding the source decodes.
"""

from __future__ import annotations

import base64
from datetime import date

from models.game_record import GameRecord
from services.sources.gmail_source import (
    GmailSource,
    _parse_microsoft_store,
    _parse_nintendo,
)


def _b64url(text: str) -> str:
    """Encode body text the way Gmail does (URL-safe base64), as ``_decode_body`` expects."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _raw_message(subject: str | None, date_header: str | None, body: str) -> dict:
    """Build a raw Gmail message dict matching the shape ``_header``/``_decode_body`` expect."""
    headers: list[dict] = []
    if subject is not None:
        headers.append({"name": "Subject", "value": subject})
    if date_header is not None:
        headers.append({"name": "Date", "value": date_header})
    return {
        "payload": {
            "headers": headers,
            "body": {"data": _b64url(body)},
        }
    }


# A representative RFC 2822 Date header reused across cases.
DATE_HEADER = "Tue, 10 Jun 2025 09:15:00 +0000"
EXPECTED_DATE = date(2025, 6, 10)


def _nintendo_receipt(title: str) -> str:
    """A representative Nintendo eShop purchase receipt body (Item section)."""
    return (
        "Receipt\n--------------------\n"
        "Purchase Information\n--------------------\n"
        "Item\n"
        "1x\n"
        f"{title}\n"
        "Device Type\n"
        "Nintendo Switch / Nintendo Switch 2\n"
        "Total Charge (incl. VAT)\n52,50 kr.\n"
    )


def _microsoft_order(*items: str, extra: str = "") -> str:
    """A representative Microsoft order receipt body (pipe-delimited item table)."""
    rows = "\n".join(f"                 | {item} | 1     | DKK 29.75" for item in items)
    return (
        "Microsoft\nThank you for shopping with us\n"
        "** Order details\n----------------\n"
        "Item description | Quantity | Price\n"
        f"{rows}\n"
        f"{extra}\n"
        "** Payment\n----------\nA VAT invoice will be available.\n"
    )


class TestNintendoParser:
    """``_parse_nintendo`` turns an eShop receipt into a Switch GameRecord."""

    def test_parses_title_platform_source_and_date(self) -> None:
        raw = _raw_message(
            subject="Thank you for your Nintendo eShop purchase",
            date_header=DATE_HEADER,
            body=_nintendo_receipt("Arcade Archives SUNSETRIDERS"),
        )

        records = _parse_nintendo(raw)

        assert len(records) == 1
        record = records[0]
        assert record.title == "Arcade Archives SUNSETRIDERS"
        assert record.platforms == ["Nintendo Switch"]
        assert record.source == "gmail"
        assert record.purchase_date == EXPECTED_DATE

    def test_returns_empty_for_statement_without_item_section(self) -> None:
        # A "Transaction Statement" funds receipt has no Item section -> no game.
        raw = _raw_message(
            subject="Transaction Statement",
            date_header=DATE_HEADER,
            body="Transaction Statement\nFunds Added\n52,50 kr.\nRemaining Balance\n0 kr.\n",
        )

        assert _parse_nintendo(raw) == []

    def test_does_not_raise_on_empty_payload(self) -> None:
        # Malformed input must degrade to [], never raise.
        assert _parse_nintendo({}) == []


class TestMicrosoftStoreParser:
    """``_parse_microsoft_store`` parses the order item table into GameRecords."""

    def test_parses_single_item_title_source_and_date(self) -> None:
        raw = _raw_message(
            subject="Your Microsoft order #123 has been processed",
            date_header=DATE_HEADER,
            body=_microsoft_order("Zombies Ate My Neighbors and Ghoul Patrol"),
        )

        records = _parse_microsoft_store(raw)

        assert len(records) == 1
        record = records[0]
        assert record.title == "Zombies Ate My Neighbors and Ghoul Patrol"
        assert record.source == "gmail"
        assert record.purchase_date == EXPECTED_DATE
        assert record.platforms == ["Xbox"]  # default when no platform token in body

    def test_parses_multiple_line_items(self) -> None:
        raw = _raw_message(
            subject="Your Microsoft order #456 has been processed",
            date_header=DATE_HEADER,
            body=_microsoft_order("Halo Infinite", "Forza Horizon 5"),
        )

        records = _parse_microsoft_store(raw)

        assert [r.title for r in records] == ["Halo Infinite", "Forza Horizon 5"]

    def test_skips_header_and_publisher_rows(self) -> None:
        raw = _raw_message(
            subject="Your Microsoft order #789 has been processed",
            date_header=DATE_HEADER,
            body=_microsoft_order(
                "Psychonauts 2", extra="                 | By: Xbox Game Studios |   |"
            ),
        )

        records = _parse_microsoft_store(raw)

        # Only the product row becomes a record; header and "By:" rows are skipped.
        assert [r.title for r in records] == ["Psychonauts 2"]

    def test_platform_parsed_from_body_tokens(self) -> None:
        raw = _raw_message(
            subject="Your Microsoft order #321 has been processed",
            date_header=DATE_HEADER,
            body=_microsoft_order("Age of Empires IV", extra="Play on Windows PC."),
        )

        records = _parse_microsoft_store(raw)

        assert records[0].platforms == ["Windows"]

    def test_returns_empty_when_no_item_table(self) -> None:
        raw = _raw_message(
            subject="We noticed a new sign-in",
            date_header=DATE_HEADER,
            body="A new sign-in to your Microsoft account was detected.",
        )

        assert _parse_microsoft_store(raw) == []

    def test_does_not_raise_on_empty_payload(self) -> None:
        assert _parse_microsoft_store({}) == []


class TestSourceSkipping:
    """An unavailable ``GmailSource`` is skipped without error (Req 3.5, 10.5)."""

    def _unauthenticated_source(self, tmp_path) -> GmailSource:  # type: ignore[no-untyped-def]
        # Point at paths that do not exist so authentication cannot succeed and
        # no real Google client is ever built (no network).
        return GmailSource(
            credentials_path=str(tmp_path / "missing_credentials.json"),
            token_path=str(tmp_path / "missing_token.json"),
            redirect_uri="http://localhost",
        )

    def test_fetch_records_returns_empty_when_auth_unavailable(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        source = self._unauthenticated_source(tmp_path)

        assert source.fetch_records() == []

    def test_is_available_is_false_when_token_missing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        source = self._unauthenticated_source(tmp_path)

        # is_available lazily attempts auth; with no token it must report False.
        assert source.is_available() is False

    def test_authenticate_returns_false_without_raising(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        source = self._unauthenticated_source(tmp_path)

        assert source.authenticate() is False

    def test_last_error_is_sanitized_string(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        source = self._unauthenticated_source(tmp_path)

        source.authenticate()

        assert isinstance(source.last_error, str)
        assert source.last_error
        # Sanitized: must not leak the underlying filesystem path.
        assert "missing_token.json" not in source.last_error

    def test_fetch_records_returns_list_of_game_records_type(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        source = self._unauthenticated_source(tmp_path)

        result = source.fetch_records()

        assert isinstance(result, list)
        assert all(isinstance(item, GameRecord) for item in result)
