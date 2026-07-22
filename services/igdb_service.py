"""Cover art from IGDB, the games industry's metadata database.

Replaces a general web image search, which returned whatever the open web
offered for "<title> cover art" — screenshots, fan art, wallpapers, wrong
regional editions. IGDB returns the actual product shot, at a consistent
aspect ratio, for every platform including Nintendo exclusives.

Two things make this more involved than the other service clients:

* **It is OAuth, not an API key.** Twitch issues a bearer token from a client
  id and secret; the token expires. It is fetched lazily and reused until it
  does, because a token request per lookup would double every call.
* **Search is fuzzy.** "Zelda: Tears of the Kingdom" must find "The Legend of
  Zelda: Tears of the Kingdom". IGDB's search handles this, but it also
  happily returns DLC, bundles and remasters, so results are filtered to
  actual games with a cover before the best is taken.

Like every external dependency here, failure degrades to ``None`` rather than
raising: a missing cover is cosmetic, and must never break a library listing
(Req 10.3).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_API_URL = "https://api.igdb.com/v4/games"
_TIMEOUT = 8

#: Refresh a little before expiry so a lookup never races the boundary.
_TOKEN_SKEW_SECONDS = 60

#: IGDB serves several sizes from one image id. "cover_big" (264x374) is the
#: box-art aspect the library grid is built around — retina-ish at the card
#: sizes used and small enough to stay snappy on a phone; "cover_small"
#: (90x128) backs the add-game suggestion thumbnails, where the art only has to
#: confirm the title is the right one.
_IMAGE_TEMPLATE = "https://images.igdb.com/igdb/image/upload/t_{size}/{image_id}.jpg"


def _cover_url(image_id: str, size: str = "cover_big") -> str:
    """Build the CDN URL for an IGDB image id at one of its served sizes."""
    return _IMAGE_TEMPLATE.format(size=size, image_id=image_id)


#: IGDB spells the desktop platform "PC (Microsoft Windows)"; the library, and
#: everyone typing a platform by hand, says "PC". No other name needs mapping —
#: "Nintendo Switch", "PlayStation 5" and the rest already read naturally.
_PLATFORM_LABELS = {"PC (Microsoft Windows)": "PC"}


def _platform_label(name: str) -> str:
    """Present an IGDB platform name the way the library writes it."""
    return _PLATFORM_LABELS.get(name, name)


@dataclass(frozen=True)
class GameSuggestion:
    """One candidate game for the add-game search: enough to pick it with confidence."""

    name: str
    platforms: tuple[str, ...]
    cover_url: str | None


#: Publisher/collection branding that prefixes a store listing but is not part
#: of the game's name in any catalogue. "ACA NEOGEO METAL SLUG" is Metal Slug.
_PREFIXES = ("aca neogeo ", "arcade archives ", "nintendo switch online ")

#: Edition and packaging suffixes, which stores add and catalogues do not.
_SUFFIX = re.compile(
    r"\s*[-–—:]\s*(digital|standard|deluxe|premium|complete|definitive|legendary|gold|"
    r"ultimate|year \d+|game of the year|goty|anniversary)\b.*$",
    re.IGNORECASE,
)

#: Trailing "(2026)", "(Video Game 2025)", and anything after a stray rating.
_PARENTHETICAL = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")
_RATING_TAIL = re.compile(r"\s*[⭐★]\s*[\d.]+\s*$")

_ROMAN = {"2": "II", "3": "III", "4": "IV", "5": "V", "6": "VI", "7": "VII"}


def _search_variants(title: str) -> list[str]:
    """Progressively plainer forms of a title, best first.

    Library titles come from purchase emails and manual entry, so they carry
    what a shop wrote: trademark symbols, edition suffixes, collection
    prefixes, a scraped rating. Catalogues carry none of that, so searching
    the raw string finds nothing — which is most of what still fell back to
    the web image search.
    """
    variants: list[str] = []

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", value).strip(" -–—:")
        if value and value not in variants:
            variants.append(value)

    cleaned = title.replace("™", "").replace("®", "").replace("©", "")
    add(cleaned)

    without_tail = _RATING_TAIL.sub("", cleaned)
    add(without_tail)
    add(_PARENTHETICAL.sub("", without_tail))

    base = _SUFFIX.sub("", _PARENTHETICAL.sub("", without_tail))
    add(base)

    lowered = base.casefold()
    for prefix in _PREFIXES:
        if lowered.startswith(prefix):
            add(base[len(prefix) :])

    # "CTR: Crash Team Racing" is catalogued as "Crash Team Racing". Only a
    # short leading token qualifies, and only as a late variant, so a real
    # subtitle ("Zelda: Tears of the Kingdom") has already matched in full.
    head, sep, tail = base.partition(":")
    if sep and len(head.strip()) <= 5 and len(tail.strip()) > 4:
        add(tail)

    # "Dragon's Dogma 2" is catalogued as "Dragon's Dogma II".
    trailing = re.search(r"\b(\d)$", base)
    if trailing and trailing.group(1) in _ROMAN:
        add(base[: trailing.start()] + _ROMAN[trailing.group(1)])

    return variants


def _normalise(name: str) -> str:
    """Reduce a title to comparable letters and digits."""
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def _best_match(title: str, games: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the entry that is actually the game asked for.

    IGDB's relevance ranking alone returns fan entries and spin-offs ahead of
    the real thing — "Super Mario Odyssey" surfaced "Super Mario Odyssey
    F.L.U.D.D." first, and "Star Fox" surfaced "Star Fox: Super Weekend". So
    an exact name match wins, then one that differs only by a subtitle, and
    anything looser is refused rather than guessed at: a wrong cover is worse
    than the fallback, because it looks deliberate.
    """
    target = _normalise(title)

    for game in games:
        if _normalise(str(game.get("name", ""))) == target:
            return game

    for game in games:
        candidate = _normalise(str(game.get("name", "")))
        # "zeldatearsofthekingdom" against "thelegendofzeldatearsofthekingdom":
        # a real title often omits the franchise prefix people never type.
        if candidate.startswith(target) or target in candidate:
            return game

    return None


