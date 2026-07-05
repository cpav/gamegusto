"""Property-based tests for :class:`GmailSource` privacy and scoping (task 4.5).

Encodes three correctness properties from ``design.md`` that together guarantee
the Gmail import is least-privilege and leak-free:

* **Property 4 -- Gmail import restricts to known purchase-confirmation senders**
  (Req 3.3, 4.3): every Gmail search query the source issues is built SOLELY from
  the ``KNOWN_SENDERS`` registry (``from:<addr>`` with ``<addr>`` a known sender),
  so unrelated mail is never queried, fetched, or turned into a record.
* **Property 5 -- Gmail import retains only contract fields** (Req 4.2): for any
  raw email payload stuffed with arbitrary junk (snippet, message id, extra
  headers/keys, raw body text), the parsed ``GameRecord`` exposes only the
  canonical contract fields and none of that raw content leaks onto it.
* **Property 6 -- Gmail import requests read-only scope only** (Req 4.1):
  ``SCOPES`` is exactly ``{gmail.readonly}`` and never broadens, regardless of how
  the source is constructed.

Every test injects a fake Gmail service (or calls the parsers directly), so no
test ever touches real network or OAuth.
"""

from __future__ import annotations

import base64
import dataclasses
import string
from collections.abc import Callable
from typing import Any

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from models.game_record import GameRecord
from services.sources.gmail_source import (
    GmailSource,
    _parse_microsoft_store,
    _parse_nintendo,
)

# A clean RFC 2822 Date header used wherever a parseable date is wanted.
VALID_DATE = "Mon, 06 May 2024 10:00:00 +0000"

# Titles are drawn from a restricted alphabet that deliberately EXCLUDES the
# junk sentinel below, so any sentinel found on a record proves a real leak.
TITLE_ALPHABET = string.ascii_letters + string.digits + " .!?-"

# A distinctive marker embedded in every piece of raw (non-contract) content.
# It can never appear in a legitimately parsed title/platform/date, so its
# presence anywhere on a record means raw email content leaked through.
SENTINEL = "\uffffJUNK\uffff"

# The canonical GameRecord contract field set (data contract v1.0.0).
CONTRACT_FIELDS = {
    "title",
    "platforms",
    "source",
    "purchase_date",
    "genre",
    "estimated_playtime",
    "community_review",
    "platform_availability",
    "external_ids",
}


