"""Write the synthetic workbooks of the upload checks to a folder.

Every file is made up: no real data. They are the files of
``tests/fixtures/workbooks.py``, for trying the loading page by hand:

* ``lineas_buena.xlsx`` - a production-lines catalogue that breaks no rule;
* ``lineas_rota.xlsx`` - the same catalogue broken on purpose, one row per
  rule a cell can break, and a header with a misspelt, a missing and an
  unknown column. ``lineas_rota.manifest.tsv`` lists every issue it must show,
  as row, column and rule;
* ``facturas_rota.xlsx`` - an invoices export with every header, and dates and
  numbers broken on purpose; with its manifest too;
* ``defectos_lineas.xlsx`` and ``defectos_facturas.xlsx`` - every defect of the
  table of section 5 of docs/frontend-plan.md that a catalogue or an export
  can show. Their manifests add the reason each issue must give first;
* ``defectos_lineas_corregido.xlsx`` - ``defectos_lineas`` after some of the
  fixes the app suggests, to upload as its correction.
  ``defectos_lineas_corregido.expected.tsv`` says how many issues the
  comparison must call cleared, remaining and new.

With ``--large`` it also writes valid ``.xlsx`` packages padded with random
bytes to an exact size, written a megabyte at a time:

* ``mediano_12MB.xlsx`` - over the 5 MB the API validates itself: kept, and
  its validation answers "not built yet";
* ``grande_300MB.xlsx`` - 4 KiB under the 300 MB limit;
* ``demasiado_301MB.xlsx`` - over the limit, refused.

Usage::

    uv run python scripts/make_test_workbooks.py <folder> [--large]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.fixtures.workbooks import (  # noqa: E402
    Explained,
    Planted,
    invoices_broken,
    invoices_defects,
    lines_broken,
    lines_defects,
    lines_defects_corrected,
    lines_good,
    write_padded_workbook,
)

from energy.adapters.yaml_dataset_specs import load_dataset_specs  # noqa: E402
from energy.domain.drafts import MIB  # noqa: E402


def _manifest(path: Path, planted: list[Planted]) -> None:
    lines = ["row\tcolumn\trule"]
    lines += [f"{row or ''}\t{column or ''}\t{rule}" for row, column, rule in planted]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _explained(path: Path, explained: list[Explained]) -> None:
    lines = ["row\tcolumn\trule\treason"]
    lines += [
        f"{row or ''}\t{column or ''}\t{rule}\t{reason}" for row, column, rule, reason in explained
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Write the files."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, help="where to write the files")
    parser.add_argument("--large", action="store_true", help="also write the padded files")
    arguments = parser.parse_args()
    folder: Path = arguments.folder
    folder.mkdir(parents=True, exist_ok=True)
    catalogue = load_dataset_specs(ROOT / "config")

    (folder / "lineas_buena.xlsx").write_bytes(lines_good())
    data, planted = lines_broken()
    (folder / "lineas_rota.xlsx").write_bytes(data)
    _manifest(folder / "lineas_rota.manifest.tsv", planted)
    data, planted = invoices_broken(catalogue.get("qad_facturas"))
    (folder / "facturas_rota.xlsx").write_bytes(data)
    _manifest(folder / "facturas_rota.manifest.tsv", planted)
    data, explained = lines_defects()
    (folder / "defectos_lineas.xlsx").write_bytes(data)
    _explained(folder / "defectos_lineas.manifest.tsv", explained)
    data, explained = invoices_defects(catalogue.get("qad_facturas"))
    (folder / "defectos_facturas.xlsx").write_bytes(data)
    _explained(folder / "defectos_facturas.manifest.tsv", explained)
    data, expected = lines_defects_corrected()
    (folder / "defectos_lineas_corregido.xlsx").write_bytes(data)
    counts = "".join(f"{name}\t{count}\n" for name, count in expected.items())
    (folder / "defectos_lineas_corregido.expected.tsv").write_text(counts, encoding="utf-8")

    if arguments.large:
        invoices = catalogue.get("qad_facturas")
        for name, size in (
            ("mediano_12MB.xlsx", 12 * MIB),
            ("grande_300MB.xlsx", 300 * MIB - 4096),
            ("demasiado_301MB.xlsx", 301 * MIB),
        ):
            write_padded_workbook(folder / name, size, invoices)

    for path in sorted(folder.iterdir()):
        print(f"{path.stat().st_size:>12,}  {path.name}")


if __name__ == "__main__":
    main()
