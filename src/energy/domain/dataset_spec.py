"""Dataset specs: what the app expects of each kind of data people load.

Layer: domain

Responsibility:
    Defines, in one place, what a dataset spec means and when one is sound. A
    spec describes one sheet of one Bronze source: the columns a file must have
    and their order, the type and rules of each column, how the rows are kept
    (a ``snapshot`` replaced whole, or ``movement`` rows that add up) and how
    they may be entered (``upload``, ``grid``, ``form``). The API serves the
    specs to the web app and builds the template of each; increment 5 checks
    uploaded files against them.

    A spec names each column by its header, exactly as the workbook writes it.
    The payload key Bronze stores the column under is never written in a spec:
    it is derived from the headers by the Bronze rules
    (:func:`energy.domain.landing.header_keys`), so a repeated ``Almac`` becomes
    ``Almac #2`` here exactly as it does in Bronze. Natural keys and the period
    column name payload keys.

    :func:`parse_dataset_spec` turns what one spec file holds into a
    :class:`DatasetSpec`, or raises :class:`DatasetSpecError` listing every
    problem found, each with the file and the path inside it.
    :func:`build_catalogue` then checks the specs against one another.

    The rules a spec declares (type, required, pattern, allowed values, range)
    are only declared here. Applying them to a file is increment 5.

    The module is pure. Reading the files is the adapter's job
    (``energy.adapters.yaml_dataset_specs``).

Depends on:
    ``energy.domain.landing``, ``energy.domain.exceptions``, standard library
    only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from energy.domain.exceptions import DatasetSpecError, UnknownDatasetError
from energy.domain.landing import (
    FORBIDDEN_SHEETS,
    LANDING_SOURCES,
    SOURCE_SYSTEM_QAD,
    LandingSource,
    header_keys,
    is_valid_dataset_key,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

#: The version of the spec format this module reads. A file declares the
#: version it was written for, and any other is refused rather than guessed at.
SPEC_VERSION: Final = 1

type Number = int | float


class DatasetKind(StrEnum):
    """How the rows of a dataset are kept.

    A ``snapshot`` (a catalogue, a product structure) is replaced whole: Silver
    reads only its latest completed load. ``movement`` rows (receipts, invoice
    lines) add up, and a corrected row replaces the old one by its natural key.
    """

    SNAPSHOT = "snapshot"
    MOVEMENT = "movement"


class DatasetOrigin(StrEnum):
    """Where a dataset comes from, read from its Bronze source.

    An ``export`` comes out of QAD and is only ever uploaded; a ``catalogue`` is
    a workbook people keep by hand.
    """

    EXPORT = "export"
    CATALOGUE = "catalogue"


class EntryMode(StrEnum):
    """How rows may be entered: a file, the editable grid, or a one-row form."""

    UPLOAD = "upload"
    GRID = "grid"
    FORM = "form"


class ColumnType(StrEnum):
    """What a column's cells hold. A ``date`` column takes real date cells only."""

    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    DATE = "date"


class Severity(StrEnum):
    """What breaking a column's rules does: an error blocks the load, a warning does not."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One column of a dataset.

    Attributes:
        header: the header text, exactly as the workbook writes it.
        key: the payload key Bronze stores the column under, derived from the
            headers; never written in a spec.
        type: what the cells hold.
        required: whether an empty cell is an error.
        pattern: for text, a regular expression the whole value must match.
        allowed: for text, the only values accepted.
        minimum: for numbers, the smallest value accepted.
        maximum: for numbers, the largest value accepted.
        severity: what breaking ``pattern``, ``allowed`` or the range does. A
            wrong type or an empty required cell is always an error.
        label: the key of the label the web app shows, in its translation file;
            ``None`` to show the header, which is already the users' own word.
    """

    header: str
    key: str
    type: ColumnType
    required: bool = False
    pattern: str | None = None
    allowed: tuple[str, ...] | None = None
    minimum: Number | None = None
    maximum: Number | None = None
    severity: Severity = Severity.ERROR
    label: str | None = None


