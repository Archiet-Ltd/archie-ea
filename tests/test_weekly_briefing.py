"""The weekly briefing: what changed, decisions due, risks rising, conformance.

Covers the old address redirecting to it, the figures agreeing with the review
board's own overdue list, every item linking to a record of the same
organisation, and two organisations never seeing each other's items.
"""

import re
import uuid
from datetime import datetime, timedelta

import pytest
from flask import template_rendered

from app.models.organization import Organization
from app.models.user import User

BRIEFING = "/solutions/briefings"


def _org(db_session, label):
    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Briefing {label} {suffix}", slug=f"briefing-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org):
    user = User(
        email=f"briefing-{org.id}-{uuid.uuid4().hex[:6]}@example.com",
        first_name="Chief",
        last_name="Architect",
        organization_id=org.id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    user.password = "TestPass123!"
    db_session.add(user)
    db_session.flush()
    return user


def _review(db_session, org, user, number, priority, status, days_ago):
    from app.models.architecture_review_board import ARBReviewItem

    review = ARBReviewItem(
        review_number=f"{number}-{uuid.uuid4().hex[:5]}",
        title=f"Review {number}",
        review_type="architecture_change",
        priority=priority,
        status=status,
        organization_id=org.id,
        submitter_id=user.id,
        submitted_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    db_session.add(review)
    db_session.flush()
    return review


def _element_change(db_session, org, name, hours_ago=1):
    from app.models.archimate_core import ArchiMateElement
    from app.models.entity_history import EntityHistory

    element = ArchiMateElement(
        name=name, type="ApplicationComponent", layer="application", organization_id=org.id
    )
    db_session.add(element)
    db_session.flush()
    stamp = datetime.utcnow() - timedelta(hours=hours_ago)
    db_session.add(
        EntityHistory(
            organization_id=org.id,
            table_name="archimate_elements",
            record_id=element.id,
            snapshot={"name": name, "type": "ApplicationComponent", "layer": "application"},
            valid_from=stamp,
            recorded_at=stamp,
            source="trigger",
        )
    )
    db_session.flush()
    return element


def _rising_risk(db_session, org, title):
    from app.models.risk import Risk
    from app.models.risk_score_history import RiskScoreHistory

    risk = Risk(title=title, likelihood=2, impact=2, organization_id=org.id)
    db_session.add(risk)
    db_session.flush()
    db_session.add_all(
        [
            RiskScoreHistory(
                risk_id=risk.id, score_kind="residual", likelihood=2, impact=2,
                recorded_at=datetime.utcnow() - timedelta(days=30), organization_id=org.id,
            ),
            RiskScoreHistory(
                risk_id=risk.id, score_kind="residual", likelihood=4, impact=5,
                recorded_at=datetime.utcnow() - timedelta(hours=2), organization_id=org.id,
            ),
        ]
    )
    db_session.flush()
    return risk


def _solution(db_session, org, name):
    from app.models.solution_models import Solution, SolutionArchiMateElement

    solution = Solution(name=name, organization_id=org.id)
    db_session.add(solution)
    db_session.flush()
    db_session.add(
        SolutionArchiMateElement(
            solution_id=solution.id, layer_type="business", element_id=1,
            element_table="business_actors", element_name="Actor",
        )
    )
    db_session.flush()
    return solution


@pytest.fixture
def seeded_org(db_session, tenant_ctx):
    org = _org(db_session, "mine")
    with tenant_ctx(org.id):
        user = _user(db_session, org)
        reviews = [
            _review(db_session, org, user, "A", "critical", "under_review", 20),
            _review(db_session, org, user, "B", "high", "submitted", 30),
            _review(db_session, org, user, "C", "low", "submitted", 2),
        ]
        element = _element_change(db_session, org, "Billing Service")
        risks = [_rising_risk(db_session, org, "Vendor lock-in"), _rising_risk(db_session, org, "Data loss")]
        solution = _solution(db_session, org, "Payments redesign")
    return {"org": org, "user": user, "reviews": reviews, "element": element,
            "risks": risks, "solution": solution}


def _get(client, login_as, user, path):
    login_as(client, user)
    return client.get(path)


def _text(response):
    html = response.get_data(as_text=True)
    start = html.find("<h1")
    body = re.sub(r"<script.*?</script>", "", html[start:], flags=re.DOTALL)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def test_old_address_redirects_to_the_briefing(db_session, client, login_as, seeded_org):
    response = _get(client, login_as, seeded_org["user"], "/solutions/architect-synthesis")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(BRIEFING)


def test_briefing_has_all_four_sections_with_linked_dated_items(
    db_session, client, login_as, seeded_org
):
    response = _get(client, login_as, seeded_org["user"], BRIEFING)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    text = _text(response)

    for heading in ("What changed", "Decisions due", "Risks rising", "Design conformance"):
        assert heading in text
    assert "2 reviews past the time limit" in text
    assert "2 risks rising" in text
    assert f"/architecture/elements/{seeded_org['element'].id}" in html
    assert "Billing Service" in text
    assert f"/arb/reviews/{seeded_org['reviews'][0].id}" in html
    assert f"/risks/{seeded_org['risks'][0].id}" in html
    # The conformance item links the solution record's Review tab directly
    # (brief03b): the old /review-packet path now only redirects there.
    assert f"/solutions/{seeded_org['solution'].id}?tab=review" in html
    today = datetime.utcnow().strftime("%-d %b %Y")
    assert text.count(today) >= 5
    for banned in ("not available", "Not available", "Ask ", "Measured", "—"):
        assert banned not in text


def test_overdue_count_equals_the_review_boards_count(
    db_session, client, login_as, seeded_org
):
    """The headline figure is the length of the list the review board builds."""
    seen = []

    def record(sender, template, context, **extra):
        if template.name == "arb/dashboard.html":
            seen.append(len(context["overdue_items"]))

    template_rendered.connect(record, client.application)
    try:
        _get(client, login_as, seeded_org["user"], "/arb/")
    finally:
        template_rendered.disconnect(record, client.application)
    assert seen, "the review board page did not render"

    text = _text(_get(client, login_as, seeded_org["user"], BRIEFING))
    assert f"{seen[0]} reviews past the time limit" in text
    assert seen[0] == 2


def test_an_empty_section_says_so_and_names_the_page(
    db_session, client, login_as, tenant_ctx
):
    org = _org(db_session, "empty")
    with tenant_ctx(org.id):
        user = _user(db_session, org)
    text = _text(_get(client, login_as, user, BRIEFING))
    assert "Nothing was added or changed in the model this week." in text
    assert "Check the change history." in text
    assert "Check the review board." in text
    assert "Check the risk register." in text
    assert "Check the solutions list." in text


def test_two_organisations_never_see_each_others_items(
    db_session, client, login_as, tenant_ctx, seeded_org
):
    other = _org(db_session, "other")
    with tenant_ctx(other.id):
        other_user = _user(db_session, other)
        _review(db_session, other, other_user, "Z", "critical", "under_review", 40)
        _element_change(db_session, other, "Their Secret System")
        _rising_risk(db_session, other, "Their private risk")
        _solution(db_session, other, "Their solution")

    mine = _get(client, login_as, seeded_org["user"], BRIEFING)
    mine_text = _text(mine)
    for foreign in ("Their Secret System", "Their private risk", "Their solution", "Review Z"):
        assert foreign not in mine_text
    assert "2 reviews past the time limit" in mine_text

    theirs = _text(_get(client, login_as, other_user, BRIEFING))
    for own in ("Billing Service", "Vendor lock-in", "Payments redesign", "Review A"):
        assert own not in theirs
    assert "1 review past the time limit" in theirs


def test_the_old_api_still_answers_with_the_same_figures(
    db_session, client, login_as, seeded_org
):
    response = _get(client, login_as, seeded_org["user"], "/solutions/architect-synthesis/api")
    assert response.status_code == 200
    body = response.get_json()
    # The interface keeps its own single 21-day limit, so only the 30-day-old
    # review counts here; the briefing uses the review board's per-priority limits.
    assert body["arb"]["overdue_open"] == 1
    assert body["arb"]["sla_days"] == 21


def test_an_earlier_week_is_listed_and_opens_with_content(
    db_session, client, login_as, tenant_ctx
):
    org = _org(db_session, "earlier")
    with tenant_ctx(org.id):
        user = _user(db_session, org)
        _element_change(db_session, org, "Old Gateway", hours_ago=24 * 10)
    html = _get(client, login_as, user, BRIEFING).get_data(as_text=True)
    link = re.search(r'href="(/solutions/briefings\?week=[0-9-]+)"', html)
    assert link, "no earlier week listed"
    page = _text(_get(client, login_as, user, link.group(1).replace("&amp;", "&")))
    assert "Old Gateway" in page
