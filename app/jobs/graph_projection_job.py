"""Scheduled consumer of the event log that keeps each organisation's graph
projection snapshot current.

One tenant at a time through the existing tenant-safe harness
(``run_for_each_tenant``): inside the per-organisation scope the job loads the
organisation's stored snapshot (or rebuilds it), applies the event-log tail
since the snapshot's offset, and stores a new snapshot when enough has
changed. Once a day per organisation it also rebuilds from the relational store
and compares, which is what catches a write that bypassed the event log.

All of the work is ``graph_projection.refresh_organisation``; this module only
puts it on the schedule.
"""

from __future__ import annotations

import logging

from app.jobs.tenant_safe_job import JobRun, run_for_each_tenant

logger = logging.getLogger(__name__)

JOB_NAME = "graph_projection_refresh"


def _refresh_one_tenant(organization_id: int) -> dict:
    from app.modules.intelligence.services.graph_projection import refresh_organisation

    return refresh_organisation(organization_id)


def run_graph_projection_refresh(app, *, organization_ids=None) -> JobRun:
    """Refresh every active organisation's snapshot (or just ``organization_ids``)."""
    return run_for_each_tenant(
        app,
        JOB_NAME,
        _refresh_one_tenant,
        organization_ids=organization_ids,
        use_lock=True,
    )
