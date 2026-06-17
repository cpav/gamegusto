"""Read-only Gmail import of purchase-confirmation emails (``source="gmail"``).

Least privilege by construction: this source requests ONLY the
``gmail.readonly`` scope (Req 4.1) and builds its search query SOLELY from the
``KNOWN_SENDERS`` registry, so unrelated mail is never matched (Req 3.3, 4.3).
Each matched email is parsed down to the contract fields (title, platforms,
purchase_date) and the raw email content (bodies, headers, snippets, message
ids) is discarded immediately after parsing — it is never stored on the record
or on the source (Req 4.2).

Like every record source, :meth:`fetch_records` never raises to its caller: on
any auth/fetch failure it records a sanitized error and returns ``[]`` so the
rest of the library keeps working (Req 10.4).
"""

from __future__ import annotations

import base64
import re
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any, Callable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from models.game_record import GameRecord
from services.error_handler import ErrorHandler

# A parser turns one matched email payload into zero or more GameRecords. A single
# email may list multiple purchased items (e.g. a Microsoft order), so parsers
# return a list (empty when the email is not a parseable game purchase).
EmailParser = Callable[[dict], list[GameRecord]]


class GmailSource:
    """Read-only Gmail import of purchase-confirmation emails (source='gmail').

    Least privilege: requests ONLY ``gmail.readonly`` (Req 4.1). Searches ONLY
    known purchase-confirmation senders (Req 3.3, 4.3). Extracts
    title/platform/purchase_date and discards raw email content (Req 4.2).
    """

    name = "gmail"

    # Read-only scope only — no broader permission is ever requested (Req 4.1).
    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    # Known purchase-confirmation senders. The Gmail query is built ONLY from this
    # registry, so unrelated mail is never matched (Req 3.3, 4.3). Extensible:
    # adding a retailer means adding a sender here plus a parser in the registry.
    KNOWN_SENDERS: dict[str, str] = {
        "nintendo": "no-reply@accounts.nintendo.com",
        "microsoft_store": "microsoft-noreply@microsoft.com",
    }

    def __init__(
        self,
        token_path: str,
        parser_registry: dict[str, EmailParser] | None = None,
    ) -> None:
        self._token_path = token_path
        self._parsers = parser_registry or self._default_parsers()
        self._service: Any = None  # lazily-built googleapiclient resource
        self._last_error: str | None = None

    def is_available(self) -> bool:
        """True when a read-only Gmail service can be established (Req 3.6).

        Lazily authenticates (loading the cached read-only token) so the library
        assembly's availability gate reflects real connectability rather than
        whether a fetch has already completed.
        """
        if self._service is None:
            self.authenticate()
        return self._service is not None

    @property
    def last_error(self) -> str | None:
        """The most recent sanitized failure message, if any (Req 10.4)."""
        return self._last_error

    def authenticate(self) -> bool:
        """Load the cached read-only token, refreshing if needed, and build the
        Gmail service (Req 4.1).

        Returns ``False`` (never raises) when no valid token is available, so an
        unconnected source is simply skipped by the library assembly.
        """
        try:
            creds = Credentials.from_authorized_user_file(self._token_path, self.SCOPES)
            if not creds.valid:
                if creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    return False
            self._service = build("gmail", "v1", credentials=creds)
            return True
        except Exception as exc:
            self._last_error = ErrorHandler.sanitize_error(exc, "gmail")
            return False

    def fetch_records(self) -> list[GameRecord]:
        """Search only known senders, parse matches into GameRecords with
        ``source='gmail'`` and ``purchase_date``, and discard raw content
        (Req 3.3, 4.2, 4.3).

        On failure returns ``[]`` (the import degrades; other sources continue).
        """
        try:
            if self._service is None and not self.authenticate():
                return []
            records: list[GameRecord] = []
            for sender_id, sender_addr in self.KNOWN_SENDERS.items():
                parser = self._parsers.get(sender_id)
                if parser is None:
                    continue
                for raw in self._search(f"from:{sender_addr}"):  # restricted query
                    records.extend(parser(raw))  # zero or more contract records
                    # raw is discarded here — never stored (Req 4.2)
            return records
        except Exception as exc:  # auth/fetch failure (Req 10.4)
            self._last_error = ErrorHandler.sanitize_error(exc, "gmail")
            return []

    def _search(self, query: str) -> list[dict]:
        """Run the restricted query and return raw message dicts (kept local).

        Uses ``users.messages.list`` (paginated) to find matching ids, then
        ``users.messages.get`` to retrieve each payload. The returned dicts are
        consumed by a parser and discarded — they are never stored (Req 4.2).
        """
        if self._service is None:
            return []
        messages = self._service.users().messages()
        raw_messages: list[dict] = []
        page_token: str | None = None
        while True:
            listing = messages.list(userId="me", q=query, pageToken=page_token).execute()
            for meta in listing.get("messages", []):
                message_id = meta.get("id")
                if not message_id:
                    continue
                raw_messages.append(
                    messages.get(userId="me", id=message_id, format="full").execute()
                )
            page_token = listing.get("nextPageToken")
            if not page_token:
                return raw_messages

    @staticmethod
    def _default_parsers() -> dict[str, EmailParser]:
        """Per-retailer parser registry keyed by ``KNOWN_SENDERS`` id."""
        return {
            "nintendo": _parse_nintendo,
            "microsoft_store": _parse_microsoft_store,
        }


