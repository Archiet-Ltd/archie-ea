"""The headline figures a launch customer sees agree on every screen that shows them.

Owner count, application cost, vendor count and gap count are each read from one store
(app/services/portfolio_figures.py). This seeds two organisations with different numbers,
signs in as a user of each and reads every screen and API that displays a figure, asserting
they all give the same answer and that one organisation never sees the other's.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

# org A: 6 applications, 3 with a recorded owner, 3 with a cost totalling 175,000, 3 gaps
# org B: 5 applications, 1 with a recorded owner, 1 with a cost totalling 7,000, 1 gap
ORG_A = {"apps": 6, "owners": 3, "costs": [100000, 50000, 25000], "gaps": 3}
ORG_B = {"apps": 5, "owners": 1, "costs": [7000], "gaps": 1}
VENDORS = 2


def _login(client, user_id, org_id):
    from tests._session_test_helpers import mint_test_sid

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        sess["_sid"] = mint_test_sid(user_id, organization_id=org_id)


def _seed_org(db_session, org, spec, label):
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.implementation_migration import Gap
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"agree-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Agree",
        last_name=label,
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.role = Role.query.filter_by(name="Architect").one()
    db_session.add(user)
    db_session.flush()
    apps = []
    for i in range(spec["apps"]):
        cost = spec["costs"][i] if i < len(spec["costs"]) else None
        app_row = ApplicationComponent(
            name=f"{label}-app-{i}-{uuid.uuid4().hex[:6]}",
            organization_id=org.id,
            total_cost_of_ownership=cost,
            lifecycle_status="2.1 strategic",
        )
        db_session.add(app_row)
        apps.append(app_row)
    db_session.flush()
    for app_row in apps[: spec["owners"]]:
        db_session.add(
            ApplicationOwner(
                application_id=app_row.id,
                user_id=user.id,
                organization_id=org.id,
                ownership_type="primary",
            )
        )
    for i in range(spec["gaps"]):
        db_session.add(
            Gap(
                name=f"{label}-gap-{i}-{uuid.uuid4().hex[:6]}",
                gap_kind="capability_shortfall",
                severity="medium",
                organization_id=org.id,
            )
        )
    if label == "A":
        # A duplicate record with an owner and a cost: not an application, so it must change no figure.
        dup = ApplicationComponent(
            name=f"(Duplicate) {label}-dup-{uuid.uuid4().hex[:6]}",
            organization_id=org.id,
            total_cost_of_ownership=None,
        )
        db_session.add(dup)
        db_session.flush()
        db_session.add(
            ApplicationOwner(
                application_id=dup.id, user_id=user.id, organization_id=org.id, ownership_type="backup"
            )
        )
    db_session.flush()
    return user


@pytest.fixture
def two_orgs(db_session, make_org):
    from app.models.vendor.vendor_organization import VendorOrganization

    org_a, org_b = make_org("agree-a"), make_org("agree-b")
    user_a = _seed_org(db_session, org_a, ORG_A, "A")
    user_b = _seed_org(db_session, org_b, ORG_B, "B")
    before = db_session.query(VendorOrganization).count()
    for i in range(VENDORS):
        code = f"VEND-AGREE-{uuid.uuid4().hex[:8]}"
        db_session.add(
            VendorOrganization(name=f"Agree vendor {i} {code}", code=code, seed_source_id=f"test:{code}")
        )
    db_session.flush()
    return {"A": (org_a, user_a), "B": (org_b, user_b), "vendors": before + VENDORS}


def _get(app, user, org, url):
    client = app.test_client()
    _login(client, user.id, org.id)
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)
    resp = client.get(url)
    assert resp.status_code == 200, f"{url} -> {resp.status_code}"
    return resp


def _dash_with_cfo(app, user, org):
    """The overview; the CFO lens is always in it."""
    return _get(app, user, org, "/dashboard/overview").get_data(as_text=True)


def _int(text):
    return int(text.replace(",", ""))


def _read_screens(app, user, org):
    """Every figure the shown screens and APIs give for this organisation."""
    figures = {}

    dash = _get(app, user, org, "/dashboard/overview").get_data(as_text=True)
    m = re.search(r'data-testid="owner-count">\s*(\d+) of (\d+) applications have an owner', dash)
    figures["owners: dashboard"] = (int(m.group(1)), int(m.group(2))) if m else None
    m = re.search(
        r'data-testid="cost-count">\s*(\d+) of (\d+) applications have a cost, ([\d,]+) a year', dash
    )
    figures["cost: dashboard"] = _int(m.group(3)) if m else None
    figures["cost apps: dashboard"] = int(m.group(1)) if m else None

    apps_page = _get(app, user, org, "/applications/").get_data(as_text=True)
    m = re.search(r"(\d+) of (\d+) applications in the portfolio have a named owner", apps_page)
    figures["owners: application list"] = (int(m.group(1)), int(m.group(2))) if m else None

    tiers = _get(app, user, org, "/dashboard/api/tco/cost-tiers").get_json()["summary"]
    figures["cost: cost-tiers api"] = round(tiers["total_tco"])
    figures["cost apps: cost-tiers api"] = tiers["apps_with_tco"]

    vendors_page = _get(app, user, org, "/applications/vendors").get_data(as_text=True)
    m = re.search(r"Total Vendors</p>\s*<div[^>]*>\s*<p[^>]*>\s*([\d,]+)\s*</p>", vendors_page)
    figures["vendors: vendor page"] = _int(m.group(1)) if m else None
    m = re.search(r'data-testid="cfo-kpi-vendors">\s*([\d,]+)\s*</p>', _dash_with_cfo(app, user, org))
    figures["vendors: dashboard"] = _int(m.group(1)) if m else None
    figures["vendors: vendor api"] = _get(app, user, org, "/api/v1/vendors/?per_page=1").get_json()[
        "data"
    ]["pagination"]["total"]

    gap_page = _get(app, user, org, "/enterprise/implementation/gap-analysis").get_data(as_text=True)
    m = re.search(r"Recorded Gaps</p>\s*<div[^>]*>\s*(\d+)\s*<", gap_page)
    figures["gaps: gap analysis"] = int(m.group(1)) if m else None
    figures["gaps: implementation api"] = len(
        _get(app, user, org, "/implementation/api/gaps").get_json()["gaps"]
    )
    figures["gaps: roadmap api"] = len(_get(app, user, org, "/api/roadmap/gaps").get_json()["gaps"])
    return figures


def _assert_same(figures, prefix, expected):
    seen = {k: v for k, v in figures.items() if k.startswith(prefix)}
    assert seen, f"no screen read for {prefix}"
    wrong = {k: v for k, v in seen.items() if v != expected}
    assert not wrong, f"expected {expected} everywhere, got {seen}"


def test_figures_agree_on_every_shown_screen(app, two_orgs):
    for label, spec in (("A", ORG_A), ("B", ORG_B)):
        org, user = two_orgs[label]
        figures = _read_screens(app, user, org)
        _assert_same(figures, "owners:", (spec["owners"], spec["apps"]))
        _assert_same(figures, "cost:", sum(spec["costs"]))
        _assert_same(figures, "cost apps:", len(spec["costs"]))
        _assert_same(figures, "vendors:", two_orgs["vendors"])
        _assert_same(figures, "gaps:", spec["gaps"])


def test_the_two_organisations_never_see_each_others_figures(app, two_orgs):
    from app.services import portfolio_figures

    org_a, _ = two_orgs["A"]
    org_b, _ = two_orgs["B"]
    assert portfolio_figures.owner_figure(org_a.id) == {
        "applications_with_owner": ORG_A["owners"],
        "application_count": ORG_A["apps"],
    }
    assert portfolio_figures.owner_figure(org_b.id)["applications_with_owner"] == ORG_B["owners"]
    assert portfolio_figures.cost_figure(org_a.id)["total_annual_cost"] == sum(ORG_A["costs"])
    assert portfolio_figures.cost_figure(org_b.id)["total_annual_cost"] == sum(ORG_B["costs"])
    assert portfolio_figures.gap_count(org_a.id) == ORG_A["gaps"]
    assert portfolio_figures.gap_count(org_b.id) == ORG_B["gaps"]

    # A's screens carry none of B's numbers: B's cost is not part of A's total, B's gaps not in A's list.
    org, user = two_orgs["A"]
    dash = _get(app, user, org, "/dashboard/overview").get_data(as_text=True)
    assert "182,000" not in dash and "7,000 a year" not in dash
    gaps = _get(app, user, org, "/implementation/api/gaps").get_json()["gaps"]
    assert all(g["name"].startswith("A-gap-") for g in gaps)


def test_nothing_recorded_shows_a_dash_not_a_zero(app, db_session, make_org):
    from app.models.user import Role, User
    from app.services import portfolio_figures

    org = make_org("agree-empty")
    assert portfolio_figures.cost_figure(org.id)["total_annual_cost"] is None
    assert portfolio_figures.percent(0, 0) is None
    Role.insert_roles()
    user = User(
        email=f"agree-empty-{uuid.uuid4().hex[:8]}@example.com",
        first_name="E",
        last_name="E",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.role = Role.query.filter_by(name="Architect").one()
    db_session.add(user)
    db_session.flush()
    dash = _get(app, user, org, "/dashboard/overview").get_data(as_text=True)
    assert "applications have a cost" not in dash or "0 of 0" not in dash


def test_the_cfo_tab_is_always_shown_and_says_what_is_missing_when_empty(app, db_session, two_orgs):
    from app.utils.role_access import NOT_READY_ENDPOINTS

    assert "dashboard_v2.overview#cfo_tab" not in NOT_READY_ENDPOINTS
    org, user = two_orgs["A"]
    dash = _get(app, user, org, "/dashboard/overview").get_data(as_text=True)
    assert 'data-testid="layer-tab-cfo"' in dash
    assert "activeRole === 'cfo'" in dash
    # With no cost data the lens shows the shared empty state, naming the figure and where it is filled.
    assert 'data-testid="cfo-empty-state"' in dash
    assert "No cost figures yet" in dash
    assert "cost import" in dash and "/consolidation-list/" in dash
    assert "vendor pages" in dash and "/applications/vendors" in dash


def test_owners_never_exceed_applications_with_a_duplicate_row(app, two_orgs):
    from app.services import portfolio_figures

    org, user = two_orgs["A"]
    fig = portfolio_figures.owner_figure(org.id)
    assert fig == {"applications_with_owner": ORG_A["owners"], "application_count": ORG_A["apps"]}
    assert fig["applications_with_owner"] <= fig["application_count"]
    page = _get(app, user, org, "/applications/").get_data(as_text=True)
    m = re.search(r"(\d+) of (\d+) applications in the portfolio have a named owner", page)
    assert m and int(m.group(1)) <= int(m.group(2))


def test_empty_vendor_catalogue_is_a_dash_on_the_tile_and_the_page(app, db_session, two_orgs):
    from app.models.vendor.vendor_organization import VendorOrganization

    db_session.query(VendorOrganization).delete(synchronize_session=False)
    db_session.flush()
    org, user = two_orgs["A"]
    dash = _dash_with_cfo(app, user, org)
    tile = re.search(r'data-testid="cfo-kpi-vendors">\s*(\S+)\s*</p>', dash)
    assert tile and tile.group(1) == "—", tile and tile.group(1)
    page = _get(app, user, org, "/applications/vendors").get_data(as_text=True)
    m = re.search(r"Total Vendors</p>\s*<div[^>]*>\s*<p[^>]*>\s*(\S+)\s*</p>", page)
    assert m and m.group(1) == "—", m and m.group(1)
