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
from typing import Any, Callable, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from models.game_record import GameRecord
from services.error_handler import ErrorHandler

# A parser turns one matched email payload into a GameRecord (or None).
EmailParser = Callable[[dict], Optional[GameRecord]]


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
        "microsoft_store": "account-security-noreply@accountprotection.microsoft.com",
    }

    def __init__(
        self,
        credentials_path: str,
        token_path: str,
        redirect_uri: str,
        parser_registry: dict[str, EmailParser] | None = None,
    ) -> None:
        self._credentials_path = credentials_path
        self._token_path = token_path
        self._redirect_uri = redirect_uri
        self._parsers = parser_registry or self._default_parsers()
        self._service: Any = None  # lazily-built googleapiclient resource
        self._available = False
        self._last_error: str | None = None

    def is_available(self) -> bool:
        """True once a read-only fetch has succeeded (Req 3.6)."""
        return self._available

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
            self._available = False
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
                    record = parser(raw)  # extract minimal contract fields
                    if record is not None:
                        records.append(record)
                    # raw is discarded here — never stored (Req 4.2)
            self._available = True
            return records
        except Exception as exc:  # auth/fetch failure (Req 10.4)
            self._available = False
            self._last_error = ErrorHandler.sanitize_error(exc, "gmail")
            return []

    def _search(self, query: str) -> list[dict]:
        """Run the restricted query and return raw message dicts (kept local).

        Uses ``users.messages.list`` to find matching ids, then
        ``users.messages.get`` to retrieve each payload. The returned dicts are
        consumed by a parser and discarded — they are never stored (Req 4.2).
        """
        if self._service is None:
            return []
        messages = self._service.users().messages()
        listing = messages.list(userId="me", q=query).execute()
        raw_messages: list[dict] = []
        for meta in listing.get("messages", []):
            message_id = meta.get("id")
            if not message_id:
                continue
            raw_messages.append(messages.get(userId="me", id=message_id, format="full").execute())
        return raw_messages

    @staticmethod
    def _default_parsers() -> dict[str, EmailParser]:
        """Per-retailer parser registry keyed by ``KNOWN_SENDERS`` id."""
        return {
            "nintendo": _parse_nintendo,
            "microsoft_store": _parse_microsoft_store,
        }


# --- Per-retailer parsers (raw email dict -> GameRecord | None) ---------------
#
# Each parser pulls only the contract fields (title, platforms, purchase_date)
# from a raw Gmail message and returns a GameRecord with ``source="gmail"``.
# Raw content is read here and never retained. Parsers are robust: they return
# None when a title cannot be extracted and never raise (Req 4.2).


def _parse_nintendo(raw: dict) -> GameRecord | None:
    """Parse a Nintendo eShop confirmation into a GameRecord (Switch family)."""
    try:
        payload = raw.get("payload", {})
        title = _extract_title(_header(payload, "Subject"), _decode_body(payload))
        if not title:
            return None
        return GameRecord(
            title=title,
            platforms=["Nintendo Switch"],
            source="gmail",
            purchase_date=_parse_purchase_date(_header(payload, "Date")),
        )
    except Exception:
        return None


def _parse_microsoft_store(raw: dict) -> GameRecord | None:
    """Parse a Microsoft Store confirmation into a GameRecord (Xbox/Windows)."""
    try:
        payload = raw.get("payload", {})
        body = _decode_body(payload)
        subject = _header(payload, "Subject")
        title = _extract_title(subject, body)
        if not title:
            return None
        return GameRecord(
            title=title,
            platforms=_extract_microsoft_platforms(f"{subject or ''}\n{body}"),
            source="gmail",
            purchase_date=_parse_purchase_date(_header(payload, "Date")),
        )
    except Exception:
        return None


# --- Payload extraction helpers (read raw, then discard) ----------------------

_TITLE_LINE = re.compile(r"^\s*title\s*[:\-]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_SUBJECT_PREFIXES = re.compile(
    r"^\s*(your\s+receipt\s+for|order\s+confirmation|receipt\s+for|"
    r"thank\s+you\s+for\s+your\s+purchase\s+of|your\s+purchase\s+of|purchase\s+of)\s*[:\-]?\s*",
    re.IGNORECASE,
)


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


def _extract_title(subject: str | None, body: str) -> str | None:
    """Extract the purchased game's title from the body, falling back to subject.

    Returns ``None`` when no usable title can be found.
    """
    match = _TITLE_LINE.search(body)
    if match:
        title = match.group(1).strip()
        if title:
            return title
    if subject:
        cleaned = _SUBJECT_PREFIXES.sub("", subject).strip().strip("\"'").strip()
        if cleaned:
            return cleaned
    return None


def _extract_microsoft_platforms(text: str) -> list[str]:
    """Parse owned platform(s) from a Microsoft Store email's text."""
    lowered = text.lower()
    platforms: list[str] = []
    if "xbox" in lowered:
        platforms.append("Xbox")
    if "windows" in lowered or "pc" in lowered:
        platforms.append("Windows")
    return platforms or ["Xbox"]


def _parse_purchase_date(date_header: str | None) -> date | None:
    """Reduce an RFC 2822 ``Date`` header to a date (no time/timezone retained)."""
    if not date_header:
        return None
    try:
        parsed = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return None
    return parsed.date() if parsed is not None else None