#: How many candidates to pull before re-ranking. Generous, because IGDB's own
#: relevance can bury the exact match several rows down; the pool is trimmed to
#: the caller's ``limit`` after re-ranking.
_SEARCH_POOL = 20


def _rank_by_name(query: str, games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reorder IGDB results so the closest title to ``query`` comes first.

    An exact name wins, then a name that starts with the query, then one that
    merely contains it; shorter names rank ahead of longer within a tier, so
    the bare title beats its subtitled relatives. Python's stable sort keeps
    IGDB's own relevance order as the final tiebreak.
    """
    target = _normalise(query)

    def rank(game: dict[str, Any]) -> tuple[int, int]:
        name = _normalise(str(game.get("name", "")))
        if name == target:
            tier = 0
        elif name.startswith(target):
            tier = 1
        elif target and target in name:
            tier = 2
        else:
            tier = 3
        return (tier, len(name))

    return sorted(games, key=rank)


def _to_suggestion(game: dict[str, Any]) -> GameSuggestion | None:
    """Parse one IGDB search row into a suggestion, or ``None`` if it is unusable."""
    if not isinstance(game, dict):
        return None
    name = str(game.get("name", "")).strip()
    if not name:
        return None
    image_id = (game.get("cover") or {}).get("image_id")
    cover = _cover_url(str(image_id), "cover_small") if image_id else None
    platforms = sorted(
        {
            _platform_label(str(entry.get("name", "")))
            for entry in game.get("platforms") or []
            if isinstance(entry, dict) and entry.get("name")
        }
    )
    return GameSuggestion(name=name, platforms=tuple(platforms), cover_url=cover)


class _Http(Protocol):
    """The slice of ``requests`` used here, so tests need no network."""

    def post(self, url: str, **kwargs: Any) -> Any: ...


class IgdbService:
    """Looks up cover art by title. Degrades to ``None`` on any failure."""

    def __init__(
        self,
        client_id: str | None,
        client_secret: str | None,
        http: _Http | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or requests
        self._token: str | None = None
        self._token_expires_at = 0.0

    @property
    def is_available(self) -> bool:
        """Whether credentials were configured at all."""
        return bool(self._client_id and self._client_secret)

    def find_cover(self, title: str, platform: str | None = None) -> str | None:
        """Return a cover image URL for ``title``, or ``None``.

        ``platform`` is accepted for call-site symmetry with the previous
        image search but deliberately unused: IGDB covers are per-game, not
        per-platform, and filtering by platform mostly loses matches for games
        whose Switch release is catalogued under a different entry.
        """
        if not self.is_available or not title.strip():
            return None

        token = self._access_token()
        if token is None:
            return None

        for variant in _search_variants(title):
            cover = self._search_one(variant, token)
            if cover:
                return cover

        logger.info("IGDB had no match for %r; falling back to image search", title)
        return None

    def _search_one(self, title: str, token: str) -> str | None:
        """One search, one best-match decision."""

        # Only "has a cover" is filtered. An earlier version also required
        # `category = 0` to drop DLC — it matched NOTHING, including plain main
        # games, because that attribute is no longer populated the way it was.
        # Every lookup missed and fell back to a web image search, silently.
        # Ranking is done below on the names instead, which does not depend on
        # a schema detail staying still.
        body = (
            f'search "{title.replace(chr(34), "")}";'
            " fields name, cover.image_id;"
            " where cover != null;"
            " limit 15;"
        )

        try:
            response = self._http.post(
                _API_URL,
                headers={
                    "Client-ID": str(self._client_id),
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                data=body,
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            games = response.json()
        except Exception as exc:  # noqa: BLE001 - degrade on any IGDB failure
            logger.warning("IGDB lookup failed for %r: %s", title, exc)
            return None

        if not isinstance(games, list) or not games:
            return None

        best = _best_match(title, games)
        if best is None:
            return None

        image_id = (best.get("cover") or {}).get("image_id")
        return _cover_url(str(image_id)) if image_id else None

    def search_games(self, query: str, limit: int = 8) -> list[GameSuggestion]:
        """Live title suggestions for the add-game box, best match first.

        This is the store-search feel: a partial title in, a short list of real
        games out — each with the platforms it shipped on and its box art, so
        the picker confirms *this* game (there are three "Star Wars
        Battlefront"s) before adding it. Unlike :meth:`find_cover` the query is
        passed through untouched: IGDB's search is built for partial, live-typed
        input, and the variant cleaning exists for messy *stored* titles, not
        for someone halfway through typing one.

        Two things keep the list looking like a shelf rather than a database
        dump. ``parent_game`` and ``version_parent`` are null-filtered at the
        source, dropping add-ons, skins and Deluxe/GOTY editions — IGDB's own
        relevance otherwise put *five* "Batman" skin packs above the game. Then
        a generous pool is re-ranked locally so the closest title floats to the
        top, because relevance alone still buried "Super Mario Odyssey" beneath
        its spin-offs. Degrades to ``[]`` on any failure — free-text Add stays.
        """
        if not self.is_available or not query.strip():
            return []

        token = self._access_token()
        if token is None:
            return []

        body = (
            f'search "{query.replace(chr(34), "")}";'
            " fields name, cover.image_id, platforms.name;"
            " where version_parent = null & parent_game = null & cover != null;"
            f" limit {_SEARCH_POOL};"
        )

        try:
            response = self._http.post(
                _API_URL,
                headers={
                    "Client-ID": str(self._client_id),
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                data=body,
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            games = response.json()
        except Exception as exc:  # noqa: BLE001 - degrade on any IGDB failure
            logger.warning("IGDB search failed for %r: %s", query, exc)
            return []

        if not isinstance(games, list):
            return []
        ranked = _rank_by_name(query, [g for g in games if isinstance(g, dict)])
        suggestions = [s for s in (_to_suggestion(game) for game in ranked) if s is not None]
        return suggestions[:limit]

    def _access_token(self) -> str | None:
        """A valid bearer token, fetching one only when the last has expired."""
        if self._token and time.time() < self._token_expires_at:
            return self._token

        try:
            response = self._http.post(
                _TOKEN_URL,
                params={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - degrade rather than raise
            logger.warning("IGDB token request failed: %s", exc)
            return None

        token = payload.get("access_token")
        if not token:
            return None

        self._token = str(token)
        lifetime = float(payload.get("expires_in", 0))
        self._token_expires_at = time.time() + lifetime - _TOKEN_SKEW_SECONDS
        return self._token
