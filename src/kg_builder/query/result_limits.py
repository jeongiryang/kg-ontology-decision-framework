"""Bounded result limits shared by generation and both validation stages."""

from __future__ import annotations

from .query_plan import QueryPlan, SelectionMode


DEFAULT_MAX_RESULT_ROWS = 100
COURSE_LIST_MAX_RESULT_ROWS = 250
ABSOLUTE_MAX_RESULT_ROWS = COURSE_LIST_MAX_RESULT_ROWS


def maximum_rows_for(plan: QueryPlan, configured_max: int) -> int:
    """Return the narrowest limit allowed for this validated plan.

    Ordinary queries retain the original 100-row ceiling.  Only a requested
    course list may use the larger, still-bounded ceiling needed for the
    verified 2026 curriculum catalog.
    """

    mode_maximum = (
        COURSE_LIST_MAX_RESULT_ROWS
        if plan.selection_mode is SelectionMode.COURSE_LIST
        else DEFAULT_MAX_RESULT_ROWS
    )
    return min(configured_max, mode_maximum)