@dataclass(frozen=True, slots=True)
class BronzeTarget:
    """Where the rows of a dataset land.

    Attributes:
        source_key: the Bronze source, as ``bronze.ingestion_runs`` constrains it.
        sheet: the worksheet, exactly as the workbook names it.
        dataset_key: the catalogue's name, for the sources that need one.
        header_row: the 1-based row that holds the headers.
    """

    source_key: str
    sheet: str
    dataset_key: str | None
    header_row: int


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """Everything the app knows about one kind of data people load.

    Attributes:
        key: the dataset's name, lowercase snake_case.
        kind: how its rows are kept.
        origin: where it comes from, read from its Bronze source.
        label: the key of its name in the web app's translation file.
        description: the key of its description there.
        entry_modes: how its rows may be entered.
        bronze: where its rows land.
        silver_table: the Silver table its rows feed, as ``silver.<name>``.
        natural_key: the payload keys that identify a row.
        period_column: for movements, the payload key of the column that dates
            each row; ``None`` for snapshots.
        columns: every column, in the workbook's order.
    """

    key: str
    kind: DatasetKind
    origin: DatasetOrigin
    label: str
    description: str
    entry_modes: tuple[EntryMode, ...]
    bronze: BronzeTarget
    silver_table: str
    natural_key: tuple[str, ...]
    period_column: str | None
    columns: tuple[ColumnSpec, ...]

    @property
    def headers(self) -> tuple[str, ...]:
        """Return the header texts, in order: the template's header row."""
        return tuple(column.header for column in self.columns)

    @property
    def keys(self) -> tuple[str, ...]:
        """Return the payload keys, in order."""
        return tuple(column.key for column in self.columns)


@dataclass(frozen=True, slots=True)
class DatasetCatalogue:
    """Every dataset spec, sorted by key.

    Attributes:
        specs: the specs.
    """

    specs: tuple[DatasetSpec, ...]

    def __iter__(self) -> Iterator[DatasetSpec]:
        """Iterate over the specs, by key."""
        return iter(self.specs)

    def __len__(self) -> int:
        """Return how many specs there are."""
        return len(self.specs)

    def get(self, key: str) -> DatasetSpec:
        """Return the spec of one dataset.

        Args:
            key: the dataset's key.

        Returns:
            Its :class:`DatasetSpec`.

        Raises:
            UnknownDatasetError: if no spec has that key.
        """
        for spec in self.specs:
            if spec.key == key:
                return spec
        raise UnknownDatasetError("no dataset spec has that key", dataset_key=key)


# -----------------------------------------------------------------------------
# The format
# -----------------------------------------------------------------------------
_TOP_FIELDS: Final = frozenset(
    {
        "version",
        "key",
        "kind",
        "label",
        "description",
        "entry_modes",
        "bronze",
        "silver",
        "natural_key",
        "period_column",
        "columns",
    }
)
_TOP_REQUIRED: Final = _TOP_FIELDS - {"period_column"}
_BRONZE_FIELDS: Final = frozenset({"source_key", "sheet", "dataset_key", "header_row"})
_BRONZE_REQUIRED: Final = frozenset({"source_key", "sheet"})
_SILVER_FIELDS: Final = frozenset({"table"})
_COLUMN_FIELDS: Final = frozenset(
    {"header", "type", "required", "pattern", "allowed", "min", "max", "severity", "label"}
)
_COLUMN_REQUIRED: Final = frozenset({"header", "type"})

# A key of the web app's translation file, such as "datasets.qad_facturas.name".
_LABEL_KEY: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
_SILVER_TABLE: Final = re.compile(r"silver\.[a-z][a-z0-9_]*")
# Excel refuses these characters in a sheet name, and names longer than 31.
_SHEET_NAME_FORBIDDEN: Final = frozenset("[]:*?/\\")
_SHEET_NAME_MAX: Final = 31
_NUMERIC_TYPES: Final = frozenset({ColumnType.INTEGER, ColumnType.DECIMAL})


class _Problems:
    """Collects what is wrong with one spec, each with where it is."""

    def __init__(self, origin: str) -> None:
        self._origin = origin
        self.lines: list[str] = []

    def add(self, path: str, problem: str) -> None:
        """Record one problem at a path such as ``columns[3].type``."""
        where = f"{self._origin}: {path}" if path else self._origin
        self.lines.append(f"{where}: {problem}")


