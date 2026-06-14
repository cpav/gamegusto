"""Unit tests for the Gmail per-retailer parsers and source skipping (task 4.6).

Covers the Nintendo eShop and Microsoft Store parsers turning representative raw
Gmail message dicts into correct ``GameRecord``s, robustness on malformed mail
(returns ``None``, never raises), and the source-level skip contract: an
unavailable ``GmailSource`` returns ``[]`` with a sanitized ``last_error`` rather
than raising (Req 3.3, 3.6).

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


class TestNintendoParser:
    """``_parse_nintendo`` turns an eShop confirmation into a Switch GameRecord."""

    def test_parses_title_platform_source_and_date(self) -> None:
        raw = _raw_message(
            subject="Your receipt for your Nintendo eShop purchase",
            date_header=DATE_HEADER,
            body=(
                "Thank you for your purchase!\n"
                "Title: The Legend of Zelda: Tears of the Kingdom\n"
                "Total: $69.99"
            ),
        )

        record = _parse_nintendo(raw)

        assert record is not None
        assert record.title == "The Legend of Zelda: Tears of the Kingdom"
        assert record.platforms == ["Nintendo Switch"]
        assert record.source == "gmail"
        assert record.purchase_date == EXPECTED_DATE

    def test_returns_none_when_no_title_and_no_subject(self) -> None:
        # No "Title:" line and no subject to fall back to -> no extractable title.
        raw = _raw_message(
            subject=None,
            date_header=DATE_HEADER,
            body="Your account statement is ready.",
        )

        assert _parse_nintendo(raw) is None

    def test_does_not_raise_on_empty_payload(self) -> None:
        # Malformed input must degrade to None, never raise.
        assert _parse_nintendo({}) is None


class TestMicrosoftStoreParser:
    """``_parse_microsoft_store`` parses Xbox/Windows platform from the email text."""

    def test_parses_xbox_platform_from_body(self) -> None:
        raw = _raw_message(
            subject="Order confirmation",
            date_header=DATE_HEADER,
            body="Title: Halo Infinite\nPlatform: Xbox Series X|S\nThanks for your order.",
        )

        record = _parse_microsoft_store(raw)

        assert record is not None
        assert record.title == "Halo Infinite"
        assert record.platforms == ["Xbox"]
        assert record.source == "gmail"
        assert record.purchase_date == EXPECTED_DATE

    def test_parses_windows_platform_when_pc_mentioned(self) -> None:
        raw = _raw_message(
            subject="Order confirmation",
            date_header=DATE_HEADER,
            body="Title: Age of Empires IV\nAvailable on Windows PC.",
        )

        record = _parse_microsoft_store(raw)

        assert record is not None
        assert record.title == "Age of Empires IV"
        assert record.platforms == ["Windows"]
        assert record.source == "gmail"
        assert record.purchase_date == EXPECTED_DATE

    def test_parses_both_platforms_when_xbox_and_windows_mentioned(self) -> None:
        raw = _raw_message(
            subject="Order confirmation",
            date_header=DATE_HEADER,
            body="Title: Forza Horizon 5\nPlay on Xbox and Windows PC.",
        )

        record = _parse_microsoft_store(raw)

        assert record is not None
        assert record.platforms == ["Xbox", "Windows"]

    def test_title_falls_back_to_cleaned_subject(self) -> None:
        # No "Title:" line in the body -> the subject prefix is stripped to a title.
        raw = _raw_message(
            subject="Your receipt for: Sea of Thieves",
            date_header=DATE_HEADER,
            body="Enjoy your game on Xbox.",
        )

        record = _parse_microsoft_store(raw)

        assert record is not None
        assert record.title == "Sea of Thieves"
        assert record.platforms == ["Xbox"]

    def test_returns_none_when_no_title_extractable(self) -> None:
        # No "Title:" line and no subject fallback -> parser yields None (never raises).
        raw = _raw_message(
            subject=None,
            date_header=DATE_HEADER,
            body="We noticed a new sign-in.",
        )

        assert _parse_microsoft_store(raw) is None

    def test_does_not_raise_on_empty_payload(self) -> None:
        assert _parse_microsoft_store({}) is None


class TestSourceSkipping:
    """An unavailable ``GmailSource`` is skipped without error (Req 3.6, 10.4)."""

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

    def test_is_available_is_false_after_failed_fetch(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        source = self._unauthenticated_source(tmp_path)

        source.fetch_records()

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
        # The source-level contract: callers always get a list (here empty), so the
        # library assembly can keep iterating other sources without special-casing.
        source = self._unauthenticated_source(tmp_path)

        result = source.fetch_records()

        assert isinstance(result, list)
        assert all(isinstance(item, GameRecord) for item in result)
