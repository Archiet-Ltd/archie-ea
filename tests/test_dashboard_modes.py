"""Dashboard two-mode regression tests (shell-overhaul Wave 1, Task 5).

A brand-new, sparse org used to render the same "Health Score 5 / six red 0%
bars / 8 all-zero Solution Pipeline rows" report card as an established one —
the product owner's screenshot evidence for a cluttered, broken-looking UI.
`/dashboard/overview` (app/modules/dashboard/v2/routes/dashboard_views.py)
now computes ``dashboard_mode`` server-side from counts it already fetches:
guided (a setup journey, no red, no fabricated zeros) for an org with fewer
than 5 applications AND zero capability mappings; data (health score +
visualizations) otherwise.

Login pattern follows tests/test_sidebar_render.py::_login (the proven
`client.session_transaction()` + Flask-Login g-cache-clear pattern for this
route). All fixture rows are seeded *before* `_login()` runs: the db_session
fixture keeps one app context open for the whole test (so `client.get(...)`
reuses it rather than pushing a fresh one — see test_ba_tenant_and_authz.py's
`_login` docstring), and creating ORM rows *after* login but before the first
real request left a stale anonymous user cached on that shared `g` in one
manual trial here — seeding first sidesteps it entirely and matches every
other adopter of this pattern in the suite.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _grant_admin(db_session, user):
    """Attach a Role whose permissions bitfield includes Permission.ADMINISTER.

    User.is_admin() (app/models/user.py) checks self.role.permissions, NOT the
    is_org_admin/is_platform_admin booleans -- setting those alone leaves
    is_admin() False and the "Invite your team" guided step never renders.
    Mirrors tests/test_ba_tenant_and_authz.py::_grant_admin.
    """
    from app.models.user import Permission, Role

    role = Role.query.filter(Role.name.in_(("Administrator", "Admin", "admin"))).first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    elif role.permissions is None or (role.permissions & Permission.ADMINISTER) != Permission.ADMINISTER:
        role.permissions = Permission.ADMINISTER
    user.role = role
    db_session.flush()


def _make_user(db_session, make_org, label, enterprise_role="platform_admin", admin=True):
    from app.models.user import User

    org = make_org(f"dash-{label}")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"dash-{label}-{suffix}@example.com",
        first_name="Dash",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
        is_org_admin=True,
    )
    db_session.add(user)
    db_session.flush()
    if admin:
        _grant_admin(db_session, user)
    return user, org


def test_sparse_org_gets_guided_setup(app, db_session, make_org):
    """0 applications, 0 mappings -> guided mode: setup card, no health score,
    no red 0% score bars."""
    user, _org = _make_user(db_session, make_org, "sparse")

    client = app.test_client()
    _login(client, user.id)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert 'data-testid="guided-setup"' in html
    assert 'data-testid="health-score-value"' not in html
    # No red-styled 0% figure (the "six red 0% bars" defect).
    assert "text-destructive\">0%" not in html


def test_established_org_gets_data_mode(app, db_session, make_org):
    """>=5 applications and >=1 capability mapping -> data mode: health score
    leads, no guided setup card."""
    from sqlalchemy import insert

    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability

    user, org = _make_user(db_session, make_org, "established")

    # Several before_insert listeners (e.g. archimate_relationship_sync's
    # auto-created "serving" relationship on an ApplicationCapabilityMapping)
    # pull organization_id from g.current_org_id rather than the triggering
    # row, same as the real per-request path -- set it here since this fixture
    # runs outside a request.
    from flask import g

    g.current_org_id = org.id

    apps = []
    for i in range(6):
        app_component = ApplicationComponent(
            name=f"Test App {i}-{uuid.uuid4().hex[:6]}",
            organization_id=org.id,
        )
        db_session.add(app_component)
        apps.append(app_component)
    db_session.flush()

    # BusinessCapability's before_insert listener auto-creates an
    # ArchiMateElement via a raw Core insert that never sets organization_id
    # (app/models/business_capabilities.py create_capability_archimate_element)
    # -- unrelated pre-existing schema-drift bug (archimate_elements.organization_id
    # is NOT NULL at the DB level but absent from the ORM model entirely). Pre-set
    # archimate_element_id here so that listener's no-op branch is taken instead.
    cap_name = f"Test Capability {uuid.uuid4().hex[:6]}"
    elem_id = db_session.execute(
        insert(ArchiMateElement.__table__).values(
            name=cap_name, type="Capability", layer="Strategy", organization_id=org.id
        )
    ).inserted_primary_key[0]
    db_session.flush()

    capability = BusinessCapability(
        name=cap_name,
        level=1,
        organization_id=org.id,
        archimate_element_id=elem_id,
    )
    db_session.add(capability)
    db_session.flush()

    mapping = ApplicationCapabilityMapping(
        application_component_id=apps[0].id,
        business_capability_id=capability.id,
        organization_id=org.id,
    )
    db_session.add(mapping)
    db_session.flush()

    client = app.test_client()
    _login(client, user.id)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert 'data-testid="health-score-value"' in html
    assert 'data-testid="guided-setup"' not in html


def test_workspace_cards_match_my_work_zone(app, db_session, make_org):
    """Shell-overhaul Wave 2, Task 5: the data-mode "Your Workspace" quick-access
    cards used to be a hand-maintained per-role card list in
    dashboards/overview.html, parallel to (and free to drift from)
    get_sidebar_zones()'s "my_work" zone -- the sidebar's own single source of
    truth for a role's primary-work links (app/utils/role_access.py). The
    template now renders straight from that zone, so a solution_architect's
    workspace cards must be exactly the same labels as
    get_sidebar_zones(user)'s my_work zone, in the same order."""
    from sqlalchemy import insert

    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability
    from app.utils.role_access import get_sidebar_zones

    # solution_architect, not platform_admin: this test's fixture setup
    # doesn't touch the platform_admin-specific question at all --
    # test_workspace_cards_render_for_platform_admin below covers that role
    # directly, now that dashboard-platform-admin-workspace-gap-brief-v1's
    # fix (2026-09-22) restored this section for it. This test only proves
    # the zone-vs-cards data agreement described in its own docstring, for
    # any role.
    user, org = _make_user(db_session, make_org, "workspace", enterprise_role="solution_architect", admin=False)

    # Force data mode (>=5 applications and >=1 capability mapping), same
    # fixture shape as test_established_org_gets_data_mode above -- the
    # workspace cards only render in data mode, not guided mode.
    from flask import g

    g.current_org_id = org.id

    apps = []
    for i in range(6):
        app_component = ApplicationComponent(
            name=f"Test App {i}-{uuid.uuid4().hex[:6]}",
            organization_id=org.id,
        )
        db_session.add(app_component)
        apps.append(app_component)
    db_session.flush()

    cap_name = f"Test Capability {uuid.uuid4().hex[:6]}"
    elem_id = db_session.execute(
        insert(ArchiMateElement.__table__).values(
            name=cap_name, type="Capability", layer="Strategy", organization_id=org.id
        )
    ).inserted_primary_key[0]
    db_session.flush()

    capability = BusinessCapability(
        name=cap_name,
        level=1,
        organization_id=org.id,
        archimate_element_id=elem_id,
    )
    db_session.add(capability)
    db_session.flush()

    mapping = ApplicationCapabilityMapping(
        application_component_id=apps[0].id,
        business_capability_id=capability.id,
        organization_id=org.id,
    )
    db_session.add(mapping)
    db_session.flush()

    expected_labels = [
        link["label"]
        for zone in get_sidebar_zones(user)
        if zone["zone"] == "my_work"
        for link in zone["links"]
    ]
    assert expected_labels, "solution_architect's my_work zone must not be empty"

    client = app.test_client()
    _login(client, user.id)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert 'data-testid="health-score-value"' in html, "expected data mode, not guided mode"

    for label in expected_labels:
        assert html.count(label) >= 1, (
            f"Your Workspace cards missing my_work zone link {label!r} -- "
            "the cards must render from get_sidebar_zones(), not a "
            "hand-maintained parallel list"
        )


