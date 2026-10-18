"""Port for checking the access token a caller presents.

Layer: ports

Responsibility:
    Declares how the application turns the token of an ``Authorization: Bearer``
    header into an identity it can trust. A verifier answers one question: was
    this token issued by our identity provider, for this application, to a
    person, and is it still valid? It reports who the person is and which role
    values the provider attached. It decides nothing about what a role may do:
    that is :mod:`energy.api.security`. This protocol never mentions Entra ID,
    signing keys or JWTs; those belong to
    :mod:`energy.adapters.entra_token_verifier`.

Depends on:
    ``energy.domain``, standard library typing only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    """What a token says about its holder, once the token has been checked.

    Attributes:
        subject: the identifier of the person in the identity provider. It is
            stable: it does not change when the person's name or address does.
        tenant_id: the directory the person belongs to.
        name: the person's display name, when the token carries one.
        username: the name the person signs in with, when the token carries
            one. For display only: it can change, so it identifies nobody.
        roles: the role values the provider attached, as it wrote them. They
            are not interpreted here.
        expires_at: when the token stops being valid, timezone-aware UTC.
    """

    subject: str
    tenant_id: str
    name: str | None
    username: str | None
    roles: tuple[str, ...]
    expires_at: datetime


@runtime_checkable
class TokenVerifier(Protocol):
    """Interface for checking a bearer token."""

    def verify(self, token: str) -> VerifiedToken:
        """Check a token and return what it says about its holder.

        Args:
            token: the token exactly as the caller presented it.

        Returns:
            The :class:`VerifiedToken`.

        Raises:
            AuthenticationError: if the token is malformed, is not signed by the
                identity provider, was issued for someone else, has expired, or
                carries no person. ``details["reason"]`` says which, for the
                log.
            IdentityProviderUnavailableError: if the token cannot be checked
                because the provider's signing keys could not be read.
        """
        ...


__all__ = ["TokenVerifier", "VerifiedToken"]
