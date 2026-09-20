"""Tests for the canonical hourly UTC grid.

Layer: tests (unit)

Covers :mod:`energy.domain.time_grid`. The grid is what makes readings from
seven differently-cadenced sources comparable, so these tests concentrate on the
cases that silently corrupt data when handled wrongly: naive datetimes,
daylight-saving transitions, and misaligned boundaries.

Each test is skipped until Phase 2.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

PHASE = "Phase 2: the time grid is not implemented yet"


@pytest.mark.skip(reason=PHASE)
def test_aligned_timestamps_are_recognised() -> None:
    """A timestamp with zero minutes, seconds and microseconds is aligned."""
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_naive_datetime_is_rejected() -> None:
    """Every entry point raises NaiveDatetimeError for a naive datetime.

    Assuming UTC for a naive timestamp is how local-time readings silently end
    up shifted by hours, so the platform refuses to guess.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_align_floors_by_default() -> None:
    """Aligning 14:37 yields 14:00, not 15:00.

    A reading timestamped mid-hour describes consumption that began in that
    hour; rounding up attributes it to an hour that had not started.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_align_strict_rejects_unaligned() -> None:
    """Strict mode raises rather than moving an unaligned timestamp."""
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_windows_tile_without_overlap() -> None:
    """Consecutive windows tile a period with no gap and no overlap.

    Property test: for any aligned range, the generated windows are contiguous
    and each window's end equals the next window's start. This is what makes
    summing across windows safe from double-counting.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_grid_is_unaffected_by_dst() -> None:
    """The UTC grid has 24 hours on every day, including DST transition days.

    A local-time grid would have 23 or 25 hours on those days. Storing UTC is
    what avoids that, and this test pins the behaviour.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_localise_handles_dst_transition() -> None:
    """Localising across a DST boundary yields the correct wall-clock times.

    Both directions matter: the skipped hour in spring and the repeated hour in
    autumn.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_find_gaps_distinguishes_missing_from_zero() -> None:
    """Gap detection reports absent intervals, not intervals whose value is zero.

    A zero reading means the plant consumed nothing; an absent one means no data
    arrived. Conflating them deflates every average computed over the period.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_resample_step_rejects_indivisible_cadence() -> None:
    """A source cadence that does not divide the hour evenly raises.

    A 7-minute source cannot be mapped onto an hourly grid without an ambiguous
    boundary, so it must fail loudly at configuration time.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_time_window_rejects_inverted_bounds() -> None:
    """A window whose end is not after its start raises InvalidTimeWindowError."""
    raise AssertionError("not implemented")
