"""Microsoft Entra ID implementation of the token verifier port.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.token_verifier.TokenVerifier` for the
    access tokens Entra ID issues to the web app. A token is accepted only when
    every one of these holds, checked in this order:

    1. its header names the algorithm RS256 and a key identifier. A token that
       names no algorithm, or a symmetric one, is refused before any key is
       looked at;
    2. it is signed by the tenant's published key with that identifier;
    3. it has not expired and is already valid, with a minute of tolerance for
       clocks that disagree;
    4. it is a version 2.0 token of this tenant, from this tenant's issuer;
    5. its audience is this application;
    6. it carries the scope the application exposes, which means a person is
       signed in. A token an application obtained for itself has no scope;
    7. it names the person.

    The signing keys are fetched from Entra ID, kept for a day, and fetched
    again when a token names a key that is not among them - but never more often
    than every five minutes, so a flood of forged tokens cannot make this
    process call Entra ID on every request. If Entra ID does not answer, the
    keys already held keep working.

    This module knows nothing about roles or permissions: it passes the ``roles``
    claim on as it found it.

Depends on:
    ``energy.ports.token_verifier``, ``energy.domain.exceptions``,
    ``energy.core``, ``jwt`` (PyJWT), ``httpx``.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import httpx
import jwt

from energy.core.constants import ENTRA_LOGIN_HOST
from energy.core.logging import get_logger
from energy.domain.exceptions import AuthenticationError, IdentityProviderUnavailableError
from energy.ports.token_verifier import VerifiedToken

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

logger = get_logger(__name__)

_ALGORITHM: Final = "RS256"
_ACCEPTED_VERSION: Final = "2.0"
# Tolerance for a clock that runs ahead of or behind Entra ID's.
_CLOCK_TOLERANCE_SECONDS: Final = 60

# Microsoft's advice is to look for new signing keys about once a day.
_KEYS_MAX_AGE_SECONDS: Final = 24 * 60 * 60
# After a successful fetch, how long an unknown key identifier is refused
# without asking again.
_REFETCH_AFTER_SUCCESS_SECONDS: Final = 5 * 60
# After a failed fetch, how long before the next attempt.
_REFETCH_AFTER_FAILURE_SECONDS: Final = 30
_FETCH_TIMEOUT_SECONDS: Final = 5.0

_REQUIRED_CLAIMS: Final = ("exp", "nbf", "iat", "iss", "aud")


def _refuse(reason: str) -> AuthenticationError:
    """Build the refusal for one failed check.

    Args:
        reason: a short, stable word for the log. It never reaches the caller.

    Returns:
        The error to raise.
    """
    return AuthenticationError("the access token was refused", reason=reason)


class _SigningKeys:
    """The tenant's signing keys, held in memory and refreshed sparingly.

    Args:
        fetch: reads the key document from the identity provider.
        clock: a monotonic clock, in seconds.
    """

    def __init__(self, fetch: Callable[[], Mapping[str, Any]], clock: Callable[[], float]) -> None:
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, Any] = {}
        self._fresh_until = 0.0
        self._no_fetch_before = 0.0

    def get(self, key_id: str) -> Any | None:
        """Return the public key with an identifier, or ``None`` if the tenant has none.

        Args:
            key_id: the ``kid`` of a token's header.

        Returns:
            The public key, or ``None``.

        Raises:
            IdentityProviderUnavailableError: if no key is held and none could
                be fetched.
        """
        with self._lock:
            now = self._clock()
            if (not self._keys or now >= self._fresh_until) and now >= self._no_fetch_before:
                self._refresh(now)
            if not self._keys:
                raise IdentityProviderUnavailableError(
                    "the signing keys of the identity provider could not be read"
                )
            key = self._keys.get(key_id)
            if key is None and now >= self._no_fetch_before:
                # Entra ID publishes a new key before it signs with it, so an
                # unknown identifier usually means the held keys are old.
                self._refresh(now)
                key = self._keys.get(key_id)
            return key

    def _refresh(self, now: float) -> None:
        """Fetch the keys once. On failure, keep the ones already held."""
        try:
            key_set = jwt.PyJWKSet.from_dict(dict(self._fetch()))
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, TypeError) as exc:
            self._no_fetch_before = now + _REFETCH_AFTER_FAILURE_SECONDS
            logger.warning(
                "the signing keys could not be fetched",
                error=type(exc).__name__,
                keys_held=len(self._keys),
            )
            return

        self._keys = {
            key.key_id: key.key
            for key in key_set.keys
            if key.key_id and key.public_key_use in ("sig", None)
        }
        self._fresh_until = now + _KEYS_MAX_AGE_SECONDS
        self._no_fetch_before = now + _REFETCH_AFTER_SUCCESS_SECONDS


class EntraTokenVerifier:
    """Checks Entra ID access tokens issued to one application of one tenant.

    Args:
        tenant_id: the tenant's identifier, a GUID.
        client_id: the application's identifier, a GUID. It is the audience a
            version 2.0 token carries.
        scope_name: the scope the application exposes, for example
            ``access_as_user``.
        fetch_keys: reads the tenant's key document. Defaults to an HTTPS
            request to Entra ID; tests pass their own.
        clock: a monotonic clock in seconds, for the age of the held keys.
            Tests pass their own.
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        scope_name: str,
        *,
        fetch_keys: Callable[[], Mapping[str, Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._scope_name = scope_name
        self._issuer = f"{ENTRA_LOGIN_HOST}/{tenant_id}/v2.0"
        self._keys_url = f"{ENTRA_LOGIN_HOST}/{tenant_id}/discovery/v2.0/keys"
        self._keys = _SigningKeys(fetch_keys or self._fetch_keys, clock)

    def _fetch_keys(self) -> Mapping[str, Any]:
        """Read the tenant's key document from Entra ID."""
        response = httpx.get(self._keys_url, timeout=_FETCH_TIMEOUT_SECONDS)
        response.raise_for_status()
        document: Mapping[str, Any] = response.json()
        return document

    def verify(self, token: str) -> VerifiedToken:
        """Check a token and return what it says about its holder.

        Args:
            token: the token exactly as the caller presented it.

        Returns:
            The :class:`~energy.ports.token_verifier.VerifiedToken`.

        Raises:
            AuthenticationError: if any check of the module docstring fails.
            IdentityProviderUnavailableError: if the signing keys could not be
                read and none are held.
        """
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise _refuse("malformed") from exc

        if header.get("alg") != _ALGORITHM:
            raise _refuse("algorithm")
        key_id = header.get("kid")
        if not isinstance(key_id, str) or not key_id:
            raise _refuse("no_key_id")

        key = self._keys.get(key_id)
        if key is None:
            raise _refuse("unknown_key")

        claims = self._decode(token, key)
        self._check_origin(claims)
        return self._identity(claims)

    @staticmethod
    def _decode(token: str, key: Any) -> dict[str, Any]:
        """Check the signature and the validity period, and return the claims.

        The issuer and the audience are compared afterwards, by
        :meth:`_check_origin`, so that a token of the wrong version is reported
        as such and not as a wrong issuer.
        """
        try:
            return jwt.decode(
                token,
                key,
                algorithms=[_ALGORITHM],
                leeway=_CLOCK_TOLERANCE_SECONDS,
                options={
                    "require": list(_REQUIRED_CLAIMS),
                    "verify_aud": False,
                    "verify_iss": False,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise _refuse("expired") from exc
        except jwt.ImmatureSignatureError as exc:
            raise _refuse("not_yet_valid") from exc
        except jwt.InvalidSignatureError as exc:
            raise _refuse("bad_signature") from exc
        except jwt.PyJWTError as exc:
            raise _refuse("invalid") from exc

    def _check_origin(self, claims: Mapping[str, Any]) -> None:
        """Check that the token is ours: version, issuer, tenant, audience, scope."""
        if claims.get("ver") != _ACCEPTED_VERSION:
            # The registration's manifest must ask for version 2 tokens
            # (`requestedAccessTokenVersion`); version 1 names another issuer
            # and another audience.
            raise _refuse("wrong_version")
        if claims.get("iss") != self._issuer:
            raise _refuse("wrong_issuer")
        if claims.get("tid") != self._tenant_id:
            raise _refuse("wrong_tenant")

        audience = claims.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience
        if not isinstance(audiences, list) or self._client_id not in audiences:
            raise _refuse("wrong_audience")

        scopes = claims.get("scp")
        if not isinstance(scopes, str) or self._scope_name not in scopes.split():
            raise _refuse("missing_scope")

    def _identity(self, claims: Mapping[str, Any]) -> VerifiedToken:
        """Read the person and the role values out of checked claims."""
        subject = claims.get("oid")
        if not isinstance(subject, str) or not subject:
            raise _refuse("no_subject")

        roles = claims.get("roles")
        name = claims.get("name")
        username = claims.get("preferred_username")
        return VerifiedToken(
            subject=subject,
            tenant_id=self._tenant_id,
            name=name if isinstance(name, str) and name else None,
            username=username if isinstance(username, str) and username else None,
            roles=tuple(role for role in roles if isinstance(role, str))
            if isinstance(roles, list)
            else (),
            expires_at=datetime.fromtimestamp(float(claims["exp"]), tz=UTC),
        )


__all__ = ["EntraTokenVerifier"]
