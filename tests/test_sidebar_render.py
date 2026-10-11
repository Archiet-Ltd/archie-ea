"""Render regression tests for the persona sidebar (shell-overhaul Wave 1, Task 3).

`app/templates/components/admin_sidebar.html` used to render one identical,
~102-link navigation for every role (see `test_sidebar_role_filtering.py`'s
docstring for the parallel history of `admin_sidebar_northstar_phase2.html`,
which was fixed first). This file proves the rewritten template actually
renders `app.utils.role_access.get_sidebar_zones(current_user)` — not a
hand-maintained parallel list — by hitting the real, highest-traffic route
(`/dashboard/overview`, `layouts/admin_base.html`) as a logged-in user of each
flagship persona and counting `<a ` tags inside the `data-testid="sidebar"`
container.

Follows the `client.get(...)` + session-login pattern already proven in
`tests/test_remaining_500_routes.py::_login` / `_make_logged_in_client`.
"""

from __future__ import annotations

import re
import uuid

import pytest

from app.utils.role_access import SIDEBAR_LINK_BUDGET
from scripts.check_sidebar_links import count_links as _real_link_count

pytestmark = pytest.mark.usefixtures("db_session")

SIDEBAR_BUDGET = SIDEBAR_LINK_BUDGET


def _login(client, user_id):
    """Standard Flask-Login test-client pattern (see test_remaining_500_routes.py)."""
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


def _make_logged_in_client(app, db_session, make_org, role, label):
    from app.models.user import Permission, Role, User

    org = make_org(f"sidebar-{label}")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"sidebar-{label}-{suffix}@example.com",
        first_name="Sidebar",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
        # get_sidebar_zones() filters the Admin zone on the real
        # `is_platform_admin` boolean (defaults False), not on
        # `enterprise_role` — see its docstring. Without this the
        # platform_admin fixture rendered its Admin zone away and the two
        # platform_admin assertions below measured a sidebar the role never
        # actually sees (15 links instead of the full set).
        is_platform_admin=(role == "platform_admin"),
    )
    if role == "platform_admin":
        # get_sidebar_zones() gates the whole Admin zone on user.is_admin(),
        # which reads user.role.permissions — not on is_platform_admin alone
        # (see that function's own docstring/comment). Role rows are seeded
        # by Role.insert_roles() in normal deploys; a fresh test database may
        # not have run it, so create-if-missing here exactly like
        # tests/test_r32_ai_permission_gate.py and every other call site that
        # queries for the Administrator role. Without this, the query below
        # silently returns None on a fresh DB, user.role stays unset,
        # is_admin() is False, and the two assertions after this fixture
        # measure a sidebar with no Admin zone at all — passing on a
        # long-lived dev database that already has the row from earlier
        # seeding, and failing only on a genuinely fresh one.
        Role.insert_roles()
        user.role = Role.query.filter(
            Role.permissions.op("&")(Permission.ADMINISTER) == Permission.ADMINISTER
        ).first()
        user.is_org_admin = True
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    _login(client, user.id)
    return client


_SIDEBAR_RE = re.compile(
    r'data-testid="sidebar".*?</aside>', re.DOTALL,
)


def _sidebar_html(app, db_session, make_org, role, label):
    client = _make_logged_in_client(app, db_session, make_org, role, label)
    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, (
        f"/dashboard/overview returned {resp.status_code} for role={role}: "
        f"{resp.get_data(as_text=True)[:2000]}"
    )
    html = resp.get_data(as_text=True)
    match = _SIDEBAR_RE.search(html)
    assert match, "sidebar container (data-testid=\"sidebar\") not found in response"
    return match.group(0)


@pytest.mark.parametrize(
    "role,label",
    [
        ("solution_architect", "sa"),
        ("enterprise_architect", "ea"),
        ("cto", "cto"),
    ],
)
def test_flagship_persona_sidebar_within_budget(app, db_session, make_org, role, label):
    sidebar_html = _sidebar_html(app, db_session, make_org, role, label)
    link_count = _real_link_count(sidebar_html)
    assert link_count <= SIDEBAR_BUDGET, (
        f"{role} sidebar renders {link_count} links, budget is {SIDEBAR_BUDGET}"
    )