def _join(path: str, name: str) -> str:
    """Return the path of a field inside a mapping at ``path``."""
    return f"{path}.{name}" if path else name


_YAML_KINDS: Final[Mapping[type, str]] = {type(None): "nothing", dict: "a mapping", list: "a list"}


def _describe(value: object) -> str:
    """Name a YAML value the way the person who wrote the file sees it."""
    if isinstance(value, bool):
        return f"{str(value).lower()} (YAML reads an unquoted Yes, No, On or Off as true or false)"
    if isinstance(value, str | int | float):
        return repr(value) if isinstance(value, str) else str(value)
    return _YAML_KINDS.get(type(value), type(value).__name__)


def _fields(
    value: object,
    path: str,
    allowed: frozenset[str],
    required: frozenset[str],
    problems: _Problems,
) -> dict[str, object] | None:
    """Return the fields of a mapping, reporting unknown and missing ones."""
    if not isinstance(value, dict):
        problems.add(path or "(the file)", f"must be a mapping, not {_describe(value)}")
        return None
    fields: dict[str, object] = {}
    for name, field in value.items():
        if isinstance(name, str) and name in allowed:
            fields[name] = field
        else:
            expected = ", ".join(sorted(allowed))
            problems.add(_join(path, str(name)), f"unknown field; expected one of {expected}")
    for name in sorted(required - fields.keys()):
        problems.add(_join(path, name), "missing")
    return fields


def _text(value: object, path: str, problems: _Problems) -> str | None:
    """Return non-empty text, or report it."""
    if isinstance(value, str) and value.strip():
        return value
    problems.add(path, f"must be text, not {_describe(value)}")
    return None


def _choice(value: object, choices: type[StrEnum], path: str, problems: _Problems) -> str | None:
    """Return one of an enumeration's values, or report it."""
    names = tuple(choice.value for choice in choices)
    if isinstance(value, str) and value in names:
        return value
    problems.add(path, f"{_describe(value)} is not one of {', '.join(names)}")
    return None


def _number(value: object, path: str, problems: _Problems) -> Number | None:
    """Return a number, or report it."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    problems.add(path, f"must be a number, not {_describe(value)}")
    return None


def _label_key(value: object, path: str, problems: _Problems) -> str | None:
    """Return a key of the web app's translation file, or report it."""
    if isinstance(value, str) and _LABEL_KEY.fullmatch(value):
        return value
    problems.add(path, f"{_describe(value)} is not a translation key such as datasets.<key>.name")
    return None


# -----------------------------------------------------------------------------
# Parts of a spec
# -----------------------------------------------------------------------------
def _check_version(value: object, problems: _Problems) -> None:
    """Report a version this module does not read."""
    if not isinstance(value, int) or isinstance(value, bool) or value != SPEC_VERSION:
        problems.add(
            "version",
            f"{_describe(value)} is not a version this build reads; it reads {SPEC_VERSION}",
        )


def _parse_key(value: object, expected: str | None, problems: _Problems) -> str | None:
    """Return the dataset's key: snake_case, and the file's name when one is given."""
    key = _text(value, "key", problems)
    if key is None:
        return None
    if not is_valid_dataset_key(key):
        problems.add("key", f"{key!r} is not lowercase snake_case")
        return None
    if expected is not None and key != expected:
        problems.add("key", f"{key!r} differs from the file's name, {expected!r}")
        return None
    return key


def _parse_sheet(value: object, problems: _Problems) -> str | None:
    """Return a worksheet name Excel accepts and Bronze may land."""
    sheet = _text(value, "bronze.sheet", problems)
    if sheet is None:
        return None
    if sheet.strip().lower() in FORBIDDEN_SHEETS:
        problems.add("bronze.sheet", f"{sheet!r} is a calculated sheet, which Bronze refuses")
        return None
    if len(sheet) > _SHEET_NAME_MAX or _SHEET_NAME_FORBIDDEN & set(sheet):
        problems.add(
            "bronze.sheet",
            f"{sheet!r} is not a sheet name Excel accepts: at most 31 characters, none of []:*?/\\",
        )
        return None
    return sheet


