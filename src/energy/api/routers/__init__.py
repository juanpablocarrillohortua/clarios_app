"""HTTP route handlers grouped by resource.

Layer: api

Each module owns one resource family and exposes a module-level ``router`` that
``energy.api.main`` mounts. Handlers validate input, call a service, and
serialise the result - they contain no business rules and open no database
connections of their own.

Submodules are imported explicitly here rather than relied upon implicitly:
mypy's strict mode disables implicit re-export, so ``from energy.api.routers
import kpis`` only type-checks when the name is bound and listed in ``__all__``.
"""

from __future__ import annotations

from energy.api.routers import client_config, health, ingest, kpis, me, runs, uploads

__all__ = ["client_config", "health", "ingest", "kpis", "me", "runs", "uploads"]
