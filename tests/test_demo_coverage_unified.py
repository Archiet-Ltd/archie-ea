"""QA-035: the demo company's coverage is in the unified store with a model element on both
ends, so the coverage slice, the chat tool and the legacy link table all agree."""
from __future__ import annotations

import os

from app.commands.seed_demo_company import seed_demo_company


def _seed():
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]


def _demo_org_and_user():
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    org_id = db.session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
    return org_id, User.query.filter_by(email="demo@lantern-quay.example.com", organization_id=org_id).one()


def test_demo_coverage_is_in_the_unified_store_with_elements_on_both_ends(app, db_session, make_org):
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability
    from app.modules.genome.services.coverage_slice import build_coverage_slice

    _seed()
    org_id, _user = _demo_org_and_user()

    assert BusinessCapability.query.filter_by(organization_id=org_id, archimate_element_id=None).count() == 0
    assert UnifiedCapability.query.filter_by(organization_id=org_id, archimate_element_id=None).count() == 0
    legacy = ApplicationCapabilityMapping.query.filter_by(organization_id=org_id).count()
    assert legacy == 20

    slice_ = build_coverage_slice(org_id)  # raises when an end has no model element
    assert len(slice_["cells"]) == legacy
    assert len(slice_["capabilities"]) == 24


def test_a_second_seed_adds_no_second_mapping(app, db_session, make_org):
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    _seed()
    first = UnifiedApplicationCapabilityMapping.query.count()
    assert first >= 20
    _seed()
    assert UnifiedApplicationCapabilityMapping.query.count() == first
