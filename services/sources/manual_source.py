"""User-entered records staged in memory (``source="manual"``).

The manual source surfaces games the user added by hand through the library
view. Those records already live in the canonical store, so this source simply
reads them back from :class:`MemoryService` and exposes only the ones tagged
``source="manual"`` (Req 3.4). Unlike connector-backed sources it is always
available: manual entry needs no external credentials or network, so
:meth:`is_available` is unconditionally ``True`` (Req 3.6).

Like every record source, :meth:`fetch_records` never raises to its caller —
``MemoryService.get_records`` already degrades to ``[]`` on failure.
"""

from __future__ import annotations

from models.game_record import GameRecord
from services.memory_service import MemoryService


class ManualSource:
    """Returns the user's manually-entered records staged in memory (Req 3.4)."""

    name = "manual"

    def __init__(self, memory: MemoryService, user_id: str) -> None:
        """Build the source around the shared ``memory`` store and a ``user_id``."""
        self._memory = memory
        self._user_id = user_id

    def is_available(self) -> bool:
        """Always ``True`` — manual entry needs no external connection (Req 3.6)."""
        return True

    def fetch_records(self) -> list[GameRecord]:
        """Return stored records tagged ``source="manual"`` (never raises, Req 3.4)."""
        return [
            record
            for record in self._memory.get_records(self._user_id)
            if record.source == "manual"
        ]