def test_procurement_sidebar_excludes_arb_dashboard(app, db_session, make_org):
    sidebar_html = _sidebar_html(app, db_session, make_org, "procurement", "proc")
    assert "ARB Dashboard" not in sidebar_html, (
        "procurement is not a board role and must not see the ARB dashboard link"
    )


def test_enterprise_architect_sidebar_includes_arb_dashboard(app, db_session, make_org):
    sidebar_html = _sidebar_html(app, db_session, make_org, "enterprise_architect", "ea-arb")
    assert "ARB Dashboard" in sidebar_html, (
        "enterprise_architect is a board role and must see the ARB dashboard link"
    )


def test_platform_admin_hits_the_link_budget_exactly(app, db_session, make_org):
    """Task 3 fix round (coordinator review): platform_admin's two new
    admin-zone links (Salesforce Integration, Power Platform) plus the
    mandatory All-modules directory fallback (rendered in the sidebar footer
    for platform_admin — see admin_sidebar.html's `ns.has_all_modules` check)
    bring it to exactly SIDEBAR_LINK_BUDGET real, visible links: header logo
    + zone links + footer All-modules + footer logout. An equality
    assertion, not <=, so this — the role with zero headroom — pins the
    number precisely rather than letting future drift hide inside slack that
    does not exist.

    Evidence-review fix round: was 26 (23 zone links) until
    _MY_WORK_LINKS[ROLE_PLATFORM_ADMIN]'s duplicate "Applications" link
    (same endpoint as the one already in Library) was dropped, taking zone
    links to 22 and the total — and SIDEBAR_LINK_BUDGET — to 25.
    """
    sidebar_html = _sidebar_html(app, db_session, make_org, "platform_admin", "pa-budget")
    link_count = _real_link_count(sidebar_html)
    assert link_count == SIDEBAR_BUDGET, (
        f"platform_admin sidebar renders {link_count} links, expected exactly "
        f"{SIDEBAR_BUDGET} (0 headroom left — see role_access.py's "
        f"SIDEBAR_LINK_BUDGET comment)"
    )


@pytest.mark.parametrize(
    "role,label",
    [
        ("solution_architect", "sa-ask"),
        ("enterprise_architect", "ea-ask"),
        ("cto", "cto-ask"),
        ("procurement", "proc-ask"),
        ("platform_admin", "pa-ask"),
    ],
)
def test_ask_link_renders_first_under_my_work_and_the_twin_map_has_none(
    app, db_session, make_org, role, label
):
    """One front door to the Ask page, under My work, before that persona's own
    links; the Twin map is reached from the Ask page and has no link of its own."""
    sidebar_html = _sidebar_html(app, db_session, make_org, role, label)
    ask = sidebar_html.find('href="/intelligence/ask"')
    assert ask != -1, f"{role}: no Ask a question link in the rendered sidebar"
    assert sidebar_html.count('href="/intelligence/ask"') == 1
    my_work = sidebar_html.find("My work")
    library = sidebar_html.find("Library", my_work)
    assert my_work != -1 and library != -1 and my_work < ask < library
    # First link of the zone: nothing but the heading sits between them.
    first_link = sidebar_html.find("<a ", my_work)
    assert first_link == sidebar_html.rfind("<a ", 0, ask + 1)
    assert "Ask a question" in sidebar_html
    assert "/intelligence/twin-map" not in sidebar_html


def test_platform_admin_applications_link_not_duplicated(app, db_session, make_org):
    """Evidence-review finding: platform_admin's My-work zone used to list an
    "Applications" link pointing at the exact same endpoint
    (unified_applications.application_list) as the one already in Library,
    so the label rendered twice with no way to tell them apart. Assert the
    label appears exactly once now that the My-work duplicate is gone."""
    sidebar_html = _sidebar_html(app, db_session, make_org, "platform_admin", "pa-dup-apps")
    label_count = len(re.findall(r">Applications<", sidebar_html))
    assert label_count == 1, (
        f"platform_admin sidebar renders the 'Applications' label {label_count} "
        f"times, expected exactly 1 (My work's duplicate should be gone; "
        f"Library's copy should remain)"
    )


