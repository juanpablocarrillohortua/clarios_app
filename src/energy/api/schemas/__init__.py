"""Pydantic request and response models for the HTTP boundary.

Layer: api

These models are the API's public contract and are versioned with it. They
are deliberately separate from the domain entities and from the SQLModel
tables: a change to a database column must never silently change the shape
of an HTTP response.
"""

from __future__ import annotations

__all__: list[str] = []
