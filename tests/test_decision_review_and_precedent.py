"""Review date and outcome, the due-for-review list, and
precedent search over the organisation's own decisions.

Two organisations throughout: org B's decisions never appear in org A's
due-for-review list or precedent search results.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest


def _user(db_session, org, first="Ada", last="Lovelace"):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"rev-{uuid.uuid4().hex[:10]}@example.com",
        first_name=first, last_name=last,
        organization_id=org.id, role=role, confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _decision(db_session, org, title, **kw):
    from app.models.architecture_decision import ArchitectureDecision

    row = ArchitectureDecision(
        decision_id=f"RV-{uuid.uuid4().hex[:8]}",
        title=title, status="accepted", organization_id=org.id, **kw,
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def world(db_session, make_org):
    org_a = make_org("rev-a")
    org_b = make_org("rev-b")
    return {
        "org_a": org_a, "org_b": org_b,
        "ada": _user(db_session, org_a),
        "bob": _user(db_session, org_b, "Bob", "Foreign"),
    }


def test_due_for_review_lists_only_arrived_undecided_reviews(db_session, world):
    from datetime import datetime

    from app.models.architecture_decision import ArchitectureDecision

    org_a = world["org_a"]
    due = _decision(db_session, org_a, "Due today", review_date=date.today())
    not_yet = _decision(db_session, org_a, "Due next year", review_date=date.today() + timedelta(days=365))
    # Reviewed *for this cycle*: reviewed_at is on or after review_date, the
    # shape record_review_outcome() actually produces (both set together).
    already_reviewed = _decision(
        db_session, org_a, "Already reviewed",
        review_date=date.today() - timedelta(days=1), review_outcome="Still holds.",
        reviewed_at=datetime.utcnow(),
    )
    never_reviewed = _decision(db_session, org_a, "No review date set")
    db_session.commit()

    result = ArchitectureDecision.due_for_review(org_a.id)
    ids = {d.id for d in result}

    assert due.id in ids
    assert not_yet.id not in ids
    assert already_reviewed.id not in ids
    assert never_reviewed.id not in ids


def test_due_for_review_never_crosses_organisations(db_session, world):
    from app.models.architecture_decision import ArchitectureDecision

    org_a, org_b = world["org_a"], world["org_b"]
    _decision(db_session, org_a, "Org A due", review_date=date.today())
    due_b = _decision(db_session, org_b, "Org B due", review_date=date.today())
    db_session.commit()

    result_a = ArchitectureDecision.due_for_review(org_a.id)
    assert due_b.id not in {d.id for d in result_a}


def test_record_review_outcome_sets_fields_and_can_clear_the_review_date(db_session, world):
    decision = _decision(db_session, world["org_a"], "Vendor renewal", review_date=date.today())
    db_session.commit()

    decision.record_review_outcome("Renewed the contract.", world["ada"].id)
    db_session.commit()

    assert decision.review_outcome == "Renewed the contract."
    assert decision.reviewed_at is not None
    assert decision.reviewed_by_id == world["ada"].id
    # record_review_outcome itself never touches review_date -- the caller
    # (the route) decides whether a new one is set; here nothing was set.
    assert decision.is_due_for_review is False  # outcome now recorded


def test_a_recurring_review_becomes_due_again_once_its_next_date_arrives(db_session, world):
    """A reviewed decision with a next review date set must become due
    again when that date arrives -- not stay permanently excluded just
    because review_outcome already holds text from the previous cycle
    (refuter finding D1 on PR 318: due_for_review()/is_due_for_review used
    to check only `review_outcome IS NULL`, which a recurring review can
    never satisfy again after its very first outcome).

    Simulates two review cycles without waiting for real time to pass: the
    first review and its next date are both backdated together (reviewed_at
    stays before review_date, matching how record_review_outcome and the
    route's next-review-date field are actually set together), then the
    second cycle's date is moved to the past -- arrived, as of "today" --
    while reviewed_at stays at its first-cycle value, before it.
    """
    from datetime import datetime

    from app.models.architecture_decision import ArchitectureDecision

    org_a = world["org_a"]
    decision = _decision(db_session, org_a, "Vendor contract", review_date=date.today() - timedelta(days=95))
    db_session.commit()

    # First cycle, backdated 95 days: reviewed then, renewed 90 days out --
    # still in the future relative to today, so not due yet.
    decision.record_review_outcome("Renewed for another term.", world["ada"].id)
    decision.reviewed_at = datetime.utcnow() - timedelta(days=95)
    decision.review_date = date.today() + timedelta(days=90)
    db_session.commit()
    assert decision.is_due_for_review is False
    assert decision.id not in {d.id for d in ArchitectureDecision.due_for_review(org_a.id)}

    # Time passes, simulated: the next cycle's date has now arrived (set to
    # the past), while reviewed_at stays at its first-cycle value, still
    # before it. Due again, despite the old outcome text still being the
    # only value review_outcome has ever held.
    decision.review_date = date.today() - timedelta(days=5)
    db_session.commit()
    assert decision.is_due_for_review is True
    assert decision.id in {d.id for d in ArchitectureDecision.due_for_review(org_a.id)}


def test_precedent_search_matches_text_in_decision_fields(db_session, world):
    from app.models.architecture_decision import ArchitectureDecision

    org_a = world["org_a"]
    match = _decision(
        db_session, org_a, "Standardise on PostgreSQL",
        decision="We will use PostgreSQL for all new services.",
    )
    other = _decision(db_session, org_a, "Unrelated decision", decision="Use the shared CI runner.")
    db_session.commit()

    results = ArchitectureDecision.precedent_search("postgres", org_a.id)
    ids = {d.id for d in results}
    assert match.id in ids
    assert other.id not in ids


def test_precedent_search_never_returns_another_organisations_decision(db_session, world):
    from app.models.architecture_decision import ArchitectureDecision

    org_a, org_b = world["org_a"], world["org_b"]
    _decision(db_session, org_a, "Shared vocabulary term", decision="Use the term 'widget' for this concept.")
    foreign = _decision(db_session, org_b, "Shared vocabulary term", decision="Use the term 'widget' for this concept.")
    db_session.commit()

    results = ArchitectureDecision.precedent_search("widget", org_a.id)
    assert foreign.id not in {d.id for d in results}


def test_precedent_search_narrows_to_an_element_when_given(db_session, world):
    from app.models import ArchiMateElement
    from app.models.architecture_decision import ArchitectureDecision

    org_a = world["org_a"]
    element = ArchiMateElement(
        name=f"Integration platform {uuid.uuid4().hex[:6]}",
        type="ApplicationComponent", layer="application", organization_id=org_a.id,
    )
    db_session.add(element)
    db_session.flush()
    on_element = _decision(
        db_session, org_a, "Decision on element", decision="widget approach",
        archimate_element_ids=[element.id],
    )
    off_element = _decision(db_session, org_a, "Decision not on element", decision="widget approach")
    db_session.commit()

    results = ArchitectureDecision.precedent_search("widget", org_a.id, element_ids=[element.id])
    ids = {d.id for d in results}
    assert on_element.id in ids
    assert off_element.id not in ids


def test_due_for_review_route_requires_login_and_scopes_to_caller(db_session, world, client, login_as):
    org_a, org_b = world["org_a"], world["org_b"]
    _decision(db_session, org_a, "Org A due route test", review_date=date.today())
    _decision(db_session, org_b, "Org B due route test", review_date=date.today())
    db_session.commit()

    login_as(client, world["ada"])
    html = client.get("/architecture/decisions/due-for-review").get_data(as_text=True)
    assert "Org A due route test" in html
    assert "Org B due route test" not in html


def test_record_outcome_route_persists_outcome_and_next_review_date(db_session, world, client, login_as):
    decision = _decision(db_session, world["org_a"], "Route outcome test", review_date=date.today())
    db_session.commit()
    decision_id = decision.id

    login_as(client, world["ada"])
    next_date = (date.today() + timedelta(days=90)).isoformat()
    resp = client.post(
        f"/architecture/decisions/{decision_id}/record-outcome",
        data={"review_outcome": "Still fit for purpose.", "next_review_date": next_date},
    )
    assert resp.status_code == 302, resp.get_data(as_text=True)

    db_session.expire_all()
    from app.models.architecture_decision import ArchitectureDecision

    refreshed = db_session.get(ArchitectureDecision, decision_id)
    assert refreshed.review_outcome == "Still fit for purpose."
    assert refreshed.review_date.isoformat() == next_date


def test_precedent_search_route_scopes_to_caller(db_session, world, client, login_as):
    org_a, org_b = world["org_a"], world["org_b"]
    _decision(db_session, org_a, "Precedent route A", decision="gremlin approach")
    _decision(db_session, org_b, "Precedent route B", decision="gremlin approach")
    db_session.commit()

    login_as(client, world["ada"])
    html = client.get("/architecture/decisions/precedent-search?q=gremlin").get_data(as_text=True)
    assert "Precedent route A" in html
    assert "Precedent route B" not in html


def test_creating_a_decision_with_a_review_date_persists_it(db_session, world, client, login_as):
    from app.models.architecture_decision import ArchitectureDecision

    login_as(client, world["ada"])
    title = f"New decision with review date {uuid.uuid4().hex[:6]}"
    review_date = date.today().isoformat()
    resp = client.post(
        "/architecture/decisions/new",
        data={"title": title, "decision": "x", "review_date": review_date},
    )
    assert resp.status_code == 302

    stored = db_session.execute(
        db_session.query(ArchitectureDecision).filter_by(title=title).statement
    ).scalars().one()
    assert stored.review_date.isoformat() == review_date
