"""Domain exception hierarchy.

Layer: domain

Every error the platform raises on purpose descends from :class:`EnergyError`.
Exceptions carry structured context (``details``) rather than pre-formatted
messages, so the API layer can serialise them and the logging layer can index
them without re-parsing strings.

This module is pure: it imports nothing from ``energy`` and performs no I/O, so
it is safe to import from any layer including ``domain`` itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence


class EnergyError(Exception):
    """Base class for every deliberate error raised by the platform.

    Args:
        message: human-readable description, in English, without trailing period.
        **details: structured context attached to the error, for example
            ``plant_id="P-001"`` or ``column="energy_kwh"``. These land in log
            records and in API error payloads.
    """

    #: Stable, machine-readable code used by the API error envelope.
    code: str = "energy_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details

    def __str__(self) -> str:
        """Return the message with its structured context appended, if any."""
        if not self.details:
            return self.message
        rendered = ", ".join(f"{key}={value!r}" for key, value in sorted(self.details.items()))
        return f"{self.message} ({rendered})"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation for API responses.

        Returns:
            A mapping with ``code``, ``message`` and ``details`` keys.
        """
        return {"code": self.code, "message": self.message, "details": self.details}


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
class ConfigurationError(EnergyError):
    """Raised when configuration is missing, malformed, or mutually inconsistent."""

    code = "configuration_error"


class UnknownSourceError(ConfigurationError):
    """Raised when ``sources.yaml`` does not define the requested source key."""

    code = "unknown_source"


class UnknownKPIError(ConfigurationError):
    """Raised when ``kpis.yaml`` does not define the requested KPI key."""

    code = "unknown_kpi"


class DatasetSpecError(ConfigurationError):
    """Raised when one or more dataset specs are malformed.

    Every problem found is listed, not only the first, so one run shows them
    all. Each names the file, the path inside it and what is wrong, for example
    ``config/datasets/qad_facturas.yaml: columns[24].type: 'decimel' is not
    one of text, integer, decimal, date``.

    Args:
        problems: one line per problem.
    """

    code = "invalid_dataset_spec"

    def __init__(self, problems: Sequence[str]) -> None:
        count = len(problems)
        super().__init__(
            f"dataset specs are invalid ({count} problem{'' if count == 1 else 's'})",
            problems=list(problems),
        )
        self.problems: tuple[str, ...] = tuple(problems)

    def __str__(self) -> str:
        """Return the heading and every problem, one per line."""
        return "\n  ".join((f"{self.message}:", *self.problems))


# -----------------------------------------------------------------------------
# Domain rules
# -----------------------------------------------------------------------------
class DomainRuleError(EnergyError):
    """Base class for violations of an invariant the business defines."""

    code = "domain_rule_error"


class InvalidTimeWindowError(DomainRuleError):
    """Raised when a time window is not aligned to the canonical grid or is inverted."""

    code = "invalid_time_window"


class NaiveDatetimeError(DomainRuleError):
    """Raised when a timezone-naive datetime reaches the domain.

    Every timestamp crossing a boundary into the domain must be timezone-aware
    and expressed in UTC. Adapters localise on the way in; nothing downstream
    guesses a timezone.
    """

    code = "naive_datetime"


class UnitMismatchError(DomainRuleError):
    """Raised when arithmetic is attempted between incompatible units of measure."""

    code = "unit_mismatch"


class NegativeEnergyError(DomainRuleError):
    """Raised when an energy amount is negative outside an explicitly signed context."""

    code = "negative_energy"


class InvalidPeriodError(DomainRuleError):
    """Raised when a period of the equivalent-batteries reports cannot be answered.

    A date or month that does not parse, a start after the end, a month no
    version of the equation covers, or a request for the frozen close of months
    that are not closed whole.
    """

    code = "invalid_period"


class CloseRefusedError(DomainRuleError):
    """Raised when a month of the equivalent batteries may not be closed as asked.

    The month is already closed and no reclose was requested, it has nothing to
    close, or a blocking data-quality check has rows and the warnings were not
    accepted. ``details`` says which.
    """

    code = "close_refused"


class EmptyQuestionError(DomainRuleError):
    """Raised when the chat assistant is asked an empty or blank question."""

    code = "empty_question"


# -----------------------------------------------------------------------------
# Chat assistant
# -----------------------------------------------------------------------------
class ChatNotConfiguredError(ConfigurationError):
    """Raised when the chat assistant is used but this process cannot run it.

    Its model key is missing, or its instructions (the system prompt file) are
    missing or empty. The rest of the platform works without it.
    """

    code = "chat_not_configured"


