"""Database access: engine, sessions, ORM models, and health probes.

Layer: db (infrastructure)

This package owns the SQLAlchemy engine and the SQLModel table definitions. It
is imported by ``adapters`` (which implement the repository ports on top of it)
and by ``api`` (for dependency injection and health checks). The ``domain``
layer must never import it.
"""

from __future__ import annotations

from energy.db.session import dispose_engine, get_engine, get_sessionmaker, session_scope

__all__ = ["dispose_engine", "get_engine", "get_sessionmaker", "session_scope"]
