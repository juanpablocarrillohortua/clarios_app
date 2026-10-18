"""Enforce the layer dependency rule and prove every module imports cleanly.

Two jobs:

1. **Import sweep.** Every module under ``src/energy`` is imported. A placeholder
   whose docstring is its deliverable is still code, and a typo in one would
   otherwise go unnoticed until the phase that implements it.

2. **Layer rule.** The hexagonal boundaries are parsed out of the source with
   ``ast`` and checked. Documenting a dependency rule is not the same as having
   one; this test is what makes it real. It reads imports statically rather than
   at runtime so that a violation inside a ``TYPE_CHECKING`` block - which would
   never execute - is caught too.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import energy

SRC_ROOT = Path(energy.__file__).resolve().parent

# What each layer is allowed to import from inside `energy`.
# `domain` and `core` depend on nothing; `api`, `dashboards` and `pipelines` are
# siblings and may never import one another.
ALLOWED: dict[str, frozenset[str]] = {
    "core": frozenset(),
    "domain": frozenset(),
    "ports": frozenset({"domain"}),
    "schemas": frozenset({"domain", "core"}),
    "adapters": frozenset({"domain", "ports", "core", "db"}),
    "db": frozenset({"domain", "core"}),
    "services": frozenset({"domain", "ports", "core"}),
    "analytics": frozenset({"domain", "core"}),
    "pipelines": frozenset({"domain", "ports", "adapters", "services", "schemas", "core", "db"}),
    "api": frozenset({"domain", "ports", "services", "schemas", "db", "core"}),
    "dashboards": frozenset({"domain", "ports", "services", "core"}),
}

SIBLING_ENTRY_POINTS = frozenset({"api", "dashboards", "pipelines"})

# Exceptions to ALLOWED, by module. Each entry point needs one place where
# adapters are bound to the ports its code depends on. `pipelines` may import
# adapters as a layer; the API may not, so its composition root is named here:
# `api/deps.py`, and no other module of `api`, may import `adapters`.
# Decided in docs/adr/0005-api-composition-root.md. Do not add to this without
# a decision record of its own.
COMPOSITION_ROOTS: dict[str, frozenset[str]] = {
    "api/deps.py": frozenset({"adapters"}),
}


def _iter_modules() -> list[str]:
    """Return every importable module name under ``energy``."""
    names = ["energy"]
    for info in pkgutil.walk_packages([str(SRC_ROOT)], prefix="energy."):
        # __main__ executes on import via its `if __name__` guard being absent
        # from the import path; it is exercised separately by the CLI tests.
        if info.name.endswith(".__main__"):
            continue
        names.append(info.name)
    return sorted(names)


def _layer_of(module_name: str) -> str | None:
    """Return the layer a module belongs to, or ``None`` for the package root."""
    parts = module_name.split(".")
    if len(parts) < 2:
        return None
    return parts[1]


def _internal_imports(path: Path) -> set[str]:
    """Return every ``energy.*`` module imported by a source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("energy"):
                    found.add(alias.name)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.level == 0
            and node.module.startswith("energy")
        ):
            found.add(node.module)

    return found


ALL_MODULES = _iter_modules()
ALL_SOURCE_FILES = sorted(SRC_ROOT.rglob("*.py"))


@pytest.mark.unit
@pytest.mark.parametrize("module_name", ALL_MODULES)
def test_module_imports_cleanly(module_name: str) -> None:
    """Every module under `energy` imports without error."""
    importlib.import_module(module_name)


@pytest.mark.unit
def test_every_module_has_a_docstring() -> None:
    """Every module carries a module-level docstring.

    The docstring is the Phase 1 deliverable for placeholder modules, so an
    empty one is a missing deliverable rather than a style nit.
    """
    missing: list[str] = []
    for path in ALL_SOURCE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstring = ast.get_docstring(tree)
        if not docstring or len(docstring.strip()) < 20:
            missing.append(str(path.relative_to(SRC_ROOT)))

    assert not missing, f"modules without a substantive docstring: {missing}"