class ChatModelUnavailableError(EnergyError):
    """Raised when the language model behind the chat assistant did not answer.

    The vendor refused the request, timed out, or could not be reached. The
    question may well be fine: the failure is on this side.
    """

    code = "chat_model_unavailable"


# -----------------------------------------------------------------------------
# Data quality and ingestion
# -----------------------------------------------------------------------------
class DataQualityError(EnergyError):
    """Base class for errors detected while validating ingested data."""

    code = "data_quality_error"


class SchemaValidationError(DataQualityError):
    """Raised when a dataframe fails its Pandera contract.

    Args:
        message: what failed.
        **details: should include ``schema``, ``layer`` and ``failure_cases``.
    """

    code = "schema_validation_error"


class DuplicateReadingError(DataQualityError):
    """Raised when the same (meter, timestamp) pair arrives twice with different values."""

    code = "duplicate_reading"


class QuarantinedRowsError(DataQualityError):
    """Raised when a batch exceeds the tolerated share of rejected rows.

    Individual bad rows are routed to ``silver.quarantine`` without raising; this
    error signals that so many rows failed that the batch itself is untrustworthy.
    """

    code = "quarantined_rows"


class IngestionError(EnergyError):
    """Base class for failures while reading from an external source system."""

    code = "ingestion_error"


class SourceUnavailableError(IngestionError):
    """Raised when a source system cannot be reached or authenticated against."""

    code = "source_unavailable"


class UnsupportedFileFormatError(IngestionError):
    """Raised when an uploaded file is not a format any adapter can read."""

    code = "unsupported_file_format"


class ContentHashMismatchError(IngestionError):
    """Raised when a re-read of a Bronze payload does not match its stored hash."""

    code = "content_hash_mismatch"


class EmptyFileError(UnsupportedFileFormatError):
    """Raised when an uploaded file has no bytes at all."""

    code = "empty_file"


class FileTooLargeError(IngestionError):
    """Raised when an upload is larger than the limit. The API answers 413."""

    code = "file_too_large"


class LengthRequiredError(IngestionError):
    """Raised when an upload does not say its size first. The API answers 411."""

    code = "length_required"


class InvalidFilenameError(IngestionError):
    """Raised when an upload names no usable file name."""

    code = "invalid_filename"


class IncompleteUploadError(IngestionError):
    """Raised when an upload ends with fewer or more bytes than it announced."""

    code = "incomplete_upload"


class UploadAbortedError(IngestionError):
    """Raised when the caller goes away before an upload has arrived whole."""

    code = "client_disconnected"


# -----------------------------------------------------------------------------
# Persistence and repositories
# -----------------------------------------------------------------------------
class RepositoryError(EnergyError):
    """Base class for persistence failures surfaced through a repository port."""

    code = "repository_error"


class EntityNotFoundError(RepositoryError):
    """Raised when a lookup by identifier finds nothing."""

    code = "entity_not_found"


class UnknownDatasetError(EntityNotFoundError):
    """Raised when no dataset spec has the requested key. The API answers 404."""

    code = "unknown_dataset"


class UnknownDraftError(EntityNotFoundError):
    """Raised when the caller has no live draft of that ID.

    A draft of someone else is answered the same way, so the answer does not
    tell that it exists. The API answers 404.
    """

    code = "unknown_draft"


class DraftBusyError(RepositoryError):
    """Raised when a draft cannot be worked on now.

    It is still arriving, it failed, or another validation of it is running.
    The API answers 409.
    """

    code = "draft_busy"


class UnknownLoadError(EntityNotFoundError):
    """Raised when no load of the caller's plants has that run ID. The API answers 404."""

    code = "unknown_load"


class CommitCountMismatchError(RepositoryError):
    """Raised when a commit lands a different number of rows than were staged.

    The commit is rolled back. It means a bug, not a user's mistake: the API
    answers 500.
    """

    code = "commit_count_mismatch"


class DatabaseUnavailableError(RepositoryError):
    """Raised when the database cannot be reached or a required extension is missing."""

    code = "database_unavailable"


# -----------------------------------------------------------------------------
# Committing a draft (increment 7)
# -----------------------------------------------------------------------------
class CommitRefusedError(EnergyError):
    """Base class for the reasons a draft is not committed.

    Each subclass has an HTTP status of its own in the API. Nothing was
    written when one is raised.
    """

    code = "commit_refused"


class NotValidatedError(CommitRefusedError):
    """Raised when a draft has not been validated, so it cannot be committed. 409."""

    code = "not_validated"


class DraftBlockedError(CommitRefusedError):
    """Raised when a draft has errors, which block the load. 422."""

    code = "draft_blocked"