def _b64(text: str) -> str:
    """URL-safe base64 encode body text the way Gmail returns it."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# Fake Gmail service (captures every issued query; serves a generated mailbox)
# ---------------------------------------------------------------------------


class _Executable:
    """Mimics a googleapiclient request object exposing ``.execute()``."""

    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        return self._result


class _FakeMessages:
    """Stands in for ``service.users().messages()``.

    ``list`` records the ``q`` it is handed and returns the ids of mailbox
    entries whose sender matches the ``from:<addr>`` query. ``get`` records which
    ids were actually fetched and returns the crafted raw payload.
    """

    def __init__(
        self,
        mailbox: dict[str, dict[str, Any]],
        captured_queries: list[str],
        fetched_ids: list[str],
    ) -> None:
        self._mailbox = mailbox
        self._captured_queries = captured_queries
        self._fetched_ids = fetched_ids

    def list(self, userId: str, q: str, pageToken: str | None = None) -> _Executable:
        self._captured_queries.append(q)
        prefix = "from:"
        addr = q[len(prefix) :] if q.startswith(prefix) else None
        ids = [
            {"id": mid}
            for mid, entry in self._mailbox.items()
            if addr is not None and entry["sender"] == addr
        ]
        # Single page: no nextPageToken, so the source's pagination loop stops.
        return _Executable({"messages": ids})

    def get(self, userId: str, id: str, format: str) -> _Executable:
        self._fetched_ids.append(id)
        return _Executable(self._mailbox[id]["raw"])


class _FakeUsers:
    def __init__(self, messages: _FakeMessages) -> None:
        self._messages = messages

    def messages(self) -> _FakeMessages:
        return self._messages


class _FakeService:
    def __init__(self, messages: _FakeMessages) -> None:
        self._users = _FakeUsers(messages)

    def users(self) -> _FakeUsers:
        return self._users


def _confirmation_email(title: str, sender: str) -> dict[str, Any]:
    """A minimal purchase-confirmation payload parseable by either retailer parser.

    The body carries both a Nintendo-style Item section and a Microsoft-style
    single-row order table for the same ``title``, so whichever parser the sender
    maps to extracts exactly one record.
    """
    body = (
        "Item\n1x\n"
        f"{title}\n"
        "** Order details\n"
        "Item description | Quantity | Price\n"
        f"| {title} | 1 | DKK 10\n"
        "** Payment\n"
        "Thank you for your purchase."
    )
    return {
        "sender": sender,
        "raw": {
            "payload": {
                "headers": [
                    {"name": "From", "value": sender},
                    {"name": "Subject", "value": f"Order Confirmation: {title}"},
                    {"name": "Date", "value": VALID_DATE},
                ],
                "body": {"data": _b64(body)},
            }
        },
    }


# ---------------------------------------------------------------------------
# Property 4: Gmail import restricts to known purchase-confirmation senders
# ---------------------------------------------------------------------------

_KNOWN_ADDRS = list(GmailSource.KNOWN_SENDERS.values())

# An "unknown" sender is any address that is not in the registry.
_unknown_addr = (
    st.emails()
    .map(lambda e: e.lower())
    .filter(lambda e: e not in GmailSource.KNOWN_SENDERS.values())
)

_sender = st.one_of(st.sampled_from(_KNOWN_ADDRS), _unknown_addr)


def _make_source_with_mailbox(
    mailbox: dict[str, dict[str, Any]],
) -> tuple[GmailSource, list[str], list[str]]:
    """Build a GmailSource wired to a fake service serving ``mailbox``."""
    captured_queries: list[str] = []
    fetched_ids: list[str] = []
    messages = _FakeMessages(mailbox, captured_queries, fetched_ids)
    source = GmailSource(token_path="token.json")
    # Inject the fake resource directly so authenticate() (and the network) is
    # never touched.
    source._service = _FakeService(messages)
    return source, captured_queries, fetched_ids


@given(senders=st.lists(_sender, max_size=12))
@settings(deadline=None)
def test_queries_target_only_known_senders(senders: list[str]) -> None:
    """Every issued query is ``from:<known sender>`` and unknown mail is ignored.

    The source issues exactly one query per registry entry, never references an
    address outside the registry, only fetches mail from known senders, and
    produces one ``gmail`` record per known-sender email.

    **Validates: Requirements 3.3, 4.3**
    """
    mailbox: dict[str, dict[str, Any]] = {}
    for i, sender in enumerate(senders):
        mailbox[f"msg-{i}"] = _confirmation_email(f"Game {i}", sender)

    source, captured_queries, fetched_ids = _make_source_with_mailbox(mailbox)

    records = source.fetch_records()

    known = set(GmailSource.KNOWN_SENDERS.values())

    # Every captured query is built solely from a known sender address.
    for query in captured_queries:
        assert query.startswith("from:")
        assert query[len("from:") :] in known

    # The query set is exactly one per registry entry -- never more, never an
    # unknown sender.
    assert set(captured_queries) == {f"from:{addr}" for addr in known}

    # No unknown sender address is ever queried.
    for sender in senders:
        if sender not in known:
            assert f"from:{sender}" not in captured_queries

    # Only known-sender mail is ever fetched, and so every record comes from a
    # known sender (source == "gmail").
    for fetched in fetched_ids:
        assert mailbox[fetched]["sender"] in known
    assert all(record.source == "gmail" for record in records)

    # One record per known-sender email; unrelated mail produces nothing.
    known_email_count = sum(1 for s in senders if s in known)
    assert len(records) == known_email_count


@given(unknown=_unknown_addr)
def test_unknown_sender_alone_yields_no_records(unknown: str) -> None:
    """A mailbox of only unknown-sender mail is never matched or imported.

    **Validates: Requirements 3.3, 4.3**
    """
    mailbox = {"msg-0": _confirmation_email("Some Game", unknown)}
    source, captured_queries, fetched_ids = _make_source_with_mailbox(mailbox)

    records = source.fetch_records()

    assert records == []
    assert fetched_ids == []  # unknown mail is never even retrieved
    # Queries were still confined to the registry.
    assert set(captured_queries) == {f"from:{addr}" for addr in GmailSource.KNOWN_SENDERS.values()}


# ---------------------------------------------------------------------------
# Property 5: Gmail import retains only contract fields
# ---------------------------------------------------------------------------

_title = (
    st.text(alphabet=TITLE_ALPHABET, min_size=1, max_size=40)
    .map(str.strip)
    .filter(lambda s: bool(s))
)

# Junk text always carries the sentinel, so it is detectable wherever it lands.
_junk = st.text(max_size=20).map(lambda s: SENTINEL + s)


@st.composite
def _junk_payloads(draw: st.DrawFn) -> tuple[str, dict[str, Any]]:
    """A raw Gmail payload whose only parseable title is ``title`` and whose
    every other field is sentinel-marked junk."""
    title = draw(_title)

    n_headers = draw(st.integers(min_value=0, max_value=3))
    extra_headers = [
        {"name": f"X-Junk-{i}{SENTINEL}", "value": draw(_junk)} for i in range(n_headers)
    ]
    extra_keys = draw(
        st.dictionaries(
            st.text(max_size=5).map(lambda s: SENTINEL + s),
            _junk,
            max_size=3,
        )
    )

    # The clean title appears both in a Nintendo Item section and a Microsoft
    # order-table row, so either parser extracts exactly it; junk lives elsewhere.
    body = (
        "Item\n1x\n"
        f"{title}\n"
        "** Order details\n"
        "Item description | Quantity | Price\n"
        f"| {title} | 1 | DKK 10\n"
        "** Payment\n"
        f"{draw(_junk)}\ntrailing {draw(_junk)}"
    )

    raw: dict[str, Any] = {
        "id": draw(_junk),
        "snippet": draw(_junk),
        "threadId": draw(_junk),
        "historyId": draw(_junk),
        "internalDate": draw(_junk),
        "labelIds": [draw(_junk)],
        "payload": {
            "headers": [
                {"name": "Subject", "value": draw(_junk)},
                {"name": "Date", "value": VALID_DATE},
                *extra_headers,
            ],
            "body": {"data": _b64(body)},
        },
    }
    raw.update(extra_keys)
    return title, raw


_PARSERS: list[Callable[[dict], list[GameRecord]]] = [
    _parse_nintendo,
    _parse_microsoft_store,
]


@given(payload=_junk_payloads(), parser=st.sampled_from(_PARSERS))
@settings(deadline=None)
def test_parsed_record_retains_only_contract_fields(
    payload: tuple[str, dict[str, Any]],
    parser: Callable[[dict], list[GameRecord]],
) -> None:
    """No raw email content survives parsing; only contract fields remain.

    The parsed record is a ``GameRecord`` confined to the canonical contract
    fields, its title is exactly the parsed title, and none of the injected raw
    junk (snippet, message id, extra headers/keys, raw body) leaks anywhere onto
    the serialized record.

    **Validates: Requirements 4.2**
    """
    title, raw = payload

    records = parser(raw)

    assert len(records) == 1  # the payload always carries exactly one parseable title
    record = records[0]
    assert isinstance(record, GameRecord)
    assert record.source == "gmail"

    # The record is confined to the contract field set -- nothing extra exists.
    assert {f.name for f in dataclasses.fields(record)} == CONTRACT_FIELDS

    # Only the legitimately parsed title is retained; raw content is dropped.
    assert record.title == title
    assert record.genre is None
    assert record.estimated_playtime is None
    assert record.community_review is None
    assert record.platform_availability == []
    assert record.external_ids == {}

    # Definitive leak check: the sentinel (present in every raw junk value but
    # never in a parsed title/platform/date) must not appear anywhere on the
    # serialized record.
    serialized = repr(dataclasses.asdict(record))
    assert SENTINEL not in serialized


@given(parser=st.sampled_from(_PARSERS), junk=_junk)
def test_untitled_junk_payload_yields_no_record(
    parser: Callable[[dict], list[GameRecord]],
    junk: str,
) -> None:
    """A payload with no extractable title parses to ``[]`` (nothing stored).

    **Validates: Requirements 4.2**
    """
    raw: dict[str, Any] = {
        "id": junk,
        "snippet": junk,
        "payload": {
            "headers": [{"name": "Date", "value": VALID_DATE}],
            "body": {"data": _b64(junk)},
        },
    }
    # Guard: ensure the junk body cannot satisfy either parser's title extraction
    # (no standalone "item" line for Nintendo; no pipe row for Microsoft).
    assume("item" not in junk.lower())
    assume("|" not in junk)

    assert parser(raw) == []


# ---------------------------------------------------------------------------
# Property 6: Gmail import requests read-only scope only
# ---------------------------------------------------------------------------

READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# Substrings that mark a scope broader than read-only.
_BROADER_SCOPE_MARKERS = [
    "gmail.modify",
    "gmail.compose",
    "gmail.send",
    "gmail.insert",
    "gmail.settings",
    "gmail.labels",
    "mail.google.com",  # the full mailbox-access scope
]


def test_scopes_are_exactly_readonly() -> None:
    """``SCOPES`` is exactly the single read-only scope.

    **Validates: Requirements 4.1**
    """
    assert GmailSource.SCOPES == [READONLY_SCOPE]


def test_no_scope_grants_more_than_readonly() -> None:
    """No declared scope grants more than read-only access.

    **Validates: Requirements 4.1**
    """
    for scope in GmailSource.SCOPES:
        assert scope == READONLY_SCOPE
        assert "readonly" in scope
        for marker in _BROADER_SCOPE_MARKERS:
            assert marker not in scope


@given(token_path=st.text(max_size=40))
def test_construction_never_broadens_scope(token_path: str) -> None:
    """However the source is constructed, the requested scope stays read-only.

    **Validates: Requirements 4.1**
    """
    source = GmailSource(token_path=token_path)
    assert source.SCOPES == [READONLY_SCOPE]