def _parse_dataset_key(
    fields: Mapping[str, object], source: LandingSource, problems: _Problems
) -> tuple[bool, str | None]:
    """Return whether the dataset key is sound, and the key itself."""
    present = "dataset_key" in fields
    if not source.needs_dataset_key:
        if present:
            problems.add(
                "bronze.dataset_key", f"source {source.key!r} takes no dataset_key; remove it"
            )
        return not present, None
    if not present:
        problems.add("bronze.dataset_key", f"missing; source {source.key!r} needs one")
        return False, None
    value = fields["dataset_key"]
    if isinstance(value, str) and is_valid_dataset_key(value):
        return True, value
    problems.add("bronze.dataset_key", f"{_describe(value)} is not lowercase snake_case")
    return False, None


def _parse_header_row(fields: Mapping[str, object], problems: _Problems) -> int | None:
    """Return the 1-based header row; 1 when the spec does not say."""
    value = fields.get("header_row", 1)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    problems.add("bronze.header_row", f"must be a row number from 1, not {_describe(value)}")
    return None


def _parse_bronze(value: object, problems: _Problems) -> tuple[BronzeTarget, LandingSource] | None:
    """Return where the rows land, and the Bronze source that takes them."""
    fields = _fields(value, "bronze", _BRONZE_FIELDS, _BRONZE_REQUIRED, problems)
    if fields is None or "source_key" not in fields:
        return None
    raw_source = fields["source_key"]
    source = LANDING_SOURCES.get(raw_source) if isinstance(raw_source, str) else None
    if source is None:
        problems.add(
            "bronze.source_key",
            f"{_describe(raw_source)} is not a Bronze source; "
            f"expected one of {', '.join(LANDING_SOURCES)}",
        )
        return None
    sheet = _parse_sheet(fields["sheet"], problems) if "sheet" in fields else None
    sound_key, dataset_key = _parse_dataset_key(fields, source, problems)
    header_row = _parse_header_row(fields, problems)
    if sheet is None or not sound_key or header_row is None:
        return None
    return BronzeTarget(source.key, sheet, dataset_key, header_row), source


def _parse_silver(value: object, problems: _Problems) -> str | None:
    """Return the Silver table the dataset feeds."""
    fields = _fields(value, "silver", _SILVER_FIELDS, _SILVER_FIELDS, problems)
    if fields is None or "table" not in fields:
        return None
    table = fields["table"]
    if isinstance(table, str) and _SILVER_TABLE.fullmatch(table):
        return table
    problems.add("silver.table", f"{_describe(table)} is not a table such as silver.<name>")
    return None


def _parse_entry_modes(
    value: object, origin: DatasetOrigin | None, problems: _Problems
) -> tuple[EntryMode, ...] | None:
    """Return how rows may be entered. A QAD export is only ever uploaded."""
    if not isinstance(value, list) or not value:
        problems.add("entry_modes", f"must be a non-empty list, not {_describe(value)}")
        return None
    modes: list[EntryMode] = []
    for index, item in enumerate(value):
        mode = _choice(item, EntryMode, f"entry_modes[{index}]", problems)
        if mode is None:
            return None
        if EntryMode(mode) in modes:
            problems.add(f"entry_modes[{index}]", f"{mode!r} is listed twice")
            return None
        modes.append(EntryMode(mode))
    if origin is DatasetOrigin.EXPORT and set(modes) != {EntryMode.UPLOAD}:
        problems.add("entry_modes", "a QAD export is only ever uploaded: [upload]")
        return None
    return tuple(modes)


def _parse_header(value: object, path: str, problems: _Problems) -> str | None:
    """Return a header text. Bronze trims headers, so a spec must not pad them."""
    header = _text(value, path, problems)
    if header is not None and header != header.strip():
        problems.add(path, f"{header!r} begins or ends with a space, which Bronze trims")
        return None
    return header


