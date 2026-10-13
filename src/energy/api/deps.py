"""Dependency injection wiring for the API layer.

Layer: api

Declares the ``Annotated`` aliases FastAPI route handlers use to receive their
collaborators. This is the single place where concrete adapters are bound to the
abstract ports the routers depend on, which is what keeps the routers testable:
a test overrides the provider, not the router.

It is the API's composition root: this module, and no other module of ``api``,
may import ``energy.adapters`` (ADR 0005, enforced by
``tests/unit/test_architecture.py``). It builds and hands over, and holds no
rule of its own: what a role may do is decided in :mod:`energy.api.security`.

Wired today: settings, database sessions, the request id, and the signed-in
caller. Repository and service providers arrive with the endpoints that need
them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from energy.adapters.entra_token_verifier import EntraTokenVerifier
from energy.api import security
from energy.api.security import Permission, Principal
from energy.core.constants import ENTRA_API_SCOPE_NAME
from energy.core.settings import Settings, get_settings
from energy.db.session import get_sessionmaker
from energy.domain.exceptions import AuthenticationError, SignInNotConfiguredError
from energy.ports.token_verifier import TokenVerifier

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Reads `Authorization: Bearer <token>`. It never answers by itself
# (`auto_error=False`): `get_principal` decides, so every refusal has the same
# shape. Declaring it also gives /docs its "Authorize" button.
_bearer = HTTPBearer(
    auto_error=False,
    description="An Entra ID access token issued for this application.",
)


def get_app_settings() -> Settings:
    """Return the process-wide settings.

    Returns:
        The cached :class:`~energy.core.settings.Settings` instance. Override
        this dependency in tests to inject a different configuration.
    """
    return get_settings()


def get_db_session() -> Iterator[Session]:
    """Provide one database session per request.

    The session is committed when the handler returns normally and rolled back if
    it raises, then always closed. Handlers receive an open session and must not
    commit or close it themselves.

    Yields:
        An open :class:`~sqlalchemy.orm.Session`.

    Raises:
        Exception: whatever the handler raised, after rolling back.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_request_id(request: Request) -> str:
    """Return the correlation id bound to this request.

    Args:
        request: the inbound request, populated by
            :class:`~energy.api.middleware.RequestContextMiddleware`.

    Returns:
        The ``X-Request-ID`` value, or an empty string if the middleware did not
        run (which only happens in unit tests that bypass it).
    """
    return str(getattr(request.state, "request_id", ""))


def require_api_key(
    settings: Annotated[Settings, Depends(get_app_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    """Reject requests that do not present a valid API key.

    For devices only. Phase 1 accepts any non-empty key outside production so
    that the scaffold is usable locally; Phase 3 replaces this with real key
    lookup in :mod:`energy.api.security`. People do not use it: they present a
    token, checked by :func:`get_principal`.

    Args:
        settings: process configuration, used to decide how strict to be.
        x_api_key: the inbound ``X-API-Key`` header.

    Returns:
        The presented key.

    Raises:
        HTTPException: ``401`` when the header is missing in a deployed
            environment.
    """
    if not x_api_key:
        if settings.is_production:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing API key",
                headers={"WWW-Authenticate": settings.api.api_key_header},
            )
        return ""
    return x_api_key


@lru_cache(maxsize=4)
def _entra_token_verifier(tenant_id: str, client_id: str) -> TokenVerifier:
    """Build the verifier for one app registration, once per process.

    It is kept because it holds the tenant's signing keys: a new one per
    request would fetch them every time.
    """
    return EntraTokenVerifier(
        tenant_id=tenant_id, client_id=client_id, scope_name=ENTRA_API_SCOPE_NAME
    )


def get_token_verifier(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TokenVerifier | None:
    """Bind the token verifier port to its Entra ID adapter.

    Args:
        settings: process configuration.

    Returns:
        The verifier, or ``None`` when ``ENTRA_TENANT_ID`` and
        ``ENTRA_CLIENT_ID`` are not both set. Override this dependency in tests
        to check tokens without a network.
    """
    entra = settings.entra
    if entra.tenant_id is None or entra.client_id is None:
        return None
    return _entra_token_verifier(entra.tenant_id, entra.client_id)


def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[TokenVerifier | None, Depends(get_token_verifier)],
) -> Principal:
    """Return the person calling, or refuse the request.

    A plain function on purpose: checking a token can fetch the signing keys
    over the network, and FastAPI runs a plain dependency in a worker thread,
    off the event loop.

    Args:
        request: the inbound request. The caller's identifier is left on it for
            the access log.
        credentials: the bearer token of the ``Authorization`` header, if any.
        verifier: the bound token verifier.

    Returns:
        The authenticated :class:`~energy.api.security.Principal`.

    Raises:
        AuthenticationError: if no bearer token was presented, or the one
            presented was refused. HTTP 401.
        SignInNotConfiguredError: if a token arrived but this process was not
            told which tenant and application to accept. HTTP 503.
        IdentityProviderUnavailableError: if the token could not be checked.
            HTTP 503.
    """
    # Checked first, so "no token" is a 401 whatever else is wrong.
    if credentials is None:
        raise AuthenticationError("no bearer token was presented", reason="missing")
    if verifier is None:
        raise SignInNotConfiguredError("sign-in is not configured on this server")

    principal = security.principal_from_token(verifier.verify(credentials.credentials))
    request.state.subject = principal.subject
    return principal


# -----------------------------------------------------------------------------
# Annotated aliases - route handlers depend on these, never on the functions.
# -----------------------------------------------------------------------------
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated["Session", Depends(get_db_session)]
RequestIdDep = Annotated[str, Depends(get_request_id)]
APIKeyDep = Annotated[str, Depends(require_api_key)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


def requires(permission: Permission) -> Callable[[Principal], Principal]:
    """Build the dependency that lets a request through only with a permission.

    Attach it to a router or a route::

        dependencies = [Depends(requires(Permission.LOADS_WRITE))]

    Args:
        permission: what the caller must be allowed to do.

    Returns:
        A dependency that returns the caller's
        :class:`~energy.api.security.Principal`, or raises
        :class:`~energy.domain.exceptions.PermissionDeniedError` (HTTP 403).
        Without a valid token the request is refused earlier, with 401.
    """

    def _guard(principal: PrincipalDep) -> Principal:
        security.require_permission(principal, permission)
        return principal

    return _guard


__all__ = [
    "APIKeyDep",
    "PrincipalDep",
    "RequestIdDep",
    "SessionDep",
    "SettingsDep",
    "get_app_settings",
    "get_db_session",
    "get_principal",
    "get_request_id",
    "get_token_verifier",
    "require_api_key",
    "requires",
]
