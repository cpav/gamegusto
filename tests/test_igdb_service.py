"""IGDB cover-art lookup tests.

The HTTP layer is faked rather than the service, so the real request shaping,
token reuse and degradation paths all execute.
"""

from __future__ import annotations

from typing import Any

from services.igdb_service import (
    GameSuggestion,
    IgdbService,
    _rank_by_name,
    _search_variants,
    _to_suggestion,
)


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self) -> Any:
        return self._payload


class FakeHttp:
    """Records calls and replays queued responses."""

    def __init__(self, token: Any = None, games: Any = None) -> None:
        self.token_response = (
            token
            if token is not None
            else FakeResponse({"access_token": "tok-1", "expires_in": 3600})
        )
        self.games_response = (
            games
            if games is not None
            else FakeResponse([{"name": "Batman: Arkham Knight", "cover": {"image_id": "co2l7z"}}])
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, kwargs))
        return self.token_response if "oauth2/token" in url else self.games_response


def test_returns_a_cover_url_for_a_known_game() -> None:
    http = FakeHttp()
    service = IgdbService("id", "secret", http=http)

    url = service.find_cover("Batman Arkham Knight")

    assert url == "https://images.igdb.com/igdb/image/upload/t_cover_big/co2l7z.jpg"


def test_reuses_the_token_across_lookups() -> None:
    """A token request per lookup would double every call."""
    http = FakeHttp()
    service = IgdbService("id", "secret", http=http)

    service.find_cover("Hades")
    service.find_cover("Celeste")

    token_calls = [url for url, _ in http.calls if "oauth2/token" in url]
    assert len(token_calls) == 1


def test_no_credentials_means_unavailable() -> None:
    """The whole feature is optional; missing config must not raise."""
    service = IgdbService(None, None, http=FakeHttp())

    assert service.is_available is False
    assert service.find_cover("Hades") is None


def test_degrades_when_the_token_request_fails() -> None:
    http = FakeHttp(token=FakeResponse({}, status=500))
    service = IgdbService("id", "secret", http=http)

    assert service.find_cover("Hades") is None


def test_degrades_when_the_search_fails() -> None:
    http = FakeHttp(games=FakeResponse({}, status=503))
    service = IgdbService("id", "secret", http=http)

    assert service.find_cover("Hades") is None


def test_returns_none_when_nothing_matches() -> None:
    http = FakeHttp(games=FakeResponse([]))
    service = IgdbService("id", "secret", http=http)

    assert service.find_cover("A Game That Does Not Exist") is None


def test_returns_none_when_the_match_has_no_cover() -> None:
    http = FakeHttp(games=FakeResponse([{"name": "Obscure", "cover": None}]))
    service = IgdbService("id", "secret", http=http)

    assert service.find_cover("Obscure") is None


def test_quotes_in_a_title_cannot_break_the_query() -> None:
    """The search term is interpolated into IGDB's query language."""
    http = FakeHttp()
    IgdbService("id", "secret", http=http).find_cover('Some "Quoted" Game')

    _, kwargs = next((c for c in http.calls if "games" in c[0]), ("", {}))
    assert kwargs["data"].count('"') == 2  # only the ones wrapping the term


# --- picking the right entry -----------------------------------------------
#
# These encode what real IGDB responses actually looked like. The first
# version filtered on `category = 0` to drop DLC, which matched NOTHING —
# not even plain main games — so every lookup silently fell back to a web
# image search. Ranking now happens on names, which does not depend on a
# schema attribute staying put.


def _games(*names: str) -> list[dict[str, Any]]:
    return [{"name": n, "cover": {"image_id": f"img-{i}"}} for i, n in enumerate(names)]


def test_exact_name_wins_over_a_higher_ranked_spin_off() -> None:
    """IGDB ranked a fan entry above the real game for this exact query."""
    http = FakeHttp(
        games=FakeResponse(_games("Super Mario Odyssey F.L.U.D.D.", "Super Mario Odyssey"))
    )
    url = IgdbService("id", "secret", http=http).find_cover("Super Mario Odyssey")

    assert url and url.endswith("img-1.jpg")


