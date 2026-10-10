"""ArchitectureChangeImpactService + ChangeRequest tenant isolation
(PR 297 defects 2-3: ChangeRequest had no organization_id column at all,
and the service's own BFS ran with no tenant fence)."""

from __future__ import annotations

import uuid


def _element(db_session, org_id, name, etype="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=etype, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _application_component(db_session, org_id, element_id, name="App"):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(
        name=name, organization_id=org_id, archimate_element_id=element_id
    )
    db_session.add(comp)
    db_session.flush()
    return comp


def _change_request(db_session, org_id, scope_app_ids):
    from app.models.architecture_review_board import ChangeRequest

    cr = ChangeRequest(
        change_request_number=f"CR-{uuid.uuid4().hex[:8].upper()}",
        title="Test change",
        description="Test change request",
        change_type="technology_change",
        status="draft",
        organization_id=org_id,
    )
    db_session.add(cr)
    db_session.flush()
    # impact_assessment is read by _extract_scope_app_ids via getattr; not a
    # mapped column on ChangeRequest, but set here the same way the service
    # reads it (a plain attribute is enough within this session).
    cr.impact_assessment = {"scope_app_ids": scope_app_ids}
    return cr


def test_assess_change_impact_finds_downstream_apps(app, db_session, make_org):
    org = make_org("change-impact-basic")
    root = _element(db_session, org.id, "Root App")
    downstream = _element(db_session, org.id, "Downstream App")
    _relationship(db_session, org.id, root, downstream)
    root_comp = _application_component(db_session, org.id, root.id, name="Root App")
    _application_component(db_session, org.id, downstream.id, name="Downstream App")
    cr = _change_request(db_session, org.id, [root_comp.id])
    db_session.commit()

    from app.services.architecture_change_impact_service import ArchitectureChangeImpactService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = ArchitectureChangeImpactService().assess_change_impact(cr.id)

    assert result["scope_app_count"] == 1
    assert result["impact_radius"] == 1
    assert result["downstream_apps"] == ["Downstream App"]
    assert result["blast_radius_tier"] == "local"


def test_change_request_lookup_never_crosses_org_boundary(app, db_session, make_org):
    """The old ChangeRequest model had no organization_id at all, so
    db.session.get(ChangeRequest, id) returned any org's row."""
    org_a = make_org("change-impact-org-a")
    org_b = make_org("change-impact-org-b")

    b_root = _element(db_session, org_b.id, "Org B Root")
    b_comp = _application_component(db_session, org_b.id, b_root.id, name="Org B App")
    cr_b = _change_request(db_session, org_b.id, [b_comp.id])
    db_session.commit()

    from app.services.architecture_change_impact_service import ArchitectureChangeImpactService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_a.id
        result = ArchitectureChangeImpactService().assess_change_impact(cr_b.id)

    assert result == {"error": "not_found", "change_request_id": cr_b.id}


def test_downstream_apps_never_cross_org_boundary(app, db_session, make_org):
    """Even if a change request's own scope accidentally named another
    org's application id, the downstream walk must never return it."""
    org_a = make_org("change-impact-walk-org-a")
    org_b = make_org("change-impact-walk-org-b")

    a_root = _element(db_session, org_a.id, "A Root")
    a_comp = _application_component(db_session, org_a.id, a_root.id, name="A App")

    b_root = _element(db_session, org_b.id, "B Root")
    b_downstream = _element(db_session, org_b.id, "B Downstream")
    _relationship(db_session, org_b.id, b_root, b_downstream)
    b_root_comp = _application_component(db_session, org_b.id, b_root.id, name="B Root App")
    _application_component(db_session, org_b.id, b_downstream.id, name="B Downstream App")

    # Org A's scope list names A's own app AND org B's real app id -- an
    # attacker-controlled payload naming a foreign id, not a guess.
    cr_a = _change_request(db_session, org_a.id, [a_comp.id, b_root_comp.id])
    db_session.commit()

    from app.services.architecture_change_impact_service import ArchitectureChangeImpactService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_a.id
        result = ArchitectureChangeImpactService().assess_change_impact(cr_a.id)

    # The tenant-fenced ApplicationComponent query drops org B's real id --
    # only org A's own app resolves into scope.
    assert result["scope_app_count"] == 1
    assert "B Downstream App" not in result["downstream_apps"]
    assert "B Root App" not in result["downstream_apps"]


NO_PRE_MIGRATION_TEST_NOTE = """
No test here for the migration's backfill UPDATE itself: this test
harness builds its schema with create_all() from the current model
(organization_id already NOT NULL), so there is no way to get a
pre-migration row -- one with no organization_id at all -- into this
database to backfill. The migration's own idempotency guards (column
existence, NULL-count check before tightening to NOT NULL) were
reviewed by hand; exercising the UPDATE ... FROM against a genuine
pre-migration snapshot would need a separate, migration-level test
harness, out of scope here.
"""
