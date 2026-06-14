"""Shared pytest configuration.

Registers a fast Hypothesis profile for the default (developer/CI) run so the
property suite stays quick without losing meaningful coverage. Set
``HYPOTHESIS_PROFILE=thorough`` to run more examples when deeper exploration is
wanted (e.g. nightly).
"""

from __future__ import annotations

import os

from hypothesis import settings

settings.register_profile("fast", max_examples=30, deadline=None)
settings.register_profile("thorough", max_examples=200, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "fast"))
