"""Live contract checks for the official stores (excluded from the default run).

The stores' data is not a documented API and can change without notice. These
hit the real services for the Danish market, so a change shows up here as a
named failure rather than as the agent quietly falling back to web search.

Run with: ``.venv/bin/python -m pytest -m integration tests/test_stores_live.py``
"""

from __future__ import annotations

import pytest

from services.stores.base import Store
from services.stores.nintendo_store import NintendoStore
from services.stores.xbox_store import XboxStore

pytestmark = pytest.mark.integration

_STORES = [
    pytest.param(XboxStore(), "Forza Horizon", id="xbox"),
    pytest.param(NintendoStore(), "Mario Kart", id="nintendo"),
]


@pytest.mark.parametrize(("store", "title"), _STORES)
def test_deals_come_back_priced_in_kroner(store: Store, title: str) -> None:
    deals = store.deals("da-DK", 10)

    assert deals, "the store listed no deals; its page or service may have changed"
    assert all(d.currency == "DKK" for d in deals)
    assert all(0 < d.price < d.regular_price for d in deals)


@pytest.mark.parametrize(("store", "title"), _STORES)
def test_a_well_known_title_is_found_and_priced(store: Store, title: str) -> None:
    found = store.lookup(title, "da-DK", 3)

    assert found, f"no match for {title!r}; the store's search may have changed"
    assert any(title.lower() in f.title.lower() for f in found)
    assert all(f.currency == "DKK" for f in found)
