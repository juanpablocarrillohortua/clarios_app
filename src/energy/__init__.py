"""Clarios Energy - industrial energy and gas consumption monitoring platform.

This package is a modular monolith organised as a hexagonal (ports and adapters)
architecture around a Bronze/Silver/Gold medallion data flow.

Layer dependency rule (enforced by ``tests/unit/test_architecture.py``):

===============  =========================================================
Layer            May import
===============  =========================================================
``domain``       nothing inside ``energy`` except ``domain`` itself
``ports``        ``domain``
``adapters``     ``domain``, ``ports``, ``core``, ``db``
``services``     ``domain``, ``ports``, ``core``
``pipelines``    ``domain``, ``ports``, ``adapters``, ``services``, ``core``
``api``          ``domain``, ``ports``, ``services``, ``db``, ``core``
``dashboards``   ``domain``, ``ports``, ``services``, ``core``
``analytics``    ``domain``, ``core``
``core``         nothing inside ``energy``
===============  =========================================================

``api``, ``dashboards`` and ``pipelines`` are siblings: none of them may import
another. That is what keeps the monolith modular.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
