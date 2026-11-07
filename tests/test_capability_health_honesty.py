"""A capability health score must come from a measurement, or not exist.

The 30 Aug 2026 QA audit, Medium #5:

    "Every domain ... shows an identical 60% score, aligning with 0/191
     capabilities having any recorded maturity assessment, suggesting the health
     formula falls back to a fixed default when inputs are missing instead of
     surfacing an honest 'not assessed' state... The dashboard's headline claim
     of a 'real-time assessment... based on maturity, coverage, and technical
     risk' is misleading -- a user could conclude the portfolio has meaningfully
     differentiated health when no real assessment has occurred."

The reading was right and the diagnosis was slightly off, which makes it worse
rather than better. 60 is not a hardcoded default: the formula starts at 100,
subtracts nothing for an unknown maturity gap, subtracts 40 for having no
applications, and lands on exactly 60 every time. A number with no measurement
behind it, arrived at honestly by arithmetic, and rendered identically to a
measured one.

CLAUDE.md: "A 0 that means 'not computed' is indistinguishable from a measured
zero; use None -> em dash." These tests hold that line for 60.
"""

import uuid

import pytest


def _capability(db, org_id, name, **kwargs):
    from app.models.business_capabilities import BusinessCapability

    capability = BusinessCapability(name=name, organization_id=org_id, **kwargs)
    db.session.add(capability)
    return capability


@pytest.fixture
def metrics_for(app):
    """Run the health service inside one org's tenant context."""
    from flask import g

    from app import db
    from app.models.organization import Organization
    from app.modules.capabilities.services.capability_health_service import (
        CapabilityHealthService,
    )

    created = {}

    def _run(build):
        suffix = uuid.uuid4().hex[:8]
        with app.app_context():
            org = Organization(name="Health %s" % suffix, slug="health-%s" % suffix)
            db.session.add(org)
            db.session.flush()
            g.current_org_id = org.id
            created["org"] = org.id
            build(db, org.id)
            db.session.commit()
            # The service caches per tenant for 60s; a fresh org key avoids it.
            return CapabilityHealthService().get_capability_health_metrics()

    return _run


def test_a_capability_with_nothing_recorded_has_no_score(app, metrics_for):
    """Not 60. Nothing was measured, so there is no number to report."""
    name = "Unmeasured %s" % uuid.uuid4().hex[:8]

    metrics = metrics_for(lambda db, org_id: _capability(db, org_id, name))

    item = next(h for h in metrics["health_by_capability"] if h["name"] == name)
    assert item["score"] is None, (
        "an unassessed capability reported %r -- a score with no measurement "
        "behind it is indistinguishable from a real one" % (item["score"],)
    )
    assert item["status"] == "Not assessed"


def test_a_capability_with_a_maturity_assessment_is_scored(app, metrics_for):
    """Guard the guard: withholding every score is not honesty, it is silence."""
    name = "Measured %s" % uuid.uuid4().hex[:8]

    metrics = metrics_for(
        lambda db, org_id: _capability(
            db, org_id, name, current_maturity_level=2, target_maturity_level=4
        )
    )

    item = next(h for h in metrics["health_by_capability"] if h["name"] == name)
    assert item["score"] is not None
    # gap of 2 (-30) and no applications (-40) from a base of 100.
    assert item["score"] == 30
    assert item["status"] == "Critical"


def test_the_average_is_taken_over_what_was_measured(app, metrics_for):
    """Averaging unmeasured capabilities is what produced the uniform 60%."""
    measured = "Measured %s" % uuid.uuid4().hex[:8]
    blank_one = "Blank one %s" % uuid.uuid4().hex[:8]
    blank_two = "Blank two %s" % uuid.uuid4().hex[:8]

    def build(db, org_id):
        _capability(db, org_id, measured, current_maturity_level=2,
                    target_maturity_level=4)
        _capability(db, org_id, blank_one)
        _capability(db, org_id, blank_two)

    metrics = metrics_for(build)

    assert metrics["total_capabilities"] == 3
    assert metrics["assessed_capabilities"] == 1
    assert metrics["unassessed_capabilities"] == 2
    # The one measured score, undiluted by two capabilities nobody assessed.
    assert metrics["average_health"] == 30


def test_the_average_is_none_when_nothing_has_been_assessed(app, metrics_for):
    """"No capability has been assessed" and "everything scored 0" differ."""
    def build(db, org_id):
        _capability(db, org_id, "Blank A %s" % uuid.uuid4().hex[:6])
        _capability(db, org_id, "Blank B %s" % uuid.uuid4().hex[:6])

    metrics = metrics_for(build)

    assert metrics["assessed_capabilities"] == 0
    assert metrics["average_health"] is None


def test_an_unassessed_capability_is_not_counted_as_at_risk(app, metrics_for):
    """Unknown is a different queue from bad, and must not inflate the risk list."""
    name = "Blank %s" % uuid.uuid4().hex[:8]

    metrics = metrics_for(lambda db, org_id: _capability(db, org_id, name))

    assert all(entry["name"] != name for entry in metrics["at_risk_list"])
    assert metrics["at_risk_capabilities"] == 0


def test_capabilities_are_grouped_by_the_domain_they_record(app, metrics_for):
    """Not one group per capability: the index groups by the domain the roadmap groups by."""
    suffix = uuid.uuid4().hex[:8]
    field, finance = "Field Operations %s" % suffix, "Finance %s" % suffix

    def build(db, org_id):
        for number, domain in enumerate((field, field, field, finance)):
            _capability(db, org_id, "Cap %s %s" % (number, suffix), business_domain=domain,
                        current_maturity_level=2, target_maturity_level=3)
        _capability(db, org_id, "Standalone %s" % suffix, current_maturity_level=2, target_maturity_level=3)

    metrics = metrics_for(build)

    counts = {d["name"]: d["count"] for d in metrics["health_by_domain"]}
    assert counts[field] == 3 and counts[finance] == 1
    assert counts["Standalone %s" % suffix] == 1
    assert len(counts) == 3
