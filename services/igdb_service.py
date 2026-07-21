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
import time
from typing import Any, Protocol

import requests

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_API_URL = "https://api.igdb.com/v4/games"
_TIMEOUT = 8

#: Refresh a little before expiry so a lookup never races the boundary.
_TOKEN_SKEW_SECONDS = 60

#: IGDB serves several sizes from one image id. This is the box-art aspect the
#: library grid is built around; "t_cover_big" is 264x374, retina-ish at the
#: card sizes used and small enough to stay snappy on a phone.
_IMAGE_TEMPLATE = "https://images.igdb.com/igdb/image/upload/t_cover_big/{image_id}.jpg"


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

        # `where cover != null` keeps entries that have art; category 0 is a
        # main game, which drops DLC, expansions and bundles that otherwise
        # outrank the thing actually being searched for.
        body = (
            f'search "{title.replace(chr(34), "")}";'
            " fields name, cover.image_id;"
            " where cover != null & category = 0;"
            " limit 5;"
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

        image_id = (games[0].get("cover") or {}).get("image_id")
        return _IMAGE_TEMPLATE.format(image_id=image_id) if image_id else None

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
