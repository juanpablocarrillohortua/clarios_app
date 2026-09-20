"""Request-scoped middleware: correlation ids and structured access logging.

Layer: api

Every request gets a correlation id - taken from an inbound ``X-Request-ID``
header when the caller supplies one, generated otherwise - which is bound to the
structlog context so that *every* log line produced while handling the request
carries it, including lines emitted deep inside services and repositories. The
id is echoed back on the response so a client can quote it in a support ticket.

One structured access log line is emitted per request, with method, path, status
and duration. Uvicorn's own access log is silenced in ``core.logging`` so these
are not duplicated.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

from energy.core.constants import REQUEST_ID_HEADER
from energy.core.logging import bind_request_id, clear_request_context, get_logger

if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response

logger = get_logger(__name__)

# Paths that must not produce an access log line on every call: probes run every
# few seconds and would otherwise drown out real traffic.
QUIET_PATHS: frozenset[str] = frozenset({"/health", "/ready", "/metrics"})

_SERVER_ERROR_THRESHOLD = 500
_CLIENT_ERROR_THRESHOLD = 400


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a correlation id to the log context and emit one access log per request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Handle one request.

        Args:
            request: the inbound request.
            call_next: the next handler in the middleware chain.

        Returns:
            The downstream response with ``X-Request-ID`` attached.

        Raises:
            Exception: re-raised after logging, so FastAPI's exception handlers
                still run and the client receives the correct status code.
        """
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        clear_request_context()
        bind_request_id(request_id)
        request.state.request_id = request_id

        started = time.perf_counter()
        quiet = request.url.path in QUIET_PATHS

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "request failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 2),
            )
            clear_request_context()
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id

        if not quiet or response.status_code >= _CLIENT_ERROR_THRESHOLD:
            log = logger.info
            if response.status_code >= _SERVER_ERROR_THRESHOLD:
                log = logger.error
            elif response.status_code >= _CLIENT_ERROR_THRESHOLD:
                log = logger.warning
            log(
                "request handled",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
                client=request.client.host if request.client else None,
            )

        clear_request_context()
        return response


__all__ = ["QUIET_PATHS", "RequestContextMiddleware"]
