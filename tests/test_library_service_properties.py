"""Property-based tests for :class:`LibraryService` assembly (task 6.5).

Encodes two correctness properties from ``design.md``:

* **Property 1** — Dedup is precedence-aware and key-normalized
  (Validates: Requirements 2.3, 3.1, 3.5)
* **Property 2** — Source unavailability does not break assembly
  (Validates: Requirements 3.6, 10.4)

Everything is exercised against in-process fakes: ``FakeSource`` implements the
``RecordSource`` protocol, ``FakeMemory`` subclasses :class:`MemoryService` with
its two record methods overridden, and ``IdentityEnricher`` returns records
untouched. No network, AWS, Tavily, or Bedrock call is ever made, so
dedup/precedence is observed without enrichment noise.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.library_service import LibraryService
from models.game_record import GameRecord
from services.memory_service import MemoryService
from services.sources.base import RecordSource

USER_ID = "test-user"


# --- Fakes (no network) ---------------------------------------------------


class FakeSource:
    """In-process :class:`RecordSource` returning a preset record list (Req 3.1)."""

    def __init__(self, name: str, records: list[GameRecord], available: bool) -> None:
        self.name = name
        self._records = records
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def fetch_records(self) -> list[GameRecord]:
        return list(self._records)


class FakeMemory(MemoryService):
    """Stub memory: serves a preset library and captures what gets persisted."""

    def __init__(self, existing: list[GameRecord]) -> None:
        self._existing = existing
        self.stored: list[GameRecord] | None = None

    def get_records(self, user_id: str) -> list[GameRecord]:
        return list(self._existing)

    def store_records(self, user_id: str, records: list[GameRecord]) -> bool:
        self.stored = list(records)
        return True


class IdentityEnricher:
    """Returns records untouched so dedup/precedence is tested without enrichment noise."""

    def enrich(self, record: GameRecord) -> GameRecord:
        return record


# --- Strategies -----------------------------------------------------------

# Small title/platform pools rendered with case + surrounding-whitespace variants
# so distinct draws collide on the normalized dedup key (casefold + strip).
_TITLE_POOL = ["hades", "celeste", "stardew valley", "hollow knight", "tunic"]
_PLATFORM_POOL = ["PC", "Switch", "Xbox", "PlayStation"]
_CASES = ["lower", "upper", "title", "orig"]
_PADDING = ["", " ", "  ", "\t"]


def _apply_case(text: str, mode: str) -> str:
    if mode == "lower":
        return text.lower()
    if mode == "upper":
        return text.upper()
    if mode == "title":
        return text.title()
    return text


def _cased_ws(base: str) -> st.SearchStrategy[str]:
    """A case/surrounding-whitespace variant of ``base`` (same normalized key)."""
    return st.builds(
        lambda mode, lead, trail: f"{lead}{_apply_case(base, mode)}{trail}",
        st.sampled_from(_CASES),
        st.sampled_from(_PADDING),
        st.sampled_from(_PADDING),
    )


@st.composite
def _platforms(draw: st.DrawFn) -> list[str]:
    """0-N platforms; the first (key-bearing) one is a normalized-equal variant."""
    if draw(st.booleans()):
        return []
    first = draw(st.sampled_from(_PLATFORM_POOL))
    extras = draw(st.lists(st.sampled_from(["DLC", "Deluxe"]), max_size=2))
    return [draw(_cased_ws(first)), *extras]


@st.composite
def _record(draw: st.DrawFn, source_label: str) -> GameRecord:
    """A record whose title/first-platform are drawn to force key collisions."""
    base = draw(st.sampled_from(_TITLE_POOL))
    return GameRecord(
        title=draw(_cased_ws(base)),
        platforms=draw(_platforms()),
        source="manual",
        genre=draw(st.one_of(st.none(), st.just("Action"))),
        external_ids=({"src": source_label} if draw(st.booleans()) else {}),
    )


def _record_list(source_label: str) -> st.SearchStrategy[list[GameRecord]]:
    return st.lists(_record(source_label), max_size=4)


def _existing_list() -> st.SearchStrategy[list[GameRecord]]:
    """The stored library precondition: already deduped, as MemoryService guarantees.

    ``MemoryService.store_records`` dedups by ``dedup_key`` before persisting, so
    ``get_records`` never returns internal duplicates; the generator mirrors that.
    """
    return st.lists(_record("memory"), max_size=4, unique_by=lambda r: r.dedup_key)


# --- Reference implementation (mirror of the spec) ------------------------


def _dedup_first_wins(records: list[GameRecord]) -> list[GameRecord]:
    """Keep the first record per dedup key, preserving order (earliest wins)."""
    seen: set[str] = set()
    result: list[GameRecord] = []
    for record in records:
        if record.dedup_key in seen:
            continue
        seen.add(record.dedup_key)
        result.append(record)
    return result


def _expected_merged(
    existing: list[GameRecord], available_source_records: list[list[GameRecord]]
) -> list[GameRecord]:
    """Precedence stream: existing memory first, then sources in list order."""
    stream = list(existing)
    for records in available_source_records:
        stream.extend(records)
    return _dedup_first_wins(stream)


# --- Property 1: Dedup is precedence-aware and key-normalized --------------


@settings(deadline=None)
@given(
    existing=_existing_list(),
    source_records=st.lists(_record_list("source"), min_size=1, max_size=3),
)
def test_dedup_is_precedence_aware_and_key_normalized(
    existing: list[GameRecord], source_records: list[list[GameRecord]]
) -> None:
    """Property 1: one record per normalized key, the earliest-precedence winner.

    Precedence: existing memory records win over any source; among sources,
    earlier in the list wins; within a source, earlier records win.

    **Validates: Requirements 2.3, 3.1, 3.5**
    """
    memory = FakeMemory(existing)
    sources: list[RecordSource] = [
        FakeSource(f"source-{i}", recs, True) for i, recs in enumerate(source_records)
    ]
    service = LibraryService(sources=sources, enricher=IdentityEnricher(), memory=memory)  # type: ignore[arg-type]

    result = service.refresh(USER_ID)

    # (a) No two retained records share a normalized dedup key.
    keys = [r.dedup_key for r in result]
    assert len(keys) == len(set(keys))

    # Every unique input key is present in the assembled library.
    all_keys = {r.dedup_key for r in existing}
    for recs in source_records:
        all_keys |= {r.dedup_key for r in recs}
    assert set(keys) == all_keys

    # (b) For each key the retained record is the earliest-precedence contributor;
    # full ordered equality also pins the winner's exact (cased/padded) value.
    assert result == _expected_merged(existing, source_records)

    # The persisted library matches the returned merged list (Req 3.5, 8.1).
    assert memory.stored == result


# --- Property 2: Source unavailability does not break assembly -------------


@settings(deadline=None)
@given(
    existing=_existing_list(),
    specs=st.lists(st.tuples(_record_list("source"), st.booleans()), max_size=4),
)
def test_source_unavailability_does_not_break_assembly(
    existing: list[GameRecord], specs: list[tuple[list[GameRecord], bool]]
) -> None:
    """Property 2: unavailable sources are skipped; assembly still completes.

    The result equals what only the available sources would yield (deduped,
    precedence-aware), and unavailable sources contribute no records.

    **Validates: Requirements 3.6, 10.4**
    """
    memory = FakeMemory(existing)
    sources: list[RecordSource] = [
        FakeSource(f"source-{i}", recs, available) for i, (recs, available) in enumerate(specs)
    ]
    service = LibraryService(sources=sources, enricher=IdentityEnricher(), memory=memory)  # type: ignore[arg-type]

    result = service.refresh(USER_ID)

    available_records = [recs for recs, available in specs if available]
    assert result == _expected_merged(existing, available_records)

    # Records that exist *only* behind an unavailable source never appear.
    available_keys = set()
    for recs in available_records:
        available_keys |= {r.dedup_key for r in recs}
    existing_keys = {r.dedup_key for r in existing}
    unavailable_only_keys = set()
    for recs, available in specs:
        if not available:
            unavailable_only_keys |= {r.dedup_key for r in recs}
    unavailable_only_keys -= available_keys | existing_keys
    result_keys = {r.dedup_key for r in result}
    assert unavailable_only_keys.isdisjoint(result_keys)


@settings(deadline=None)
@given(
    existing=_existing_list(),
    source_records=st.lists(_record_list("source"), min_size=1, max_size=3),
)
def test_all_sources_unavailable_returns_existing_memory(
    existing: list[GameRecord], source_records: list[list[GameRecord]]
) -> None:
    """Property 2: with every source unavailable, only existing memory remains.

    **Validates: Requirements 3.6, 10.4**
    """
    memory = FakeMemory(existing)
    sources: list[RecordSource] = [
        FakeSource(f"source-{i}", recs, False) for i, recs in enumerate(source_records)
    ]
    service = LibraryService(sources=sources, enricher=IdentityEnricher(), memory=memory)  # type: ignore[arg-type]

    result = service.refresh(USER_ID)

    assert result == _dedup_first_wins(existing)
