"""Tests for ``IntelligenceQueryService.programme_for_element`` (L5): work
packages seeded directly on an element, each carrying its own blast-radius
traversal via the same ``cross_layer_impact`` code path L1/L6 already use --
no second traversal implementation, no fabricated cost figures.

Fixtures (app, db_session, make_org) are discovered via
app/modules/conftest.py's import of tests.conftest,
same pattern as test_query_service.py. No import needed here.
"""

from __future__ import annotations

import datetime as _dt


def _element(db_session, org_id, name, layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _work_package(db_session, element, *, name="Migrate to cloud", status="in_progress",
                   progress_percentage=40.0, owner_id=None, estimated_cost=None, actual_cost=None,
                   start_date=None, end_date=None):
    from app.models.unified_work_package import UnifiedWorkPackage

    wp = UnifiedWorkPackage(
        name=name,
        archimate_element_id=element.id,
        organization_id=element.organization_id,
        business_capability="Test Capability",
        status=status,
        progress_percentage=progress_percentage,
        owner_id=owner_id,
        estimated_cost=estimated_cost,
        actual_cost=actual_cost,
        start_date=start_date,
        end_date=end_date,
    )
    db_session.add(wp)
    db_session.flush()
    return wp


def test_element_with_no_work_packages_returns_honest_empty(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("programme-lens-empty")
    a = _element(db_session, org.id, "A")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    assert result["work_packages"] == []
    assert result["reasons"] == ["no_work_package_recorded"]


def test_unknown_element_returns_element_not_found_reason(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("programme-lens-unknown")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(999999999)

    assert result["work_packages"] == []
    assert result["reasons"] == ["element_not_found"]


def test_no_tenant_context_returns_honest_reason(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("programme-lens-no-ctx")
    a = _element(db_session, org.id, "A")
    _work_package(db_session, a)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        result = IntelligenceQueryService.programme_for_element(a.id)

    assert result["work_packages"] == []
    assert result["reasons"] == ["no_tenant_context"]


def test_work_package_with_real_cost_data_returns_variance(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("programme-lens-costed")
    a = _element(db_session, org.id, "A")
    _work_package(
        db_session, a, name="Migrate ERP", status="in_progress",
        estimated_cost=100000.0, actual_cost=120000.0,
    )
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    assert result["reasons"] == []
    assert len(result["work_packages"]) == 1
    row = result["work_packages"][0]
    assert row["name"] == "Migrate ERP"
    assert row["cost_reason"] is None
    assert row["cost_variance_pct"] == 20.0


def test_work_package_with_no_estimated_cost_is_honestly_not_costed(app, db_session, make_org):
    """Pins the exact fabrication risk found in the model's own
    calculate_budget_variance() helper: a work package with no estimate must
    never report a variance of 0 -- that reads as "on budget", a claim
    nobody measured."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("programme-lens-uncosted")
    a = _element(db_session, org.id, "A")
    _work_package(db_session, a, name="Discovery spike", estimated_cost=None, actual_cost=None)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    row = result["work_packages"][0]
    assert row["cost_variance_pct"] is None
    assert row["cost_reason"] == "not_costed"


def test_overdue_and_owner_fields_render_honestly(app, db_session, make_org):
    from app.models.user import User
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("programme-lens-owner")
    a = _element(db_session, org.id, "A")
    owner = User(email="owner@example.com", first_name="Pat", last_name="Owner", organization_id=org.id)
    db_session.add(owner)
    db_session.flush()

    past = _dt.datetime.utcnow() - _dt.timedelta(days=5)
    _work_package(
        db_session, a, name="Overdue migration", status="in_progress",
        owner_id=owner.id, end_date=past,
    )
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    row = result["work_packages"][0]
    assert row["is_overdue"] is True
    assert row["owner"] == "Pat Owner"


def test_owner_absent_reads_as_honest_none_not_a_placeholder(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("programme-lens-no-owner")
    a = _element(db_session, org.id, "A")
    _work_package(db_session, a, name="Unowned work", owner_id=None)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    assert result["work_packages"][0]["owner"] is None


def test_work_package_blast_radius_reuses_cross_layer_impact(app, db_session, make_org):
    """Pin extended (plateau/gap addition): the programme lens adds ``plateau``/
    ``plateau_reason`` to every affected row in place, so the rows are no
    longer byte-for-byte identical to a fresh ``cross_layer_impact`` call --
    they are identical apart from those two added keys, which is what this
    now checks explicitly rather than a bare equality."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("programme-lens-blast")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    _work_package(db_session, a, name="A is being replaced")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        programme_result = IntelligenceQueryService.programme_for_element(a.id, max_depth=3)
        direct_impact = IntelligenceQueryService.cross_layer_impact(a.id, max_depth=3, with_owner=True)

    assert len(programme_result["work_packages"]) == 1
    affected_rows = programme_result["work_packages"][0]["affected_rows"]
    assert len(affected_rows) == len(direct_impact["rows"]) == 1
    for affected_row, direct_row in zip(affected_rows, direct_impact["rows"]):
        assert "plateau" in affected_row and "plateau_reason" in affected_row
        stripped = {k: v for k, v in affected_row.items() if k not in ("plateau", "plateau_reason")}
        assert stripped == direct_row


def test_multiple_work_packages_on_one_element_each_get_their_own_row(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("programme-lens-multi")
    a = _element(db_session, org.id, "A")
    _work_package(db_session, a, name="Phase 1")
    _work_package(db_session, a, name="Phase 2")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    names = {wp["name"] for wp in result["work_packages"]}
    assert names == {"Phase 1", "Phase 2"}


def _foreign_owner_result(app, db_session, make_org, suffix):
    from app.models.user import User
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org(f"programme-lens-foreign-owner-{suffix}")
    other = make_org(f"programme-lens-foreign-owner-other-{suffix}")
    a = _element(db_session, org.id, "A")
    foreigner = User(
        email=f"foreign-{suffix}@example.com", first_name="Fran", last_name="Foreign",
        organization_id=other.id,
    )
    db_session.add(foreigner)
    db_session.flush()
    _work_package(db_session, a, name="Owned by someone elsewhere", owner_id=foreigner.id)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        return IntelligenceQueryService.programme_for_element(a.id)


def test_a_work_package_owner_from_another_organisation_is_never_named(app, db_session, make_org):
    result = _foreign_owner_result(app, db_session, make_org, "fence")

    row = result["work_packages"][0]
    assert row["owner"] is None
    assert "Fran" not in repr(result) and "foreign-fence@example.com" not in repr(result)


def test_the_owner_tenant_predicate_is_what_stops_the_foreign_owner_leak(
    app, db_session, make_org, monkeypatch
):
    """Mutation proof: replace the seam with a predicate that matches every
    user and the foreign owner IS named, so the test above is not passing
    by accident."""
    import sqlalchemy as sa

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    monkeypatch.setattr(
        IntelligenceQueryService, "_owner_user_tenant_predicate", staticmethod(lambda org_id: sa.true())
    )

    result = _foreign_owner_result(app, db_session, make_org, "mutation")

    # The foreign user's real name, not just a truthy value, proves the
    # predicate seam is what stops the leak rather than something else.
    assert result["work_packages"][0]["owner"] == "Fran Foreign"
