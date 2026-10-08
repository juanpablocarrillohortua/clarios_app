"""What a Bronze to Silver build of the equivalent-batteries data reports.

Layer: domain

Responsibility:
    Defines the data-quality report a build of the equivalent-batteries (EQUS)
    Silver layer returns, and how it reads as text. The build conforms what the
    exports delivered: it drops repeated structure links, leaves out rows that
    are not what their sheet says they are, and creates catalogue rows for codes
    nobody describes. Each of those is a decision that changes a number, so
    none of them may be silent. The report is how a person closing a month
    sees what was done to the data and what still needs someone's attention.

    Nothing here touches a database. The report is filled by whoever implements
    :class:`energy.ports.equs_silver.SilverBuildRepository`.

Depends on:
    standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from decimal import Decimal

# How many examples of a finding are listed before the rest is summarised.
DEFAULT_EXAMPLES: Final = 10


@dataclass(frozen=True, slots=True)
class StructureSheet:
    """What happened to the rows of one product-structure sheet.

    Attributes:
        sheet: the worksheet.
        rows_read: rows of its latest completed Bronze run.
        header_rows: header rows pasted among the data, dropped.
        incomplete_rows: rows without a parent, a component or a quantity, which
            are therefore not links, dropped.
        duplicates: repeats of a link inside the sheet, dropped. A link is the
            same link when parent, component, validity dates and quantity agree.
        in_another_sheet: links that another sheet also has, kept once.
        reread_rows: rows read by a pasted header rather than by the sheet's
            own, because they sit below one.
        links: rows that reached Silver.
    """

    sheet: str
    rows_read: int
    header_rows: int
    incomplete_rows: int
    duplicates: int
    in_another_sheet: int
    reread_rows: int
    links: int


@dataclass(frozen=True, slots=True)
class ItemQuantity:
    """An item and how much of it was produced.

    Attributes:
        item: the item code.
        quantity: the quantity produced, net of reversals.
        receipts: how many production receipts that is.
    """

    item: str
    quantity: Decimal
    receipts: int


@dataclass(frozen=True, slots=True)
class SeveralAssembled:
    """A formed battery whose paste adds up more than one assembled battery.

    Attributes:
        item: the formed battery.
        assembled: how many assembled batteries its paste adds up.
        quantity: how much of it was produced.
    """

    item: str
    assembled: int
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class LineWithoutPlant:
    """A production line that no plant area claims.

    Attributes:
        line: the production line.
        receipts: how many production receipts name it; zero for a line that is
            in the catalogue but was not used.
    """

    line: str
    receipts: int


@dataclass(frozen=True, slots=True)
class PolyMismatch:
    """An item of the plastic catalogue whose factor is not its category's.

    Attributes:
        item: the item code.
        category: its category; ``None`` when it has none.
        factor: the factor the catalogue gives the item.
        category_factor: the factor the category table gives the category;
            ``None`` when the table does not list it.
    """

    item: str
    category: str | None
    factor: Decimal | None
    category_factor: Decimal | None


@dataclass(frozen=True, slots=True)
class CategoryConflict:
    """An item QAD marks "N/A" although the plastic catalogue classifies it.

    Attributes:
        item: the item code.
        poly_category: the category the plastic catalogue gives it.
        receipts: how many production receipts carry the "N/A".
    """

    item: str
    poly_category: str
    receipts: int


@dataclass(frozen=True, slots=True)
class SilverBuildReport:
    """The outcome of one Bronze to Silver build.

    Attributes:
        rows: rows per Silver table and view, after the build.
        structure: one entry per product-structure sheet.
        receipts_left_out: production receipts without a transaction number, a
            date, an item or a quantity, which cannot be movements.
        invoice_lines_left_out: invoice lines left out for the same reason.
        placeholders: per catalogue, rows nobody describes, created so that
            every reference holds.
        items_without_description: the item codes among those.
        lines_without_plant: production lines without a plant area.
        poly_factor_mismatches: plastic items whose factor is not their
            category's. Reported, never corrected.
        poly_repeated_items: items listed more than once in the plastic
            catalogue; the first row was used.
        formed_without_paste: formed batteries that were produced but have no
            kg of positive paste, so they add nothing to Formation.
        formed_with_several_assembled: formed batteries whose paste adds up
            more than one assembled battery, so they may count paste twice.
        decorated_without_pieces: decorated batteries that were produced but
            have no decoration pieces.
        na_with_poly_category: items QAD marks "N/A" that the plastic catalogue
            classifies.
        notes: things about this build a reader should know.
        concurrent_refresh: whether the two conversion tables were refreshed
            without blocking their readers.
        seconds: how long the build took.
    """

    rows: Mapping[str, int]
    structure: tuple[StructureSheet, ...]
    receipts_left_out: int
    invoice_lines_left_out: int
    placeholders: Mapping[str, int]
    items_without_description: tuple[str, ...]
    lines_without_plant: tuple[LineWithoutPlant, ...]
    poly_factor_mismatches: tuple[PolyMismatch, ...]
    poly_repeated_items: tuple[str, ...]
    formed_without_paste: tuple[ItemQuantity, ...]
    decorated_without_pieces: tuple[ItemQuantity, ...]
    na_with_poly_category: tuple[CategoryConflict, ...]
    formed_with_several_assembled: tuple[SeveralAssembled, ...] = ()
    notes: tuple[str, ...] = ()
    concurrent_refresh: bool = True
    seconds: float = 0.0

    @property
    def duplicates_dropped(self) -> dict[str, int]:
        """Return the repeated links dropped inside each structure sheet."""
        return {sheet.sheet: sheet.duplicates for sheet in self.structure}


def _listed(values: Sequence[str], examples: int) -> str:
    """Join the first values and say how many more there are."""
    shown = ", ".join(values[:examples])
    rest = len(values) - examples
    return f"{shown}, and {rest:,} more" if rest > 0 else shown


def _finding(title: str, values: Sequence[str], examples: int) -> list[str]:
    """Render one check: its count, then its first examples."""
    if not values:
        return [f"{title}: none"]
    return [f"{title}: {len(values):,}", f"  {_listed(values, examples)}"]


def describe(report: SilverBuildReport, examples: int = DEFAULT_EXAMPLES) -> list[str]:
    """Render a report as the lines a person reads after a build.

    Args:
        report: the report.
        examples: how many examples of each finding to list.

    Returns:
        The lines, without trailing newlines.
    """
    lines = ["Rows per Silver table"]
    lines += [f"  {table:<22} {count:>9,}" for table, count in report.rows.items()]

    lines += ["", "Product structure, per sheet"]
    lines.append(
        f"  {'sheet':<10} {'read':>7} {'headers':>8} {'not a link':>11} "
        f"{'repeated':>9} {'in 2 sheets':>12} {'kept':>7}"
    )
    lines += [
        f"  {sheet.sheet:<10} {sheet.rows_read:>7,} {sheet.header_rows:>8,} "
        f"{sheet.incomplete_rows:>11,} {sheet.duplicates:>9,} "
        f"{sheet.in_another_sheet:>12,} {sheet.links:>7,}"
        for sheet in report.structure
    ]
    reread = [f"{sheet.sheet} ({sheet.reread_rows:,})" for sheet in report.structure]
    reread = [
        text for text, sheet in zip(reread, report.structure, strict=True) if sheet.reread_rows
    ]
    if reread:
        lines.append(f"  Rows read by a pasted header: {', '.join(reread)}")

    lines += ["", "Checks"]
    lines.append(
        f"Movements left out: {report.receipts_left_out:,} production receipts, "
        f"{report.invoice_lines_left_out:,} invoice lines"
    )
    created = [f"{table} {count:,}" for table, count in report.placeholders.items() if count]
    lines.append(f"Catalogue rows nobody describes: {', '.join(created) if created else 'none'}")
    lines += _finding("Items without description", report.items_without_description, examples)
    lines += _finding(
        "Production lines without plant area",
        [f"{line.line} ({line.receipts:,} receipts)" for line in report.lines_without_plant],
        examples,
    )
    lines += _finding(
        "Clasif_Poly items whose factor is not their category's",
        [
            f"{item.item} {item.category or '(no category)'}: {item.factor} "
            f"against {item.category_factor if item.category_factor is not None else 'no entry'}"
            for item in report.poly_factor_mismatches
        ],
        examples,
    )
    lines += _finding(
        "Clasif_Poly items listed more than once", report.poly_repeated_items, examples
    )
    lines += _finding(
        "Formed batteries produced without positive paste",
        [f"{item.item} ({item.quantity:,.0f})" for item in report.formed_without_paste],
        examples,
    )
    lines += _finding(
        "Formed batteries whose paste adds up several assembled batteries",
        [
            f"{item.item} ({item.assembled} assembled, {item.quantity:,.0f} produced)"
            for item in report.formed_with_several_assembled
        ],
        examples,
    )
    lines += _finding(
        "Decorated batteries produced without decoration pieces",
        [f"{item.item} ({item.quantity:,.0f})" for item in report.decorated_without_pieces],
        examples,
    )
    lines += _finding(
        'Items QAD marks "N/A" that Clasif_Poly classifies',
        [
            f"{item.item} {item.poly_category} ({item.receipts:,} receipts)"
            for item in report.na_with_poly_category
        ],
        examples,
    )

    if report.notes:
        lines += ["", "Notes"]
        lines += [f"  {note}" for note in report.notes]
    return lines


__all__ = [
    "DEFAULT_EXAMPLES",
    "CategoryConflict",
    "ItemQuantity",
    "LineWithoutPlant",
    "PolyMismatch",
    "SeveralAssembled",
    "SilverBuildReport",
    "StructureSheet",
    "describe",
]
