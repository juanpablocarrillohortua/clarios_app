"""Pandera dataframe contracts, one module per medallion layer.

Layer: schemas

These schemas are the executable definition of what each layer guarantees.
A dataframe crossing a layer boundary is validated against the target
layer's schema first; rows that fail are routed to quarantine rather than
silently dropped. Schemas describe shape, dtype, nullability and ranges -
never how the data is computed.
"""

from __future__ import annotations

__all__: list[str] = []
