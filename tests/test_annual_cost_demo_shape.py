"""The Overview run cost on an organisation shaped like the demonstration company.

The demonstration organisation records cost in the applications' own cost columns: no contracts, no
recorded annual total on most rows, twenty-one live applications and one decommissioned. For every
role that may see spend, the Overview total must equal the sum of the applications list's annual
cost column and of each record's figure; a role that may not see spend must be shown no cost.
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

LIVE = 21
EACH = 25_200


def _estate(db_session, make_org, make_user, shape):
    from app.models.application_portfolio import ApplicationComponent

    org, other = make_org("demo-shape"), make_org("demo-shape-other")
    rows = []
    for index in range(LIVE):
        rows.append(ApplicationComponent(
            organization_id=org.id, name="Demo app %02d" % index, lifecycle_status="2. OPERATIONAL", **shape))
    rows.append(ApplicationComponent(
        organization_id=org.id, name="Retired app", lifecycle_status="5. DECOMMISSIONED", **shape))
    db_session.add(ApplicationComponent(
        organization_id=other.id, name="Other org app", lifecycle_status="2. OPERATIONAL", total_cost_of_ownership=777))
    db_session.add_all(rows)
    db_session.flush()
    return org, rows[:LIVE]


@pytest.fixture
def make_user(db_session):
    import uuid

    def _make(org, role):
        from app.models.user import User

        user = User(email=f"shape-{uuid.uuid4().hex[:8]}@example.com", first_name="Shape", last_name="Viewer",
                    organization_id=org.id, confirmed=True, enterprise_role=role)
        db_session.add(user)
        db_session.flush()
        return user

    return _make


def _get(client, login_as, user, path):
    login_as(client, user)
    return client.get(path)


def _overview_total(html):
    match = re.search(r'data-testid="card-run-cost-value"[^>]*title="£([\d,]+)"', html)
    return int(match.group(1).replace(",", "")) if match else None


SHAPES = {
    "monthly infrastructure only": {"infrastructure_cost_monthly": 2_100},
    "licence and monthly infrastructure": {"license_cost_annual": 10_000, "infrastructure_cost_monthly": 1_266.6666666667},
    "legacy columns": {"license_cost": 25_200},
}


@pytest.mark.parametrize("role", ["cto", "portfolio_manager", "procurement", "finance"])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_overview_list_and_records_state_the_same_cost(app, db_session, make_org, make_user, login_as, role, shape):
    org, live = _estate(db_session, make_org, make_user, SHAPES[shape])
    user = make_user(org, role)
    client = app.test_client()

    overview = _overview_total(_get(client, login_as, user, "/dashboard/overview").get_data(as_text=True))
    assert overview is not None, "the Overview states no annual run cost"

    html = _get(client, login_as, user, "/applications/?page_size=100").get_data(as_text=True)
    listed = []
    for row in live:
        cell = re.search(r'data-testid="app-cost-%d"[^>]*>(.*?)</td>' % row.id, html, re.S)
        assert cell, row.name
        text = " ".join(re.sub(r"<[^>]+>", " ", cell.group(1)).split())
        listed.append(float(text.replace("£", "").replace(",", "")))
    assert len(listed) == LIVE

    on_records = 0.0
    for row in live[:3]:
        record = _get(client, login_as, user, "/applications/%d?tab=cost" % row.id).get_data(as_text=True)
        figure = re.search(r'data-testid="annual-cost-value">\s*£([\d,]+)', record)
        assert figure, row.name
        on_records += float(figure.group(1).replace(",", ""))
        assert float(figure.group(1).replace(",", "")) == listed[live.index(row)]
    assert overview == round(sum(listed))


@pytest.mark.parametrize("role", ["enterprise_architect", "business_architect", "business_analyst", "solution_architect"])
def test_a_role_that_may_not_see_spend_is_shown_no_cost(app, db_session, make_org, make_user, login_as, role):
    org, live = _estate(db_session, make_org, make_user, SHAPES["monthly infrastructure only"])
    viewer = make_user(org, role)
    client = app.test_client()
    page = _get(client, login_as, viewer, "/dashboard/overview").get_data(as_text=True)
    assert "card-run-cost-value" not in page and "25,200" not in page
    html = _get(client, login_as, viewer, "/applications/?page_size=100").get_data(as_text=True)
    assert 'data-testid="app-cost-' not in html and "25,200" not in html
    record = _get(client, login_as, viewer, "/applications/%d?tab=cost" % live[0].id).get_data(as_text=True)
    assert "25,200" not in record and 'data-testid="annual-cost-value"' not in record
    assert record.count("Not recorded") == 0