def test_a_franchise_prefix_the_user_omitted_still_matches() -> None:
    """Nobody types "The Legend of" — the library says "Zelda: ...".."""
    http = FakeHttp(games=FakeResponse(_games("The Legend of Zelda: Tears of the Kingdom")))
    url = IgdbService("id", "secret", http=http).find_cover("Zelda: Tears of the Kingdom")

    assert url and url.endswith("img-0.jpg")


def test_punctuation_and_case_do_not_decide_a_match() -> None:
    http = FakeHttp(games=FakeResponse(_games("Worms W.M.D")))
    url = IgdbService("id", "secret", http=http).find_cover("worms wmd")

    assert url and url.endswith("img-0.jpg")


def test_an_unrelated_result_is_refused_rather_than_guessed() -> None:
    """A wrong cover looks deliberate; the fallback does not."""
    http = FakeHttp(games=FakeResponse(_games("Star Fox: Super Weekend", "Star Wars")))
    assert IgdbService("id", "secret", http=http).find_cover("Celeste") is None


def test_the_query_no_longer_filters_on_category() -> None:
    """That filter excluded remakes, editions and bundles — and everything else."""
    http = FakeHttp()
    IgdbService("id", "secret", http=http).find_cover("Spyro Reignited Trilogy")

    _, kwargs = next(c for c in http.calls if "games" in c[0])
    assert "category" not in kwargs["data"]
    assert "cover != null" in kwargs["data"]


# --- messy library titles --------------------------------------------------
#
# Titles arrive from purchase emails and manual entry, so they carry whatever
# a shop wrote. Searching the raw string found nothing for a quarter of a real
# 77-game library; these are the exact shapes that failed.


def test_store_branding_is_stripped_before_searching() -> None:
    """Store branding is not part of the name any catalogue uses.

    Casing is left as the source wrote it — IGDB's search is case-insensitive,
    so normalising it would be work that buys nothing.
    """
    assert "METAL SLUG" in _search_variants("ACA NEOGEO METAL SLUG")
    assert "SUNSETRIDERS" in _search_variants("Arcade Archives SUNSETRIDERS")


def test_trademark_symbols_never_reach_the_search() -> None:
    assert "Far Cry 5" in _search_variants("Far Cry® 5")
    assert "LEGO 2K Drive" in _search_variants("LEGO® 2K Drive")


def test_edition_suffixes_are_dropped() -> None:
    assert "It Takes Two" in _search_variants("It Takes Two - Digital Version")
    assert "Warhammer 40,000: Space Marine 2" in _search_variants(
        "Warhammer 40,000: Space Marine 2 – Year 1 Edition"
    )


def test_parentheticals_and_scraped_ratings_are_dropped() -> None:
    """One record's title had a star rating scraped onto the end of it."""
    assert "Star Fox" in _search_variants("Star Fox (2026)")
    assert "Final Fantasy Tactics: The Ivalice Chronicles" in _search_variants(
        "Final Fantasy Tactics: The Ivalice Chronicles (Video Game 2025) ⭐ 8.0"
    )


def test_a_trailing_numeral_is_also_tried_as_a_roman_one() -> None:
    """IGDB lists "Dragon's Dogma II"; the receipt said 2."""
    assert "Dragon's Dogma II" in _search_variants("Dragon's Dogma 2")


def test_a_short_abbreviation_prefix_is_tried_without_it() -> None:
    assert "Crash Team Racing" in _search_variants("CTR: Crash Team Racing")


def test_a_real_subtitle_is_not_mistaken_for_an_abbreviation() -> None:
    """The full title must be tried first, and must remain a candidate."""
    variants = _search_variants("Zelda: Tears of the Kingdom")
    assert variants[0] == "Zelda: Tears of the Kingdom"
    assert "Tears of the Kingdom" not in variants[:1]


