"""The Compliance persona can use the compliance routes, and no route names a role nobody holds.

Nine routes once required ``compliance_officer``, a role no account can have, so the persona that owns
compliance policies, violations and risks was turned away from the APIs built for it. Each is called
here as an ordinary Compliance account, a read-only Viewer and an organisation administrator, and the
answer must follow the product-manager ruling: Compliance owns policies, violations and the risk API;
workflow and business-process creation stay with administrators and architects.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_sidebar_offers_only_permitted_pages import _org, _ordinary_person
from tests.test_sidebar_render import _person

# (view function, method, compliance may call it)
_ROUTES = [
    ("create_compliance_policy", "POST", True),
    ("create_compliance_violation", "POST", True),
    ("update_compliance_violation", "PUT", True),
    ("list_risks", "GET", True),
    ("create_risk", "POST", True),
    ("get_risk_details", "GET", True),
    ("delete_risk", "DELETE", True),
    ("api_create_workflow", "POST", False),
    ("api_create_business_process", "POST", False),
]


def _url(app, view_name):
    rules = [r for r in app.url_map.iter_rules() if r.endpoint.rsplit(".", 1)[-1] == view_name]
    assert rules, f"{view_name} is not registered"
    rule = rules[0]
    return re.sub(r"<[^>]+>", "999999", rule.rule)


def _viewer(app, db_session, make_org, role, label, org):
    from app.models.user import Role, User

    client = _ordinary_person(app, db_session, make_org, role, label, org)
    user = User.query.filter(User.email.like(f"nav3-{label}-%")).one()
    user.role = Role.query.filter_by(name="Viewer").one()
    db_session.flush()
    return client


def _call(client, method, url):
    return client.open(url, method=method, json={})


def test_no_route_requires_a_role_nobody_holds():
    root = Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(r"require_roles\([^)]*compliance_officer")
    offenders = [str(p) for p in root.rglob("*.py") if pattern.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert offenders == []


@pytest.mark.parametrize("view,method,compliance_allowed", _ROUTES)
def test_each_compliance_route_follows_the_ruling(app, db_session, make_org, view, method, compliance_allowed):
    org = _org(make_org, f"cr-{view}")
    url = _url(app, view)

    compliance = _ordinary_person(app, db_session, make_org, "compliance", f"cr-c-{view}", org)
    status = _call(compliance, method, url).status_code
    if compliance_allowed:
        assert status != 403, f"{method} {url}: a Compliance account is refused ({status})"
    else:
        assert status == 403, f"{method} {url}: a Compliance account is admitted ({status})"

    viewer = _viewer(app, db_session, make_org, "compliance", f"cr-v-{view}", org)
    assert _call(viewer, method, url).status_code == 403, f"{method} {url}: a Compliance Viewer is admitted"

    admin, _ = _person(app, db_session, make_org, "application_manager", f"cr-a-{view}", admin=True, org=org)
    assert _call(admin, method, url).status_code != 403, f"{method} {url}: an administrator is refused"


def test_a_viewer_with_the_compliance_archetype_is_refused_controls(app, db_session, make_org):
    """role_archetype names what a person does; it must not grant a read-only account a writing page."""
    from app.models.user import Role, User

    org = _org(make_org, "archetype")
    for label, role_name, expected in (("arch-viewer", "Viewer", 403), ("arch-user", "User", 200)):
        client = _ordinary_person(app, db_session, make_org, "application_manager", label, org)
        user = User.query.filter(User.email.like(f"nav3-{label}-%")).one()
        user.role = Role.query.filter_by(name=role_name).one()
        user.role_archetype = "compliance"
        db_session.flush()
        assert client.get("/policy-monitoring/").status_code == expected, f"{role_name} with archetype compliance"


def test_controls_shows_the_read_error_banner_when_its_reads_fail(app, db_session, make_org, monkeypatch):
    """The journey asserts this banner is absent; this shows it is what a failing read renders."""
    from app.modules.governance.routes import policy_monitoring_routes as routes

    org = _org(make_org, "banner")
    client = _ordinary_person(app, db_session, make_org, "compliance", "banner", org)
    html = client.get("/policy-monitoring/").get_data(as_text=True)
    assert 'data-testid="policy-fetch-error"' not in html

    monkeypatch.setattr(routes, "get_policy_monitoring_data", lambda: {})
    monkeypatch.setattr(routes, "get_policy_compliance_report", lambda: {})
    html = client.get("/policy-monitoring/").get_data(as_text=True)
    assert 'data-testid="policy-fetch-error"' in html
