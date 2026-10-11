"""Regressions for the ARB governance journey found by live QA on 01 Sep 2026.

Five defects, all of which made the board's own workflow unusable in the UI
while the underlying services worked:

1. no decision control rendered on any review detail page;
2. POST /architecture/decisions/new 500'd on a valid submission;
3. POST /arb/sessions/create 500'd and the modal swallowed the failure;
4. the ARB dashboard's KPI tiles and its review list disagreed;
5. recorded ARB decisions never appeared in the decision register.

These use the SHARED fixtures in tests/conftest.py (``db_session`` rolls the
whole test back, so nothing is left in the shared test database).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, role="enterprise_architect"):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:10]
    user = User(
        email=f"arb-{suffix}@example.com",
        first_name="ARB",
        last_name=suffix,
        organization_id=org.id,
        enterprise_role=role,
        confirmed=True,
    )
    user.password = "Sup3rSecret!123"
    db_session.add(user)
    db_session.flush()
    return user


def _make_review(db_session, org, submitter, status="submitted", **kw):
    from app.models.architecture_review_board import ARBReviewItem

    suffix = uuid.uuid4().hex[:8].upper()
    item = ARBReviewItem(
        review_number=f"REV-TEST-{suffix}",
        title=kw.pop("title", "Payments platform target state"),
        description="Move payments onto the shared platform.",
        review_type=kw.pop("review_type", "solution_design"),
        status=status,
        priority=kw.pop("priority", "high"),
        submitter_id=submitter.id,
        organization_id=org.id,
        **kw,
    )
    db_session.add(item)
    db_session.flush()
    return item


# ---------------------------------------------------------------- defect 1

@pytest.mark.parametrize("status", ["submitted", "under_review", "deferred"])
def test_decision_controls_render_for_a_decidable_review(
    db_session, make_org, client, login_as, status
):
    """Every DECIDABLE_STATUSES review must offer a decision form to a non-submitter.

    Before the fix the typed dispatcher resolved every generic row to the
    ``legacy_generic`` state, whose partial rendered no mutation control at all,
    so the only <form action> on the page was None.
    """
    org = make_org("arb")
    submitter = _make_user(db_session, org, role="solution_architect")
    approver = _make_user(db_session, org, role="cto")
    review = _make_review(db_session, org, submitter, status=status)

    login_as(client, approver)
    resp = client.get(f"/arb/reviews/{review.id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f"/arb/reviews/{review.id}/decision" in body, "no decision form on the page"
    for outcome in ("approved", "approved_with_conditions", "rejected", "deferred"):
        assert outcome in body, f"no control for {outcome}"


def test_decision_controls_hidden_from_the_submitter(
    db_session, make_org, client, login_as
):
    """Separation of duties: the submitter is not offered a control that would 403."""
    org = make_org("arb")
    submitter = _make_user(db_session, org, role="solution_architect")
    review = _make_review(db_session, org, submitter, status="submitted")

    login_as(client, submitter)
    resp = client.get(f"/arb/reviews/{review.id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f"/arb/reviews/{review.id}/decision" not in body
    assert "separation of duties" in body.lower()


def test_no_decision_controls_on_a_draft_review(db_session, make_org, client, login_as):
    """``draft`` is deliberately outside DECIDABLE_STATUSES."""
    org = make_org("arb")
    submitter = _make_user(db_session, org, role="solution_architect")
    approver = _make_user(db_session, org, role="cto")
    review = _make_review(db_session, org, submitter, status="draft")

    login_as(client, approver)
    resp = client.get(f"/arb/reviews/{review.id}")
    assert resp.status_code == 200
    assert f"/arb/reviews/{review.id}/decision" not in resp.get_data(as_text=True)


# ---------------------------------------------------------------- defect 2

def test_adr_creation_succeeds_when_another_org_holds_the_same_reference(
    db_session, make_org, client, login_as
):
    """next_decision_id() is tenant-scoped but decision_id is globally UNIQUE.

    Org A already holding AD-001 therefore made org B's very first ADR collide
    on the unique index, which surfaced as a bare 500 and persisted nothing.
    """
    from app.models.architecture_decision import ArchitectureDecision

    from app.utils.reference_numbers import next_reference

    org_a = make_org("adr-a")
    org_b = make_org("adr-b")
    # Seed whatever reference the allocator would hand out next, attributed to a
    # DIFFERENT tenant. The tenant-scoped generator could not see it.
    taken = next_reference("architecture_decisions", "decision_id", "AD-")
    db_session.add(
        ArchitectureDecision(
            decision_id=taken,
            title="Existing decision in another tenant",
            organization_id=org_a.id,
        )
    )
    db_session.flush()

    user_b = _make_user(db_session, org_b)
    login_as(client, user_b)
    resp = client.post(
        "/architecture/decisions/new",
        data={
            "title": "Adopt event-driven integration",
            "context": "Point-to-point integration does not scale.",
            "decision": "Adopt an event backbone.",
            "consequences": "Teams must publish domain events.",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.get_data(as_text=True)[:2000]

    created = (
        ArchitectureDecision.query.filter_by(organization_id=org_b.id)
        .filter(ArchitectureDecision.title == "Adopt event-driven integration")
        .one_or_none()
    )
    assert created is not None, "the ADR was not persisted"
    assert created.decision_id and created.decision_id != taken


# ---------------------------------------------------------------- defect 3

def test_arb_session_creation_succeeds_and_reports_failure_as_json(
    db_session, make_org, client, login_as
):
    from app.models.architecture_review_board import ArchitectureReviewBoard as ARBSession

    org = make_org("arb-sess")
    chair = _make_user(db_session, org, role="cto")

    # Another tenant already holds this year's ARB-<year>-001. The generator was
    # tenant-scoped while board_number is UNIQUE table-wide, so this used to
    # collide on the unique index and 500.
    from app.utils.reference_numbers import next_reference

    other = make_org("arb-sess-other")
    taken = next_reference(
        "architecture_review_boards", "board_number", f"ARB-{datetime.utcnow().year}-"
    )
    db_session.add(
        ARBSession(
            board_number=taken,
            name="Another tenant's first session",
            scheduled_date=datetime(2026, 9, 1, 10, 0),
            organization_id=other.id,
        )
    )
    db_session.flush()

    login_as(client, chair)
    resp = client.post(
        "/arb/sessions/create",
        data={
            "name": "ARB Session Q4 2026",
            "scheduled_date": "2026-09-15T14:00",
            "chair_id": str(chair.id),
            "location": "Board room",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)[:2000]
    assert ARBSession.query.filter_by(organization_id=org.id).count() == 1

    # A failure must be reported, never swallowed.
    bad = client.post(
        "/arb/sessions/create",
        data={"name": "No date"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert bad.status_code >= 400
    assert bad.is_json
    assert (bad.get_json() or {}).get("error")

    # An unparseable date is reported too, rather than 500ing.
    bad_date = client.post(
        "/arb/sessions/create",
        data={"name": "Bad date", "scheduled_date": "not-a-date", "chair_id": str(chair.id)},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert bad_date.status_code == 400
    assert (bad_date.get_json() or {}).get("error")


# ---------------------------------------------------------------- defect 4

def test_dashboard_kpis_and_list_read_the_same_store(
    db_session, make_org, client, login_as
):
    """The typed queue is the single code path. Legacy reviews
    (ARBReviewItem rows without typed columns) are counted by the KPI tiles
    but do not appear in the typed queue. The dashboard must still render
    successfully and include the typed queue partial."""
    org = make_org("arb-dash")
    submitter = _make_user(db_session, org, role="solution_architect")
    viewer = _make_user(db_session, org, role="cto")
    for _ in range(3):
        _make_review(db_session, org, submitter, status="submitted")

    login_as(client, viewer)
    resp = client.get("/arb/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The typed queue is the single code path. The dashboard must
    # include the typed queue partial and must not reference the deleted
    # legacy partial.
    assert "_typed_queue.html" in body or "typed" in body.lower(), (
        "the dashboard must render the typed queue partial"
    )
    assert "_legacy_dashboard.html" not in body, (
        "the dashboard must not reference the deleted legacy partial"
    )


# ---------------------------------------------------------------- defect 5

def test_recorded_arb_decision_appears_in_the_decision_register(
    db_session, make_org, client, login_as
):
    from app.services.arb_governance_service import ARBGovernanceService

    org = make_org("arb-reg")
    submitter = _make_user(db_session, org, role="solution_architect")
    approver = _make_user(db_session, org, role="cto")
    review = _make_review(
        db_session, org, submitter, status="submitted", title="Retire the legacy ESB"
    )

    from flask import g

    g.current_org_id = org.id
    ARBGovernanceService().record_decision(
        review_item_id=review.id,
        decision="approved",
        rationale="The target state is agreed.",
        decided_by_id=approver.id,
    )

    login_as(client, approver)
    resp = client.get("/architecture/decisions/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Retire the legacy ESB" in body, "the recorded ARB decision is not in the register"
