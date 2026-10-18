"""Purge abandoned drafts of the web app.

Layer: pipelines

Responsibility:
    The composition root of ``energy drafts purge``. A draft is kept 30 days
    after it was last worked on (question 17 of docs/frontend-plan.md); an
    upload that never finished goes after a day. The purge removes them from
    schema ``staging`` and removes their archived original too, unless another
    draft points at it or Bronze has catalogued the same bytes: an original
    that was loaded is evidence and stays.

    Nothing schedules it yet: it runs when it is called.

Depends on:
    ``energy.adapters``, ``energy.services``, ``energy.core``,
    ``energy.db``, ``energy.domain``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.postgres_bronze_repo import PostgresBronzeLandingRepository
from energy.adapters.postgres_draft_repo import PostgresDraftRepository
from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.adapters.zip_package import ZipPackageInspector
from energy.core.settings import get_settings
from energy.db.session import get_engine
from energy.pipelines.flows.equs_bronze_load import build_storage
from energy.services.draft_service import DraftService

if TYPE_CHECKING:
    from uuid import UUID

    from energy.services.draft_service import PurgeOutcome


def purge_drafts(dry_run: bool = False, only: UUID | None = None) -> PurgeOutcome:
    """Remove expired drafts and the originals nothing else needs.

    Args:
        dry_run: count what would go, and remove nothing.
        only: purge this one draft, expired or not, and nothing else.

    Returns:
        What was removed, or would be.
    """
    settings = get_settings()
    engine = get_engine()
    service = DraftService(
        repository=PostgresDraftRepository(engine),
        storage=build_storage(),
        reader=CalamineWorkbookReader(),
        inspector=ZipPackageInspector(),
        catalogue=load_dataset_specs(settings.data.config_dir),
        bucket=settings.minio.bucket_bronze,
    )
    bronze = PostgresBronzeLandingRepository(engine)
    return service.purge(
        landed=lambda content_hash: bronze.find_file(content_hash) is not None,
        dry_run=dry_run,
        only=only,
    )


__all__ = ["purge_drafts"]
