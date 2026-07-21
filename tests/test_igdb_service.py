"""IGDB cover-art lookup tests.

The HTTP layer is faked rather than the service, so the real request shaping,
token reuse and degradation paths all execute.
"""

from __future__ import annotations

from typing import Any

from services.igdb_service import IgdbService


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


def test_query_excludes_dlc_and_bundles() -> None:
    """Searching a popular title otherwise surfaces its season pass first."""
    http = FakeHttp()
    IgdbService("id", "secret", http=http).find_cover("Hades")

    _, kwargs = next((c for c in http.calls if "games" in c[0]), ("", {}))
    assert "category = 0" in kwargs["data"]
    assert "cover != null" in kwargs["data"]


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
