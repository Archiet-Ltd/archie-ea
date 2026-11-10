"""The agreeing-figures check holds the layer counts and the critical gap count.

The store-agreement check asks every screen that states a layer's element count, or
how many gaps are critical, and fails when two of them answer differently. These
tests seed two organisations, one with elements of a type its layer does not list and
gaps whose priority and severity differ, and read the check's own findings.
"""
import importlib.util
import os
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_store_agreement.py")


def _load_gate():
    spec = importlib.util.spec_from_file_location("check_store_agreement", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()

pytestmark = pytest.mark.usefixtures("db_session")

CONCEPTS = ["motivation layer elements", "critical gaps", "closed gaps"]


def _user(db_session, org):
    from app.models.user import User

    user = User(email="figures-%s@example.com" % uuid.uuid4().hex[:8], first_name="Figures",
                last_name="Check", organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _seed(db_session, org, goals, strays, critical, closed=0):
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import Gap

    for index in range(goals):
        db_session.add(ArchiMateElement(name="Goal %d" % index, type="Goal", layer="Motivation",
                                        organization_id=org.id))
    for index in range(strays):
        db_session.add(ArchiMateElement(name="Stray %d" % index, type="Node", layer="Motivation",
                                        organization_id=org.id))
    for index in range(critical):
        db_session.add(Gap(name="Critical %d" % index, severity="critical", priority="medium",
                           organization_id=org.id))
    # Critical by priority only: a critical gap on every screen (the one rule of the gap register).
    db_session.add(Gap(name="Urgent", severity="high", priority="critical", organization_id=org.id))
    # Closed by resolution status, with and without a work package: closed means resolved, not planned.
    for index, status in enumerate(("resolved", "closed", "completed")[:closed]):
        db_session.add(Gap(name="Closed %d" % index, severity="low", priority="low",
                           resolution_status=status, organization_id=org.id))
    db_session.add(Gap(name="Still open", severity="low", priority="low",
                       resolution_status="identified", organization_id=org.id))


def _observe(app, db_session, tenant_ctx, org, user):
    from app import db

    db_session.commit()
    with tenant_ctx(org.id):
        observations, notes = gate.observe_tenant(
            app, db, org.id, user, concepts={name: gate.CONCEPTS[name] for name in CONCEPTS})
    findings, more_notes = gate.compare(observations)
    return observations, findings, notes + more_notes


def test_layer_and_critical_gap_figures_agree_in_each_of_two_organisations(
        app, db_session, make_org, tenant_ctx):
    mine, theirs = make_org("figures-mine"), make_org("figures-theirs")
    _seed(db_session, mine, goals=3, strays=2, critical=2, closed=1)
    _seed(db_session, theirs, goals=6, strays=1, critical=7, closed=3)
    mine_user, their_user = _user(db_session, mine), _user(db_session, theirs)

    for org, user, goals, critical, closed in ((mine, mine_user, 3, 3, 1), (theirs, their_user, 6, 8, 3)):
        observations, findings, notes = _observe(app, db_session, tenant_ctx, org, user)
        assert findings == [], (findings, notes, observations)
        assert not any("no-evidence" in note for note in notes), notes
        layer = {name: count for name, count, _s, _u in observations["motivation layer elements"]}
        assert set(layer.values()) == {goals}, layer
        gaps = {name: count for name, count, _s, _u in observations["critical gaps"]}
        assert set(gaps.values()) == {critical}, gaps
        shut = {name: count for name, count, _s, _u in observations["closed gaps"]}
        assert set(shut.values()) == {closed}, shut
        assert len(shut) == 3, shut
