"""Investment figures reach only the people allowed to see spend, and the total is the sum of the estimates.

Two organisations each have capabilities and work packages with estimates. A person without spend
authority opens the matrix pages and the JSON that feeds them and finds no pound sign and none of the
estimates; a person with it finds the total equal to the estimates of their own organisation, the same
figure the work package list gives, and nothing from the other organisation.
"""
import re
import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

# Estimates chosen so no figure or sum is a substring of another, and the shared package splits evenly.
A_ONE, A_TWO, A_SHARED = 12345.0, 6789.0, 1750.0
A_TOTAL = A_ONE + A_TWO + A_SHARED
B_ONE = 777777.0

PAGES = ("/strategic/investment-matrix", "/architecture/investment-priorities")
FEEDS = ("/strategic/api/investment-analysis",)


def _person(db_session, org, persona, admin=False):
    from app.models.user import User

    user = User(email="p-%s@example.com" % uuid.uuid4().hex[:8], first_name="A", last_name="B",
                organization_id=org.id, enterprise_role=persona, confirmed=True)
    user.password = "Test-password-1!"
    db_session.add(user)
    db_session.flush()
    if admin:
        user.grant_org_admin()
        db_session.flush()
    return user


def _capability(db_session, org, name):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(name="%s %s" % (name, uuid.uuid4().hex[:6]), organization_id=org.id,
                            strategic_importance="high", level=1)
    db_session.add(cap)
    db_session.flush()
    return cap


def _package(db_session, org, caps, cost):
    from app.models.unified_work_package import UnifiedWorkPackage

    wp = UnifiedWorkPackage(name="Package %s" % uuid.uuid4().hex[:6], organization_id=org.id,
                            capability_id=caps[0].id, capability_ids=[c.id for c in caps],
                            estimated_cost=cost, start_date=datetime(2027, 1, 4))
    db_session.add(wp)
    db_session.flush()
    return wp