def test_workspace_cards_render_for_platform_admin(app, db_session, make_org):
    """dashboard-platform-admin-workspace-gap-brief-v1 (2026-09-22): the
    "Your Workspace" section used to be skipped entirely for platform_admin
    on a stale comment -- read directly against role_access.py, the four
    admin-zone links the comment named were never actually in this role's
    my_work zone, so there was nothing to deduplicate. platform_admin is
    "the default enterprise_role for every user who has not picked one"
    (role_access.py's own comment), so this hid Ask -- and every other
    my_work card -- from most real accounts. This pins the fix: a
    platform_admin now sees their own my_work zone's cards, the same way
    every other role already did."""
    from sqlalchemy import insert

    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability
    from app.utils.role_access import get_sidebar_zones

    user, org = _make_user(db_session, make_org, "workspace-admin", enterprise_role="platform_admin", admin=True)

    from flask import g

    g.current_org_id = org.id

    apps = []
    for i in range(6):
        app_component = ApplicationComponent(
            name=f"Admin Test App {i}-{uuid.uuid4().hex[:6]}",
            organization_id=org.id,
        )
        db_session.add(app_component)
        apps.append(app_component)
    db_session.flush()

    cap_name = f"Admin Test Capability {uuid.uuid4().hex[:6]}"
    elem_id = db_session.execute(
        insert(ArchiMateElement.__table__).values(
            name=cap_name, type="Capability", layer="Strategy", organization_id=org.id
        )
    ).inserted_primary_key[0]
    db_session.flush()

    capability = BusinessCapability(
        name=cap_name,
        level=1,
        organization_id=org.id,
        archimate_element_id=elem_id,
    )
    db_session.add(capability)
    db_session.flush()

    mapping = ApplicationCapabilityMapping(
        application_component_id=apps[0].id,
        business_capability_id=capability.id,
        organization_id=org.id,
    )
    db_session.add(mapping)
    db_session.flush()

    expected_labels = [
        link["label"]
        for zone in get_sidebar_zones(user)
        if zone["zone"] == "my_work"
        for link in zone["links"]
    ]
    assert expected_labels, "platform_admin's my_work zone must not be empty"
    assert "Ask a question" in expected_labels, (
        "platform_admin's my_work zone should carry the shared Ask link, "
        "same as every other role"
    )

    client = app.test_client()
    _login(client, user.id)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert 'data-testid="health-score-value"' in html, "expected data mode, not guided mode"

    for label in expected_labels:
        assert html.count(label) >= 1, (
            f"Your Workspace cards missing my_work zone link {label!r} for "
            "platform_admin -- the section must render for this role too, "
            "not only every other one"
        )


