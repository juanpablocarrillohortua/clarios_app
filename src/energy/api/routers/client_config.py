"""Settings the web app reads before anyone is signed in.

Layer: api

``GET /config`` is public, because the app needs its answer in order to sign a
user in. It returns identifiers only, never a secret:

- the tenant, the app registration and the scope, all of which a browser shows
  in the address of the sign-in page anyway;
- the Power BI report the dashboard shows. Its two identifiers appear in the
  report's address and open nothing without the token of a user who has access.

Serving them from here, and not baking them into the app at build time, keeps
them out of the repository and out of the image, and lets one image run in every
environment. ``.env`` is the single place they are written.

This process never calls Power BI. It tells the app which report to ask for,
and the browser asks with the signed-in user's own token.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, status

from energy.api.deps import SettingsDep
from energy.api.schemas.responses import (
    AuthClientConfig,
    ClientConfigResponse,
    PowerBIClientConfig,
)
from energy.core.constants import POWERBI_REPORT_SCOPE

if TYPE_CHECKING:
    from energy.core.settings import EntraSettings, PowerBISettings

router = APIRouter(tags=["session"])


def _auth(entra: EntraSettings) -> AuthClientConfig | None:
    """Return the sign-in settings, or ``None`` when an identifier is missing."""
    if (
        entra.tenant_id is None
        or entra.client_id is None
        or entra.authority is None
        or entra.api_scope is None
    ):
        return None
    return AuthClientConfig(
        tenant_id=entra.tenant_id,
        client_id=entra.client_id,
        authority=entra.authority,
        api_scope=entra.api_scope,
    )


def _powerbi(powerbi: PowerBISettings) -> PowerBIClientConfig | None:
    """Return the report of the dashboard, or ``None`` when an identifier is missing."""
    if (
        powerbi.workspace_id is None
        or powerbi.report_id is None
        or powerbi.report_api_url is None
        or powerbi.report_web_url is None
    ):
        return None
    return PowerBIClientConfig(
        workspace_id=powerbi.workspace_id,
        report_id=powerbi.report_id,
        scope=POWERBI_REPORT_SCOPE,
        report_api_url=powerbi.report_api_url,
        report_web_url=powerbi.report_web_url,
    )


@router.get(
    "/config",
    summary="Settings the web app needs before sign-in",
    status_code=status.HTTP_200_OK,
)
async def read_client_config(settings: SettingsDep) -> ClientConfigResponse:
    """Return the sign-in settings and the dashboard's report, or say that there are none.

    Args:
        settings: injected process configuration.

    Returns:
        A :class:`~energy.api.schemas.responses.ClientConfigResponse`.

        Its ``auth`` is ``None`` when ``ENTRA_TENANT_ID`` and ``ENTRA_CLIENT_ID``
        are not both set; the app then says that sign-in is not configured.

        Its ``powerbi`` is ``None`` when ``POWERBI_WORKSPACE_ID`` and
        ``POWERBI_REPORT_ID`` are not both set; the dashboard then says that the
        report is not configured, and the rest of the app works.
    """
    return ClientConfigResponse(
        auth=_auth(settings.entra),
        powerbi=_powerbi(settings.powerbi),
    )


__all__ = ["router"]