def _parse_pattern(value: object, path: str, problems: _Problems) -> str | None:
    """Return a regular expression that compiles."""
    pattern = _text(value, path, problems)
    if pattern is None:
        return None
    try:
        re.compile(pattern)
    except re.error as exc:
        problems.add(path, f"{pattern!r} is not a regular expression: {exc}")
        return None
    return pattern


def _parse_allowed(value: object, path: str, problems: _Problems) -> tuple[str, ...] | None:
    """Return a list of allowed values: text, none repeated."""
    if not isinstance(value, list) or not value:
        problems.add(path, f"must be a non-empty list, not {_describe(value)}")
        return None
    allowed: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            problems.add(f"{path}[{index}]", f"must be quoted text, not {_describe(item)}")
            return None
        if item in allowed:
            problems.add(f"{path}[{index}]", f"{item!r} is listed twice")
            return None
        allowed.append(item)
    return tuple(allowed)


@dataclass(frozen=True, slots=True)
class _Rules:
    """The value rules of one column, as parsed."""

    sound: bool
    pattern: str | None = None
    allowed: tuple[str, ...] | None = None
    minimum: Number | None = None
    maximum: Number | None = None
    severity: Severity = Severity.ERROR


def _parse_rules(
    fields: Mapping[str, object], kind: ColumnType, path: str, problems: _Problems
) -> _Rules:
    """Return a column's value rules, each allowed only on the types it fits."""
    before = len(problems.lines)
    text_only = {"pattern", "allowed"} & fields.keys()
    numbers_only = {"min", "max"} & fields.keys()
    if kind is not ColumnType.TEXT:
        for name in sorted(text_only):
            problems.add(_join(path, name), f"applies to text columns, not {kind.value}")
    if kind not in _NUMERIC_TYPES:
        for name in sorted(numbers_only):
            problems.add(_join(path, name), f"applies to number columns, not {kind.value}")
    pattern = (
        _parse_pattern(fields["pattern"], _join(path, "pattern"), problems)
        if "pattern" in fields
        else None
    )
    allowed = (
        _parse_allowed(fields["allowed"], _join(path, "allowed"), problems)
        if "allowed" in fields
        else None
    )
    minimum = _number(fields["min"], _join(path, "min"), problems) if "min" in fields else None
    maximum = _number(fields["max"], _join(path, "max"), problems) if "max" in fields else None
    if minimum is not None and maximum is not None and minimum > maximum:
        problems.add(_join(path, "min"), f"{minimum} is greater than max, {maximum}")
    severity = Severity.ERROR
    if "severity" in fields:
        if not (text_only or numbers_only):
            problems.add(
                _join(path, "severity"),
                "applies to pattern, allowed, min and max, and this column has none",
            )
        chosen = _choice(fields["severity"], Severity, _join(path, "severity"), problems)
        severity = Severity(chosen) if chosen else severity
    return _Rules(
        sound=len(problems.lines) == before,
        pattern=pattern,
        allowed=allowed,
        minimum=minimum,
        maximum=maximum,
        severity=severity,
    )


def _parse_column(value: object, path: str, problems: _Problems) -> ColumnSpec | None:
    """Return one column. Its key is set later, from every header together."""
    fields = _fields(value, path, _COLUMN_FIELDS, _COLUMN_REQUIRED, problems)
    if fields is None:
        return None
    header = (
        _parse_header(fields["header"], _join(path, "header"), problems)
        if "header" in fields
        else None
    )
    kind = (
        _choice(fields["type"], ColumnType, _join(path, "type"), problems)
        if "type" in fields
        else None
    )
    required = fields.get("required", False)
    if not isinstance(required, bool):
        problems.add(_join(path, "required"), f"must be true or false, not {_describe(required)}")
    label = (
        _label_key(fields["label"], _join(path, "label"), problems) if "label" in fields else None
    )
    if header is None or kind is None or not isinstance(required, bool):
        return None
    rules = _parse_rules(fields, ColumnType(kind), path, problems)
    if not rules.sound or ("label" in fields and label is None):
        return None
    return ColumnSpec(
        header=header,
        key=header,
        type=ColumnType(kind),
        required=required,
        pattern=rules.pattern,
        allowed=rules.allowed,
        minimum=rules.minimum,
        maximum=rules.maximum,
        severity=rules.severity,
        label=label,
    )


