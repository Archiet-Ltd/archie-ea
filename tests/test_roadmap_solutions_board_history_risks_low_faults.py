"""Five small faults on the roadmap, solutions, review board, change history and risk pages.

Each test uses two real organisations, so a page can only pass by reading its own organisation's rows.
"""
import re
import uuid
from datetime import date

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _person(db_session, org, label):
    from app.models.user import Role, User

    user = User(
        email="low-%s-%s@example.com" % (label, uuid.uuid4().hex[:8]),
        first_name="Dana" if label == "mine" else "Ravi",
        last_name="Okoye" if label == "mine" else "Shah",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.role = Role.query.filter_by(name="Architect").first()
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def two_orgs(db_session, make_org):
    mine, theirs = make_org("low-mine"), make_org("low-theirs")
    return (mine, _person(db_session, mine, "mine")), (theirs, _person(db_session, theirs, "theirs"))


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


# 19: one plateau name must not read as one plateau in two states.
def test_two_plateaus_with_one_name_are_told_apart_on_the_roadmap(app, client, login_as, db_session, two_orgs):
    from app.models.implementation_migration import Plateau
    from app.services import work_package_service

    (mine, me), (theirs, other) = two_orgs
    name = "Modelled, Q4 2026"
    earlier = Plateau(name=name, organization_id=mine.id, sequence_order=1, target_date=date(2026, 10, 15))
    later = Plateau(name=name, organization_id=mine.id, sequence_order=2, target_date=date(2026, 12, 15))
    only = Plateau(name=name, organization_id=theirs.id, sequence_order=1, target_date=date(2026, 12, 15))
    db_session.add_all([earlier, later, only])
    db_session.flush()
    for wp_name in ("Retire the old billing run", "Move billing to the platform"):
        work_package_service.create_work_package(
            organization_id=mine.id, name=wp_name, plateau_id=later.id, status="planned", priority="medium",
            progress_percentage=0, start_date=None, end_date=None,
        )
    db_session.commit()

    login_as(client, me)
    html = client.get("/genome/roadmap/").get_data(as_text=True)
    names = [_text(n) for n in re.findall(r'data-testid="roadmap-plateau-name">(.*?)</span>', html, re.S)]
    assert len(names) == 2, names
    assert len(set(names)) == 2, "two plateaus called alike must read differently: %s" % names
    assert "15 Oct 2026" in " ".join(names) and "15 Dec 2026" in " ".join(names), names

    login_as(client, other)
    theirs_html = client.get("/genome/roadmap/").get_data(as_text=True)
    their_names = [_text(n) for n in re.findall(r'data-testid="roadmap-plateau-name">(.*?)</span>', theirs_html, re.S)]
    assert their_names == [name], their_names
    assert "Retire the old billing run" not in theirs_html


# 21(b): an unassigned lead reads as plain English.
def test_solutions_page_says_no_lead_yet_not_led_by_unassigned(app, client, login_as, db_session, two_orgs):
    from app.models.solution_models import Solution

    (mine, me), (theirs, other) = two_orgs
    for org, user, lead in ((mine, me, None), (theirs, other, "Priya Nair")):
        for n in (1, 2):
            db_session.add(Solution(
                name="Lead test %s %d" % (org.slug, n), organization_id=org.id, created_by_id=user.id,
                status="planned", governance_status="draft", technical_lead=lead,
            ))
    db_session.commit()

    login_as(client, me)
    mine_text = _text(client.get("/solutions/").get_data(as_text=True)).lower()
    assert "no solution has a lead yet" in mine_text
    assert "led by unassigned" not in mine_text

    login_as(client, other)
    their_text = _text(client.get("/solutions/").get_data(as_text=True)).lower()
    assert "every solution is led by priya nair" in their_text
    assert "no solution has a lead yet" not in their_text


# 25: the sidebar's Review board opens the board's own page, not the decisions register.
def test_review_board_page_is_not_the_decisions_page(app, client, login_as, two_orgs):
    (mine, me), _ = two_orgs
    login_as(client, me)
    board = client.get("/arb/")
    decisions = client.get("/architecture/decisions/")
    assert board.status_code == 200 and decisions.status_code == 200
    board_html, decisions_html = board.get_data(as_text=True), decisions.get_data(as_text=True)
    title = lambda h: _text(re.search(r"<title>(.*?)</title>", h, re.S).group(1))  # noqa: E731
    heading = lambda h: _text(re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S).group(1))  # noqa: E731
    assert title(board_html) != title(decisions_html)
    assert heading(board_html) == "Architecture Review Board"
    assert heading(decisions_html) != heading(board_html)
    assert 'href="/arb/reviews"' in board_html and 'href="/arb/sessions"' in board_html


# 27(b): mapping a risk to an application shows in the change history, with who and when.
def test_mapping_a_risk_shows_in_the_change_history(app, client, login_as, db_session, two_orgs):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.risk import Risk

    (mine, me), (theirs, other) = two_orgs
    suffix = uuid.uuid4().hex[:6]
    risk = Risk(title="Billing outage %s" % suffix, likelihood=3, impact=4, organization_id=mine.id, owner="Ops")
    application = ApplicationComponent(name="Ledger %s" % suffix, organization_id=mine.id)
    db_session.add_all([risk, application])
    db_session.commit()

    login_as(client, me)
    mapped = client.post("/api/risks/%d/links" % risk.id, json={"entity_type": "application", "entity_id": application.id})
    assert mapped.status_code == 201, mapped.get_data(as_text=True)

    login_as(client, me)
    page = _text(client.get("/intelligence/history/changes").get_data(as_text=True))
    assert "Risk mapping" in page
    assert "Billing outage %s mapped to Ledger %s" % (suffix, suffix) in page
    assert "Dana Okoye" in page

    login_as(client, other)
    theirs_page = _text(client.get("/intelligence/history/changes").get_data(as_text=True))
    assert "Billing outage %s" % suffix not in theirs_page
    assert "Ledger %s" % suffix not in theirs_page


# 27(c): one breadcrumb trail, the sidebar's.
def test_risk_register_shows_one_breadcrumb_trail(app, client, login_as, two_orgs):
    (mine, me), _ = two_orgs
    login_as(client, me)
    html = client.get("/risks/").get_data(as_text=True)
    trails = re.findall(r'<nav[^>]*aria-label="[Bb]readcrumb"[^>]*>.*?</nav>', html, re.S)
    assert len(trails) == 1, [_text(t) for t in trails]
    assert "Risk Register" not in _text(trails[0])
    assert "Governance" not in _text(trails[0])
    assert _text(trails[0]).startswith("Home")
