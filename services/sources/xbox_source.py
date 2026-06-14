"""Owned games from the Xbox platform API (``source="xbox"``).

Ownership is read through the Xbox Live title-history surface, reached with an
``XBL3.0`` token minted by the OAuth2 authorization-code flow (Microsoft token
exchange → Xbox Live user token → XSTS authorization). Each retrieved title is
mapped to a canonical :class:`GameRecord` carrying the fields Xbox actually
exposes — title, owned platforms, and the stable ``titleId`` (Req 3.2). Genre,
playtime, availability, and community review are *not* exposed by Xbox; those
are enrichment fields populated later by Tavily, so they stay at their defaults.

Like every record source, :meth:`fetch_records` never raises to its caller: on
any auth/network/parse failure it records a sanitized error, marks the source
unavailable, and returns ``[]`` so the rest of the library keeps working
(Req 10.4).
"""

from __future__ import annotations

from typing import Any

import requests

from models.game_record import GameRecord
from services.error_handler import ErrorHandler

# OAuth2 + Xbox Live (XBL3.0) endpoints, declared once so the flow's external
# contract lives in a single place. The redirect URI must match the one
# registered for the Azure application that issued the client credentials.
REDIRECT_URI = "https://localhost/auth/xbox/callback"
MS_TOKEN_URL = "https://login.live.com/oauth20_token.srf"
XBL_USER_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTHORIZE_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
TITLE_HISTORY_URL = (
    "https://titlehub.xboxlive.com/users/xuid({xuid})/titles/titleHistory/decoration/detail"
)

# The OAuth scope and XSTS relying party required to read title history.
OAUTH_SCOPE = "XboxLive.signin offline_access"
XBL_RELYING_PARTY = "http://xboxlive.com"

_HTTP_TIMEOUT = 15  # seconds; bounds every outbound call so a hang degrades cleanly.


class XboxSource:
    """Owned games from the Xbox platform API (source='xbox')."""

    name = "xbox"

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None  # cached XBL3.0 Authorization header value
        self._xuid: str | None = None
        self._last_error: str | None = None

    def is_available(self) -> bool:
        """True only after a successful authenticate left a usable token (Req 3.6)."""
        return self._token is not None

    @property
    def last_error(self) -> str | None:
        """The most recent sanitized failure message, if any (Req 10.4)."""
        return self._last_error

    def authenticate(self, auth_code: str) -> bool:
        """Run the OAuth2 authorization-code flow and cache an XBL3.0 token.

        Trades the ``auth_code`` for a Microsoft access token, exchanges that for
        an Xbox Live user token, then authorizes an XSTS token to build the
        ``XBL3.0`` header used by the title-history call. Returns ``False`` (never
        raises) on failure so an unconnected source is simply skipped.
        """
        try:
            access_token = self._exchange_code(auth_code)
            user_token = self._authenticate_xbl(access_token)
            claims = self._authorize_xsts(user_token)
            self._token = f"XBL3.0 x={claims['uhs']};{claims['xsts_token']}"
            self._xuid = claims["xuid"]
            self._last_error = None
            return True
        except Exception as exc:  # token-exchange/auth failure (Req 10.4)
            self._token = None
            self._xuid = None
            self._last_error = ErrorHandler.sanitize_error(exc, "xbox")
            return False

    def fetch_records(self) -> list[GameRecord]:
        """Map every retrieved title to a GameRecord with ``source='xbox'`` (Req 3.2).

        On any failure returns ``[]`` and marks the source unavailable; malformed
        individual titles are skipped without aborting the import (Req 10.4).
        """
        try:
            if self._token is None or self._xuid is None:
                return []
            records: list[GameRecord] = []
            for title in self._fetch_titles():
                record = _map_title(title)
                if record is not None:
                    records.append(record)
            self._last_error = None
            return records
        except Exception as exc:  # network/parse failure (Req 10.4)
            self._token = None
            self._xuid = None
            self._last_error = ErrorHandler.sanitize_error(exc, "xbox")
            return []

    def _exchange_code(self, auth_code: str) -> str:
        """Exchange the authorization code for a Microsoft OAuth2 access token."""
        response = requests.post(
            MS_TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": auth_code,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
                "scope": OAUTH_SCOPE,
            },
            timeout=_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        return str(response.json()["access_token"])

    def _authenticate_xbl(self, access_token: str) -> str:
        """Trade the Microsoft access token for an Xbox Live user token."""
        response = requests.post(
            XBL_USER_AUTH_URL,
            json={
                "Properties": {
                    "AuthMethod": "RPS",
                    "SiteName": "user.auth.xboxlive.com",
                    "RpsTicket": f"d={access_token}",
                },
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT",
            },
            headers={"Content-Type": "application/json", "x-xbl-contract-version": "1"},
            timeout=_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        return str(response.json()["Token"])

    def _authorize_xsts(self, user_token: str) -> dict[str, str]:
        """Authorize an XSTS token and return its token, user hash, and xuid."""
        response = requests.post(
            XSTS_AUTHORIZE_URL,
            json={
                "Properties": {"SandboxId": "RETAIL", "UserTokens": [user_token]},
                "RelyingParty": XBL_RELYING_PARTY,
                "TokenType": "JWT",
            },
            headers={"Content-Type": "application/json", "x-xbl-contract-version": "1"},
            timeout=_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        claims = payload["DisplayClaims"]["xui"][0]
        return {
            "xsts_token": str(payload["Token"]),
            "uhs": str(claims["uhs"]),
            "xuid": str(claims["xid"]),
        }

    def _fetch_titles(self) -> list[dict[str, Any]]:
        """Call the title-history endpoint and return the raw per-title dicts."""
        response = requests.get(
            TITLE_HISTORY_URL.format(xuid=self._xuid),
            headers={
                "Authorization": self._token or "",
                "x-xbl-contract-version": "2",
                "Accept-Language": "en-US",
            },
            timeout=_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        titles = data.get("titles", []) if isinstance(data, dict) else []
        if not isinstance(titles, list):
            return []
        return [title for title in titles if isinstance(title, dict)]


# --- Raw title mapping (dict -> GameRecord | None) ----------------------------
#
# Maps one raw Xbox title dict to a canonical GameRecord. Defensive by design:
# a title missing a usable name is skipped (returns None) rather than raising,
# so a single malformed entry never aborts the import (Req 10.4). Per the data
# contract, only title/platforms/external_ids are populated; purchase_date and
# the enrichment fields stay at their defaults.


def _map_title(title: dict[str, Any]) -> GameRecord | None:
    """Map a raw Xbox title to a GameRecord, or ``None`` when it lacks a name."""
    name = title.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    external_ids: dict[str, str] = {}
    title_id = title.get("titleId")
    if title_id is not None:
        external_ids["xbox"] = str(title_id)
    return GameRecord(
        title=name.strip(),
        platforms=_extract_platforms(title),
        source="xbox",
        external_ids=external_ids,
    )


def _extract_platforms(title: dict[str, Any]) -> list[str]:
    """Pull the owned device/platform list from a raw title dict (defensive)."""
    raw = title.get("devices")
    if raw is None:
        raw = title.get("platforms")
    if not isinstance(raw, list):
        return []
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()]
