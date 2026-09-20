"""Application layer: use cases that orchestrate the domain.

Layer: services

A service coordinates ports and domain objects to fulfil one use case, and
owns the transaction boundary for it. Services may import ``domain``,
``ports`` and ``core``; they must never import a concrete adapter, a
framework, or a sibling entry point.
"""

from __future__ import annotations

__all__: list[str] = []
