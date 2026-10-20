"""Roles, permissions, and the principal a checked token stands for.

Layer: tests (unit)

``energy.api.security`` is the only place where a role is given a meaning. These
tests pin that meaning down: the table of section 7 of ``docs/frontend-plan.md``
is restated here on purpose, so that changing what a role may do is a visible
change to a test, never a side effect.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from energy.api.security import (
    GRANTS,
    Permission,
    Principal,
    Role,
    permissions_of,
    principal_from_token,
    require_permission,
    require_roles,
)
from energy.domain.exceptions import PermissionDeniedError
from energy.ports.token_verifier import VerifiedToken

pytestmark = pytest.mark.unit

EXPIRY = datetime(2030, 1, 1, tzinfo=UTC)


def token(*roles: str) -> VerifiedToken:
    return VerifiedToken(
        subject="00000000-0000-4000-8000-00000000abcd",
        tenant_id="00000000-0000-4000-8000-0000000000aa",
        name="Ada Lovelace",
        username="ada@example.test",
        roles=roles,
        expires_at=EXPIRY,
    )


def person(*roles: Role) -> Principal:
    return Principal(
        subject="someone",
        roles=frozenset(roles),
        plant_scope=("1515",),
        is_device=False,
        expires_at=EXPIRY,
    )


# =============================================================================
# The table
# =============================================================================
def test_the_table_says_what_each_role_may_do() -> None:
    assert dict(GRANTS) == {
        Permission.DASHBOARD_VIEW: {Role.VIEWER, Role.OPERATOR, Role.ENGINEER, Role.ADMIN},
        Permission.LOADS_WRITE: {Role.OPERATOR, Role.ENGINEER, Role.ADMIN},
        Permission.CHAT_USE: {Role.VIEWER, Role.OPERATOR, Role.ENGINEER, Role.ADMIN},
    }


def test_every_permission_has_a_row() -> None:
    """A permission nobody is granted would lock an endpoint for everyone."""
    assert set(GRANTS) == set(Permission)
    assert all(GRANTS[permission] for permission in Permission)


def test_no_permission_of_people_is_granted_to_devices() -> None:
    assert all(Role.DEVICE not in roles for roles in GRANTS.values())


def test_the_table_cannot_be_changed_at_run_time() -> None:
    with pytest.raises(TypeError):
        GRANTS[Permission.LOADS_WRITE] = frozenset({Role.VIEWER})  # type: ignore[index]


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.VIEWER, {Permission.DASHBOARD_VIEW, Permission.CHAT_USE}),
        (Role.OPERATOR, {Permission.DASHBOARD_VIEW, Permission.LOADS_WRITE, Permission.CHAT_USE}),
        (Role.ENGINEER, {Permission.DASHBOARD_VIEW, Permission.LOADS_WRITE, Permission.CHAT_USE}),
        (Role.ADMIN, {Permission.DASHBOARD_VIEW, Permission.LOADS_WRITE, Permission.CHAT_USE}),
        (Role.DEVICE, set()),
    ],
)
def test_the_permissions_of_one_role(role: Role, expected: set[Permission]) -> None:
    assert permissions_of(person(role)) == expected


def test_a_person_without_a_role_may_do_nothing() -> None:
    assert permissions_of(person()) == set()


def test_permissions_add_up_across_roles() -> None:
    assert permissions_of(person(Role.VIEWER, Role.OPERATOR)) == {
        Permission.DASHBOARD_VIEW,
        Permission.LOADS_WRITE,
        Permission.CHAT_USE,
    }


# =============================================================================
# From a token to a principal
# =============================================================================
def test_a_token_becomes_a_person_scoped_to_the_one_plant() -> None:
    principal = principal_from_token(token("operator"))

    assert principal.subject == "00000000-0000-4000-8000-00000000abcd"
    assert principal.roles == {Role.OPERATOR}
    assert principal.plant_scope == ("1515",)
    assert principal.is_device is False
    assert principal.expires_at == EXPIRY
    assert principal.display_name == "Ada Lovelace"
    assert principal.username == "ada@example.test"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((), set()),
        (("viewer", "engineer"), {Role.VIEWER, Role.ENGINEER}),
        (("admin",), {Role.ADMIN}),
        # A value this application does not define grants nothing.
        (("superuser", "viewer"), {Role.VIEWER}),
        # Role values are exact: a typing mistake in Entra ID grants nothing.
        (("Operator", "OPERATOR", " operator"), set()),
        # A person is never a device, whatever the token says.
        (("device",), set()),
        (("device", "viewer"), {Role.VIEWER}),
    ],
)
def test_only_the_roles_of_people_are_taken_from_a_token(
    values: tuple[str, ...], expected: set[Role]
) -> None:
    assert principal_from_token(token(*values)).roles == expected


# =============================================================================
# The checks
# =============================================================================
def test_has_role_is_true_for_any_of_the_given_roles() -> None:
    operator = person(Role.OPERATOR)

    assert operator.has_role(Role.OPERATOR)
    assert operator.has_role(Role.ADMIN, Role.OPERATOR)
    assert not operator.has_role(Role.ADMIN, Role.ENGINEER)


def test_holding_any_of_no_roles_is_holding_nothing() -> None:
    assert not person(Role.ADMIN).has_role()


def test_require_roles_passes_or_names_what_was_needed() -> None:
    require_roles(person(Role.ENGINEER), Role.ENGINEER, Role.ADMIN)

    with pytest.raises(PermissionDeniedError) as caught:
        require_roles(person(Role.VIEWER), Role.ENGINEER, Role.ADMIN)
    assert caught.value.code == "forbidden"
    assert caught.value.details == {"roles": ["admin", "engineer"]}


def test_require_permission_passes_or_names_the_permission() -> None:
    require_permission(person(Role.OPERATOR), Permission.LOADS_WRITE)

    with pytest.raises(PermissionDeniedError) as caught:
        require_permission(person(Role.VIEWER), Permission.LOADS_WRITE)
    assert caught.value.details == {"permission": "loads:write"}

    with pytest.raises(PermissionDeniedError):
        require_permission(person(), Permission.DASHBOARD_VIEW)
