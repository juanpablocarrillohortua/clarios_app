"""Abstract interfaces the application depends on.

Layer: ports

Every port is a :class:`typing.Protocol` describing what the application
needs, expressed in domain terms. Ports may import ``domain`` and nothing
else - no driver, no SDK, no framework. ``adapters`` supplies the concrete
implementations, and dependency injection binds them at the edge.
"""

from __future__ import annotations

__all__: list[str] = []
