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

Wired today: settings, database sessions, the request id, the signed-in
caller, the dataset specs (read once, when the app is built), the writer of
their templates, the draft and load services of the loading page, and the
chat assistant (each built on first use and kept on the app). Repository and
service providers arrive with the endpoints that need them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.chat_wiring import build_chat_model, build_reporting_query, require_chat_key
from energy.adapters.entra_token_verifier import EntraTokenVerifier
from energy.adapters.local_storage import LocalObjectStorage
from energy.adapters.minio_storage import MinioObjectStorage
from energy.adapters.postgres_draft_repo import PostgresDraftRepository
from energy.adapters.postgres_load_committer import PostgresLoadCommitter
from energy.adapters.postgres_load_ledger import PostgresLoadLedger
from energy.adapters.xlsx_dataset_template import XlsxDatasetTemplateWriter
from energy.adapters.yaml_chat_sources import load_chat_sources
from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.adapters.zip_package import ZipPackageInspector
from energy.api import security
from energy.api.security import Permission, Principal
from energy.core.constants import ENTRA_API_SCOPE_NAME
from energy.core.settings import Settings, get_settings
from energy.db.session import get_engine, get_sessionmaker
from energy.domain.dataset_spec import DatasetCatalogue
from energy.domain.exceptions import AuthenticationError, SignInNotConfiguredError
from energy.ports.dataset_template import DatasetTemplateWriter
from energy.ports.object_storage import ObjectStorage
from energy.ports.token_verifier import TokenVerifier
from energy.services.chat_data_service import ChatDataService
from energy.services.chat_service import ChatService
from energy.services.draft_service import DraftService
from energy.services.load_service import LoadService

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


def load_dataset_catalogue(settings: Settings) -> DatasetCatalogue:
    """Read and check every dataset spec of ``<CONFIG_DIR>/datasets``.

    Called once, by :func:`energy.api.main.create_app`, so a malformed spec
    stops the API before it serves anything.

    Args:
        settings: the configuration, which names ``CONFIG_DIR``.

    Returns:
        The :class:`~energy.domain.dataset_spec.DatasetCatalogue`.

    Raises:
        DatasetSpecError: listing every problem of every spec.
    """
    return load_dataset_specs(settings.data.config_dir)


def get_dataset_catalogue(request: Request) -> DatasetCatalogue:
    """Return the dataset specs the app was built with.

    Args:
        request: the current request, whose app holds them.

    Returns:
        The :class:`~energy.domain.dataset_spec.DatasetCatalogue`.
    """
    catalogue: DatasetCatalogue = request.app.state.dataset_catalogue
    return catalogue


def get_template_writer() -> DatasetTemplateWriter:
    """Return the writer of dataset templates.

    Returns:
        The ``.xlsx`` writer, as the port the routers depend on.
    """
    return XlsxDatasetTemplateWriter()


def build_object_storage(settings: Settings) -> ObjectStorage:
    """Return the object storage originals are archived into.

    Args:
        settings: the configuration; ``ARCHIVE_BACKEND`` chooses MinIO (the
            stack) or a directory.

    Returns:
        The storage, as the port.
    """
    if settings.archive.backend == "local":
        return LocalObjectStorage(settings.archive.local_dir)
    return MinioObjectStorage(settings.minio)


def get_draft_service(request: Request) -> DraftService:
    """Return the draft service, building it on first use.

    Args:
        request: the current request, whose app holds the settings, the
            dataset specs and, once built, the service.

    Returns:
        The :class:`~energy.services.draft_service.DraftService`.
    """
    service: DraftService | None = getattr(request.app.state, "draft_service", None)
    if service is None:
        settings: Settings = request.app.state.settings
        service = DraftService(
            repository=PostgresDraftRepository(get_engine()),
            storage=build_object_storage(settings),
            reader=CalamineWorkbookReader(),
            inspector=ZipPackageInspector(),
            catalogue=request.app.state.dataset_catalogue,
            bucket=settings.minio.bucket_bronze,
        )
        request.app.state.draft_service = service
    return service


def get_load_service(request: Request) -> LoadService:
    """Return the load service, building it on first use.

    Args:
        request: the current request, whose app holds the settings, the
            dataset specs and, once built, the service.

    Returns:
        The :class:`~energy.services.load_service.LoadService`.
    """
    service: LoadService | None = getattr(request.app.state, "load_service", None)
    if service is None:
        settings: Settings = request.app.state.settings
        engine = get_engine()
        service = LoadService(
            drafts=get_draft_service(request),
            committer=PostgresLoadCommitter(engine),
            ledger=PostgresLoadLedger(engine),
            storage=build_object_storage(settings),
            catalogue=request.app.state.dataset_catalogue,
        )
        request.app.state.load_service = service
    return service


def get_chat_service(request: Request) -> ChatService:
    """Return the chat assistant, building it on first use.

    It is wired as ``pipelines/flows/chat.py`` wires it for the development
    page: the model, with the data tools of
    :class:`~energy.services.chat_data_service.ChatDataService`, reading
    where ``CHAT_DATA_SOURCE`` says. Once built it is kept on the app, so the
    simulated data are built once. A failure is not kept: the next request
    tries again, and the API starts and serves everything else without a key.

    Args:
        request: the current request, whose app holds the settings and, once
            built, the service.

    Returns:
        The :class:`~energy.services.chat_service.ChatService`.

    Raises:
        ChatNotConfiguredError: if the model key is missing, the instructions
            are missing or empty, or database mode has no reporting login
            (HTTP 503). The key is checked first, so a refusal builds nothing.
    """
    service: ChatService | None = getattr(request.app.state, "chat_service", None)
    if service is None:
        settings: Settings = request.app.state.settings
        require_chat_key(settings)
        data = ChatDataService(
            build_reporting_query(settings),
            load_chat_sources(settings.data.config_dir),
            row_limit=settings.chat.sql_row_limit,
            timeout_seconds=settings.chat.sql_timeout_seconds,
        )
        model = build_chat_model(
            settings, tools=(data.list_sources, data.describe_reporting, data.run_sql)
        )
        service = ChatService(
            model,
            history_limit=settings.chat.context_window_limit,
            notes=(data.sources_note(),),
        )
        request.app.state.chat_service = service
    return service


# -----------------------------------------------------------------------------
# Annotated aliases - route handlers depend on these, never on the functions.
# -----------------------------------------------------------------------------
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated["Session", Depends(get_db_session)]
RequestIdDep = Annotated[str, Depends(get_request_id)]
APIKeyDep = Annotated[str, Depends(require_api_key)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
DatasetCatalogueDep = Annotated[DatasetCatalogue, Depends(get_dataset_catalogue)]
TemplateWriterDep = Annotated[DatasetTemplateWriter, Depends(get_template_writer)]
DraftServiceDep = Annotated[DraftService, Depends(get_draft_service)]
LoadServiceDep = Annotated[LoadService, Depends(get_load_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]


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
    "ChatServiceDep",
    "DatasetCatalogueDep",
    "DraftServiceDep",
    "LoadServiceDep",
    "PrincipalDep",
    "RequestIdDep",
    "SessionDep",
    "SettingsDep",
    "TemplateWriterDep",
    "build_object_storage",
    "get_app_settings",
    "get_chat_service",
    "get_dataset_catalogue",
    "get_db_session",
    "get_draft_service",
    "get_load_service",
    "get_principal",
    "get_request_id",
    "get_template_writer",
    "get_token_verifier",
    "load_dataset_catalogue",
    "require_api_key",
    "requires",
]
