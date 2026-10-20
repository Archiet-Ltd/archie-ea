"""R1-B74 slice 1: the submission review panel on the ARB review detail page.

A board member sees the elements the submission touches, what fails if they
fail together, and the risks the change reaches, each read from the one
answer service's stored answers with their citations and permanent addresses.
A review that names no elements shows the panel with a plain sentence and no
answer; elements with nothing downstream show the envelope's own empty answer.

Two real organisations are used in the isolation test.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from app.modules.architecture.services.review_submission_answers import (
    submission_review_answers,
)


def _user(db_session, org_id, role="enterprise_architect"):
    from app.models.user import User

    user = User(
        email=f"r1b74-{uuid.uuid4().hex[:10]}@example.com",
        first_name="R1B74",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _link(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _review(db_session, org_id, user, impacted_element_ids):
    from app.models.architecture_review_board import (
        ARBReviewItem,
        ArchitectureReviewBoard,
        ReviewType,
    )

    board = ArchitectureReviewBoard(
        organization_id=org_id,
        board_number=f"ARB-{_dt.datetime.utcnow().year}-{uuid.uuid4().hex[:6]}",
        name="Review session",
        status="scheduled",
        scheduled_date=_dt.datetime.utcnow(),
        impacted_element_ids=list(impacted_element_ids),
    )
    db_session.add(board)
    db_session.flush()
    review = ARBReviewItem(
        organization_id=org_id,
        review_number=f"REV-{_dt.datetime.utcnow().year}-{uuid.uuid4().hex[:6]}",
        title="Review item",
        review_type=ReviewType.ARCHITECTURE_CHANGE,
        status="draft",
        submitter_id=user.id,
        arb_session_id=board.id,
    )
    db_session.add(review)
    db_session.flush()
    return review, board


def _answer_count(db_session):
    from app.extensions import db
    from app.models.answer_record import AnswerRecord

    return db.session.execute(db.select(db.func.count()).select_from(AnswerRecord)).scalar_one()


# --- the panel shows both stored answers --------------------------------------


def test_review_shows_stored_submission_answers(app, db_session, make_org, client, login_as):
    org = make_org("org-a")
    user = _user(db_session, org.id)
    e1 = _element(db_session, org.id, "Order service")
    e2 = _element(db_session, org.id, "Payment service")
    e3 = _element(db_session, org.id, "Catalogue service")
    e4 = _element(db_session, org.id, "Customer database")
    e0 = _element(db_session, org.id, "Network", type_="TechnologyNode", layer="technology")
    _link(db_session, org.id, e1, e4)
    _link(db_session, org.id, e2, e4)
    _link(db_session, org.id, e3, e4)
    _link(db_session, org.id, e0, e1)
    from app.models.risk import Risk

    db_session.add(
        Risk(
            organization_id=org.id,
            archimate_element_id=e0.id,
            title="Network outage",
            likelihood=3,
            impact=4,
        )
    )
    db_session.commit()
    review, _board = _review(db_session, org.id, user, [e1.id, e2.id, e3.id])
    db_session.commit()
    login_as(client, user)

    resp = client.get(f"/arb/reviews/{review.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Order service" in html and "Payment service" in html and "Catalogue service" in html
    assert "What breaks if these fail together" in html
    assert "Customer database" in html
    assert "Risks reaching Order service" in html
    assert "Network outage" in html
    assert "/intelligence/answers/" in html


# --- the second load re-reads instead of asking again -------------------------


def test_second_page_load_re_reads_the_stored_answer(app, db_session, make_org, client, login_as):
    org = make_org("org-a")
    user = _user(db_session, org.id)
    e1 = _element(db_session, org.id, "Order service")
    e2 = _element(db_session, org.id, "Payment service")
    _link(db_session, org.id, e1, e2)
    db_session.commit()
    review, _board = _review(db_session, org.id, user, [e1.id, e2.id])
    db_session.commit()
    login_as(client, user)

    client.get(f"/arb/reviews/{review.id}")
    after_first = _answer_count(db_session)
    assert after_first == 2, f"expected two stored answers after the first load, got {after_first}"

    client.get(f"/arb/reviews/{review.id}")
    assert _answer_count(db_session) == after_first


# --- isolation: another organisation sees nothing -----------------------------


def test_org_b_cannot_see_org_a_review_or_its_answers(app, db_session, make_org, client, login_as):
    org_a = make_org("org-a")
    org_b = make_org("org-b")
    user_a = _user(db_session, org_a.id)
    user_b = _user(db_session, org_b.id)
    e1 = _element(db_session, org_a.id, "Order service")
    review, _board = _review(db_session, org_a.id, user_a, [e1.id])
    db_session.commit()
    review_id = review.id
    user_b_id = user_b.id

    # A real request uses a fresh session; here the shared fixture session
    # still holds org A's review in its identity map, so expunge first so the
    # tenant filter is really exercised (see tests/conftest.py db_session note).
    db_session.expunge_all()
    login_as(client, user_b_id)
    resp = client.get(f"/arb/reviews/{review_id}")
    assert resp.status_code == 404


# --- empty cases --------------------------------------------------------------


def test_review_with_no_elements_shows_plain_absence(app, db_session, make_org, client, login_as):
    org = make_org("org-a")
    user = _user(db_session, org.id)
    review, _board = _review(db_session, org.id, user, [])
    db_session.commit()
    login_as(client, user)

    resp = client.get(f"/arb/reviews/{review.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "This review names no elements, so there is nothing to show here." in html
    assert "/intelligence/answers/" not in html


def test_elements_with_nothing_downstream_show_the_envelope_empty_answer(
    app, db_session, make_org, client, login_as
):
    org = make_org("org-a")
    user = _user(db_session, org.id)
    e1 = _element(db_session, org.id, "Lonely service")
    e2 = _element(db_session, org.id, "Orphan service")
    db_session.commit()
    review, _board = _review(db_session, org.id, user, [e1.id, e2.id])
    db_session.commit()
    login_as(client, user)

    resp = client.get(f"/arb/reviews/{review.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Nothing is recorded as depending on these yet" in html
    assert "No open risk reaches this" in html
    assert "/intelligence/answers/" in html


# --- the service function itself ---------------------------------------------


def test_service_returns_none_when_review_names_no_elements(app, db_session, make_org):
    org = make_org("org-a")
    user = _user(db_session, org.id)
    review, _board = _review(db_session, org.id, user, [])
    db_session.commit()

    with app.app_context():
        from flask import g

        g.current_org_id = org.id
        answers = submission_review_answers(review)

    assert answers == {"set_impact": None, "risk_propagation": None}


def test_service_returns_stored_answers_with_addresses(app, db_session, make_org):
    org = make_org("org-a")
    user = _user(db_session, org.id)
    e1 = _element(db_session, org.id, "Order service")
    e2 = _element(db_session, org.id, "Payment service")
    _link(db_session, org.id, e1, e2)
    db_session.commit()
    review, _board = _review(db_session, org.id, user, [e1.id, e2.id])
    db_session.commit()

    with app.app_context():
        from flask import g

        g.current_org_id = org.id
        answers = submission_review_answers(review)

    assert answers["set_impact"] is not None
    assert answers["risk_propagation"] is not None
    assert len(answers["set_impact"]["address"]) == 32
    assert answers["set_impact"]["page_url"].startswith("/intelligence/answers/")
    assert answers["risk_propagation"]["page_url"].startswith("/intelligence/answers/")
    assert answers["set_impact"]["question_kind"] == "set_impact"
    assert answers["risk_propagation"]["question_kind"] == "risk_propagation"


# --- fix round: what the risk card covers, unresolved elements, same-day rows --


def test_other_organisations_element_ids_show_the_not_found_sentence(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("org-a")
    org_b = make_org("org-b")
    user = _user(db_session, org_a.id)
    foreign = _element(db_session, org_b.id, "Foreign service")
    db_session.commit()
    review, _board = _review(db_session, org_a.id, user, [foreign.id])
    db_session.commit()
    login_as(client, user)

    resp = client.get(f"/arb/reviews/{review.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "The elements this review names were not found in your model" in html
    assert "This review names no elements" not in html
    assert "Foreign service" not in html


def test_risk_card_names_the_element_it_covers(app, db_session, make_org, client, login_as):
    org = make_org("org-a")
    user = _user(db_session, org.id)
    e1 = _element(db_session, org.id, "Order service")
    e2 = _element(db_session, org.id, "Payment service")
    _link(db_session, org.id, e1, e2)
    db_session.commit()
    review, _board = _review(db_session, org.id, user, [e1.id, e2.id])
    db_session.commit()
    login_as(client, user)

    html = client.get(f"/arb/reviews/{review.id}").get_data(as_text=True)

    assert "Risks reaching Order service" in html
    assert "Risks that reach this change" not in html
    assert "The risks of the other elements are not included yet" in html


def test_first_element_not_found_is_said_in_the_risk_card(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("org-a")
    org_b = make_org("org-b")
    user = _user(db_session, org_a.id)
    foreign = _element(db_session, org_b.id, "Foreign service")
    own = _element(db_session, org_a.id, "Order service")
    db_session.commit()
    review, _board = _review(db_session, org_a.id, user, [foreign.id, own.id])
    db_session.commit()
    login_as(client, user)

    html = client.get(f"/arb/reviews/{review.id}").get_data(as_text=True)

    assert "What breaks if these fail together" in html
    assert "The first element this review names was not found in your model" in html
    assert "Foreign service" not in html


def test_two_same_day_stored_answers_do_not_blank_the_panel(
    app, db_session, make_org, client, login_as
):
    from app.models.answer_record import AnswerRecord

    org = make_org("org-a")
    user = _user(db_session, org.id)
    e1 = _element(db_session, org.id, "Order service")
    e2 = _element(db_session, org.id, "Payment service")
    _link(db_session, org.id, e1, e2)
    db_session.commit()
    review, _board = _review(db_session, org.id, user, [e1.id, e2.id])
    db_session.commit()
    login_as(client, user)

    client.get(f"/arb/reviews/{review.id}")
    for record in db_session.query(AnswerRecord).all():
        db_session.add(
            AnswerRecord(
                organization_id=record.organization_id,
                address=uuid.uuid4().hex,
                question_kind=record.question_kind,
                params=record.params,
                truth_class=record.truth_class,
                confidence=record.confidence,
                confidence_basis=record.confidence_basis,
                rows=record.rows,
                summary=record.summary,
                reasons=record.reasons,
                elements=record.elements,
                citations=record.citations,
                freshness=record.freshness,
                truncated=record.truncated,
                computed_at=record.computed_at,
            )
        )
    db_session.commit()
    assert _answer_count(db_session) == 4

    resp = client.get(f"/arb/reviews/{review.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "What breaks if these fail together" in html
    assert "Risks reaching Order service" in html
