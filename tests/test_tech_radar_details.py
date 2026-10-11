"""Radar entries carry a review date and a requesting initiative; standards read
their ring from the radar.

Covers the service rules behind the radar and standards screens: details are
only written when sent (a ring move keeps them), an initiative from another
organisation is refused, and a standard published with a ring places its
technology element on the radar in the same transaction.
"""

from datetime import date

import pytest

from app.modules.tech_radar import service as radar


def _tech_element(db_session, org, name):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(
        name=name, type="SystemSoftware", layer="Technology", organization_id=org.id
    )
    db_session.add(element)
    db_session.flush()
    return element


def _initiative(db_session, org, name):
    from app.models.strategic import StrategicInitiative

    initiative = StrategicInitiative(name=name, organization_id=org.id)
    db_session.add(initiative)
    db_session.flush()
    return initiative


def test_details_are_recorded_and_a_ring_move_keeps_them(app, db_session, make_org, tenant_ctx):
    org = make_org("radar")
    element = _tech_element(db_session, org, "Stream Broker")
    initiative = _initiative(db_session, org, "Integration Programme")

    with tenant_ctx(org.id):
        entry = radar.classify(
            element.id, "assess", "Evaluate for order events", None,
            review_date=date(2027, 3, 31), requesting_initiative_id=initiative.id,
        )
        assert (entry.ring, entry.review_date, entry.requesting_initiative_id) == (
            "assess", date(2027, 3, 31), initiative.id)

        moved = radar.classify(element.id, "trial", None, None)
        assert moved.ring == "trial"
        assert moved.rationale == "Evaluate for order events"
        assert moved.review_date == date(2027, 3, 31)
        assert moved.requesting_initiative_id == initiative.id
        assert moved.to_dict()["requesting_initiative_name"] == "Integration Programme"


def test_an_initiative_from_another_organisation_is_refused(app, db_session, make_org, tenant_ctx):
    mine = make_org("radar-mine")
    theirs = make_org("radar-theirs")
    element = _tech_element(db_session, mine, "Queue")
    foreign = _initiative(db_session, theirs, "Their Programme")
    db_session.expunge_all()

    with tenant_ctx(mine.id):
        with pytest.raises(ValueError):
            radar.classify(element.id, "assess", None, None, requesting_initiative_id=foreign.id)


def test_same_type_peers_are_listed_with_their_ring(app, db_session, make_org, tenant_ctx):
    org = make_org("radar-peers")
    first = _tech_element(db_session, org, "Queue A")
    second = _tech_element(db_session, org, "Queue B")

    with tenant_ctx(org.id):
        radar.classify(first.id, "hold", None, None)
        state = radar.radar_state()
    peers = state["unclassified_peers"][second.id]
    assert [(p["name"], p["ring"]) for p in peers] == [("Queue A", "hold")]
    held = state["rings"]["hold"][0]
    assert [(p["name"], p["ring"]) for p in held["same_type"]] == [("Queue B", None)]


def test_a_standard_published_with_a_ring_places_its_element_on_the_radar(
    app, db_session, make_org, tenant_ctx
):
    from app.models.tech_radar import TechRadarEntry
    from app.modules.governance.routes.governance_dashboard_routes import (
        _create_standard,
        _standards_by_domain,
    )

    org = make_org("standards")
    element = _tech_element(db_session, org, "Message Bus")

    with tenant_ctx(org.id):
        standard, error = _create_standard({
            "technology_name": "Message Bus", "category": "Messaging", "status": "preferred",
            "ring": "adopt", "archimate_element_id": str(element.id),
            "sunset_date": "2030-06-30", "rationale": "Supported broker",
        }, None)
        assert error is None
        assert standard.sunset_date == date(2030, 6, 30)
        entry = TechRadarEntry.query.filter_by(archimate_element_id=element.id).one()
        assert entry.ring == "adopt"

        bare, error = _create_standard({
            "technology_name": "Legacy FTP", "category": "Messaging", "status": "deprecated",
        }, None)
        assert error is None
        grouped = _standards_by_domain()
    rows = {r["standard"].technology_name: r["ring"] for r in grouped["Messaging"]}
    assert rows == {"Message Bus": "adopt", "Legacy FTP": None}


def test_a_ring_without_an_element_is_refused(app, db_session, make_org, tenant_ctx):
    from app.modules.governance.routes.governance_dashboard_routes import _create_standard

    org = make_org("standards-ring")
    with tenant_ctx(org.id):
        standard, error = _create_standard({
            "technology_name": "Broker", "category": "Messaging", "status": "approved",
            "ring": "adopt",
        }, None)
    assert standard is None
    assert "technology element" in error
