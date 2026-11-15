"""The demonstration organisation records decisions against the systems the module recordings open.

A system's impact graph lists the decisions recorded against it; without any, the graph says nothing has been
decided about a system the organisation has plainly decided about. The seed writes them for the demonstration
organisation only, once, and another organisation's systems are left alone.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _system(db_session, org, name):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    return element


def test_the_systems_a_decision_governs_list_it_and_another_organisation_sees_none(app, db_session, make_org):
    from app.commands.seed_demo_company import _seed_decisions
    from app.models.architecture_decision import ArchitectureDecision

    mine, theirs = make_org("demo"), make_org("customer")
    my_ledger = _system(db_session, mine, "Calibration Ledger")
    my_scheduler = _system(db_session, mine, "Field Service Scheduler")
    their_ledger = _system(db_session, theirs, "Calibration Ledger")

    assert _seed_decisions(mine.id, None) == {"architecture_decisions_created": 3}

    assert len(ArchitectureDecision.affecting_elements([my_ledger.id], mine.id)) == 2
    assert len(ArchitectureDecision.affecting_elements([my_scheduler.id], mine.id)) == 1
    assert ArchitectureDecision.affecting_elements([their_ledger.id], theirs.id) == []
    # a decision written for one organisation is never listed against another organisation's system
    assert ArchitectureDecision.affecting_elements([my_ledger.id], theirs.id) == []


def test_seeding_twice_adds_nothing_and_two_organisations_can_both_be_seeded(app, db_session, make_org):
    from app.commands.seed_demo_company import _seed_decisions

    first, second = make_org("demo"), make_org("demo")
    for org in (first, second):
        _system(db_session, org, "Calibration Ledger")
        _system(db_session, org, "Field Service Scheduler")

    assert _seed_decisions(first.id, None)["architecture_decisions_created"] == 3
    assert _seed_decisions(first.id, None)["architecture_decisions_created"] == 0
    assert _seed_decisions(second.id, None)["architecture_decisions_created"] == 3, \
        "decision ids are unique across organisations, so a second organisation must get its own"


def test_the_overlapping_copy_carries_the_owner_vendor_and_capability_of_the_system_it_overlaps(app, db_session, make_org, tenant_ctx):
    org = make_org("demo")
    with tenant_ctx(org.id):
        _check_the_copy(org)


def _check_the_copy(org):
    from app import db as _db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.user import User
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from scripts.capture_screenshots import _complete_overlapping_copy

    db_session = _db.session
    suffix = uuid.uuid4().hex[:8]
    person = User(email=f"owner-{suffix}@example.test", first_name="Own", last_name="Er", organization_id=org.id)
    db_session.add(person)
    capability = BusinessCapability(name=f"Records {suffix}", organization_id=org.id)
    db_session.add(capability)
    db_session.flush()
    source = ApplicationComponent(name="Document Management", organization_id=org.id, vendor_name="OpenText",
                                  lifecycle_status="3. sunset", application_code=f"SRC-{suffix}")
    copy = ApplicationComponent(name="Document Management", organization_id=org.id, lifecycle_status="active",
                                application_code=f"COPY-{suffix}")
    db_session.add_all([source, copy])
    db_session.flush()
    db_session.add(ApplicationOwner(application_id=source.id, user_id=person.id, organization_id=org.id,
                                    ownership_type="primary"))
    db_session.add(ApplicationCapabilityMapping(application_component_id=source.id,
                                                business_capability_id=capability.id, organization_id=org.id))
    db_session.flush()

    _complete_overlapping_copy(f"COPY-{suffix}")

    assert copy.vendor_name == "OpenText"
    assert ApplicationOwner.query.filter_by(application_id=copy.id, user_id=person.id).count() == 1
    assert ApplicationCapabilityMapping.query.filter_by(application_component_id=copy.id).count() == 1
    _complete_overlapping_copy(f"COPY-{suffix}")  # idempotent
    assert ApplicationOwner.query.filter_by(application_id=copy.id).count() == 1
