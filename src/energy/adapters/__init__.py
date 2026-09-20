"""Concrete implementations of the ports, one per external system.

Layer: adapters

Adapters are where the outside world is allowed in: Excel files, ERP
exports, historian APIs, MinIO, PostgreSQL. Each one implements a port and
translates between the external representation and the domain, including
unit conversion and timezone localisation. All of them write to Bronze
with full provenance; none of them contain business rules.
"""

from __future__ import annotations

__all__: list[str] = []
