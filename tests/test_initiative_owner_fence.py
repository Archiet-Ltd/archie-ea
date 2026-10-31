"""A plan created from a strategic page is owned only by someone in the caller's organisation.

The plan's owner is named on the page's "Plans created here" card, so an owner id the request
supplies must belong to the caller's organisation; another organisation's person is refused and
nothing is stored.
"""

import uuid


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name="Plan %s %s" % (label, suffix), slug="plan-%s-%s" % (label, suffix))
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, first):
    from app.models.user import User

    user = User(
        first_name=first, last_name="Tester", email="%s-%s@example.test" % (first.lower(), uuid.uuid4().hex[:6]),
        password="test-password-123", confirmed=True, organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_a_plan_owner_from_another_organisation_is_refused(app, db_session, client, login_as):
    from app.models.strategic import StrategicInitiative

    ours, theirs = _org(db_session, "ours"), _org(db_session, "theirs")
    caller = _user(db_session, ours, "Caller")
    colleague = _user(db_session, ours, "Colleague")
    stranger = _user(db_session, theirs, "Stranger")
    name = "Fence plan %s" % uuid.uuid4().hex[:8]

    login_as(client, caller)
    refused = client.post("/strategic/api/initiatives/from-health", json={"name": name, "owner_id": stranger.id})
    assert refused.status_code == 400
    assert refused.get_json()["error"] == "Choose an owner from your organisation."
    assert StrategicInitiative.query.filter_by(name=name).count() == 0

    login_as(client, caller)
    created = client.post("/strategic/api/initiatives/from-health", json={"name": name, "owner_id": colleague.id})
    assert created.status_code == 200, created.get_json()
    assert created.get_json()["initiative"]["owner_name"] == "Colleague Tester"
    plan = StrategicInitiative.query.filter_by(name=name).one()
    assert (plan.organization_id, plan.owner_id, plan.record_kind) == (ours.id, colleague.id, "remediation_plan")
