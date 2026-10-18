"""Drafts end to end, against a real database and a real MinIO.

Layer: tests (integration)

What "done" means for increment 5 of docs/frontend-plan.md, checked here:

* a deliberately broken file shows every failure, with row, column and rule;
* the original is archived, under the key that carries its hash, with that
  hash;
* Bronze is unchanged: every Bronze table holds exactly the rows it held
  before, compared by count and by a digest of its contents;
* an upload passes through the API without being held: the memory traced
  while a file twice the limit below streams through stays under that limit.

And for increment 6:

* each defect of the table of section 5 comes back from the database with
  its reason first;
* a corrected file uploaded as a correction is compared in SQL: exactly the
  issues cleared, remaining and new that the fixture plants;
* nothing reaches Bronze, Silver or Gold, compared table by table, and the
  earlier original in MinIO keeps its bytes and its metadata.

The PostgreSQL adapter is also held here to the promises the in-memory
stand-in of the unit tests keeps: owner only, a second validation refused,
results replaced, filters and paging, the purge.
"""

from __future__ import annotations

import hashlib
import tracemalloc
from typing import TYPE_CHECKING, Any, Final
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from tests.fixtures.workbooks import (
    INVOICES_SHEET,
    LINES_HEADERS,
    LINES_SHEET,
    build_workbook,
    invoices_broken,
    lines_broken,
    lines_defects,
    lines_defects_corrected,
    lines_good,
)

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.minio_storage import MinioObjectStorage
from energy.adapters.postgres_draft_repo import PostgresDraftRepository
from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.adapters.zip_package import ZipPackageInspector
from energy.domain.draft_comparison import ComparisonState, IssueChange
from energy.domain.draft_reasons import ReasonId
from energy.domain.draft_rules import IssueSeverity, RuleId
from energy.domain.drafts import XLSX_MEDIA_TYPE, DraftStatus
from energy.domain.exceptions import DraftBusyError, UnknownDraftError
from energy.domain.landing import landing_rows
from energy.ports.draft_repository import IssueFilter
from energy.services.draft_service import DraftService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

    from fastapi import FastAPI
    from sqlalchemy.engine import Engine

    from energy.core.settings import MinioSettings
    from energy.domain.dataset_spec import DatasetCatalogue
    from energy.domain.drafts import Draft

pytestmark = pytest.mark.integration

BUCKET: Final = "bronze-raw-drafts"
OWNER: Final = "00000000-0000-4000-8000-00000000abcd"
BRONZE_TABLES: Final = (
    "ingestion_runs",
    "raw_files",
    "raw_rct_wo",
    "raw_facturas",
    "raw_product_struc",
    "raw_item_master",
    "raw_manual_sheets",
)
MIB: Final = 1024 * 1024


