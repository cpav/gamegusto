"""The ``RecordSource`` protocol — the single seam every record origin implements.

A ``RecordSource`` is an interchangeable origin of ``GameRecord``s (Gmail,
manual entry). Defining the boundary as a ``Protocol`` lets the library
assembly, the agent, and the UI depend on the shape rather than any concrete
source, so adding or removing a source never ripples upward (Req 3.1).

The contract is deliberately failure-tolerant: ``fetch_records`` must never
raise to its caller. A source that cannot reach its backend reports
``is_available() == False`` and returns ``[]`` instead, which is what keeps a
single broken source from breaking the whole library (Req 3.6, 10.4).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from models.game_record import GameRecord


@runtime_checkable
class RecordSource(Protocol):
    """An interchangeable origin of ``GameRecord``s (Req 3.1)."""

    name: str
    """Source identifier: one of ``"gmail"`` or ``"manual"``."""

    def is_available(self) -> bool:
        """True when the source is configured/connected and reachable (Req 3.6)."""
        ...

    def fetch_records(self) -> list[GameRecord]:
        """Return records conforming to the data contract.

        CONTRACT: never raises to the caller. On failure the source returns an
        empty list and reports unavailability via :meth:`is_available` (Req 10.4).
        """
        ...
