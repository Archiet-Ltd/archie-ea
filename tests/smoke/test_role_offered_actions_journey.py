"""A person is offered only the actions the page behind them admits.

The technology radar, a solution, a vendor and the administration home each
used to show every person an action that ended in "forbidden" for some roles.
A Viewer, an architect who did not make the solution, and an organisation
administrator open each screen; every link the screen offers must open a page
with content, and the people the route admits still find the action and finish
it.
"""

import uuid
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SKIP_PATHS = ("/account/logout", "/logout")


@pytest.fixture(scope="module")
def world(live_server):
    """One organisation with a creator, another architect, a Viewer, an
    organisation administrator and a platform administrator, a technology on
    the radar, a solution the creator made and a vendor."""
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out = {"emails": {}, "suffix": suffix}
    with app.app_context():
        from app.models.org_role import OrgRole
        from app.models.organization import Organization
        from app.models.solution_models import Solution
        from app.models.technology_layer import Node
        from app.models.user import Role, User
        from app.models.vendor.vendor_organization import VendorOrganization
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        roles = {n: Role.query.filter_by(name=n).one() for n in ("Architect", "Viewer", "Administrator")}
        org = Organization(name="Offered Actions Org %s" % suffix, slug="offered-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        db.session.commit()

        people = [
            ("creator", "solution_architect", "Architect"),
            ("architect", "enterprise_architect", "Architect"),
            ("viewer", "solution_architect", "Viewer"),
            ("org_admin", "solution_architect", "Administrator"),
            ("platform_admin", "platform_admin", "Administrator"),
        ]
        ids = {}
        for key, enterprise_role, platform_role in people:
            email = "offered.%s.%s@example.com" % (key.replace("_", "-"), suffix)
            user = User(email=email, first_name="Offered", last_name=key[:12],
                        organization_id=org.id, enterprise_role=enterprise_role, confirmed=True)
            user.role = roles[platform_role]
            user.is_org_admin = key in ("org_admin", "platform_admin")
            user.is_platform_admin = key == "platform_admin"
            user.password = PASSWORD
            db.session.add(user)
            db.session.commit()
            ids[key] = user.id
            out["emails"][key] = email
        OrgRole.set_role(org.id, ids["org_admin"], "org_admin", granted_by_id=ids["org_admin"])
        db.session.commit()

        solution = Solution(name="Offered actions solution %s" % suffix,
                            description="Made by the creator.", organization_id=org.id,
                            created_by_id=ids["creator"], governance_status="draft")
        node = Node(name="Offered actions broker %s" % suffix, organization_id=org.id)
        vendor = VendorOrganization(name="Offered actions vendor %s" % suffix)
        db.session.add_all([solution, node, vendor])
        db.session.commit()
        out.update(solution=solution.id, element=node.archimate_element_id, vendor=vendor.id,
                   solution_name=solution.name)
        db.session.remove()
    return out


def _open(browser, live_server, world, who):
    context = browser.new_context()
    context.add_init_script("try { localStorage.setItem('archie_onboarding_ts', '1'); } catch (e) {}")
    page = context.new_page()
    sign_in(page, live_server, world["emails"][who])
    return context, page


def _offered_links(page, live_server):
    """Every same-site link on the page, as a path."""
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    found = set()
    for href in hrefs:
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        parsed = urlparse(href)
        if parsed.netloc and not live_server.endswith(parsed.netloc):
            continue
        if parsed.path and not any(parsed.path.startswith(p) for p in SKIP_PATHS):
            found.add(parsed.path + (("?" + parsed.query) if parsed.query else ""))
    return found


def _screens(world):
    return {
        "radar": "/technology/radar/",
        "solution": "/solutions/%d" % world["solution"],
        "vendor": "/applications/vendors/%d" % world["vendor"],
        "admin": "/admin/",
    }


@pytest.mark.parametrize("who", ["viewer", "architect", "org_admin"])
def test_no_screen_offers_an_action_that_answers_forbidden(browser, live_server, world, who):
    context, page = _open(browser, live_server, world, who)
    try:
        for name, path in _screens(world).items():
            response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            if response.status == 403:
                # The screen itself is not for this role: nothing is offered from it.
                continue
            assert response.status == 200, (who, name, response.status)
            refused = []
            for link in sorted(_offered_links(page, live_server)):
                reply = context.request.get(live_server + link, max_redirects=5)
                if reply.status == 403:
                    refused.append(link)
            assert refused == [], "%s is offered links on %s that answer forbidden: %s" % (who, name, refused)
    finally:
        context.close()


def test_viewer_sees_each_screen_and_is_told_who_can_change_it(browser, live_server, world):
    context, page = _open(browser, live_server, world, "viewer")
    try:
        page.goto(live_server + "/technology/radar/", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Offered actions broker")).to_be_visible()
        expect(page.get_by_test_id("radar-place-%d" % world["element"])).to_have_count(0)
        expect(page.get_by_test_id("radar-readonly-note")).to_be_visible()

        page.goto(live_server + "/solutions/%d" % world["solution"], timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(world["solution_name"]).first).to_be_visible()
        expect(page.get_by_test_id("record-edit")).to_have_count(0)

        page.goto(live_server + "/applications/vendors/%d" % world["vendor"], timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Offered actions vendor").first).to_be_visible()
        assert page.get_by_role("link", name="Edit Vendor").count() == 0
    finally:
        context.close()


def test_architect_who_did_not_make_the_solution_is_not_offered_edit(browser, live_server, world):
    context, page = _open(browser, live_server, world, "architect")
    try:
        page.goto(live_server + "/solutions/%d" % world["solution"], timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(world["solution_name"]).first).to_be_visible()
        expect(page.get_by_test_id("record-edit")).to_have_count(0)
    finally:
        context.close()


def test_creator_and_org_admin_edit_the_solution_from_the_record(browser, live_server, world):
    for who in ("creator", "org_admin"):
        context, page = _open(browser, live_server, world, who)
        try:
            page.goto(live_server + "/solutions/%d" % world["solution"], timeout=PAGE_TIMEOUT)
            page.get_by_test_id("record-edit").click()
            page.wait_for_url("**/edit", timeout=PAGE_TIMEOUT)
            expect(page.locator('input[name="name"]')).to_have_value(world["solution_name"])
        finally:
            context.close()


def test_architect_places_a_technology_from_the_row_menu_and_it_persists(browser, live_server, world):
    context, page = _open(browser, live_server, world, "architect")
    try:
        page.goto(live_server + "/technology/radar/", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Actions for Offered actions broker %s" % world["suffix"]).click()
        page.get_by_test_id("radar-place-%d" % world["element"]).click()
        page.wait_for_url("**/place", timeout=PAGE_TIMEOUT)
        page.locator('select[name="ring"]').select_option("trial")
        page.locator('textarea[name="rationale"]').fill("Supported by the platform team")
        page.get_by_role("button", name="Save").click()
        page.goto(live_server + "/technology/radar/", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("radar-rationale-%d" % world["element"])).to_have_text(
            "Supported by the platform team")
    finally:
        context.close()


def test_platform_admin_sees_the_shared_catalogue_pages_and_org_admin_does_not(browser, live_server, world):
    context, page = _open(browser, live_server, world, "platform_admin")
    try:
        page.goto(live_server + "/admin/", timeout=PAGE_TIMEOUT)
        for needle in ("/admin/seed-management", "/framework-management/", "/framework-config/"):
            assert any(needle in link for link in _offered_links(page, live_server)), needle
        page.goto(live_server + "/applications/vendors/%d" % world["vendor"], timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Edit Vendor").click()
        page.wait_for_url("**/edit", timeout=PAGE_TIMEOUT)
    finally:
        context.close()
    context, page = _open(browser, live_server, world, "org_admin")
    try:
        page.goto(live_server + "/admin/", timeout=PAGE_TIMEOUT)
        links = _offered_links(page, live_server)
        for needle in ("/admin/seed-management", "/framework-management/", "/framework-config/"):
            assert not any(needle in link for link in links), needle
    finally:
        context.close()