def test_platform_admin_governance_and_admin_zones_render_links(app, db_session, make_org):
    """Evidence review (Wave 1 screenshots): the live sidebar showed a
    'GOVERNANCE' heading with zero links beneath it and the whole 'ADMIN'
    zone (11 links) missing outright, despite SIDEBAR_ZONES for
    platform_admin containing both. This hits the real app/real routes (not
    a stub) specifically so a future wrong endpoint string in
    _GOVERNANCE_LINKS / _ADMIN_LINKS fails this test instead of silently
    rendering an empty or absent zone — which is exactly what let the
    original defect through."""
    sidebar_html = _sidebar_html(app, db_session, make_org, "platform_admin", "pa-gov-admin")
    assert "Governance" in sidebar_html, "platform_admin must see the Governance zone heading"
    assert "ARB Dashboard" in sidebar_html
    assert "Reviews" in sidebar_html
    assert "Sessions" in sidebar_html
    assert "Admin" in sidebar_html, "platform_admin must see the Admin zone heading"
    assert "Command Center" in sidebar_html
    assert "Users" in sidebar_html
    assert "Organizations" in sidebar_html
    assert "Salesforce Integration" in sidebar_html
    assert "Power Platform" in sidebar_html


def test_zone_heading_absent_when_all_its_links_are_guarded_out(app, db_session, make_org, monkeypatch):
    """A zone whose every endpoint is unregistered must not print its
    heading with an empty link list beneath it — that reads as "you have
    access but there's nothing here" instead of "you don't have this zone".
    Forces the guard to fail for every Governance link by pointing them at
    endpoints that do not exist, and asserts the heading disappears along
    with the links."""
    from app.utils import role_access

    bogus_links = [
        role_access._link("ARB Dashboard", "arb.does_not_exist", "layout-dashboard"),
        role_access._link("Reviews", "arb.also_missing", "shield-check"),
        role_access._link("Sessions", "arb.still_missing", "calendar"),
    ]
    monkeypatch.setattr(role_access, "_GOVERNANCE_LINKS", bogus_links)
    monkeypatch.setattr(
        role_access,
        "SIDEBAR_ZONES",
        {role: role_access._build_zones(role) for role in role_access._MY_WORK_LINKS},
    )

    sidebar_html = _sidebar_html(app, db_session, make_org, "platform_admin", "pa-gov-guarded-out")
    # A substring check on "Governance" would false-positive on the Admin
    # zone's "Governance Gates" link label baked into its own
    # matchesSearch(...) string, so match the zone-heading markup
    # specifically (mirrors _ZONE_TITLES rendering in admin_sidebar.html).
    heading_re = re.compile(
        r'role="heading"[^>]*>\s*Governance\s*</div>'
    )
    assert not heading_re.search(sidebar_html), (
        "a zone with zero surviving links must not render its heading either"
    )
    assert "ARB Dashboard" not in sidebar_html
    assert "arb.does_not_exist" not in sidebar_html


@pytest.mark.parametrize(
    "role,label",
    [
        ("enterprise_architect", "ea-allmod"),
        ("procurement", "proc-allmod"),
    ],
)
def test_sidebar_includes_all_modules_link(app, db_session, make_org, role, label):
    """The design's stated long-tail fallback ('Ctrl-K search + one new
    "All modules" directory page') must actually exist and be reachable from
    every role's sidebar — this was the Critical finding in the Task 3
    review: surfaces reachable from no sidebar on any role, with no working
    fallback. EA gets it via the Library zone; procurement too (only
    platform_admin uses the footer fallback — see
    test_platform_admin_hits_the_link_budget_exactly)."""
    sidebar_html = _sidebar_html(app, db_session, make_org, role, label)
    assert "All modules" in sidebar_html, (
        f"{role} sidebar has no reachable link to the All-modules directory"
    )