# --- add-game search (search_games) ----------------------------------------
#
# The add box searches IGDB directly: real titles, the platforms they shipped
# on, and box art, so the picker gets both the title and the platform right.


def _catalog(*games: dict[str, Any]) -> FakeHttp:
    return FakeHttp(games=FakeResponse(list(games)))


def _entry(name: str, image_id: str | None = "co1", *platforms: str) -> dict[str, Any]:
    game: dict[str, Any] = {"name": name}
    if image_id is not None:
        game["cover"] = {"image_id": image_id}
    if platforms:
        game["platforms"] = [{"name": p} for p in platforms]
    return game


def test_search_games_returns_title_platforms_and_thumbnail() -> None:
    http = _catalog(_entry("Hades", "co1", "Nintendo Switch", "PC (Microsoft Windows)"))
    results = IgdbService("id", "secret", http=http).search_games("hades")

    assert results == [
        GameSuggestion(
            name="Hades",
            # "PC (Microsoft Windows)" is presented the way the library writes it,
            # and platforms come back sorted.
            platforms=("Nintendo Switch", "PC"),
            cover_url="https://images.igdb.com/igdb/image/upload/t_cover_small/co1.jpg",
        )
    ]


def test_search_games_floats_the_exact_title_above_spin_offs() -> None:
    """IGDB relevance buried the real game beneath its spin-offs and mods."""
    http = _catalog(
        _entry("Super Mario Odyssey F.L.U.D.D.", "co1", "Nintendo Switch"),
        _entry("Super Mario Odyssey 64", "co2", "Nintendo 64"),
        _entry("Super Mario Odyssey", "co3", "Nintendo Switch"),
    )
    results = IgdbService("id", "secret", http=http).search_games("Super Mario Odyssey")

    assert results[0].name == "Super Mario Odyssey"


def test_search_games_query_filters_add_ons_and_editions() -> None:
    """parent_game/version_parent are the relationship fields; the flaky
    category enum that once matched nothing is never used."""
    http = _catalog(_entry("Hollow Knight"))
    IgdbService("id", "secret", http=http).search_games("hollow")

    _, kwargs = next(c for c in http.calls if "games" in c[0])
    body = kwargs["data"]
    assert "parent_game = null" in body
    assert "version_parent = null" in body
    assert "cover != null" in body
    assert "category" not in body


def test_search_games_trims_to_the_requested_limit() -> None:
    http = _catalog(*[_entry(f"Game {i}", f"co{i}") for i in range(20)])
    results = IgdbService("id", "secret", http=http).search_games("game", limit=3)

    assert len(results) == 3


def test_search_games_without_credentials_is_empty() -> None:
    assert IgdbService(None, None, http=_catalog(_entry("Hades"))).search_games("hades") == []


def test_search_games_degrades_to_empty_on_failure() -> None:
    http = FakeHttp(games=FakeResponse({}, status=503))
    assert IgdbService("id", "secret", http=http).search_games("hades") == []


def test_search_games_ignores_a_blank_query_without_calling() -> None:
    http = _catalog(_entry("Hades"))
    assert IgdbService("id", "secret", http=http).search_games("   ") == []
    assert not any("games" in url for url, _ in http.calls)


def test_to_suggestion_tolerates_missing_cover_and_platforms() -> None:
    assert _to_suggestion({"name": "Obscure"}) == GameSuggestion("Obscure", (), None)
    assert _to_suggestion({"cover": {"image_id": "x"}}) is None  # no name → unusable


def test_rank_prefers_exact_then_prefix_then_contains() -> None:
    games = [
        {"name": "Batman: Arkham Knight"},
        {"name": "Batman"},
        {"name": "Gotham by Batman"},
    ]
    ranked = [g["name"] for g in _rank_by_name("Batman", games)]
    assert ranked == ["Batman", "Batman: Arkham Knight", "Gotham by Batman"]
