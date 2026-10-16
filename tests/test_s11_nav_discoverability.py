"""S-11 — navigation discoverability of already-shipped capabilities.

Three real, working capabilities were reachable from nowhere in the sidebar,
and two of them had a *second*, competing implementation that made "which one
is the product?" unanswerable:

* Gap Analysis     — `enterprise.gap_analysis` (the ArchiMate `Gap` register)
                     vs `adm_kanban_view.gap_analysis` (KanbanCard rows on the
                     ADM board). Different rows, both real; the enterprise one
                     is the canonical *register* and is the one linked from
                     navigation. The Kanban one stays reachable from the board
                     it belongs to and is deliberately NOT redirected.
* Arch. Decisions  — `arch_decisions.list_decisions` vs `adrs.list_adrs`. Same
                     `architecture_decisions` table, two model classes via
                     `extend_existing`; only the `arch_decisions` one is
                     tenant-scoped (TenantMixin). Canonical = `arch_decisions`;
                     `adrs.list_adrs` now 302s to it.
* Work Packages    — `enterprise.work_packages` passed a hardcoded
                     `workpackages=[]` into a template that never read it. The
                     page is an Alpine table over `/enterprise/api/work-packages`,
                     which queries the real `WorkPackage` model.

Uses the shared fixtures in tests/conftest.py (`db_session`, `make_org`,
`login_as`) per CLAUDE.md — not a hand-rolled module-scoped app fixture.
"""

from __future__ import annotations
import datetime

import uuid

import pytest
from flask import template_rendered, url_for

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, make_org, label, role="enterprise_architect"):
    from app.models.user import User

    org = make_org(f"s11-{label}")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"s11-{label}-{suffix}@example.com",
        first_name="S11",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True, onboarding_completed_at=datetime.datetime.utcnow(),
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user, org


# ── (a) the canonical endpoints are registered and serve ────────────────────

CANONICAL = [
    "enterprise.gap_analysis",
    "arch_decisions.list_decisions",
    "enterprise.work_packages",
]


@pytest.mark.parametrize("endpoint", CANONICAL)
def test_canonical_endpoint_is_registered(app, endpoint):
    """A nav link to an unregistered endpoint raises BuildError and 500s every
    page that renders the sidebar, so registration is asserted separately from
    the response."""
    assert endpoint in app.view_functions, f"{endpoint} is not registered"


@pytest.mark.parametrize("endpoint", CANONICAL)
def test_canonical_endpoint_returns_200(app, db_session, make_org, login_as, endpoint):
    user, _ = _user(db_session, make_org, "canon")
    client = app.test_client()
    login_as(client, user)
    with app.test_request_context():
        path = url_for(endpoint)
    resp = client.get(path)
    assert resp.status_code == 200, f"{endpoint} returned {resp.status_code}"


# ── (b) the retired duplicate redirects to the canonical listing ────────────


def test_adr_listing_has_one_canonical_route_or_redirect(
    app, db_session, make_org, login_as
):
    """Measured, not assumed: `adr_bp` is only registered by
    `app.modules.architecture.register()`, which is the **Tier 2** branch of
    `_bootstrap/blueprints.py:_register_architecture`. Tier 1 (v2) is taken
    whenever USE_ARCHITECTURE_GUARDRAILS is on — the default — and returns
    early, so `adrs.list_adrs` is unreachable as shipped. That settles the
    "two competing Architecture Decisions implementations" question: only
    `arch_decisions.list_decisions` is actually routed.
    """
    assert "arch_decisions.list_decisions" in app.view_functions
    if "adrs.list_adrs" not in app.view_functions:
        assert "adrs.list_adrs" not in app.view_functions
        return

    # If Tier 2 is selected, its duplicate listing must redirect to the
    # canonical tenant-scoped route rather than serve a second implementation.
    user, _ = _user(db_session, make_org, "adr")
    client = app.test_client()
    login_as(client, user)
    with app.test_request_context():
        adr_path = url_for("adrs.list_adrs")
        canonical_path = url_for("arch_decisions.list_decisions")
    resp = client.get(adr_path)
    assert resp.status_code == 302, f"expected a redirect, got {resp.status_code}"
    assert canonical_path in resp.headers["Location"]


def test_no_nav_link_points_at_the_retired_duplicate():
    """The sidebar is data-driven from role_access; assert the loser endpoint
    is not in any role's zones."""
    from app.utils.role_access import SIDEBAR_ZONES

    for role, zones in SIDEBAR_ZONES.items():
        endpoints = {link["endpoint"] for zone in zones for link in zone["links"]}
        assert "adrs.list_adrs" not in endpoints, f"{role} still links the retired listing"


# ── (c) Work Packages is no longer a hardcoded-empty render ─────────────────


def test_work_packages_page_passes_no_hardcoded_empty_list(app, db_session, make_org, login_as):
    """Fail-first: the old handler did
    `render_template("enterprise/work_packages.html", workpackages=[])`, so
    this assertion failed with `workpackages == []` in the context — a
    permanently-empty variable that read as "there are no work packages".
    """
    user, _ = _user(db_session, make_org, "wp-ctx")
    client = app.test_client()
    login_as(client, user)

    captured = []

    def _record(sender, template, context, **extra):
        captured.append((template.name, context))

    template_rendered.connect(_record, app)
    try:
        with app.test_request_context():
            path = url_for("enterprise.work_packages")
        assert client.get(path).status_code == 200
    finally:
        template_rendered.disconnect(_record, app)

    contexts = [ctx for name, ctx in captured if name == "enterprise/work_packages.html"]
    assert contexts, "work_packages.html was not rendered"
    assert "workpackages" not in contexts[0], (
        "the handler still passes a hardcoded `workpackages` list into a template "
        "that never reads it"
    )


def test_work_packages_api_returns_a_real_row(app, db_session, make_org, login_as):
    """The page's data actually comes from `/enterprise/api/work-packages`.
    Create one row and assert it is served — proving the capability is real
    and worth a nav link, rather than a stub rendering fabricated emptiness.
    """
    from app.models.implementation_migration import WorkPackage

    user, org = _user(db_session, make_org, "wp-api")
    name = f"S11 probe {uuid.uuid4().hex[:8]}"
    db_session.add(WorkPackage(name=name, organization_id=org.id, status="planned"))
    db_session.flush()

    client = app.test_client()
    login_as(client, user)
    resp = client.get("/enterprise/api/work-packages?per_page=100")
    assert resp.status_code == 200
    assert name in resp.get_data(as_text=True), (
        "the work-packages API did not return the row that was just created"
    )


# ── (d) the links are actually rendered in the sidebar ──────────────────────


@pytest.mark.parametrize(
    "endpoint",
    ["enterprise.gap_analysis", "enterprise.work_packages", "arch_decisions.list_decisions"],
)
def test_enterprise_architect_sidebar_links_the_capability(
    app, db_session, make_org, login_as, endpoint
):
    """Fail-first: none of these three endpoints appeared anywhere in any
    role's sidebar before this change."""
    user, _ = _user(db_session, make_org, "nav")
    client = app.test_client()
    login_as(client, user)
    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200
    with app.test_request_context():
        href = url_for(endpoint)
    assert f'href="{href}"' in resp.get_data(as_text=True), (
        f"{endpoint} is not linked from the enterprise_architect sidebar"
    )
