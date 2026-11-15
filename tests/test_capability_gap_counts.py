"""The gap heatmap counts applications through the one support reader.

Two real organisations: the second one's support never reaches the first one's counts.
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_capability_support_existing_rows import _old_row, _record_of
from tests.test_capability_support_writer import relationship_between, seed

pytestmark = pytest.mark.usefixtures("db_session")


def _heatmap(tenant_ctx, org, app_ids):
    from app.services.capability_gap_service import CapabilityGapAnalysisService

    with tenant_ctx(org.id):
        return {r["capability_id"]: r["app_count"]
                for r in CapabilityGapAnalysisService().compute_capability_heatmap(app_ids)}


def test_an_application_that_realizes_and_serves_a_capability_counts_once(db_session, make_org, tenant_ctx):
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Row
    from app.services import capability_support as cs

    org, other = make_org(), make_org()
    app_row, cap = seed(db_session, org)
    other_app, other_cap = seed(db_session, other, "B")
    cs.record_capability_support(org.id, app_row.archimate_element_id, cap.archimate_element_id, kind="realization")
    cs.record_capability_support(other.id, other_app.archimate_element_id, other_cap.archimate_element_id)
    serving = relationship_between(db_session, org, app_row, cap, "serving")
    db_session.flush()
    assert serving.id
    # The shape the demonstration organisation has: one row for each of the two relationships of the pair.
    assert Row.query.filter_by(organization_id=org.id).count() == 2

    record, other_record = _record_of(org, cap), _record_of(other, other_cap)
    counts = _heatmap(tenant_ctx, org, [app_row.id])
    assert counts[record.id] == 1
    assert counts.get(other_record.id, 0) == 0


def test_a_shared_capability_keeps_its_count_when_old_rows_move_to_the_organisations_record_of(
        db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Row
    from app.services import capability_support as cs

    org = make_org()
    apps = [ApplicationComponent(name=f"Counted {i} {uuid.uuid4().hex[:6]}", organization_id=org.id) for i in range(2)]
    shared = UnifiedCapability(name=f"Shared counted {uuid.uuid4().hex[:6]}", level=1, organization_id=None,
                               scope="reference")
    db_session.add_all(apps + [shared])
    db_session.flush()
    for a in apps:
        _old_row(db_session, org, a, shared)
    ids = [a.id for a in apps]

    # What the older reader counted: the rows themselves, which sit on the shared capability.
    before = Row.query.filter_by(unified_capability_id=shared.id, organization_id=org.id).count()
    cs.backfill_unprojected_support(org.id)
    after = _heatmap(tenant_ctx, org, ids)[shared.id]
    assert before == after == 2