@pytest.mark.unit
@pytest.mark.parametrize("source_file", ALL_SOURCE_FILES, ids=lambda p: str(p.name))
def test_layer_dependency_rule(source_file: Path) -> None:
    """No module imports from a layer it is not allowed to depend on."""
    relative = source_file.relative_to(SRC_ROOT)
    if not relative.parts or relative.parts[0].endswith(".py"):
        return  # package root modules carry no layer

    layer = relative.parts[0]
    if layer not in ALLOWED:
        return

    permitted = ALLOWED[layer] | COMPOSITION_ROOTS.get(relative.as_posix(), frozenset())
    violations: list[str] = []

    for imported in _internal_imports(source_file):
        target_layer = _layer_of(imported)
        if target_layer is None or target_layer == layer:
            continue
        if target_layer not in permitted:
            violations.append(f"{imported} (layer '{target_layer}')")

    assert not violations, (
        f"{relative} is in layer '{layer}', which may import {sorted(permitted) or 'nothing'}, "
        f"but imports: {sorted(violations)}"
    )


@pytest.mark.unit
def test_domain_is_pure() -> None:
    """The domain layer imports nothing from the rest of the application.

    This is the single most important structural guarantee in the codebase: it
    is what keeps the business rules testable without a database, a container,
    or a network.
    """
    domain_files = sorted((SRC_ROOT / "domain").rglob("*.py"))
    assert domain_files, "expected the domain package to contain modules"

    for path in domain_files:
        for imported in _internal_imports(path):
            layer = _layer_of(imported)
            assert layer in (None, "domain"), (
                f"{path.relative_to(SRC_ROOT)} imports {imported}; "
                "the domain layer must depend on nothing but itself"
            )


@pytest.mark.unit
@pytest.mark.parametrize("entry_point", sorted(SIBLING_ENTRY_POINTS))
def test_entry_points_do_not_import_each_other(entry_point: str) -> None:
    """`api`, `dashboards` and `pipelines` are siblings, not dependencies.

    This is what keeps the monolith modular: each could be extracted into its
    own deployable without untangling an import from the others.
    """
    others = SIBLING_ENTRY_POINTS - {entry_point}
    for path in sorted((SRC_ROOT / entry_point).rglob("*.py")):
        for imported in _internal_imports(path):
            layer = _layer_of(imported)
            assert layer not in others, (
                f"{path.relative_to(SRC_ROOT)} imports {imported}, but '{entry_point}' "
                f"and '{layer}' are sibling entry points and must stay independent"
            )


@pytest.mark.unit
def test_only_the_composition_root_of_the_api_imports_adapters() -> None:
    """In `api`, adapters are named in `deps.py` and nowhere else.

    The exception of ADR 0005 is one module wide. A router that imported an
    adapter would need a database or a network to be tested, which is what the
    layer rule exists to prevent; this test fails for it even if someone widens
    COMPOSITION_ROOTS to let it through.
    """
    grown = "an exception to the layer rule needs a decision record of its own"
    assert set(COMPOSITION_ROOTS) == {"api/deps.py"}, grown
    assert set(COMPOSITION_ROOTS["api/deps.py"]) == {"adapters"}, grown

    importers = {
        path.relative_to(SRC_ROOT).as_posix()
        for path in (SRC_ROOT / "api").rglob("*.py")
        if any(_layer_of(imported) == "adapters" for imported in _internal_imports(path))
    }

    assert importers <= {"api/deps.py"}, (
        f"only api/deps.py may import energy.adapters, but so do: {sorted(importers)}"
    )


@pytest.mark.unit
def test_ports_declare_protocols() -> None:
    """Every port module defines at least one Protocol class.

    A port that is not a Protocol is a concrete dependency wearing the wrong
    name, and adapters would end up inheriting from it rather than satisfying it.
    """
    port_files = [p for p in (SRC_ROOT / "ports").rglob("*.py") if p.name != "__init__.py"]
    assert port_files, "expected the ports package to contain modules"

    for path in port_files:
        source = path.read_text(encoding="utf-8")
        assert "Protocol" in source, (
            f"{path.relative_to(SRC_ROOT)} defines no Protocol; ports must be structural interfaces"
        )
