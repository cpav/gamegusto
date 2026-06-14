"""Library assembly across record sources.

Coordinates the record sources into a single deduplicated, enriched library.
``refresh`` walks the injected sources in precedence order (Xbox -> Gmail ->
manual, ordered by the caller), merges their records with the user's existing
stored library, dedups by :attr:`GameRecord.dedup_key` with earlier sources
winning, enriches cache-first via Tavily, and persists the result to memory.

The service depends only on the :class:`RecordSource` protocol, so adding or
removing a source never changes this layer (Req 3.1). Unavailable sources are
skipped while the rest still run (Req 3.6, 10.4), and ``fetch_records`` never
raises by contract, so no defensive try/except is needed here.
"""

from __future__ import annotations

from models.game_record import GameRecord
from services.memory_service import MemoryService
from services.sources.base import RecordSource
from services.tavily_service import TavilyService


class LibraryService:
    """Assembles the deduplicated, enriched library from all record sources."""

    def __init__(
        self,
        sources: list[RecordSource],
        tavily: TavilyService,
        memory: MemoryService,
    ) -> None:
        """Build the service from sources (in precedence order), Tavily, and memory."""
        self._sources = sources
        self._tavily = tavily
        self._memory = memory

    def refresh(self, user_id: str) -> list[GameRecord]:
        """Merge, dedup, enrich, and persist the user's library; return the result.

        Existing stored records win over freshly fetched ones, and earlier sources
        win over later ones (Req 3.5). Unavailable sources are skipped without
        breaking assembly (Req 3.6, 10.4). New records are enriched cache-first
        (Req 5.1, 5.2) and the merged list is persisted (Req 8.1).
        """
        existing = self._memory.get_records(user_id)
        seen = {r.dedup_key for r in existing}
        merged = list(existing)
        for source in self._sources:  # Xbox -> Gmail -> manual
            if not source.is_available():
                continue  # skip; remaining sources still run (Req 3.6)
            for record in source.fetch_records():
                if record.dedup_key in seen:  # earlier source wins (Req 3.5)
                    continue
                seen.add(record.dedup_key)
                merged.append(self._enrich(record))
        self._memory.store_records(user_id, merged)  # (Req 3.5, 8.1)
        return merged

    def _enrich(self, record: GameRecord) -> GameRecord:
        """Enrich ``record`` via Tavily unless it is already enriched (Req 5.1, 5.2)."""
        if record.is_enriched():
            return record
        return self._tavily.enrich(record)
