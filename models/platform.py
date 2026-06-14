"""Owned-platform model.

An ``OwnedPlatform`` is a user-declared platform in their Platform_List. The
``name`` is free-text so users can add any platform without code changes
(Req 6.4), and each entry carries a generated ``platform_id`` for stable
edit/remove operations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class OwnedPlatform:
    """A platform the user owns, identified by a generated id (Req 6.4)."""

    name: str
    platform_id: str = field(default_factory=lambda: str(uuid.uuid4()))
