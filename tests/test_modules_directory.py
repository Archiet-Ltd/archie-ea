"""The All-modules directory page (shell-overhaul Wave 1, Task 3 fix round).

The persona sidebar diet moved dozens of real, working routes out of the DOM
for most roles. The design's stated long-tail fallback ("Ctrl-K search + one
new 'All modules' directory page") didn't actually exist before this round —
this file proves it now does: GET /modules 200s for an authenticated user and
surfaces both a zone-sourced link (Stakeholder Map, unioned from every role's
SIDEBAR_ZONES) and a curated "More tools" link (Batch Import, never in any
zone).

Follows the db_session + make_org + session-login pattern proven in
tests/test_remaining_500_routes.py and used by tests/test_sidebar_render.py
in this same wave.
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


def _make_logged_in_client(app, db_session, make_org, role="enterprise_architect"):
    from app.models.user import User

    org = make_org("modules-directory")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"modules-directory-{suffix}@example.com",
        first_name="Directory",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    _login(client, user.id)
    return client


def test_modules_directory_returns_200(app, db_session, make_org):
    client = _make_logged_in_client(app, db_session, make_org)
    resp = client.get("/modules")
    assert resp.status_code == 200, (
        f"/modules returned {resp.status_code}: {resp.get_data(as_text=True)[:2000]}"
    )


def test_modules_directory_includes_zone_sourced_link(app, db_session, make_org):
    """Assert the directory surfaces real zone-sourced links — one that is
    unambiguously zone-only and common to most roles: Architecture
    (library zone, every role)."""
    client = _make_logged_in_client(app, db_session, make_org)
    html = client.get("/modules").get_data(as_text=True)
    assert "Architecture" in html


def test_modules_directory_includes_curated_more_tools(app, db_session, make_org):
    """Corrected 20 Sep 2026 — this test previously asserted "Stakeholder Map"
    and "Batch Import" as proof the More-tools section itself renders.
    Neither actually proves that: Stakeholder Map IS a zone link
    (role_access.py's _MY_WORK_LINKS, several roles) and Batch Import IS a
    zone link too (_ADMIN_LINKS, platform_admin's zone) — both are only
    absent from More-tools for the default `enterprise_architect` test user
    because the zone-vs-More-tools dedup correctly drops the More-tools copy
    for whichever roles already have the zone copy. "Chief Architect
    Synthesis" (solution_design.architect_synthesis) is verified absent from
    every entry in role_access.py's SIDEBAR_ZONES (_HOME_LINKS,
    _LIBRARY_LINKS, _GOVERNANCE_LINKS, _ADMIN_LINKS, _MY_WORK_LINKS) — it is
    genuinely More-tools-only, so it actually tests what this test claims to
    test."""
    client = _make_logged_in_client(app, db_session, make_org)
    html = client.get("/modules").get_data(as_text=True)
    assert "Chief Architect Synthesis" in html


def test_modules_directory_includes_twin_map(app, db_session, make_org):
    """A-20 (readiness table 5.1, 2026-09-22): the Twin map has no sidebar
    link in any persona's zone by design (role_access.py's own comment --
    reached only from an Ask result, kept out of the 31-link sidebar
    budget), confirmed absent from every SIDEBAR_ZONES list the same way
    "Chief Architect Synthesis" above is -- so its More-tools row is this
    page's only findable home for it, not a dedup-hidden duplicate of a
    zone link."""
    client = _make_logged_in_client(app, db_session, make_org)
    html = client.get("/modules").get_data(as_text=True)
    assert "Twin Map" in html
    assert "/intelligence/twin-map" in html


def test_modules_directory_composer_link_carries_viewpoint_query_param(app, db_session, make_org):
    """D3 regression guard: /modules re-uses the same SIDEBAR_ZONES link data
    as the real sidebar, but its own _resolve() dropped query_params entirely
    -- so clicking ArchiMate Composer from /modules still landed on the bare
    blank-canvas URL, the founder's original bug, still live on this second
    door. Assert the href here carries the same ?viewpoint=layered as the
    real sidebar."""
    import re

    client = _make_logged_in_client(app, db_session, make_org)
    html = client.get("/modules").get_data(as_text=True)
    match = re.search(r'href="([^"]*archimate/composer[^"]*)"', html)
    assert match, "no ArchiMate Composer link found on /modules"
    assert "viewpoint=layered" in match.group(1), (
        f"/modules ArchiMate Composer link must carry ?viewpoint=layered, "
        f"got {match.group(1)!r}"
    )


def test_global_search_composer_result_carries_viewpoint_query_param(app, db_session, make_org):
    """Round 3 / 4th render site: the header Ctrl-K global search endpoint
    (/api/sidebar/search) independently does url_for(link["endpoint"]) off
    the same visible_module_links() data as /modules, with no query_params --
    so clicking "ArchiMate Composer" from the header search box still landed
    on the bare blank-canvas URL, the founder's original bug, still live on
    this fourth door. Assert the search result's url carries the same
    ?viewpoint=layered as the real sidebar and /modules."""
    client = _make_logged_in_client(app, db_session, make_org)
    resp = client.get("/api/sidebar/search?q=composer")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    data = resp.get_json()
    module_results = [r for r in data.get("results", []) if r.get("type") == "module"]
    composer_results = [r for r in module_results if "archimate/composer" in r.get("url", "")]
    assert composer_results, (
        f"no ArchiMate Composer module result found in global search: {data}"
    )
    assert any("viewpoint=layered" in r["url"] for r in composer_results), (
        f"global search ArchiMate Composer result must carry ?viewpoint=layered, "
        f"got {[r['url'] for r in composer_results]}"
    )


def test_modules_directory_requires_login(app, db_session, make_org):
    client = app.test_client()
    resp = client.get("/modules")
    assert resp.status_code in (302, 401, 403), (
        f"/modules must not be reachable anonymously, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# 30 Aug 2026 — "only advertise doors that open".
#
# The tests above prove the page renders and lists things. Nothing asserted a
# listed row leads anywhere. Requesting all 101 advertised destinations with a
# logged-in client found:
#
#   * 1 hard 500 (capability_maturity/heatmap.html called `empty_state` with
#     `cta_label=`, a kwarg only the *other* macro of that name accepts, so the
#     page died on exactly the empty database a new tenant has);
#   * 1 permanently-404 row (a deprecated, feature-flagged-off module);
#   * 4 rows that 302 onto a page this same list already offered by its own name;
#   * 19 rows shown to an enterprise_architect that return a hard 403, because
#     the page unions every role's SIDEBAR_ZONES with no per-user filter.
#
# Each is re-measured below.
# ---------------------------------------------------------------------------


def _make_user(db_session, org, *, enterprise_role, platform_admin=False):
    from app.models.user import User

    user = User(email=f"{enterprise_role}-{org.id}@modules.test")
    user.organization_id = org.id
    user.enterprise_role = enterprise_role
    user.confirmed = True
    user.is_platform_admin = platform_admin
    user.password = "Directory!12345"
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def org(make_org):
    return make_org("modules-directory")


# --------------------------------------------------------------------------
# The page renders, and renders the search control its own subtitle promises.
# --------------------------------------------------------------------------


def test_directory_renders_and_is_searchable(app, db_session, org, client, login_as):
    """The subtitle has always said "grouped and searchable" — assert the
    control exists and carries an accessible name (a placeholder is not one)."""
    user = _make_user(db_session, org, enterprise_role="enterprise_architect")
    login_as(client, user)

    response = client.get("/modules/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert 'data-testid="modules-directory-search"' in html
    assert 'for="modules-directory-search"' in html, "search input has no <label>"
    assert "Search modules by name" in html
    assert "modulesDirectory()" in html, "Alpine component not wired to the page"


# The attribute may be single- or double-quoted: `| tojson` emits double quotes
# inside the value, so the row markup is x-show='matches("Label")'. Pinning one
# quote style made this test pass on markup nobody ships -- it matched nothing
# and reported "directory rendered no rows at all", which reads as a broken page
# rather than a stale regex.
ROW_HREF = r'''<li x-show=['"]matches\([^)]*\)['"]>\s*<a href="([^"]*)"'''


def test_every_rendered_row_has_a_real_href(app, db_session, org, client, login_as):
    """No row may ship `href="#"`, an empty href, or an unresolved URL: the
    route resolves each endpoint itself and drops the ones it cannot build."""
    import re

    user = _make_user(db_session, org, enterprise_role="enterprise_architect")
    login_as(client, user)
    html = client.get("/modules/").get_data(as_text=True)

    body = html.split('data-testid="modules-directory"', 1)[1]
    hrefs = re.findall(ROW_HREF, body)
    assert hrefs, "directory rendered no rows at all"
    assert all(h.startswith("/") for h in hrefs), f"non-navigating rows: {hrefs}"
    assert "#" not in hrefs


# --------------------------------------------------------------------------
# Every advertised destination actually opens for the user it is shown to.
# --------------------------------------------------------------------------


def _rendered_hrefs(client):
    import re

    html = client.get("/modules/").get_data(as_text=True)
    body = html.split('data-testid="modules-directory"', 1)[1]
    return re.findall(ROW_HREF, body)


@pytest.mark.parametrize(
    "enterprise_role",
    [
        "enterprise_architect",
        "solution_architect",
        "business_architect",
        # security_architect added after the Policy Monitoring gap: its zone
        # link had no `requires=` predicate, so a Viewer-role assignment of
        # this persona specifically would 403 on a link the directory still
        # advertised. This param, with the persona's DEFAULT (non-Viewer)
        # role, guards the ordinary case; the narrower Viewer-role exposure
        # has its own dedicated test below
        # (test_policy_monitoring_not_advertised_to_viewer_role_security_architect).
        "security_architect",
    ],
)
def test_no_advertised_destination_is_forbidden(
    app, db_session, org, client, login_as, enterprise_role
):
    """A directory row the viewer cannot open is a dead button. This is the
    regression guard for the 19 hard-403 rows an enterprise_architect was
    shown before the role filter existed."""
    user = _make_user(db_session, org, enterprise_role=enterprise_role)
    login_as(client, user)

    forbidden = []
    for href in _rendered_hrefs(client):
        login_as(client, user)
        status = client.get(href).status_code
        if status in (401, 403, 404):
            forbidden.append((href, status))
    assert not forbidden, f"{enterprise_role} was offered unopenable rows: {forbidden}"


def test_role_exclusive_sections_are_hidden_from_other_personas(
    app, db_session, org, client, login_as
):
    """The admin / procurement / my-applications zones are EXCLUSIVE_SECTIONS
    in role_access.py. The directory unions every role's zones, so without a
    filter it advertised all three to everyone."""
    user = _make_user(db_session, org, enterprise_role="enterprise_architect")
    login_as(client, user)

    hrefs = _rendered_hrefs(client)
    leaked = [
        h
        for h in hrefs
        if h.startswith(("/admin/", "/procurement/", "/my-applications/"))
    ]
    assert not leaked, f"role-exclusive surfaces shown to a non-owner: {leaked}"


def test_platform_admin_still_sees_the_admin_zone(
    app, db_session, org, client, login_as
):
    """The filter must not be a blanket removal — the directory is still the
    long-tail answer for the persona that owns those sections."""
    user = _make_user(
        db_session, org, enterprise_role="platform_admin", platform_admin=True
    )
    login_as(client, user)

    hrefs = _rendered_hrefs(client)
    assert any(h.startswith("/admin/") for h in hrefs), (
        "platform admin lost the Admin zone entirely"
    )


# --------------------------------------------------------------------------
# _NOT_RENDERED: each entry is a recorded measurement, not an assumption.
# --------------------------------------------------------------------------


def test_not_rendered_entries_are_still_dead_or_duplicate(
    app, db_session, org, client, login_as
):
    """Every endpoint suppressed by `_NOT_RENDERED` must still be either a
    hard error or a redirect. If one becomes a live 200 page in its own right
    this fails, and the entry must be reinstated rather than staying hidden."""
    from flask import url_for

    from app.modules.modules_directory.routes import _NOT_RENDERED

    user = _make_user(
        db_session, org, enterprise_role="platform_admin", platform_admin=True
    )
    login_as(client, user)

    with app.test_request_context():
        urls = {ep: url_for(ep) for ep in _NOT_RENDERED}

    now_live = []
    for endpoint, url in urls.items():
        login_as(client, user)
        response = client.get(url)
        if response.status_code == 200:
            now_live.append((endpoint, url))
    assert not now_live, (
        "_NOT_RENDERED endpoints that now serve a real page and should be "
        f"listed again: {now_live}"
    )


def test_not_rendered_endpoints_are_absent_from_the_page_and_search(
    app, db_session, org, client, login_as
):
    """Suppressed modules must vanish from both surfaces that read the list —
    the directory page and the global-search index (which now calls
    visible_module_links(), not all_module_links())."""
    from flask import url_for
    from flask_login import login_user

    from app.modules.modules_directory.routes import (
        _NOT_RENDERED,
        all_module_links,
        visible_module_links,
    )

    user = _make_user(
        db_session, org, enterprise_role="platform_admin", platform_admin=True
    )
    login_as(client, user)

    with app.test_request_context():
        suppressed_urls = {url_for(endpoint) for endpoint in _NOT_RENDERED}

    hrefs = set(_rendered_hrefs(client))
    assert not (hrefs & suppressed_urls), (
        f"suppressed rows still rendered: {hrefs & suppressed_urls}"
    )

    with app.test_request_context("/"):
        login_user(user)
        searchable = {link["endpoint"] for link in visible_module_links()}
        known = {link["endpoint"] for link in all_module_links()}

    assert not (searchable & set(_NOT_RENDERED)), (
        "suppressed modules are still returned by global search"
    )
    # all_module_links() must keep knowing about them, or
    # tests/test_module_discoverability.py reports five phantom orphan modules.
    assert set(_NOT_RENDERED) <= known


# --------------------------------------------------------------------------
# The 500 that started this: an empty_state kwarg the macro does not accept.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# A module in both a persona zone and the More-tools catch-all must render
# once, not twice — index() now subtracts zone endpoints from _MORE_TOOLS the
# same way all_module_links() already does for global search.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "enterprise_role,platform_admin",
    [
        ("enterprise_architect", False),
        ("solution_architect", False),
        ("business_architect", False),
        ("security_architect", False),
        ("platform_admin", True),
    ],
)
def test_no_module_endpoint_rendered_twice(
    app, db_session, org, client, login_as, enterprise_role, platform_admin
):
    user = _make_user(
        db_session, org, enterprise_role=enterprise_role, platform_admin=platform_admin
    )
    login_as(client, user)

    hrefs = _rendered_hrefs(client)
    duplicates = {h for h in hrefs if hrefs.count(h) > 1}
    assert not duplicates, f"{enterprise_role} saw the same destination twice: {duplicates}"


def _section_bodies(html):
    """Split the directory page into {section title: body html} using the
    real <h2> heading markup (whitespace between the tag and the title text
    means a plain ">Title<" substring match never hits)."""
    import re

    headings = list(re.finditer(r'<h2[^>]*>\s*([^<]+?)\s*</h2>', html))
    bodies = {}
    for i, m in enumerate(headings):
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(html)
        bodies[m.group(1).strip()] = html[start:end]
    return bodies


def test_arb_dashboard_resolves_under_governance_not_my_work(
    app, db_session, org, client, login_as
):
    """arb.dashboard is assigned to _GOVERNANCE_LINKS ("ARB Dashboard") AND to
    solution_architect's _MY_WORK_LINKS ("Review Board"). The cross-bucket
    tie-break must prefer the shared "governance" placement over the
    persona-specific "my_work" one, or the Governance section loses its ARB
    dashboard entirely for every persona whose zones include both (the exact
    findability regression this fix exists to prevent)."""
    from flask import url_for

    user = _make_user(db_session, org, enterprise_role="solution_architect")
    login_as(client, user)

    html = client.get("/modules/").get_data(as_text=True)

    with app.test_request_context():
        arb_url = url_for("arb.dashboard")

    sections = _section_bodies(html)
    assert "Governance" in sections, f"Governance section missing from /modules: {list(sections)}"

    governance_body = sections["Governance"]
    assert arb_url in governance_body, (
        "ARB Dashboard must render under Governance, not My work "
        f"(arb_url={arb_url!r} not found in Governance section body)"
    )
    assert "ARB Dashboard" in governance_body

    my_work_body = sections.get("My work", "")
    assert arb_url not in my_work_body, (
        "arb.dashboard must not also render under My work once Governance owns it"
    )


def test_health_scorecard_still_resolves_under_home(
    app, db_session, org, client, login_as
):
    """Regression guard for the tie-break reorder above: Health Scorecard
    lives only in _HOME_LINKS, so it must stay under Home regardless of the
    tie-break preference order change."""
    user = _make_user(db_session, org, enterprise_role="enterprise_architect")
    login_as(client, user)

    html = client.get("/modules/").get_data(as_text=True)

    sections = _section_bodies(html)
    assert "Home" in sections, f"Home section missing from /modules: {list(sections)}"
    assert "Health Scorecard" in sections["Home"], (
        "Health Scorecard must still render under Home"
    )


def test_policy_monitoring_not_advertised_to_viewer_role_security_architect(
    app, db_session, org, client, login_as
):
    """A security_architect assigned the read-only Viewer Role (permissions=0)
    fails the underlying route's require_roles() check (Permission.GENERAL),
    so the directory must not advertise it — an advertised link that hard
    403s is a dead control."""
    from flask import url_for

    from app.models.user import Role

    Role.insert_roles()
    viewer_role = Role.query.filter_by(name="Viewer").first()
    assert viewer_role is not None

    user = _make_user(db_session, org, enterprise_role="security_architect")
    user.role = viewer_role
    db_session.flush()
    login_as(client, user)

    with app.test_request_context():
        live_url = url_for("policy_monitoring.policy_dashboard")

    hrefs = set(_rendered_hrefs(client))
    assert live_url not in hrefs, (
        "Policy Monitoring must not be advertised to a Viewer-role user who "
        "would 403 on it"
    )

    login_as(client, user)
    assert client.get(live_url).status_code == 403


def test_policy_monitoring_shows_only_the_live_endpoint(
    app, db_session, org, client, login_as
):
    """Impact-analysis-style duplicate, but by two DIFFERENT endpoints sharing
    the "Policy Monitoring" label so plain endpoint-dedup can't merge them.
    unified_low_priority.policy_monitoring_dashboard renders the same template
    with no data at all; policy_monitoring.policy_dashboard is the real
    governance dashboard (monitoring_data + compliance_report) and is what the
    security-architect zone now points at. Only that one should be advertised."""
    from flask import url_for

    user = _make_user(db_session, org, enterprise_role="security_architect")
    login_as(client, user)

    with app.test_request_context():
        live_url = url_for("policy_monitoring.policy_dashboard")
        stub_url = url_for("unified_low_priority.policy_monitoring_dashboard")

    hrefs = set(_rendered_hrefs(client))
    assert live_url in hrefs, "the real Policy Monitoring dashboard must stay reachable"
    assert stub_url not in hrefs, "the empty-shell duplicate must not also be advertised"

    login_as(client, user)
    assert client.get(live_url).status_code == 200


def test_maturity_heatmap_renders_with_no_capabilities(
    app, db_session, org, client, login_as
):
    """capability_maturity/heatmap.html imports `empty_state` from
    components/empty_state.html, whose CTA kwargs are `cta_text`/`cta_href` —
    it was calling it with `cta_label`, which raises TypeError inside Jinja and
    500s the page. The branch only renders when there are no capabilities, i.e.
    for every brand-new tenant."""
    user = _make_user(db_session, org, enterprise_role="enterprise_architect")
    login_as(client, user)

    response = client.get("/capability-maturity/heatmap")
    assert response.status_code == 200