def test_solution_pipeline_concentrated_in_one_phase_collapses_to_sentence(
    app, db_session, make_org
):
    """A data-mode org whose solutions are all bunched in one ADM phase gets
    the one-sentence fallback (spec 3's own example: "Nothing past Vision yet
    — 70 solutions are in phase A"), not 7 zero-width bars plus one full one."""
    from sqlalchemy import insert

    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_models import Solution

    user, org = _make_user(db_session, make_org, "concentrated")

    from flask import g

    g.current_org_id = org.id

    apps = []
    for i in range(6):
        app_component = ApplicationComponent(
            name=f"Test App {i}-{uuid.uuid4().hex[:6]}",
            organization_id=org.id,
        )
        db_session.add(app_component)
        apps.append(app_component)
    db_session.flush()

    cap_name = f"Test Capability {uuid.uuid4().hex[:6]}"
    elem_id = db_session.execute(
        insert(ArchiMateElement.__table__).values(
            name=cap_name, type="Capability", layer="Strategy", organization_id=org.id
        )
    ).inserted_primary_key[0]
    db_session.flush()

    capability = BusinessCapability(
        name=cap_name, level=1, organization_id=org.id, archimate_element_id=elem_id,
    )
    db_session.add(capability)
    db_session.flush()

    db_session.add(
        ApplicationCapabilityMapping(
            application_component_id=apps[0].id,
            business_capability_id=capability.id,
            organization_id=org.id,
        )
    )
    db_session.flush()

    for i in range(3):
        db_session.add(
            Solution(
                name=f"Test Solution {i}-{uuid.uuid4().hex[:6]}",
                organization_id=org.id,
                adm_phase="A",
            )
        )
    db_session.flush()

    client = app.test_client()
    _login(client, user.id)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert "Nothing past Vision yet" in html
    assert "3 solutions are in phase A" in html
    # The 8-row bar chart must not render alongside the sentence.
    assert 'B: Business' not in html


def test_invite_step_ignores_other_orgs_users(app, db_session, make_org):
    """"Invite your team"'s done-check must be scoped to the current org.

    metrics["users"] (used for the KPI strip) is a GLOBAL count -- User is not
    TenantMixin, so nothing auto-scopes it. Before the fix, the guided step
    used that global count directly: a brand-new, single-user org would read
    the invite step as Done purely because some OTHER org on the platform had
    more than one user. Org A here has exactly 1 user; org B has 3 and must
    have zero influence on org A's dashboard.
    """
    from app.models.user import User

    user_a, org_a = _make_user(db_session, make_org, "single")

    org_b = make_org("dash-crowded")
    for i in range(3):
        crowd_user = User(
            email=f"dash-crowded-{i}-{uuid.uuid4().hex[:8]}@example.com",
            first_name="Crowd",
            last_name="Tester",
            organization_id=org_b.id,
            confirmed=True,
            enterprise_role="platform_admin",
        )
        db_session.add(crowd_user)
    db_session.flush()

    client = app.test_client()
    _login(client, user_a.id)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert 'data-testid="guided-setup"' in html
    assert "Invite your team" in html
    # A fresh, single-user org: none of the three guided steps should read
    # Done -- in particular, not the invite step just because org_b is crowded.
    assert 'data-lucide="check"' not in html


def test_welcome_banner_dismiss_persists_server_side(app, db_session, make_org):
    """POST /dashboard/api/welcome-dismiss sets User.welcome_banner_dismissed_at;
    a subsequent GET must render without the banner at all (server-gated, not
    merely CSS-hidden client-side)."""
    user, _org = _make_user(db_session, make_org, "dismiss")

    client = app.test_client()
    _login(client, user.id)

    first = client.get("/dashboard/overview")
    assert first.status_code == 200
    first_html = first.get_data(as_text=True)
    # Not a plain text-substring check: "Welcome to Entelim" also appears
    # in admin_base.html's separate role-selection onboarding modal, so pin the
    # dashboard's own banner via its data-testid instead.
    assert 'data-testid="welcome-banner"' in first_html

    dismiss_resp = client.post(
        "/dashboard/api/welcome-dismiss",
        headers={"X-CSRFToken": "test"},
    )
    assert dismiss_resp.status_code == 200, dismiss_resp.get_data(as_text=True)[:1000]
    assert dismiss_resp.get_json() == {"success": True}

    second = client.get("/dashboard/overview")
    assert second.status_code == 200
    second_html = second.get_data(as_text=True)
    assert 'data-testid="welcome-banner"' not in second_html
