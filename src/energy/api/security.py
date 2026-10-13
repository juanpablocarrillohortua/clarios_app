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

    Built for people (increment 2 of ``docs/frontend-plan.md``): the roles, the
    table of what each role may do (:data:`GRANTS`), and the mapping from a
    checked token to a :class:`Principal`. That table is the only place where a
    role is given a meaning; the web app receives the resulting permissions
    from ``GET /me`` and holds no rule of its own.

    Still placeholders, for Phase 3: device API keys (:func:`hash_api_key`,
    :func:`verify_api_key`) and filtering by plant
    (:meth:`Principal.may_access_plant`, :func:`filter_plants`). Until then
    every person is scoped to the one plant there is.

Depends on:
    ``energy.core.settings``, ``energy.domain``, ``energy.ports.token_verifier``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from energy.domain.exceptions import PermissionDeniedError
from energy.domain.landing import DEFAULT_PLANT_CODE

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from energy.core.settings import Settings
    from energy.ports.token_verifier import VerifiedToken


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


class Permission(StrEnum):
    """One thing a caller may be allowed to do.

    Endpoints ask for a permission, never for a role, and the web app shows a
    section when ``GET /me`` lists its permission. A new capability is a new
    member here and a new row in :data:`GRANTS`.
    """

    #: See the dashboard and the figures behind it.
    DASHBOARD_VIEW = "dashboard:view"
    #: Load data, accept its warnings, and see the history of loads.
    LOADS_WRITE = "loads:write"


#: Which roles grant each permission. A role is listed wherever it applies:
#: nothing is inherited, so the table reads as it is enforced.
GRANTS: Final[Mapping[Permission, frozenset[Role]]] = MappingProxyType(
    {
        Permission.DASHBOARD_VIEW: frozenset(
            {Role.VIEWER, Role.OPERATOR, Role.ENGINEER, Role.ADMIN}
        ),
        Permission.LOADS_WRITE: frozenset({Role.OPERATOR, Role.ENGINEER, Role.ADMIN}),
    }
)

# The roles a signed-in person may hold. `device` is for machines and is never
# taken from a person's token, whatever the token says.
_PERSON_ROLES: Final = frozenset({Role.VIEWER, Role.OPERATOR, Role.ENGINEER, Role.ADMIN})


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller.

    Attributes:
        subject: stable identifier of the caller - the person's identifier in
            the identity provider, or a device id.
        roles: what the caller may do.
        plant_scope: plant codes the caller may access. An empty tuple means
            *no* plants, never "all plants": defaulting an empty scope to full
            access is the classic way this kind of check fails open.
        is_device: whether this is a machine principal.
        expires_at: when the credential stops being valid; ``None`` for
            non-expiring API keys.
        display_name: the person's name, for display; ``None`` when unknown.
        username: the name the person signs in with, for display; ``None``
            when unknown. It can change, so nothing may be keyed on it.
    """

    subject: str
    roles: frozenset[Role]
    plant_scope: tuple[str, ...]
    is_device: bool
    expires_at: datetime | None
    display_name: str | None = None
    username: str | None = None

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
            ``True`` when at least one role matches. ``False`` when no role is
            given: holding "any of nothing" is not holding anything.
        """
        return not self.roles.isdisjoint(roles)


def principal_from_token(token: VerifiedToken) -> Principal:
    """Build the principal a checked token stands for.

    A role value this application does not define is ignored, so a typing
    mistake in the identity provider grants nothing. So is ``device``: a person
    never becomes a machine principal.

    Args:
        token: what the token verifier read from a valid token.

    Returns:
        The :class:`Principal`, scoped to the one plant there is.
    """
    known = {role.value: role for role in _PERSON_ROLES}
    return Principal(
        subject=token.subject,
        roles=frozenset(known[value] for value in token.roles if value in known),
        plant_scope=(DEFAULT_PLANT_CODE,),
        is_device=False,
        expires_at=token.expires_at,
        display_name=token.name,
        username=token.username,
    )


def permissions_of(principal: Principal) -> frozenset[Permission]:
    """Return everything a principal may do.

    Args:
        principal: the authenticated caller.

    Returns:
        The permissions its roles grant; empty for a caller with no role.
    """
    return frozenset(
        permission for permission, roles in GRANTS.items() if principal.has_role(*roles)
    )


def require_permission(principal: Principal, permission: Permission) -> None:
    """Assert that a principal may do one thing.

    Args:
        principal: the authenticated caller.
        permission: what the caller is about to do.

    Raises:
        PermissionDeniedError: if none of the principal's roles grants it.
    """
    if not principal.has_role(*GRANTS[permission]):
        raise PermissionDeniedError("the caller may not do this", permission=permission.value)


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

    Endpoints use :func:`require_permission`; this is the check underneath, for
    the rare place that must name roles.

    Args:
        principal: the authenticated caller.
        *roles: acceptable roles.

    Raises:
        PermissionDeniedError: if the principal holds none of them.
    """
    if not principal.has_role(*roles):
        raise PermissionDeniedError(
            "the caller holds none of the roles this needs",
            roles=sorted(role.value for role in roles),
        )


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
    "GRANTS",
    "Permission",
    "Principal",
    "Role",
    "filter_plants",
    "hash_api_key",
    "permissions_of",
    "principal_from_token",
    "require_permission",
    "require_roles",
    "verify_api_key",
]
