"""Reading the dataset specs from their folder, as the API does when it starts.

What the files can get wrong that the domain never sees is here: YAML that does
not parse, a key written twice, a file that is not a spec, a missing or empty
folder. Every problem of every file is reported together, each with its file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.domain.exceptions import DatasetSpecError

if TYPE_CHECKING:
    from pathlib import Path

SOUND = """\
version: 1
key: {key}
kind: snapshot
label: datasets.{key}.name
description: datasets.{key}.description
entry_modes: [upload]
bronze:
  source_key: manual_sheets
  sheet: Hoja1
  dataset_key: {key}
silver:
  table: silver.{key}
  removed_rows: kept
natural_key: [Código]
columns:
  - {{header: Código, type: text, required: true}}
  - {{header: Activo, type: text, allowed: ["Sí", "No"]}}
"""


def write(config_dir: Path, name: str, text: str) -> Path:
    folder = config_dir / "datasets"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def problems_of(config_dir: Path) -> tuple[str, ...]:
    with pytest.raises(DatasetSpecError) as caught:
        load_dataset_specs(config_dir)
    return caught.value.problems


def test_the_committed_specs_are_sound(repo_root: Path) -> None:
    catalogue = load_dataset_specs(repo_root / "config")

    assert [spec.key for spec in catalogue] == ["lineas_produccion", "qad_facturas"]


def test_a_folder_of_sound_specs_is_read_in_full(tmp_path: Path) -> None:
    write(tmp_path, "second.yaml", SOUND.format(key="second"))
    write(tmp_path, "first.yaml", SOUND.format(key="first"))

    catalogue = load_dataset_specs(tmp_path)

    assert [spec.key for spec in catalogue] == ["first", "second"]
    assert catalogue.get("first").columns[1].allowed == ("Sí", "No")


def test_a_missing_folder_stops_everything(tmp_path: Path) -> None:
    (problem,) = problems_of(tmp_path)

    assert (
        problem
        == f"{(tmp_path / 'datasets').as_posix()}: no such folder; it must hold the dataset specs"
    )


def test_an_empty_folder_stops_everything(tmp_path: Path) -> None:
    (tmp_path / "datasets").mkdir()

    (problem,) = problems_of(tmp_path)

    assert problem.endswith("datasets: holds no dataset spec; the API needs at least one")


@pytest.mark.parametrize("name", ["README.md", "first.yml"])
def test_a_file_that_is_not_a_spec_is_refused_rather_than_skipped(
    tmp_path: Path, name: str
) -> None:
    write(tmp_path, "first.yaml", SOUND.format(key="first"))
    stray = write(tmp_path, name, SOUND.format(key="first"))

    (problem,) = problems_of(tmp_path)

    assert problem == (
        f"{stray.as_posix()}: not a dataset spec; the folder holds <key>.yaml files only"
    )


def test_a_spec_is_named_after_its_key(tmp_path: Path) -> None:
    path = write(tmp_path, "first.yaml", SOUND.format(key="second"))

    (problem,) = problems_of(tmp_path)

    assert problem == f"{path.as_posix()}: key: 'second' differs from the file's name, 'first'"


def test_yaml_that_does_not_parse_is_named_with_its_line(tmp_path: Path) -> None:
    path = write(tmp_path, "first.yaml", "version: 1\nkey: first\ncolumns: [unclosed\n")

    (problem,) = problems_of(tmp_path)

    assert problem.startswith(f"{path.as_posix()}: line 4, column 1: not valid YAML: ")


def test_a_key_written_twice_is_refused_not_overwritten(tmp_path: Path) -> None:
    # The standard YAML loader would keep the second type and say nothing.
    text = SOUND.format(key="first").replace(
        "{header: Activo, type: text,", "{header: Activo, type: text, type: date,"
    )
    path = write(tmp_path, "first.yaml", text)

    (problem,) = problems_of(tmp_path)

    assert problem.startswith(f"{path.as_posix()}: line 17, column ")
    assert problem.endswith("not valid YAML: the key 'type' appears twice")


def test_an_unquoted_no_is_explained(tmp_path: Path) -> None:
    write(tmp_path, "first.yaml", SOUND.format(key="first").replace('"No"', "No"))

    (problem,) = problems_of(tmp_path)

    assert "columns[1].allowed[1]: must be quoted text, not false" in problem


def test_the_problems_of_every_file_are_reported_together(tmp_path: Path) -> None:
    first = write(tmp_path, "first.yaml", SOUND.format(key="first").replace("snapshot", "stock"))
    second = write(tmp_path, "second.yaml", "version: 1\nkey: [second\n")

    problems = problems_of(tmp_path)

    assert len(problems) == 2
    assert problems[0].startswith(f"{first.as_posix()}: kind: 'stock' is not one of")
    assert problems[1].startswith(f"{second.as_posix()}: line ")
