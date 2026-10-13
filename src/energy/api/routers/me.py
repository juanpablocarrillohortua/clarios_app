"""The signed-in caller.

Layer: api

``GET /me`` tells the web app who is signed in and what they may do. It is the
app's only source for both: the app shows a section when the answer lists that
section's permission, and holds no rule about roles itself. Any signed-in person
may call it, with or without a role; a person without one gets an empty list and
the app shows them a page saying so.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from energy.api.deps import PrincipalDep
from energy.api.schemas.responses import MeResponse
from energy.api.security import permissions_of

router = APIRouter(tags=["session"])


@router.get(
    "/me",
    summary="Who is signed in, and what they may do",
    status_code=status.HTTP_200_OK,
)
def read_me(principal: PrincipalDep) -> MeResponse:
    """Return the caller's identity, roles and permissions.

    Args:
        principal: the signed-in caller, from the access token.

    Returns:
        A :class:`~energy.api.schemas.responses.MeResponse`.
    """
    return MeResponse(
        subject=principal.subject,
        name=principal.display_name,
        username=principal.username,
        roles=sorted(role.value for role in principal.roles),
        permissions=sorted(permission.value for permission in permissions_of(principal)),
        plant_scope=list(principal.plant_scope),
    )


__all__ = ["router"]