def _support(db_session, org, cap):
    """An application of the organisation that supports the capability, so the priorities page lists it."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    app_row = ApplicationComponent(name="App %s" % uuid.uuid4().hex[:6], organization_id=org.id)
    db_session.add(app_row)
    db_session.flush()
    db_session.add(UnifiedApplicationCapabilityMapping(application_component_id=app_row.id,
                                                       unified_capability_id=cap.id))
    db_session.flush()


@pytest.fixture
def estate(db_session, make_org):
    """Organisation A with three estimated packages, organisation B with one, and a person of each kind."""
    org_a, org_b = make_org("a"), make_org("b")
    a1, a2 = _capability(db_session, org_a, "Handle claims"), _capability(db_session, org_a, "Settle claims")
    b1 = _capability(db_session, org_b, "Handle policies")
    for org, cap in ((org_a, a1), (org_b, b1)):
        _support(db_session, org, cap)
    _package(db_session, org_a, [a1], A_ONE)
    _package(db_session, org_a, [a2], A_TWO)
    _package(db_session, org_a, [a1, a2], A_SHARED)
    _package(db_session, org_b, [b1], B_ONE)
    return {
        "org_a": org_a, "org_b": org_b, "names": (a1.name, a2.name),
        "spender": _person(db_session, org_a, "cto"),
        "restricted": _person(db_session, org_a, "enterprise_architect"),
        "other_spender": _person(db_session, org_b, "cto"),
    }


def _get(client, login_as, user, path):
    login_as(client, user)
    return client.get(path, headers={"Accept": "application/json"} if path.startswith("/strategic/api") else {})


def _estimates_in(text):
    """Every way the estimates could be written: whole, with thousands marks, or in thousands."""
    out = []
    for value in (A_ONE, A_TWO, A_SHARED, A_TOTAL, B_ONE):
        out += ["%d" % value, "{:,}".format(int(value)), "%.1fk" % (value / 1000)]
    return out


@pytest.mark.parametrize("path", PAGES + FEEDS)
def test_a_person_without_spend_authority_gets_no_pound_figure(client, login_as, estate, path):
    response = _get(client, login_as, estate["restricted"], path)
    assert response.status_code == 200, (path, response.status_code)
    text = response.get_data(as_text=True)
    assert "£" not in text, (path, [m.group(0) for m in re.finditer(r".{30}£.{30}", text)][:3])
    for figure in _estimates_in(text):
        assert figure not in text, (path, figure)
    # the capabilities themselves are still listed: only the money is held back
    assert estate["names"][0] in text


@pytest.mark.parametrize("path", PAGES)
def test_a_restricted_page_says_the_figure_is_restricted_rather_than_zero(client, login_as, estate, path):
    text = _get(client, login_as, estate["restricted"], path).get_data(as_text=True)
    card = re.search(r'data-testid="total-investment".*?</div>\s*</div>\s*</div>', text, re.S).group(0)
    assert "Restricted" in card and "budget authority" in card, card
    assert "Not estimated" not in card and "£0" not in card


def test_the_feed_for_a_restricted_person_marks_the_figures_as_hidden(client, login_as, estate):
    body = _get(client, login_as, estate["restricted"], FEEDS[0]).get_json()
    assert body["costs_hidden"] is True
    assert body["portfolio_metrics"]["total_estimated_investment"] is None
    assert all(c["investment_need"]["estimated_cost"] is None and c["investment_need"]["cost_hidden"]
               for c in body["capability_scores"])


@pytest.mark.parametrize("path", PAGES)
def test_a_person_with_spend_authority_sees_the_total_of_the_estimates(client, login_as, estate, path):
    text = _get(client, login_as, estate["spender"], path).get_data(as_text=True)
    assert "£%.1fk" % (A_TOTAL / 1000) in text, path
    # nothing of the other organisation's estimate
    for figure in ("777777", "777,777", "777.8k"):
        assert figure not in text, (path, figure)


def test_the_feed_total_is_the_sum_of_the_rows_and_of_the_estimates(client, login_as, estate):
    body = _get(client, login_as, estate["spender"], FEEDS[0]).get_json()
    rows = [c["investment_need"]["estimated_cost"] for c in body["capability_scores"]
            if c["investment_need"]["estimated_cost"] is not None]
    total = body["portfolio_metrics"]["total_estimated_investment"]
    assert sum(rows) == pytest.approx(total)
    assert total == pytest.approx(A_TOTAL)
    assert body["portfolio_metrics"]["investment_costed_count"] == len(rows) == 2


def test_the_matrix_total_equals_the_roadmap_timeline_for_the_same_work(client, login_as, estate):
    """The roadmap timeline draws each package with its estimate; its figures add up to the matrix total."""
    login_as(client, estate["spender"])
    listed = client.get("/enterprise/api/work-packages/gantt").get_json()
    on_list = sum(float(w["estimated_cost"] or 0) for w in listed["work_packages"])
    total = _get(client, login_as, estate["spender"], FEEDS[0]).get_json()["portfolio_metrics"]["total_estimated_investment"]
    assert on_list == pytest.approx(A_TOTAL)
    assert total == pytest.approx(on_list)


def test_one_organisations_estimates_are_never_counted_in_anothers_total(client, login_as, estate):
    mine = _get(client, login_as, estate["spender"], FEEDS[0]).get_json()
    theirs = _get(client, login_as, estate["other_spender"], FEEDS[0]).get_json()
    assert mine["portfolio_metrics"]["total_estimated_investment"] == pytest.approx(A_TOTAL)
    assert theirs["portfolio_metrics"]["total_estimated_investment"] == pytest.approx(B_ONE)
    assert estate["names"][0] not in [c["capability_name"] for c in theirs["capability_scores"]]
    assert "777777" not in str(mine)
    assert "12345" not in str(theirs)


def test_an_organisation_administrator_with_an_architect_persona_sees_the_figures(db_session, client, login_as, estate):
    admin = _person(db_session, estate["org_a"], "enterprise_architect", admin=True)
    text = _get(client, login_as, admin, PAGES[0]).get_data(as_text=True)
    assert "£%.1fk" % (A_TOTAL / 1000) in text
