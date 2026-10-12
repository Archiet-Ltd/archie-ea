"""Tests for the single owner chain, ``_current_ownerships_batch``, and the
``_resolve_owners_batch`` projection built on it.

Each test guards one defect from the review that withdrew the L4 ownership
read: a second owner chain, a unit read with no tenant fence, expired
ownership shown as current, and personal data serialised without being shown.

The tests call the readers inside a request context that sets NO current
organisation, so the ORM tenant listener is off and only the explicit
predicates fence the reads. That is what lets a seam be replaced and the test
go red.

Fixtures (app, db_session, make_org) come from
app/modules/intelligence/tests/conftest.py.
"""

from __future__ import annotations

import datetime as _dt

AS_OF = _dt.date(2026, 6, 15)


def _element(db_session, org_id, name="A"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _component(db_session, org_id, element):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(name=f"{element.name} App", organization_id=org_id, archimate_element_id=element.id)
    db_session.add(comp)
    db_session.flush()
    return comp


def _unit(db_session, org_id, name="Finance"):
    from app.models.enterprise_intelligence import OrganizationUnit

    unit = OrganizationUnit(organization_id=org_id, name=name, unit_type="Department")
    db_session.add(unit)
    db_session.flush()
    return unit


def _ownership(db_session, org_id, component, unit, *, kind="Business Owner", start=None, end=None,
               contact=None, email=None):
    from app.models.enterprise_intelligence import ApplicationOwnership

    row = ApplicationOwnership(
        organization_id=org_id,
        application_id=component.id,
        organization_unit_id=unit.id,
        ownership_type=kind,
        primary_contact=contact,
        contact_email=email,
        start_date=start,
        end_date=end,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _world(db_session, make_org, label):
    org = make_org(f"ownerships-{label}")
    element = _element(db_session, org.id)
    component = _component(db_session, org.id, element)
    unit = _unit(db_session, org.id)
    return org, element, component, unit


def _read(app, org_id, element_ids, as_of=AS_OF):
    from app.modules.intelligence.services.query_service import _current_ownerships_batch

    with app.test_request_context("/"):
        return _current_ownerships_batch(list(element_ids), org_id, as_of)


def test_returns_every_current_ownership_for_an_element(app, db_session, make_org):
    org, element, component, unit = _world(db_session, make_org, "all")
    _ownership(db_session, org.id, component, unit, kind="Business Owner")
    _ownership(db_session, org.id, component, unit, kind="Technical Owner")
    db_session.commit()

    rows = _read(app, org.id, [element.id])[element.id]

    assert [r["ownership_type"] for r in rows] == ["Business Owner", "Technical Owner"]
    assert {r["unit_name"] for r in rows} == {"Finance"}


def test_an_element_with_no_ownership_gets_an_empty_list(app, db_session, make_org):
    org, element, component, unit = _world(db_session, make_org, "none")
    db_session.commit()

    assert _read(app, org.id, [element.id]) == {element.id: []}


def test_rows_are_ordered_by_start_date_with_unset_last_then_id(app, db_session, make_org):
    org, element, component, unit = _world(db_session, make_org, "order")
    _ownership(db_session, org.id, component, unit, kind="No date")
    _ownership(db_session, org.id, component, unit, kind="Later", start=_dt.date(2026, 3, 1))
    _ownership(db_session, org.id, component, unit, kind="Earlier", start=_dt.date(2025, 1, 1))
    db_session.commit()

    rows = _read(app, org.id, [element.id])[element.id]

    assert [r["ownership_type"] for r in rows] == ["Earlier", "Later", "No date"]


def test_an_expired_ownership_is_not_current(app, db_session, make_org):
    org, element, component, unit = _world(db_session, make_org, "expired")
    _ownership(db_session, org.id, component, unit, kind="Old", end=AS_OF - _dt.timedelta(days=1))
    _ownership(db_session, org.id, component, unit, kind="Now")
    db_session.commit()

    rows = _read(app, org.id, [element.id])[element.id]

    assert [r["ownership_type"] for r in rows] == ["Now"]


def test_a_future_dated_ownership_is_not_current(app, db_session, make_org):
    org, element, component, unit = _world(db_session, make_org, "future")
    _ownership(db_session, org.id, component, unit, kind="Not yet", start=AS_OF + _dt.timedelta(days=1))
    db_session.commit()

    assert _read(app, org.id, [element.id])[element.id] == []


def test_boundary_days_are_current(app, db_session, make_org):
    org, element, component, unit = _world(db_session, make_org, "boundary")
    _ownership(db_session, org.id, component, unit, kind="Ends today", end=AS_OF)
    _ownership(db_session, org.id, component, unit, kind="Starts today", start=AS_OF)
    db_session.commit()

    rows = _read(app, org.id, [element.id])[element.id]

    assert {r["ownership_type"] for r in rows} == {"Ends today", "Starts today"}


def test_a_unit_from_another_organisation_is_never_returned(app, db_session, make_org):
    """The exact leakable graph from the review: a correctly scoped ownership
    row whose ``organization_unit_id`` names another tenant's unit."""
    org, element, component, unit = _world(db_session, make_org, "foreign-unit")
    other = make_org("ownerships-foreign-unit-other")
    foreign_unit = _unit(db_session, other.id, name="Other Tenant Finance")
    _ownership(db_session, org.id, component, foreign_unit, kind="Business Owner")
    db_session.commit()

    result = _read(app, org.id, [element.id])

    assert result[element.id] == []
    assert "Other Tenant Finance" not in repr(result)


def test_an_ownership_row_from_another_organisation_is_never_returned(app, db_session, make_org):
    org, element, component, unit = _world(db_session, make_org, "foreign-row")
    other = make_org("ownerships-foreign-row-other")
    _ownership(db_session, other.id, component, unit, kind="Forged row")
    db_session.commit()

    assert _read(app, org.id, [element.id])[element.id] == []


def test_a_component_from_another_organisation_yields_nothing(app, db_session, make_org):
    org = make_org("ownerships-foreign-component")
    other = make_org("ownerships-foreign-component-other")
    element = _element(db_session, org.id)
    foreign_component = _component(db_session, other.id, element)
    unit = _unit(db_session, other.id)
    _ownership(db_session, other.id, foreign_component, unit)
    db_session.commit()

    assert _read(app, org.id, [element.id])[element.id] == []


def test_the_payload_carries_only_the_fields_the_card_renders(app, db_session, make_org):
    org, element, component, unit = _world(db_session, make_org, "pii")
    unit.head_of_unit = "Pat Head"
    unit.contact_email = "unit@example.com"
    _ownership(
        db_session, org.id, component, unit, contact="Jordan Owner", email="jordan@example.com",
        start=_dt.date(2026, 1, 1),
    )
    db_session.commit()

    row = _read(app, org.id, [element.id])[element.id][0]

    assert set(row) == {"ownership_id", "ownership_type", "start_date", "unit_id", "unit_name"}
    blob = repr(row)
    for hidden in ("jordan@example.com", "Jordan Owner", "unit@example.com", "Pat Head"):
        assert hidden not in blob


def test_resolve_owners_batch_is_the_first_row_of_the_same_reader(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import NO_OWNERSHIP_REASON, _resolve_owners_batch

    org, element, component, unit = _world(db_session, make_org, "projection")
    _ownership(db_session, org.id, component, unit, kind="Second", start=_dt.date(2026, 4, 1))
    _ownership(db_session, org.id, component, unit, kind="First", start=_dt.date(2026, 1, 1))
    bare = _element(db_session, org.id, name="Bare")
    _component(db_session, org.id, bare)
    db_session.commit()

    with app.test_request_context("/"):
        rows = _read(app, org.id, [element.id])[element.id]
        owners = _resolve_owners_batch([element.id, bare.id], org.id, AS_OF)

    owner, reason = owners[element.id]
    assert reason is None
    assert owner == {
        "organization_unit_id": rows[0]["unit_id"],
        "name": rows[0]["unit_name"],
        "ownership_type": "First",
    }
    assert owners[bare.id] == (None, NO_OWNERSHIP_REASON)


def test_mutating_the_ownership_seam_lets_a_forged_row_through(app, db_session, make_org, monkeypatch):
    """Mutation proof: with the ownership predicate matching everything, the
    forged row of another tenant IS returned, so the fence test above is real."""
    import sqlalchemy as sa

    from app.modules.intelligence.services import query_service

    org, element, component, unit = _world(db_session, make_org, "mut-row")
    other = make_org("ownerships-mut-row-other")
    _ownership(db_session, other.id, component, unit, kind="Forged row")
    db_session.commit()
    monkeypatch.setattr(query_service, "_ownership_tenant_predicate", lambda org_id: sa.true())

    rows = _read(app, org.id, [element.id])[element.id]

    assert [r["ownership_type"] for r in rows] == ["Forged row"]


def test_the_unit_seam_is_an_organisation_predicate_and_a_second_fence_backs_it(
    app, db_session, make_org, monkeypatch
):
    """The unit predicate must name the unit's organisation column. Even with
    it replaced by a match-everything predicate the foreign unit is still not
    returned, because the row is re-checked after the fetch: two independent
    fences on the one read the review found unfenced."""
    import sqlalchemy as sa

    from app.modules.intelligence.services import query_service

    assert "organization_units.organization_id" in str(query_service._unit_tenant_predicate(1))

    org, element, component, unit = _world(db_session, make_org, "mut-unit")
    other = make_org("ownerships-mut-unit-other")
    foreign_unit = _unit(db_session, other.id, name="Other Tenant Finance")
    _ownership(db_session, org.id, component, foreign_unit)
    db_session.commit()
    monkeypatch.setattr(query_service, "_unit_tenant_predicate", lambda org_id: sa.true())

    # One fence replaced: the post-fetch check still drops the foreign unit.
    assert _read(app, org.id, [element.id])[element.id] == []

    # Both replaced: the foreign unit IS returned, so each fence does real work.
    monkeypatch.setattr(query_service, "_unit_belongs_to_org", lambda unit_org_id, org_id: True)
    rows = _read(app, org.id, [element.id])[element.id]
    assert [r["unit_name"] for r in rows] == ["Other Tenant Finance"]
