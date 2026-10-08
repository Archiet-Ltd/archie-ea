"""A value stream stage records its entry/exit criteria, stakeholders and
value items, and the capability x stage grid carries each mapped
capability's own maturity (None when never assessed, never a level).
"""

import warnings

from app.datetime_helpers import utcnow
from app.models.unified_capability import ValueStreamStage
from app.modules.capabilities.services import value_stream_service as vs_service


def _stream_with_stage(stage_data):
    vs = vs_service.create_value_stream({"name": "Customer Onboarding"})
    stage = vs_service.create_stage(vs.id, stage_data)
    return vs, stage


def test_stage_definition_fields_are_stored_and_listed(db_session, make_org, tenant_ctx):
    org = make_org("vs-stage-def")
    with tenant_ctx(org.id):
        vs, stage = _stream_with_stage({
            "name": "Verify identity",
            "entry_criteria": "Application form submitted",
            "exit_criteria": "Identity confirmed",
            "stakeholders": "Customer\nCompliance officer\n\n",
            "value_items": "Verified identity",
        })
        detail = vs_service.get_value_stream_with_stages(vs.id)

    row = detail["stages"][0]
    assert stage.entry_criteria == "Application form submitted"
    assert row["exit_criteria"] == "Identity confirmed"
    assert row["stakeholder_list"] == ["Customer", "Compliance officer"]
    assert row["value_item_list"] == ["Verified identity"]


def test_blank_stage_definition_is_not_recorded(db_session, make_org, tenant_ctx):
    org = make_org("vs-stage-blank")
    with tenant_ctx(org.id):
        _vs, stage = _stream_with_stage({"name": "Open account", "entry_criteria": "  "})
        assert stage.entry_criteria is None
        assert stage.stakeholders is None

        vs_service.update_stage(stage.id, {"exit_criteria": "Account open", "stakeholders": ""})
        assert stage.exit_criteria == "Account open"
        assert stage.stakeholders is None

        vs_service.update_stage(stage.id, {"exit_criteria": ""})
        assert stage.exit_criteria is None


def test_grid_rows_carry_capability_maturity(db_session, make_org, tenant_ctx):
    from app.models.unified_capability import UnifiedCapability

    org = make_org("vs-grid-maturity")
    with tenant_ctx(org.id):
        vs, stage = _stream_with_stage({"name": "Verify identity"})
        assessed = UnifiedCapability(
            name="Identity Verification", code="IDV-%s" % org.id, level=1,
            current_maturity_level=2, target_maturity_level=4,
        )
        unassessed = UnifiedCapability(name="Customer Onboarding Mgmt", code="COM-%s" % org.id, level=1)
        db_session.add_all([assessed, unassessed])
        db_session.flush()
        vs_service.upsert_mapping_cell(assessed.id, vs.id, stage.id, {"support_level": 4})
        vs_service.upsert_mapping_cell(unassessed.id, vs.id, stage.id, {"support_level": 2})
        grid = vs_service.build_bizbok_grid(vs.id)

    rows = {row["name"]: row for row in grid["capabilities"]}
    assert rows["Identity Verification"]["current_maturity_level"] == 2
    assert rows["Identity Verification"]["target_maturity_level"] == 4
    assert rows["Customer Onboarding Mgmt"]["current_maturity_level"] is None
    assert rows["Customer Onboarding Mgmt"]["target_maturity_level"] is None


def test_create_value_stream_avoids_deprecated_utcnow(db_session, make_org, tenant_ctx):
    org = make_org("vs-stage-now")

    with tenant_ctx(org.id):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            vs = vs_service.create_value_stream({"name": "Servicing"})

    assert vs.name == "Servicing"
    assert not any(
        "value_stream_service.py" in str(getattr(w, "filename", ""))
        and "datetime.datetime.utcnow()" in str(w.message)
        for w in caught
    )


def test_fetch_and_update_stage_avoid_legacy_query_get(db_session, make_org, tenant_ctx):
    org = make_org("vs-stage-fetch")
    now = utcnow().replace(tzinfo=None)

    with tenant_ctx(org.id):
        vs = vs_service.create_value_stream({"name": "Claims"})
        stage = ValueStreamStage(
            name="Assess claim",
            value_stream_id=vs.id,
            stage_order=1,
            created_at=now,
            updated_at=now,
        )
        db_session.add(stage)
        db_session.flush()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert vs_service.get_value_stream(vs.id).id == vs.id
            assert vs_service.update_stage(stage.id, {"exit_criteria": "Decision issued"}).id == stage.id

    assert not any("Query.get()" in str(w.message) for w in caught)