class Body:
    """An upload's body, handed over 64 KiB at a time."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._at = 0

    def read(self, size: int = -1, /) -> bytes:
        size = 65_536 if size < 0 else min(size, 65_536)
        chunk = self._data[self._at : self._at + size]
        self._at += len(chunk)
        return chunk


def bronze_snapshot(engine: Engine) -> dict[str, tuple[int, str]]:
    """Return, per Bronze table, its row count and a digest of every row."""
    snapshot: dict[str, tuple[int, str]] = {}
    with engine.connect() as connection:
        for table in BRONZE_TABLES:
            # The table names are this module's own constants.
            count, digest = connection.execute(
                text(
                    f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), ''))"  # noqa: S608
                    f" FROM bronze.{table} AS t"
                )
            ).one()
            snapshot[table] = (int(count), str(digest))
    return snapshot


def layers_snapshot(engine: Engine) -> dict[str, tuple[int, str]]:
    """Return, per table of Bronze, Silver and Gold, its row count and a digest of every row."""
    snapshot: dict[str, tuple[int, str]] = {}
    with engine.connect() as connection:
        tables = connection.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables"
                " WHERE table_schema IN ('bronze', 'silver', 'gold')"
                " AND table_type = 'BASE TABLE' ORDER BY 1, 2"
            )
        ).all()
        for schema, table in tables:
            # The names come from the catalogue, not from outside.
            count, digest = connection.execute(
                text(
                    f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), ''))"  # noqa: S608
                    f' FROM "{schema}"."{table}" AS t'
                )
            ).one()
            snapshot[f"{schema}.{table}"] = (int(count), str(digest))
    return snapshot


def seed_bronze(engine: Engine) -> None:
    """Land one run, one file and one row by hand, so that a write would show."""
    with engine.begin() as connection:
        run_id = connection.execute(
            text(
                "INSERT INTO bronze.ingestion_runs (flow_name, source_key, source_system,"
                " plant_code, state) VALUES ('seed', 'manual_sheets', 'excel', '1515', 'completed')"
                " RETURNING run_id"
            )
        ).scalar_one()
        file_id = connection.execute(
            text(
                "INSERT INTO bronze.raw_files (content_hash, bucket, object_key, filename,"
                " content_type, size_bytes, plant_code, source_key) VALUES (:h, 'b', 'k', 'f.xlsx',"
                " 'x', 1, '1515', 'manual_sheets') RETURNING raw_file_id"
            ),
            {"h": "b" * 64},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO bronze.raw_manual_sheets (ingestion_run_id, raw_file_id,"
                " source_system, source_uri, sheet_name, source_row, content_hash, plant_code,"
                " payload, dataset_key) VALUES (:r, :f, 'excel', 'b://k', 'Lineas Produccion', 2,"
                " :h, '1515', CAST(:p AS jsonb), 'lineas_produccion')"
            ),
            {"r": run_id, "f": file_id, "h": "c" * 64, "p": '{"Línea": "SEED"}'},
        )


@pytest.fixture(scope="module")
def catalogue(repo_root: Path) -> DatasetCatalogue:
    return load_dataset_specs(repo_root / "config")


@pytest.fixture(scope="module")
def storage(minio_settings: MinioSettings) -> MinioObjectStorage:
    adapter = MinioObjectStorage(minio_settings)
    adapter.ensure_bucket(BUCKET)
    return adapter


class TestDraftsInTheDatabase:
    @pytest.fixture(scope="class")
    def seeded(self, gold_engine: Engine) -> Engine:
        seed_bronze(gold_engine)
        return gold_engine

    @pytest.fixture
    def repository(self, seeded: Engine) -> PostgresDraftRepository:
        return PostgresDraftRepository(seeded, batch_size=4)

    @pytest.fixture
    def service(
        self,
        repository: PostgresDraftRepository,
        storage: MinioObjectStorage,
        catalogue: DatasetCatalogue,
        tmp_path: Path,
    ) -> DraftService:
        return DraftService(
            repository=repository,
            storage=storage,
            reader=CalamineWorkbookReader(),
            inspector=ZipPackageInspector(),
            catalogue=catalogue,
            bucket=BUCKET,
            temp_dir=tmp_path,
        )

    def upload(
        self, service: DraftService, data: bytes, dataset: str = "lineas_produccion"
    ) -> Draft:
        draft = service.open_upload(dataset, "rota.xlsx", XLSX_MEDIA_TYPE, len(data), OWNER, "Ada")
        return service.receive(draft, Body(data)).draft

    def test_a_broken_file_shows_every_failure_and_bronze_is_unchanged(
        self, service: DraftService, storage: MinioObjectStorage, seeded: Engine
    ) -> None:
        before = bronze_snapshot(seeded)
        data, manifest = lines_broken()

        draft = self.upload(service, data)
        validated = service.validate(draft.draft_id, OWNER)
        page = service.issues(draft.draft_id, OWNER, limit=500)

        found = [(issue.source_row, issue.column_letter, issue.rule.value) for issue in page.items]
        assert sorted(found, key=str) == sorted(manifest, key=str)
        assert validated.validation is not None
        assert validated.validation.rows_read == 9
        assert validated.validation.blocking
        # The original is archived under the key that carries its hash.
        digest = hashlib.sha256(data).hexdigest()
        assert draft.object_key is not None
        assert draft.object_key.endswith(f"/{digest}.xlsx")
        assert hashlib.sha256(storage.get(BUCKET, draft.object_key)).hexdigest() == digest
        assert list(storage.list_objects(BUCKET, "incoming/")) == []
        # Nothing reached Bronze.
        assert bronze_snapshot(seeded) == before
        assert before["raw_manual_sheets"][0] == 1

    def test_the_rows_staged_are_the_rows_bronze_would_land(
        self, service: DraftService, seeded: Engine
    ) -> None:
        data, _manifest = lines_broken()
        draft = self.upload(service, data)
        service.validate(draft.draft_id, OWNER)

        with seeded.connect() as connection:
            staged = connection.execute(
                text(
                    "SELECT source_row, content_hash FROM staging.draft_rows"
                    " WHERE draft_id = :d ORDER BY source_row"
                ),
                {"d": draft.draft_id},
            ).all()
        sheet = CalamineWorkbookReader().open_workbook(data).sheet(LINES_SHEET)
        assert [tuple(row) for row in staged] == [
            (row.source_row, row.content_hash) for row in landing_rows(sheet.rows)
        ]

    def test_dates_and_numbers_of_an_export_are_judged_as_silver_reads_them(
        self, service: DraftService, catalogue: DatasetCatalogue
    ) -> None:
        data, manifest = invoices_broken(catalogue.get("qad_facturas"))
        draft = self.upload(service, data, dataset="qad_facturas")

        validated = service.validate(draft.draft_id, OWNER)

        found = service.issues(draft.draft_id, OWNER, limit=500).items
        assert {
            (issue.source_row, issue.column_letter, issue.rule.value) for issue in found
        } == set(manifest)
        assert validated.validation is not None
        assert validated.validation.sheet_name == INVOICES_SHEET

    def test_validating_again_replaces_the_results(
        self, service: DraftService, seeded: Engine
    ) -> None:
        data, manifest = lines_broken()
        draft = self.upload(service, data)

        service.validate(draft.draft_id, OWNER)
        service.validate(draft.draft_id, OWNER)

        with seeded.connect() as connection:
            issues, rows = connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM staging.draft_issues WHERE draft_id = :d),"
                    " (SELECT count(*) FROM staging.draft_rows WHERE draft_id = :d)"
                ),
                {"d": draft.draft_id},
            ).one()
        assert (issues, rows) == (len(manifest), 9)

    def test_a_second_validation_is_refused_while_the_first_runs(
        self, service: DraftService, repository: PostgresDraftRepository
    ) -> None:
        draft = self.upload(service, lines_good())

        with repository.validation(draft.draft_id, OWNER), pytest.raises(DraftBusyError):
            service.validate(draft.draft_id, OWNER)

    def test_issues_are_filtered_and_paged_in_sql(self, service: DraftService) -> None:
        data, _manifest = lines_broken()
        draft = self.upload(service, data)
        service.validate(draft.draft_id, OWNER)

        everything = service.issues(draft.draft_id, OWNER)
        patterns = service.issues(draft.draft_id, OWNER, IssueFilter(rule=RuleId.CELL_PATTERN))
        warnings = service.issues(
            draft.draft_id, OWNER, IssueFilter(severity=IssueSeverity.WARNING)
        )
        almac = service.issues(draft.draft_id, OWNER, IssueFilter(column_key="Almac"))
        page = service.issues(draft.draft_id, OWNER, offset=3, limit=2)

        assert [(issue.source_row, issue.rule.value) for issue in everything.items[:3]] == [
            (1, "schema.column_missing"),
            (1, "schema.header_mismatch"),
            (1, "schema.column_unknown"),
        ]
        assert [issue.source_row for issue in patterns.items] == [4, 5, 8]
        assert [issue.rule for issue in warnings.items] == [RuleId.SCHEMA_COLUMN_MISSING]
        assert [issue.source_row for issue in almac.items] == [5]
        assert (page.total, len(page.items)) == (10, 2)
        assert page.items[0].params == everything.items[3].params

    def test_a_draft_is_its_owners_only(self, service: DraftService) -> None:
        draft = self.upload(service, lines_good())

        with pytest.raises(UnknownDraftError):
            service.get(draft.draft_id, "someone else")
        with pytest.raises(UnknownDraftError):
            service.issues(draft.draft_id, "someone else")
        with pytest.raises(UnknownDraftError):
            service.validate(uuid4(), OWNER)

    def test_each_defect_of_section_5_comes_back_with_its_reason(
        self, service: DraftService
    ) -> None:
        data, manifest = lines_defects()

        draft = self.upload(service, data)
        validated = service.validate(draft.draft_id, OWNER)
        page = service.issues(draft.draft_id, OWNER, limit=500)
        lost_zero = service.issues(
            draft.draft_id, OWNER, IssueFilter(reason=ReasonId.CODE_LOST_LEADING_ZEROS)
        )

        found = [
            (i.source_row, i.column_letter, i.rule.value, i.reasons[0].id.value) for i in page.items
        ]
        assert sorted(found, key=str) == sorted(manifest, key=str)
        assert all(issue.reasons[-1].id.value.startswith("default.") for issue in page.items)
        assert [(i.source_row, i.column_letter) for i in lost_zero.items] == [(5, "B")]
        assert validated.validation is not None
        for count in validated.validation.counts:
            assert sum(by.rows for by in count.reasons) == count.rows

    def test_a_correction_is_compared_in_sql_and_no_layer_changes(
        self, service: DraftService, storage: MinioObjectStorage, seeded: Engine
    ) -> None:
        layers = layers_snapshot(seeded)
        data, _ = lines_defects()
        earlier = service.validate(self.upload(service, data).draft_id, OWNER)
        assert earlier.object_key is not None
        original = storage.stat(BUCKET, earlier.object_key)
        corrected, expected = lines_defects_corrected()

        opened = service.open_correction(
            earlier.draft_id, "corregida.xlsx", XLSX_MEDIA_TYPE, len(corrected), OWNER, "Ada"
        )
        correction = service.receive(opened, Body(corrected)).draft
        validated = service.validate(correction.draft_id, OWNER)
        comparison = service.comparison(validated, OWNER)
        issues = service.issues(correction.draft_id, OWNER, limit=500)
        new = service.issues(correction.draft_id, OWNER, IssueFilter(change=IssueChange.NEW))
        cleared = service.cleared(correction.draft_id, OWNER, limit=500)

        assert comparison is not None
        assert comparison.state is ComparisonState.READY
        assert {
            "cleared": comparison.cleared,
            "remaining": comparison.remaining,
            "new": comparison.new,
        } == expected
        assert sorted(str(change) for change in issues.changes) == ["new"] + ["remaining"] * 5
        assert [(i.source_row, i.column_letter, i.rule) for i in new.items] == [
            (3, "D", RuleId.CELL_RANGE)
        ]
        assert cleared.total == expected["cleared"]
        # Both originals are kept; the earlier one is exactly as it was.
        assert correction.object_key is not None
        assert correction.object_key != earlier.object_key
        assert storage.stat(BUCKET, earlier.object_key) == original
        assert storage.get(BUCKET, earlier.object_key) == data
        assert storage.get(BUCKET, correction.object_key) == corrected
        # Nothing reached Bronze, Silver or Gold.
        assert layers_snapshot(seeded) == layers

    def test_a_correction_outlives_the_purge_of_its_earlier_draft(
        self, service: DraftService
    ) -> None:
        # Files of this test's own: the same bytes would be another test's draft.
        sound = ["PURGA01", "1010", "", 1, "", "", "", "", "Sí", ""]
        earlier_data = build_workbook({LINES_SHEET: [LINES_HEADERS, sound]})
        earlier = service.validate(self.upload(service, earlier_data).draft_id, OWNER)
        corrected = build_workbook({LINES_SHEET: [LINES_HEADERS, ["PURGA01", "101", *sound[2:]]]})
        opened = service.open_correction(
            earlier.draft_id, "b.xlsx", XLSX_MEDIA_TYPE, len(corrected), OWNER, "Ada"
        )
        correction = service.validate(
            service.receive(opened, Body(corrected)).draft.draft_id, OWNER
        )

        service.purge(landed=lambda _hash: False, only=earlier.draft_id)
        comparison = service.comparison(service.get(correction.draft_id, OWNER), OWNER)

        assert comparison is not None
        assert comparison.state is ComparisonState.PREVIOUS_GONE
        assert service.get(correction.draft_id, OWNER).previous_draft_id == earlier.draft_id
        with pytest.raises(UnknownDraftError):
            service.cleared(correction.draft_id, OWNER)

    def test_the_purge_removes_an_expired_draft_and_its_original(
        self, service: DraftService, storage: MinioObjectStorage, seeded: Engine
    ) -> None:
        data, _manifest = invoices_broken(service._catalogue.get("qad_facturas"))
        draft = self.upload(service, data, dataset="qad_facturas")
        service.validate(draft.draft_id, OWNER)
        with seeded.begin() as connection:
            connection.execute(
                text(
                    "UPDATE staging.drafts SET expires_at = now() - interval '1 minute'"
                    " WHERE draft_id = :d"
                ),
                {"d": draft.draft_id},
            )

        outcome = service.purge(landed=lambda _hash: False)

        assert outcome.drafts >= 1
        assert draft.object_key is not None
        assert not storage.exists(BUCKET, draft.object_key)
        with pytest.raises(UnknownDraftError):
            service.get(draft.draft_id, OWNER)
        with seeded.connect() as connection:
            left = connection.execute(
                text("SELECT count(*) FROM staging.draft_rows WHERE draft_id = :d"),
                {"d": draft.draft_id},
            ).scalar_one()
        assert left == 0


class TestStreamingThroughTheApi:
    """A file twice the memory bound below passes through the API without being held."""

    SIZE: Final = 96 * MIB
    BOUND: Final = 64 * MIB

    @pytest.fixture
    def bound_app(
        self,
        app: FastAPI,
        gold_engine: Engine,
        storage: MinioObjectStorage,
        catalogue: DatasetCatalogue,
        tmp_path: Path,
    ) -> Iterator[FastAPI]:
        from energy.api.deps import get_draft_service  # noqa: PLC0415

        service = DraftService(
            repository=PostgresDraftRepository(gold_engine),
            storage=storage,
            reader=CalamineWorkbookReader(),
            inspector=ZipPackageInspector(),
            catalogue=catalogue,
            bucket=BUCKET,
            temp_dir=tmp_path,
        )
        app.dependency_overrides[get_draft_service] = lambda: service
        yield app
        app.dependency_overrides.pop(get_draft_service, None)

    async def test_memory_stays_bounded_while_a_large_file_streams(
        self, bound_app: FastAPI, token_verifier: Any
    ) -> None:
        async def body() -> AsyncIterator[bytes]:
            yield b"PK\x03\x04"
            sent = 4
            while sent < self.SIZE:
                chunk = bytes([sent % 251]) * min(MIB, self.SIZE - sent)
                sent += len(chunk)
                yield chunk

        headers = {
            "Authorization": f"Bearer {token_verifier.issue('operator')}",
            "Content-Type": XLSX_MEDIA_TYPE,
            "Content-Length": str(self.SIZE),
            "X-File-Name": "grande.xlsx",
        }
        transport = httpx.ASGITransport(app=bound_app)
        tracemalloc.start()
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/drafts?dataset_key=qad_facturas", content=body(), headers=headers, timeout=300
                )
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert response.status_code == 201, response.text
        assert response.json()["size_bytes"] == self.SIZE
        assert response.json()["status"] == DraftStatus.UPLOADED.value
        assert peak < self.BOUND, f"peak {peak / MIB:.1f} MiB"