def _parse_columns(value: object, problems: _Problems) -> tuple[ColumnSpec, ...] | None:
    """Return every column, each keyed as Bronze keys its header."""
    if not isinstance(value, list) or not value:
        problems.add("columns", f"must be a non-empty list of columns, not {_describe(value)}")
        return None
    parsed = [
        _parse_column(item, f"columns[{index}]", problems) for index, item in enumerate(value)
    ]
    columns = [column for column in parsed if column is not None]
    if len(columns) != len(parsed):
        return None
    keys = header_keys([column.header for column in columns])
    return tuple(replace(column, key=key) for column, key in zip(columns, keys, strict=True))


def _parse_natural_key(
    value: object, columns: Sequence[ColumnSpec] | None, problems: _Problems
) -> tuple[str, ...] | None:
    """Return the payload keys that identify a row: required columns, none repeated."""
    if not isinstance(value, list) or not value:
        problems.add("natural_key", f"must be a non-empty list, not {_describe(value)}")
        return None
    if columns is None:
        return None
    by_key = {column.key: column for column in columns}
    keys: list[str] = []
    for index, item in enumerate(value):
        path = f"natural_key[{index}]"
        column = by_key.get(item) if isinstance(item, str) else None
        if column is None:
            problems.add(path, f"{_describe(item)} names no column")
            return None
        if not column.required:
            problems.add(path, f"{item!r} identifies a row, so its column must be required")
            return None
        if item in keys:
            problems.add(path, f"{item!r} is listed twice")
            return None
        keys.append(column.key)
    return tuple(keys)


@dataclass(frozen=True, slots=True)
class _Period:
    """The period column of a dataset, as parsed."""

    sound: bool
    column: str | None = None


def _parse_period(
    fields: Mapping[str, object],
    kind: DatasetKind | None,
    source: LandingSource | None,
    columns: Sequence[ColumnSpec] | None,
    problems: _Problems,
) -> _Period:
    """Return the column that dates each movement; snapshots have none."""
    if kind is None or source is None or columns is None:
        return _Period(sound=False)
    path, problem, column = _check_period(fields, kind, source, columns)
    if problem is not None:
        problems.add(path, problem)
        return _Period(sound=False)
    return _Period(sound=True, column=column)


def _check_period(
    fields: Mapping[str, object],
    kind: DatasetKind,
    source: LandingSource,
    columns: Sequence[ColumnSpec],
) -> tuple[str, str | None, str | None]:
    """Return where the period column is wrong and how, or else the column."""
    present = "period_column" in fields
    if kind is DatasetKind.SNAPSHOT:
        if source.period_key is not None:
            moves = f"source {source.key!r} holds movements, dated by {source.period_key!r}"
            return "kind", moves, None
        extra = "a snapshot has no period column; remove it" if present else None
        return "period_column", extra, None
    if not present:
        return "period_column", "missing; a movement is dated by one of its columns", None
    value = fields["period_column"]
    column = next((c for c in columns if isinstance(value, str) and c.key == value), None)
    if column is None or column.type is not ColumnType.DATE or not column.required:
        return "period_column", f"{_describe(value)} must name a required date column", None
    if source.period_key is not None and column.key != source.period_key:
        expected = f"must be {source.period_key!r}, the column that dates source {source.key!r}"
        return "period_column", expected, None
    return "period_column", None, column.key


# -----------------------------------------------------------------------------
# A spec, and the catalogue
# -----------------------------------------------------------------------------
def parse_dataset_spec(raw: object, origin: str, expected_key: str | None = None) -> DatasetSpec:
    """Turn what one spec file holds into a dataset spec.

    Args:
        raw: the file's content, as a YAML reader returns it.
        origin: where it came from, for the messages: the file's path.
        expected_key: the key the spec must have, which is the file's name;
            ``None`` to accept any.

    Returns:
        The :class:`DatasetSpec`.

    Raises:
        DatasetSpecError: listing every problem found, each as
            ``<origin>: <path>: <problem>``.
    """
    problems = _Problems(origin)
    spec = _parse_spec(raw, expected_key, problems)
    if spec is None or problems.lines:
        raise DatasetSpecError(problems.lines or [f"{origin}: not a dataset spec"])
    return spec


