"""Token verification tests.

Real RSA keys and real signatures, no mocking of the crypto: a test that
stubs out verification proves only that the stub was called. Only the JWKS
*fetch* is replaced, so nothing here touches the network.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from api.auth import AuthError, CognitoVerifier, build_verifier

ISSUER = "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_test"


@dataclass
class _Key:
    key: Any


class _Resolver:
    """Stands in for PyJWKClient — returns one key, fetches nothing."""

    def __init__(self, public_key: Any) -> None:
        self._key = _Key(public_key)

    def get_signing_key_from_jwt(self, token: str) -> _Key:
        return self._key


@pytest.fixture(scope="module")
def keypair() -> tuple[Any, Any]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture
def verifier(keypair: tuple[Any, Any]) -> CognitoVerifier:
    _, public = keypair
    return CognitoVerifier(issuer=ISSUER, keys=_Resolver(public))


def _token(private: Any, **overrides: Any) -> str:
    claims: dict[str, Any] = {
        "sub": "11111111-2222-3333-4444-555555555555",
        "iss": ISSUER,
        "token_use": "id",
        "exp": int(time.time()) + 3600,
        "aud": "some-client-id",
    }
    claims.update(overrides)
    # str(): jwt.encode is typed as returning Any, and the repo forbids
    # leaking that through a function annotated to return str.
    return str(jwt.encode(claims, private, algorithm="RS256"))


def test_accepts_a_valid_id_token(verifier: CognitoVerifier, keypair: tuple[Any, Any]) -> None:
    private, _ = keypair
    assert verifier.subject(_token(private)) == "11111111-2222-3333-4444-555555555555"


def test_rejects_an_access_token(verifier: CognitoVerifier, keypair: tuple[Any, Any]) -> None:
    """Access and ID tokens are signed by the same keys; only the claim differs."""
    private, _ = keypair
    with pytest.raises(AuthError, match="ID token"):
        verifier.subject(_token(private, token_use="access"))


def test_rejects_an_expired_token(verifier: CognitoVerifier, keypair: tuple[Any, Any]) -> None:
    private, _ = keypair
    with pytest.raises(AuthError):
        verifier.subject(_token(private, exp=int(time.time()) - 60))


def test_rejects_another_pools_issuer(verifier: CognitoVerifier, keypair: tuple[Any, Any]) -> None:
    private, _ = keypair
    with pytest.raises(AuthError):
        verifier.subject(_token(private, iss=ISSUER.replace("_test", "_other")))


def test_rejects_a_token_signed_by_someone_else(verifier: CognitoVerifier) -> None:
    """The signature is what actually matters — an attacker controls the claims."""
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthError):
        verifier.subject(_token(attacker))


def test_rejects_an_unsigned_token(verifier: CognitoVerifier) -> None:
    """alg=none is the classic JWT bypass; PyJWT's algorithm allowlist blocks it."""
    forged = jwt.encode({"sub": "x", "iss": ISSUER, "token_use": "id"}, key="", algorithm="none")
    with pytest.raises(AuthError):
        verifier.subject(forged)


def test_rejects_garbage(verifier: CognitoVerifier) -> None:
    with pytest.raises(AuthError):
        verifier.subject("not-a-token")


def test_requires_a_subject(verifier: CognitoVerifier, keypair: tuple[Any, Any]) -> None:
    private, _ = keypair
    claims = {"iss": ISSUER, "token_use": "id", "exp": int(time.time()) + 60}
    with pytest.raises(AuthError):
        verifier.subject(jwt.encode(claims, private, algorithm="RS256"))


def test_no_pool_configured_disables_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """The local-development path: no pool means no verifier, not a crash."""
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    assert build_verifier() is None


def test_issuer_is_derived_from_pool_and_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "eu-north-1_abc123")
    monkeypatch.setenv("AWS_REGION", "eu-north-1")
    built = build_verifier()
    assert built is not None
    assert built.issuer == "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_abc123"
