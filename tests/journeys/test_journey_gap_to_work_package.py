"""Journey: can an identified gap become planned work?

The QA audit of 30 Aug 2026 (v2, High 1) called this "the single most important
link in an EA tool" and found it returning HTTP 500:

    "The dropdown is populated with derived gaps whose ids are synthetic
     composite strings (e.g. "business-279" = {capability_type}-{capability_id}),
     not database primary keys... The page supplies a value its own write
     endpoint cannot accept... Work packages can only be created by first
     discarding the gap linkage, destroying the traceability that justifies the
     work."

GET /api/roadmap/gaps returns two kinds of gap. Stored Gap rows carry an integer
primary key. Derived gaps -- one per capability with no application mapped, 543
of them on the audited environment -- are synthesised on read and carry a
composite id. The roadmap's own dropdown mixes both, so choosing a derived gap
sent "business-279" to an endpoint doing Gap.query.get(...).

This asserts the whole chain end to end, because that is the only level at which
the defect is visible: every table involved was healthy throughout.
"""

import uuid

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

ENDPOINT = "/capability-map/api/roadmap/work-packages"


def _unmapped_capability(db, org_id):
    """A capability with no applications -- exactly what synthesises a derived gap."""
    from app.models.business_capabilities import BusinessCapability

    capability = BusinessCapability(
        name="Unmapped Capability %s" % uuid.uuid4().hex[:8],
        organization_id=org_id,
    )
    db.session.add(capability)
    db.session.commit()
    return capability.id


def _gap_count(db):
    from app.models.implementation_migration import Gap

    return db.session.execute(db.select(db.func.count(Gap.id))).scalar()


def test_a_derived_gap_can_be_turned_into_planned_work(app, client):
    """The value chain: identified gap in, work package out, traceability kept."""
    from app import db
    from app.models.unified_work_package import UnifiedWorkPackage

    with app.app_context():
        org_id = make_org(db, "GapChain")
        architect_id = make_user(
            db, org_id, "gapea", enterprise_role="enterprise_architect",
            role_name="Architect",
        )
        capability_id = _unmapped_capability(db, org_id)
        gaps_before = _gap_count(db)

    login(client, architect_id)
    name = "Close the coverage gap %s" % uuid.uuid4().hex[:8]

    response = client.post(
        ENDPOINT,
        json={"name": name, "gap_id": "business-%d" % capability_id},
    )
    assert response.status_code == 201, (
        "converting an identified gap into planned work failed: %s %s"
        % (response.status_code, response.data[:200])
    )

    # PERSISTED, and the derived gap became a real row so the link is a real link.
    with app.app_context():
        db.session.expunge_all()
        work_package = db.session.execute(
            db.select(UnifiedWorkPackage).filter_by(name=name)
        ).scalar_one_or_none()
        assert work_package is not None, "the work package did not persist"
        assert work_package.organization_id == org_id
        assert work_package.gap_id is not None, "the work package is not linked to its gap"
        assert _gap_count(db) == gaps_before + 1, (
            "no Gap row was materialised, so the work package has nothing to "
            "trace back to"
        )


def test_converting_the_same_gap_twice_does_not_duplicate_it(app, client):
    """Two work packages against one gap is normal; two gaps for one is not."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "GapTwice")
        architect_id = make_user(
            db, org_id, "gaptwice", enterprise_role="enterprise_architect",
            role_name="Architect",
        )
        capability_id = _unmapped_capability(db, org_id)

    login(client, architect_id)
    reference = "business-%d" % capability_id

    assert client.post(ENDPOINT, json={
        "name": "First slice %s" % uuid.uuid4().hex[:6], "gap_id": reference,
    }).status_code == 201

    with app.app_context():
        after_first = _gap_count(db)

    assert client.post(ENDPOINT, json={
        "name": "Second slice %s" % uuid.uuid4().hex[:6], "gap_id": reference,
    }).status_code == 201

    with app.app_context():
        assert _gap_count(db) == after_first, (
            "the second conversion created a duplicate Gap for the same capability"
        )


def test_a_meaningless_gap_reference_is_refused_not_500(app, client):
    """The old failure was a 500 behind a modal reading only "Notice / Not Found"."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "GapBad")
        architect_id = make_user(
            db, org_id, "gapbad", enterprise_role="enterprise_architect",
            role_name="Architect",
        )

    login(client, architect_id)

    malformed = client.post(ENDPOINT, json={"name": "x", "gap_id": "wobble-12"})
    assert malformed.status_code == 400, malformed.status_code
    assert "gap_id" in (malformed.get_json() or {}).get("error", "")

    # A well-formed reference to a capability that does not exist is a 404, and
    # says so, rather than raising.
    missing = client.post(ENDPOINT, json={"name": "y", "gap_id": "business-2147483000"})
    assert missing.status_code == 404, missing.status_code


def test_a_work_package_with_no_gap_is_still_allowed(app, client):
    """Guard the guard: standalone work packages must keep working."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "GapNone")
        architect_id = make_user(
            db, org_id, "gapnone", enterprise_role="enterprise_architect",
            role_name="Architect",
        )

    login(client, architect_id)
    response = client.post(ENDPOINT, json={"name": "Standalone %s" % uuid.uuid4().hex[:6]})
    assert response.status_code == 201, response.data[:200]
