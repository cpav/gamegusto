"""Cognito token verification.

Phase 1 left a single dependency, ``current_user``, as the seam where identity
would arrive. This is what fills it.

Three decisions shape this module, and each is forced by something upstream:

**The token arrives in ``X-Id-Token``, not ``Authorization``.** CloudFront's
origin access control puts its own SigV4 signature in the ``Authorization``
header when it signs requests to the Lambda function URL, so that header
cannot carry anything of ours — forwarding it makes every request fail
signature validation. See infra/stack/cloudfront.tf.

**The subject is not the storage key.** The library predates authentication
and lives under ``user_id="default"``. Keying storage by the Cognito ``sub``
would not migrate that data, it would simply hide it behind an empty account.
Authentication decides *whether* a request proceeds; the storage identity is
separate and unchanged.

**Absent configuration means auth is off.** Local development and
``scripts/mock_api.py`` run with no pool and must stay credential-free. A
missing ``COGNITO_USER_POOL_ID`` therefore disables verification. That is safe
because the deployed function always has it set — but it is the one setting
that must never be lost, so ``build_verifier`` says so loudly in its return
value rather than failing open silently.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

import jwt
from jwt import PyJWKClient

#: Header carrying the Cognito ID token. Not Authorization — see module docstring.
TOKEN_HEADER = "X-Id-Token"


class AuthError(Exception):
    """Raised when a token is missing, malformed, expired or not ours."""


class SigningKeyResolver(Protocol):
    """Resolves a token's signing key. Narrow, so tests need no network."""

    def get_signing_key_from_jwt(self, token: str) -> Any: ...


@dataclass(frozen=True)
class CognitoVerifier:
    """Verifies Cognito ID tokens against one user pool."""

    issuer: str
    keys: SigningKeyResolver

    def subject(self, token: str) -> str:
        """Return the token's ``sub``, or raise ``AuthError``.

        Checks the signature, the issuer, expiry, and that this is an ID token
        rather than an access token — they are signed by the same keys, and
        accepting either would let a token minted for a different purpose
        through.

        ``aud`` is not checked: the client id is not available to the function
        (taking it would close a Terraform dependency cycle — see
        infra/stack/lambda.tf), and with a single client in the pool the
        issuer check already pins it. Add an ``audience`` here if a second
        client is ever created.
        """
        try:
            signing_key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self.issuer,
                options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
            )
        except jwt.InvalidTokenError as exc:
            raise AuthError(str(exc)) from exc
        except Exception as exc:  # key fetch failures, malformed headers
            raise AuthError(f"could not verify token: {exc}") from exc

        if claims.get("token_use") != "id":
            raise AuthError("expected an ID token")

        subject = claims.get("sub")
        if not subject:
            raise AuthError("token has no subject")
        return str(subject)


def build_verifier(
    user_pool_id: str | None = None,
    region: str | None = None,
) -> CognitoVerifier | None:
    """Build a verifier from the environment, or ``None`` when auth is off.

    ``None`` means "no pool configured", which is the local-development and
    mock-API case. Callers must treat it as an explicit, visible decision —
    ``api.app`` logs it at startup — rather than as an absence.
    """
    pool = user_pool_id or os.environ.get("COGNITO_USER_POOL_ID")
    if not pool:
        return None

    # Lambda always provides AWS_REGION; locally it comes from the same env
    # the rest of the config uses.
    resolved_region = region or os.environ.get("AWS_REGION") or pool.split("_")[0]
    issuer = f"https://cognito-idp.{resolved_region}.amazonaws.com/{pool}"

    # PyJWKClient caches keys and refetches on an unknown key id, which is
    # what makes Cognito's key rotation a non-event.
    return CognitoVerifier(issuer=issuer, keys=PyJWKClient(f"{issuer}/.well-known/jwks.json"))
