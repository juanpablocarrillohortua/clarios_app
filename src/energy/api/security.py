"""Authentication, authorisation, and API key management.

Layer: api

Responsibility:
    Owns every decision about who a caller is and what they may see. Two
    distinct populations use this API and they need different treatment: plant
    edge devices authenticate with long-lived API keys scoped to a single plant,
    while humans authenticate with short-lived tokens carrying roles. The
    plant-scoping is the security-critical part - a compromised device key must
    not be able to read or write another plant's data, so every key carries its
    plant scope and repositories are filtered by it rather than trusting the
    caller's request parameters.

Depends on:
    ``energy.core.settings``, ``energy.domain.exceptions``, ``fastapi``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from energy.core.settings import Settings


class Role(StrEnum):
    """What a principal is allowed to do."""

    #: Read Gold KPIs and dashboards for the plants in scope.
    VIEWER = "viewer"
    #: Upload files and resolve quarantined rows.
    OPERATOR = "operator"
    #: Trigger backfills, retry runs, and edit reference data.
    ENGINEER = "engineer"
    #: Full access including key management.
    ADMIN = "admin"
    #: Machine principal: push readings for one plant, nothing else.
    DEVICE = "device"


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller.

    Attributes:
        subject: stable identifier of the caller - a username or a device id.
        roles: what the caller may do.
        plant_scope: plant codes the caller may access. An empty tuple means
            *no* plants, never "all plants": defaulting an empty scope to full
            access is the classic way this kind of check fails open.
        is_device: whether this is a machine principal.
        expires_at: when the credential stops being valid; ``None`` for
            non-expiring API keys.
    """

    subject: str
    roles: frozenset[Role]
    plant_scope: tuple[str, ...]
    is_device: bool
    expires_at: datetime | None

    def may_access_plant(self, plant_code: str) -> bool:
        """Return whether this principal may access a plant.

        Args:
            plant_code: the plant being requested.

        Returns:
            ``True`` when the plant is in scope, or the principal is an admin.
        """
        raise NotImplementedError("Phase 3: plant scope check")

    def has_role(self, *roles: Role) -> bool:
        """Return whether the principal holds any of the given roles.

        Args:
            *roles: roles to test for.

        Returns:
            ``True`` when at least one role matches.
        """
        raise NotImplementedError("Phase 3: role check")


def hash_api_key(raw_key: str, settings: Settings) -> str:
    """Hash an API key for storage.

    Keys are stored hashed, never in plaintext, so a database disclosure does not
    hand over working credentials.

    Args:
        raw_key: the key as presented by the caller.
        settings: process configuration supplying the signing secret.

    Returns:
        The hash to compare against stored values.
    """
    raise NotImplementedError("Phase 3: API key hashing")


def verify_api_key(raw_key: str, settings: Settings) -> Principal:
    """Resolve a presented API key to its principal.

    Args:
        raw_key: the key as presented by the caller.
        settings: process configuration.

    Returns:
        The authenticated :class:`Principal`.

    Raises:
        PermissionError: if the key is unknown, revoked, or expired. The error
            must not distinguish between those cases to the caller, so an
            attacker cannot enumerate valid keys.
    """
    raise NotImplementedError("Phase 3: API key verification")


def require_roles(principal: Principal, *roles: Role) -> None:
    """Assert that a principal holds at least one of the given roles.

    Args:
        principal: the authenticated caller.
        *roles: acceptable roles.

    Raises:
        PermissionError: if the principal holds none of them.
    """
    raise NotImplementedError("Phase 3: role enforcement")


def filter_plants(principal: Principal, requested: Sequence[str]) -> tuple[str, ...]:
    """Reduce a requested plant list to those the principal may see.

    Callers pass the result to repositories instead of the raw request
    parameters, which is what keeps authorisation from depending on every
    individual query remembering to check.

    Args:
        principal: the authenticated caller.
        requested: plant codes the caller asked for; empty means "all I may see".

    Returns:
        The permitted subset. Never empty-means-all: an empty result yields an
        empty response rather than an unscoped query.

    Raises:
        PermissionError: if the caller explicitly requested a plant outside their
            scope, which is worth surfacing rather than silently filtering.
    """
    raise NotImplementedError("Phase 3: plant scope filtering")


__all__ = [
    "Principal",
    "Role",
    "filter_plants",
    "hash_api_key",
    "require_roles",
    "verify_api_key",
]
