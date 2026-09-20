"""HTTP interface: FastAPI application, routers, and request/response schemas.

Layer: api

One of three sibling entry points (``api``, ``dashboards``, ``pipelines``). It
may depend on ``domain``, ``ports``, ``services``, ``db`` and ``core``, and must
never import either sibling.

Routers stay thin: they parse and validate input, delegate to a service, and
serialise the result. Business rules belong in ``domain``, orchestration in
``services``.
"""

from __future__ import annotations

__all__: list[str] = []
