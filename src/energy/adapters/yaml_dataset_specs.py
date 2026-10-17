"""Read the dataset specs from their YAML files.

Layer: adapters

Responsibility:
    Reads every spec of ``<CONFIG_DIR>/datasets/``, one file per dataset named
    ``<key>.yaml``, and hands each to the domain, which decides whether it is
    sound (:mod:`energy.domain.dataset_spec`). Every problem in every file is
    collected, and one :class:`~energy.domain.exceptions.DatasetSpecError`
    lists them all, so a single run shows everything that needs fixing.

    It is the one loader of the specs: the API reads them through it at start,
    ``energy datasets check`` through the pipelines' wiring, and the tests
    directly. Anything else in the folder is an error rather than something
    skipped, so a misnamed spec (``.yml``) can never be silently ignored.

    YAML that repeats a key in one mapping is refused too. The standard loader
    keeps the last value and says nothing, so two ``type`` lines in one column
    would pass unnoticed.

Depends on:
    ``energy.domain``, ``pyyaml``, standard library.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import yaml

from energy.domain.dataset_spec import DatasetCatalogue, build_catalogue, parse_dataset_spec
from energy.domain.exceptions import DatasetSpecError

if TYPE_CHECKING:
    from pathlib import Path

    from energy.domain.dataset_spec import DatasetSpec

#: The folder of ``CONFIG_DIR`` that holds the specs.
DATASETS_FOLDER: Final = "datasets"
SPEC_SUFFIX: Final = ".yaml"


class _StrictLoader(yaml.SafeLoader):
    """A safe YAML loader that refuses a key repeated in one mapping."""


def _construct_mapping(loader: _StrictLoader, node: yaml.Node) -> dict[object, object]:
    """Build a mapping, refusing a repeated key with the line it repeats on."""
    if not isinstance(node, yaml.MappingNode):  # the tag is only ever a mapping's
        raise yaml.constructor.ConstructorError(None, None, "expected a mapping", node.start_mark)
    seen: set[object] = set()
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"the key {key!r} appears twice", key_node.start_mark
            )
        seen.add(key)
    return dict(loader.construct_mapping(node))


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _read(path: Path) -> object:
    """Return a file's YAML content.

    Raises:
        DatasetSpecError: if the file is not valid YAML, naming the line.
    """
    shown = path.as_posix()
    try:
        # What yaml.load does, with a loader that is SafeLoader plus one check.
        loader = _StrictLoader(path.read_text(encoding="utf-8"))
        try:
            return loader.get_single_data()
        finally:
            loader.dispose()
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "somewhere"
        raise DatasetSpecError([f"{shown}: {where}: not valid YAML: {exc.problem}"]) from exc
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise DatasetSpecError([f"{shown}: not valid YAML: {exc}"]) from exc


def load_dataset_specs(config_dir: Path) -> DatasetCatalogue:
    """Read, check and gather every dataset spec.

    Args:
        config_dir: the configuration directory, ``CONFIG_DIR``. The specs are
            in its ``datasets`` folder.

    Returns:
        The :class:`~energy.domain.dataset_spec.DatasetCatalogue`.

    Raises:
        DatasetSpecError: listing every problem of every file, or saying that
            the folder is missing or holds no spec.
    """
    folder = config_dir / DATASETS_FOLDER
    if not folder.is_dir():
        raise DatasetSpecError(
            [f"{folder.as_posix()}: no such folder; it must hold the dataset specs"]
        )
    problems: list[str] = []
    specs: dict[str, DatasetSpec] = {}
    for path in sorted(folder.iterdir()):
        shown = path.as_posix()
        if not path.is_file() or path.suffix != SPEC_SUFFIX:
            problems.append(f"{shown}: not a dataset spec; the folder holds <key>.yaml files only")
            continue
        try:
            specs[shown] = parse_dataset_spec(_read(path), origin=shown, expected_key=path.stem)
        except DatasetSpecError as exc:
            problems.extend(exc.problems)
    if problems:
        raise DatasetSpecError(problems)
    return build_catalogue(specs, origin=folder.as_posix())


__all__ = ["DATASETS_FOLDER", "load_dataset_specs"]
