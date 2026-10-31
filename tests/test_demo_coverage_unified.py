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


def test_demo_unsupported_set_in_chat_equals_the_coverage_slice(app, db_session, make_org, tenant_ctx):
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
    from app.modules.genome.services.coverage_slice import build_coverage_slice

    _seed()
    org_id, user = _demo_org_and_user()
    slice_ = build_coverage_slice(org_id)
    supported_ids = {c["capability_id"] for c in slice_["cells"]}
    expected = sorted(c["name"] for c in slice_["capabilities"] if c["id"] not in supported_ids)

    with tenant_ctx(org_id):
        result = ToolExecutor(user.id, persona="cto_founder").execute(
            ToolCall(id="t", name="get_genome_slice", arguments={"domain": "business", "budget_tokens": 20000}))
    assert result["success"] is True
    caps = [n for n in result["result"]["nodes"] if n["kind"] == "cap"]
    assert len(caps) == 24 and any(n["supported"] for n in caps)
    assert sorted(n["name"] for n in caps if not n["supported"]) == expected
    assert all(n["provenance"]["origin"] == "structural" for n in caps)


def test_a_second_seed_adds_no_second_mapping(app, db_session, make_org):
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    _seed()
    first = UnifiedApplicationCapabilityMapping.query.count()
    assert first >= 20
    _seed()
    assert UnifiedApplicationCapabilityMapping.query.count() == first
