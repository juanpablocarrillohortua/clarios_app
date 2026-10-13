"""Settings the web app reads before anyone is signed in.

Layer: api

``GET /config`` is public, because the app needs its answer in order to sign a
user in. It returns identifiers only, never a secret: the tenant, the app
registration and the scope, all of which a browser shows in the address of the
sign-in page anyway.

Serving them from here, and not baking them into the app at build time, keeps
them out of the repository and out of the image, and lets one image run in every
environment. ``.env`` is the single place they are written.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from energy.api.deps import SettingsDep
from energy.api.schemas.responses import AuthClientConfig, ClientConfigResponse

router = APIRouter(tags=["session"])


@router.get(
    "/config",
    summary="Settings the web app needs before sign-in",
    status_code=status.HTTP_200_OK,
)
async def read_client_config(settings: SettingsDep) -> ClientConfigResponse:
    """Return the sign-in settings, or say that there are none.

    Args:
        settings: injected process configuration.

    Returns:
        A :class:`~energy.api.schemas.responses.ClientConfigResponse`. Its
        ``auth`` is ``None`` when ``ENTRA_TENANT_ID`` and ``ENTRA_CLIENT_ID``
        are not both set; the app then says that sign-in is not configured.
    """
    entra = settings.entra
    if (
        entra.tenant_id is None
        or entra.client_id is None
        or entra.authority is None
        or entra.api_scope is None
    ):
        return ClientConfigResponse(auth=None)

    return ClientConfigResponse(
        auth=AuthClientConfig(
            tenant_id=entra.tenant_id,
            client_id=entra.client_id,
            authority=entra.authority,
            api_scope=entra.api_scope,
        )
    )


__all__ = ["router"]