class WarningsNotAcceptedError(CommitRefusedError):
    """Raised when a draft has warnings its owner did not accept. 422."""

    code = "warnings_not_accepted"


class NothingToLoadError(CommitRefusedError):
    """Raised when a draft has no row to load. 422.

    An empty snapshot would leave the previous version current, which is not
    what anyone confirming it means.
    """

    code = "nothing_to_load"


class ValidationChangedError(CommitRefusedError):
    """Raised when a draft's validation is not the one its owner confirmed. 409."""

    code = "validation_changed"


class CurrentVersionChangedError(CommitRefusedError):
    """Raised when another load became a catalogue's current version meanwhile. 409."""

    code = "current_version_changed"


class AlreadyLoadedError(CommitRefusedError):
    """Raised when a completed run already landed the same file for the same dataset. 409.

    ``details`` names that run (``run_id``).
    """

    code = "already_loaded"


class DatasetBusyError(CommitRefusedError):
    """Raised when another commit of the same dataset held its lock too long. 409."""

    code = "dataset_busy"


class OriginalMissingError(CommitRefusedError):
    """Raised when a draft's archived original is gone, so Bronze would point at nothing. 409."""

    code = "original_missing"


# -----------------------------------------------------------------------------
# Access
# -----------------------------------------------------------------------------
class AccessError(EnergyError):
    """Base class for refusals to serve a caller."""

    code = "access_error"


class AuthenticationError(AccessError):
    """Raised when a caller presents no credential, or one that cannot be trusted.

    ``details`` carries a ``reason`` for the log (``expired``, ``wrong_audience``
    and so on). It is never sent to the caller, who learns only that the
    credential was refused.
    """

    code = "unauthenticated"


class PermissionDeniedError(AccessError):
    """Raised when a caller is known but may not do what was asked."""

    code = "forbidden"


class IdentityProviderUnavailableError(AccessError):
    """Raised when a credential cannot be checked because the identity provider did not answer.

    The caller's credential may well be valid: the failure is on this side, so
    it is not an :class:`AuthenticationError`.
    """

    code = "identity_provider_unavailable"


class SignInNotConfiguredError(AccessError):
    """Raised when a credential arrives but this process was not told how to check one.

    The identifiers of the identity provider's tenant and application are
    missing from the configuration. As above, the failure is on this side.
    """

    code = "auth_not_configured"


# -----------------------------------------------------------------------------
# Pipelines
# -----------------------------------------------------------------------------
class PipelineError(EnergyError):
    """Base class for orchestration-level failures."""

    code = "pipeline_error"


class WatermarkError(PipelineError):
    """Raised when incremental ingestion state is missing, stale, or moves backwards."""

    code = "watermark_error"


class LockTimeoutError(PipelineError):
    """Raised when another run holds the lock a run needs and does not release it in time.

    Refreshing Gold and closing a month take the same lock, so two of them never
    run at once; the second one waits, up to a limit, and then gives up with this.
    """

    code = "lock_timeout"


__all__ = [
    "AccessError",
    "AlreadyLoadedError",
    "AuthenticationError",
    "ChatModelUnavailableError",
    "ChatNotConfiguredError",
    "CloseRefusedError",
    "CommitCountMismatchError",
    "CommitRefusedError",
    "ConfigurationError",
    "ContentHashMismatchError",
    "CurrentVersionChangedError",
    "DataQualityError",
    "DatabaseUnavailableError",
    "DatasetBusyError",
    "DatasetSpecError",
    "DomainRuleError",
    "DraftBlockedError",
    "DraftBusyError",
    "DuplicateReadingError",
    "EmptyFileError",
    "EmptyQuestionError",
    "EnergyError",
    "EntityNotFoundError",
    "FileTooLargeError",
    "IdentityProviderUnavailableError",
    "IncompleteUploadError",
    "IngestionError",
    "InvalidFilenameError",
    "InvalidPeriodError",
    "InvalidTimeWindowError",
    "LengthRequiredError",
    "LockTimeoutError",
    "NaiveDatetimeError",
    "NegativeEnergyError",
    "NotValidatedError",
    "NothingToLoadError",
    "OriginalMissingError",
    "PermissionDeniedError",
    "PipelineError",
    "QuarantinedRowsError",
    "RepositoryError",
    "SchemaValidationError",
    "SignInNotConfiguredError",
    "SourceUnavailableError",
    "UnitMismatchError",
    "UnknownDatasetError",
    "UnknownDraftError",
    "UnknownKPIError",
    "UnknownLoadError",
    "UnknownSourceError",
    "UnsupportedFileFormatError",
    "UploadAbortedError",
    "ValidationChangedError",
    "WarningsNotAcceptedError",
    "WatermarkError",
]