def _parse_spec(raw: object, expected_key: str | None, problems: _Problems) -> DatasetSpec | None:
    """Parse every part of a spec, so that each problem is reported."""
    fields = _fields(raw, "", _TOP_FIELDS, _TOP_REQUIRED, problems)
    if fields is None:
        return None
    if "version" in fields:
        _check_version(fields["version"], problems)
    key = _parse_key(fields["key"], expected_key, problems) if "key" in fields else None
    kind = _choice(fields["kind"], DatasetKind, "kind", problems) if "kind" in fields else None
    label = _label_key(fields["label"], "label", problems) if "label" in fields else None
    description = (
        _label_key(fields["description"], "description", problems)
        if "description" in fields
        else None
    )
    target = _parse_bronze(fields["bronze"], problems) if "bronze" in fields else None
    bronze, source = target if target else (None, None)
    origin = None
    if source is not None:
        qad = source.source_system == SOURCE_SYSTEM_QAD
        origin = DatasetOrigin.EXPORT if qad else DatasetOrigin.CATALOGUE
    modes = (
        _parse_entry_modes(fields["entry_modes"], origin, problems)
        if "entry_modes" in fields
        else None
    )
    silver = _parse_silver(fields["silver"], problems) if "silver" in fields else None
    columns = _parse_columns(fields["columns"], problems) if "columns" in fields else None
    natural_key = (
        _parse_natural_key(fields["natural_key"], columns, problems)
        if "natural_key" in fields
        else None
    )
    dataset_kind = DatasetKind(kind) if kind else None
    period = _parse_period(fields, dataset_kind, source, columns, problems)
    if (
        key is None
        or dataset_kind is None
        or origin is None
        or label is None
        or description is None
        or modes is None
        or bronze is None
        or silver is None
        or columns is None
        or natural_key is None
        or not period.sound
    ):
        return None
    return DatasetSpec(
        key=key,
        kind=dataset_kind,
        origin=origin,
        label=label,
        description=description,
        entry_modes=modes,
        bronze=bronze,
        silver_table=silver,
        natural_key=natural_key,
        period_column=period.column,
        columns=columns,
    )


def build_catalogue(specs: Mapping[str, DatasetSpec], origin: str) -> DatasetCatalogue:
    """Check the specs against one another and gather them.

    Two specs may not share a key, nor land in the same place: the same sheet of
    the same source, with the same dataset key.

    Args:
        specs: every spec, by the file it came from.
        origin: where they came from, for the messages: their folder.

    Returns:
        The :class:`DatasetCatalogue`, sorted by key.

    Raises:
        DatasetSpecError: if there is no spec, or two clash.
    """
    if not specs:
        raise DatasetSpecError([f"{origin}: holds no dataset spec; the API needs at least one"])
    problems: list[str] = []
    by_key: dict[str, str] = {}
    by_target: dict[tuple[str, str, str | None], str] = {}
    for file, spec in specs.items():
        if spec.key in by_key:
            problems.append(f"{file}: key: {spec.key!r} is also the key of {by_key[spec.key]}")
        by_key.setdefault(spec.key, file)
        target = (spec.bronze.source_key, spec.bronze.sheet.casefold(), spec.bronze.dataset_key)
        if target in by_target:
            problems.append(
                f"{file}: bronze: lands where {by_target[target]} lands, "
                f"sheet {spec.bronze.sheet!r} of source {spec.bronze.source_key!r}"
            )
        by_target.setdefault(target, file)
    if problems:
        raise DatasetSpecError(problems)
    return DatasetCatalogue(tuple(sorted(specs.values(), key=lambda spec: spec.key)))


__all__ = [
    "SPEC_VERSION",
    "BronzeTarget",
    "ColumnSpec",
    "ColumnType",
    "DatasetCatalogue",
    "DatasetKind",
    "DatasetOrigin",
    "DatasetSpec",
    "EntryMode",
    "Severity",
    "build_catalogue",
    "parse_dataset_spec",
]