@pytest.mark.parametrize(
    "role,label",
    [
        ("solution_architect", "sa-impact"),
        ("enterprise_architect", "ea-impact"),
        ("business_architect", "ba-impact"),
    ],
)
def test_impact_analysis_is_linked_under_my_work(app, db_session, make_org, role, label):
    """The reported problem: reachable in one click from the persona's own sidebar, in the zone
    for their primary jobs (between the "My work" and "Library" headings), not only via All modules."""
    sidebar_html = _sidebar_html(app, db_session, make_org, role, label)
    link = sidebar_html.find('href="/strategic/impact-analysis"')
    assert link != -1, f"{role}: no Impact Analysis link in the rendered sidebar"
    my_work = sidebar_html.find("My work")
    library = sidebar_html.find("Library", my_work)
    assert my_work != -1 and library != -1 and my_work < link < library, (
        f"{role}: the Impact Analysis link is not under the My work heading"
    )


@pytest.mark.parametrize(
    "role,label",
    [
        ("solution_architect", "sa-canvases"),
        ("enterprise_architect", "ea-canvases"),
        ("business_architect", "ba-canvases"),
        ("procurement", "proc-canvases"),
    ],
)
def test_canvases_and_frameworks_in_library_zone(app, db_session, make_org, role, label):
    """Canvas/framework UI fix: "Canvases" and "Frameworks" must appear in the
    shared Library zone for every role."""
    sidebar_html = _sidebar_html(app, db_session, make_org, role, label)
    assert "Canvases" in sidebar_html, f"{role}: no Canvases link in sidebar"
    assert "Frameworks" in sidebar_html, f"{role}: no Frameworks link in sidebar"
    # Both must be under the Library heading, not My work.
    library = sidebar_html.find("Library")
    canvases = sidebar_html.find("Canvases")
    frameworks = sidebar_html.find("Frameworks")
    assert library != -1 and library < canvases, f"{role}: Canvases not under Library"
    assert library != -1 and library < frameworks, f"{role}: Frameworks not under Library"


def test_framework_management_and_config_reachable_from_admin_dashboard(app, db_session, make_org):
    """Canvas/framework UI fix, round 2 (25 Sep 2026): Framework Management and
    Framework Configuration are reached from the admin dashboard page
    (Command Center) rather than two more Admin-zone sidebar links — the
    platform_admin sidebar has zero headroom left once "Canvases" and
    "Frameworks" join every role's Library zone (see
    app/utils/role_access.py's SIDEBAR_LINK_BUDGET comment). Command Center
    itself is still the first link in the Admin zone, so both stay one click
    away from the sidebar."""
    client = _make_logged_in_client(app, db_session, make_org, "platform_admin", "pa-fw-dash")
    resp = client.get("/admin/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Framework Management" in html
    assert "Framework Configuration" in html
    assert 'href="/framework-management/"' in html or 'href="/framework-management' in html
    assert 'href="/framework-config/"' in html or 'href="/framework-config' in html

    # A non-admin cannot reach the dashboard page at all (admin_required).
    sa_client = _make_logged_in_client(app, db_session, make_org, "solution_architect", "sa-no-fw-dash")
    sa_resp = sa_client.get("/admin/")
    assert sa_resp.status_code in (302, 403), (
        "solution_architect should not be able to load the admin dashboard page"
    )


def test_business_architect_no_duplicate_capability_frameworks(app, db_session, make_org):
    """Canvas/framework UI fix: business_architect must not have a separate
    "Capability Frameworks" link — it is now "Frameworks" in the shared Library."""
    sidebar_html = _sidebar_html(app, db_session, make_org, "business_architect", "ba-no-dup-cf")
    assert "Capability Frameworks" not in sidebar_html, (
        "business_architect must not have a separate Capability Frameworks link"
    )
    assert "Frameworks" in sidebar_html, (
        "business_architect must still see Frameworks in the Library zone"
    )