# --- Per-retailer parsers (raw email dict -> list[GameRecord]) ----------------
#
# Each parser pulls only the contract fields (title, platforms, purchase_date)
# from a raw Gmail message and returns zero or more GameRecords with
# ``source="gmail"``. A single email may contain several purchased items. Raw
# content is read here and never retained. Parsers are robust: they return an
# empty list when nothing parseable is found and never raise (Req 4.2).


def _parse_nintendo(raw: dict) -> list[GameRecord]:
    """Parse a Nintendo eShop purchase confirmation into GameRecords (Switch family).

    Returns ``[]`` for non-purchase Nintendo mail (e.g. "Transaction Statement"
    funds receipts), which have no Item section and therefore no game title.
    """
    try:
        payload = raw.get("payload", {})
        title = _extract_nintendo_title(_decode_body(payload))
        if not title:
            return []
        return [
            GameRecord(
                title=title,
                platforms=["Nintendo Switch"],
                source="gmail",
                purchase_date=_parse_purchase_date(_header(payload, "Date")),
            )
        ]
    except Exception:
        return []


def _parse_microsoft_store(raw: dict) -> list[GameRecord]:
    """Parse a Microsoft order confirmation into GameRecords (one per line item)."""
    try:
        payload = raw.get("payload", {})
        body = _decode_body(payload)
        purchase_date = _parse_purchase_date(_header(payload, "Date"))
        platforms = _extract_microsoft_platforms(body)
        return [
            GameRecord(
                title=title,
                platforms=list(platforms),
                source="gmail",
                purchase_date=purchase_date,
            )
            for title in _extract_microsoft_titles(body)
        ]
    except Exception:
        return []


# --- Payload extraction helpers (read raw, then discard) ----------------------

# A line giving the purchased quantity, e.g. "1x" / "2 x", inside the Item section.
_ITEM_QTY = re.compile(r"^\d+\s*x$", re.IGNORECASE)


def _header(payload: dict, name: str) -> str | None:
    """Return the value of a header (case-insensitive) from a Gmail payload."""
    for header in payload.get("headers", []):
        if str(header.get("name", "")).lower() == name.lower():
            value = header.get("value")
            return value if isinstance(value, str) else None
    return None


def _decode_body(payload: dict) -> str:
    """Decode a Gmail payload's text, walking MIME parts (base64url) recursively."""
    parts = payload.get("parts")
    if parts:
        return "\n".join(text for text in (_decode_body(part) for part in parts) if text)
    data = payload.get("body", {}).get("data")
    if not data:
        return ""
    try:
        # Gmail uses URL-safe base64; pad generously so decoding never fails.
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_nintendo_title(body: str) -> str | None:
    """Extract the purchased title from a Nintendo eShop receipt's Item section.

    Real eShop receipts list the game under an ``Item`` header followed by a
    quantity line (e.g. ``1x``) and then the product name. Non-purchase mail
    (e.g. "Transaction Statement" funds receipts) has no Item section, so this
    returns ``None`` and those emails are skipped.
    """
    lines = [line.strip() for line in body.splitlines()]
    for index, line in enumerate(lines):
        if line.lower() != "item":
            continue
        cursor = index + 1
        while cursor < len(lines) and (not lines[cursor] or _ITEM_QTY.match(lines[cursor])):
            cursor += 1
        if cursor < len(lines) and lines[cursor]:
            return lines[cursor]
        return None
    return None


def _extract_microsoft_titles(body: str) -> list[str]:
    """Extract purchased item titles from a Microsoft order's "Order details" table.

    Microsoft order receipts list items in a pipe-delimited table
    (``| <title> | <qty> | <price> |``) within the Order details section. The
    title is the first non-empty cell of a product row; the header row and the
    publisher ("By: …") rows are skipped. Returns one title per line item.
    """
    lines = body.splitlines()
    start, end = 0, len(lines)
    for index, line in enumerate(lines):
        lowered = line.lower()
        if "order details" in lowered:
            start = index
        elif "payment" in lowered and index > start:
            end = index
            break

    titles: list[str] = []
    for line in lines[start:end]:
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.split("|") if cell.strip()]
        if len(cells) < 2:  # product rows have title + quantity/price cells
            continue
        first = cells[0]
        if first.lower().startswith("by:") or "item description" in first.lower():
            continue
        titles.append(first)
    return titles


def _extract_microsoft_platforms(text: str) -> list[str]:
    """Parse owned platform(s) from a Microsoft Store email's text.

    Microsoft Store purchases are owned on the user's Xbox Series X/S console
    (and on PC for Play Anywhere titles). The Xbox label is reported as
    "Xbox Series X/S"; family-aware matching still covers older "Xbox One" /
    bare "Xbox" availability strings from enrichment.
    """
    lowered = text.lower()
    platforms: list[str] = []
    if "xbox" in lowered:
        platforms.append("Xbox Series X/S")
    if "windows" in lowered or "pc" in lowered:
        platforms.append("PC")
    return platforms or ["Xbox Series X/S"]


def _parse_purchase_date(date_header: str | None) -> date | None:
    """Reduce an RFC 2822 ``Date`` header to a date (no time/timezone retained)."""
    if not date_header:
        return None
    try:
        parsed = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return None
    return parsed.date() if parsed is not None else None
