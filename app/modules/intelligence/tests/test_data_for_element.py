"""Tests for ``IntelligenceQueryService.data_for_element`` (L7, the Data lens).

The lens answers "what data does this hold or produce, who stewards it, and where does it
flow?" from ``DataObject`` and ``DataLineage``. These tests prove the tenant fences (a foreign
data object, a lineage edge whose other end is another organisation's element), that absence is
stated and never filled (no zero, no invented steward), and that nothing sensitive leaks into
the payload.

Fixtures (app, db_session, make_org) come from tests.conftest via this directory's conftest.
"""

from __future__ import annotations

import uuid

from app.modules.intelligence.services.query_service import IntelligenceQueryService


def _element(db_session, org_id, name="A App"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _object(db_session, org_id, element, name="Customer master", **fields):
    from app.models.application_layer import DataObject

    obj = DataObject(name=name, archimate_element_id=element.id, organization_id=org_id, **fields)
    db_session.add(obj)
    db_session.flush()
    return obj


def _edge(db_session, org_id, source, target, **fields):
    from app.models.all_missing_models import DataLineage

    edge = DataLineage(
        name=f"{uuid.uuid4().hex[:6]}", archimate_element_id=source.id,
        target_archimate_element_id=target.id, organization_id=org_id, **fields,
    )
    db_session.add(edge)
    db_session.flush()
    return edge


def _run(app, org_id, element_id):
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return IntelligenceQueryService.data_for_element(element_id)


def _two_orgs(db_session, make_org):
    return make_org("data-lens-a"), make_org("data-lens-b")


def test_a_recorded_object_is_returned_with_steward_and_owner_as_text(app, db_session, make_org):
    org, _ = _two_orgs(db_session, make_org)
    el = _element(db_session, org.id)
    _object(db_session, org.id, el, data_type="Database Table", data_classification="Confidential",
            is_master_data=True, contains_pii=True, gdpr_scope=True, retention_period_days=365,
            data_steward="  A. Steward ", data_owner="Finance")
    db_session.commit()

    result = _run(app, org.id, el.id)

    [obj] = result["data_objects"]
    assert obj["name"] == "Customer master" and obj["steward"] == "A. Steward" and obj["owner"] == "Finance"
    assert obj["recorded_as_text"] is True and obj["contains_pii"] is True and obj["retention_period_days"] == 365
    assert "no_data_recorded" not in result["reasons"] and "no_steward_recorded" not in result["reasons"]


def test_an_object_of_another_organisation_is_never_returned(app, db_session, make_org):
    org_a, org_b = _two_orgs(db_session, make_org)
    el = _element(db_session, org_a.id)
    _object(db_session, org_b.id, el, name="Foreign data")
    db_session.commit()
    org_a_id, el_id = org_a.id, el.id
    db_session.expunge_all()

    result = _run(app, org_a_id, el_id)

    assert result["data_objects"] == []
    assert result["reasons"][0] == "no_data_recorded"


def test_an_element_of_another_organisation_reads_as_absent(app, db_session, make_org):
    org_a, org_b = _two_orgs(db_session, make_org)
    foreign = _element(db_session, org_b.id)
    _object(db_session, org_b.id, foreign)
    db_session.commit()
    org_a_id, foreign_id = org_a.id, foreign.id
    db_session.expunge_all()

    result = _run(app, org_a_id, foreign_id)

    assert result["data_objects"] == [] and result["flows"] == []
    assert result["reasons"] == ["element_not_found"]


def test_no_tenant_context_is_a_stated_absence(app, db_session, make_org):
    org, _ = _two_orgs(db_session, make_org)
    el = _element(db_session, org.id)
    db_session.commit()

    result = _run(app, None, el.id)

    assert result["reasons"] == ["no_tenant_context"] and result["data_objects"] == []


def test_no_object_is_no_data_recorded_and_no_lineage_is_stated_too(app, db_session, make_org):
    org, _ = _two_orgs(db_session, make_org)
    el = _element(db_session, org.id)
    db_session.commit()

    result = _run(app, org.id, el.id)

    assert result["data_objects"] == [] and result["flows"] == []
    assert result["reasons"] == ["no_data_recorded", "no_lineage_recorded"]


def test_blank_steward_and_owner_is_stated_and_no_name_is_invented(app, db_session, make_org):
    org, _ = _two_orgs(db_session, make_org)
    el = _element(db_session, org.id)
    _object(db_session, org.id, el, data_steward="   ", data_owner=None)
    db_session.commit()

    result = _run(app, org.id, el.id)

    [obj] = result["data_objects"]
    assert obj["steward"] is None and obj["owner"] is None
    assert result["reasons"] == ["no_steward_recorded", "no_lineage_recorded"]


def test_lineage_in_and_out_is_returned_and_names_the_other_end(app, db_session, make_org):
    org, _ = _two_orgs(db_session, make_org)
    el = _element(db_session, org.id, "Core")
    upstream = _element(db_session, org.id, "Upstream")
    downstream = _element(db_session, org.id, "Downstream")
    _edge(db_session, org.id, upstream, el, lineage_type="ETL", frequency="Daily")
    _edge(db_session, org.id, el, downstream, lineage_type="Real-time")
    db_session.commit()

    result = _run(app, org.id, el.id)

    flows = {(f["direction"], f["other_element_name"]): f for f in result["flows"]}
    assert set(flows) == {("in", "Upstream"), ("out", "Downstream")}
    assert flows[("in", "Upstream")]["frequency"] == "Daily"
    # The same name is also reachable through the elements map (added
    # alongside, for the graph rendering that needs more than a name).
    assert result["elements"][str(upstream.id)]["name"] == "Upstream"
    assert result["elements"][str(downstream.id)]["name"] == "Downstream"
    assert "no_lineage_recorded" not in result["reasons"]


def test_a_lineage_edge_to_another_organisations_element_is_dropped_not_named(app, db_session, make_org):
    org_a, org_b = _two_orgs(db_session, make_org)
    el = _element(db_session, org_a.id)
    foreign = _element(db_session, org_b.id, "Secret foreign system")
    _edge(db_session, org_a.id, el, foreign)
    db_session.commit()
    org_a_id, el_id = org_a.id, el.id
    db_session.expunge_all()

    result = _run(app, org_a_id, el_id)

    assert result["flows"] == []
    assert "Secret foreign system" not in str(result)
    assert "no_lineage_recorded" in result["reasons"]


def test_the_lineage_other_end_seam_carries_the_explicit_predicate(app, db_session, make_org, tenant_ctx, monkeypatch):
    """Mutation proof. The ambient organisation is B (the ORM filter alone WOULD find B's
    element) but the caller's organisation is A: the explicit predicate must still say no.
    Neutering ``_data_tenant_predicate`` makes the foreign element resolve, so the fence
    really is that predicate and not only the ambient listener."""
    from sqlalchemy import true as sa_true

    org_a, org_b = _two_orgs(db_session, make_org)
    foreign = _element(db_session, org_b.id, "Secret foreign system")
    db_session.commit()
    org_a_id, org_b_id, foreign_id = org_a.id, org_b.id, foreign.id

    with tenant_ctx(org_b_id):
        control = IntelligenceQueryService._lineage_other_end_visible({foreign_id}, org_a_id)
        assert control == {}

        monkeypatch.setattr(
            IntelligenceQueryService, "_data_tenant_predicate",
            staticmethod(lambda model, organization_id: sa_true()),
        )
        mutated = IntelligenceQueryService._lineage_other_end_visible({foreign_id}, org_a_id)
        assert mutated == {foreign_id: "Secret foreign system"}

        monkeypatch.undo()
        assert IntelligenceQueryService._lineage_other_end_visible({foreign_id}, org_a_id) == {}


def test_sensitive_and_operational_fields_are_never_in_the_payload(app, db_session, make_org):
    org, _ = _two_orgs(db_session, make_org)
    el = _element(db_session, org.id)
    _object(db_session, org.id, el, pii_fields='["ssn","email"]', storage_location="s3://secret-bucket",
            schema_name="secret_schema", table_name="secret_table", access_roles=["admin"])
    db_session.commit()

    text = str(_run(app, org.id, el.id))

    for leaked in ("ssn", "secret-bucket", "secret_schema", "secret_table", "access_roles", "pii_fields"):
        assert leaked not in text


def test_a_missing_figure_is_absent_not_zero(app, db_session, make_org):
    org, _ = _two_orgs(db_session, make_org)
    el = _element(db_session, org.id)
    _object(db_session, org.id, el)
    db_session.commit()

    [obj] = _run(app, org.id, el.id)["data_objects"]

    assert obj["retention_period_days"] is None
