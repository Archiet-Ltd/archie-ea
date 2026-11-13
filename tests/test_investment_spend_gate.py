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


def _build_estate(db_session, make_org, a_people, b_people):
    """Organisation A with three estimated packages, organisation B with one, and the people asked for in each."""
    org_a, org_b = make_org("a"), make_org("b")
    a1, a2 = _capability(db_session, org_a, "Handle claims"), _capability(db_session, org_a, "Settle claims")
    b1 = _capability(db_session, org_b, "Handle policies")
    for org, cap in ((org_a, a1), (org_b, b1)):
        _support(db_session, org, cap)
    _package(db_session, org_a, [a1], A_ONE)
    _package(db_session, org_a, [a2], A_TWO)
    _package(db_session, org_a, [a1, a2], A_SHARED)
    _package(db_session, org_b, [b1], B_ONE)
    people = {key: _person(db_session, org_a, persona) for key, persona in a_people.items()}
    people.update({key: _person(db_session, org_b, persona) for key, persona in b_people.items()})
    return dict(people, org_a=org_a, org_b=org_b, names=(a1.name, a2.name))


@pytest.fixture
def estate(db_session, make_org):
    """Organisation A with three estimated packages, organisation B with one, and a person of each kind."""
    return _build_estate(
        db_session, make_org,
        {"spender": "cto", "restricted": "enterprise_architect", "finance": "finance"},
        {"other_spender": "cto"})


@pytest.fixture
def restricted_estate(db_session, make_org):
    """The same two organisations, each with a solution architect and a compliance person."""
    both = {"solution_architect": "solution_architect", "compliance": "compliance"}
    return _build_estate(db_session, make_org, both,
                         {"other_" + key: persona for key, persona in both.items()})


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
    # the one sentence of the cost rule, not a wording of the page's own
    assert "Withheld" in card and "Cost figures are withheld for your role." in card, card
    assert "Not estimated" not in card and "£0" not in card


def test_the_feed_for_a_restricted_person_marks_the_figures_as_hidden(client, login_as, estate):
    body = _get(client, login_as, estate["restricted"], FEEDS[0]).get_json()
    assert body["costs_withheld"] is True
    assert body["portfolio_metrics"]["total_estimated_investment"] is None
    assert all(c["investment_need"]["estimated_cost"] is None and c["investment_need"]["costs_withheld"]
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
    admin = estate["restricted"]
    admin.grant_org_admin()
    db_session.flush()
    text = _get(client, login_as, admin, PAGES[0]).get_data(as_text=True)
    assert "£%.1fk" % (A_TOTAL / 1000) in text


@pytest.mark.parametrize("path", PAGES + FEEDS)
def test_a_finance_person_sees_the_figures_the_spend_page_shows_them(client, login_as, estate, path):
    """whoever may open the Spend page reads the investment figures too."""
    response = _get(client, login_as, estate["finance"], path)
    assert response.status_code == 200, (path, response.status_code)
    text = response.get_data(as_text=True)
    if path in FEEDS:
        body = response.get_json()
        assert body["portfolio_metrics"]["total_estimated_investment"] == pytest.approx(A_TOTAL)
        assert not body.get("costs_withheld")
    else:
        card = re.search(r'data-testid="total-investment".*?</div>\s*</div>\s*</div>', text, re.S).group(0)
        assert "£%.1fk" % (A_TOTAL / 1000) in card, card
        assert "Withheld" not in card and "Cost figures are withheld for your role." not in card, card
    assert "777777" not in text and "777,777" not in text, path


@pytest.mark.parametrize("persona", ["solution_architect", "compliance"])
@pytest.mark.parametrize("path", PAGES + FEEDS)
def test_a_solution_architect_and_a_compliance_person_get_no_figure_in_two_organisations(
        client, login_as, restricted_estate, persona, path):
    """in each organisation the same person gets no pound figure in HTML or JSON."""
    for key, estimates in ((persona, (A_ONE, A_TWO, A_SHARED, A_TOTAL)),
                           ("other_" + persona, (B_ONE,))):
        response = _get(client, login_as, restricted_estate[key], path)
        assert response.status_code == 200, (key, path, response.status_code)
        text = response.get_data(as_text=True)
        assert "£" not in text, (key, path)
        for value in estimates:
            for shown in ("%d" % value, "{:,}".format(int(value)), "%.1fk" % (value / 1000)):
                assert shown not in text, (key, path, shown)
        if path in FEEDS:
            body = response.get_json()
            assert body["costs_withheld"] is True
            assert body["portfolio_metrics"]["total_estimated_investment"] is None
            assert all(c["investment_need"]["estimated_cost"] is None for c in body["capability_scores"])
