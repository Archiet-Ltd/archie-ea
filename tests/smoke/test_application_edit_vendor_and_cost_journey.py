"""The application editor records the vendor once, and shows cost only to people who may see it.

The people: an Administrator, and a solution architect who may edit applications but holds no
spend authority.
The goals: a vendor typed on the editor is the same supplier on the application page, the Vendors
page and the supplier's own page (no stale supplier left behind); the Total cost of ownership field
is on the editor and the banner's link only for the Administrator; and a cost posted by the
solution architect is not saved.

A second organisation holds a system of its own naming the same supplier; none of it may reach the
first organisation's pages.
"""

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org, name, *, vendor=None, cost=None):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.services import vendor_register

    with create_app("testing").app_context():
        row = ApplicationComponent(name=name, organization_id=org["org_id"], lifecycle_status="operational")
        row.total_cost_of_ownership = cost
        db.session.add(row)
        db.session.flush()
        if vendor:
            vendor_register.set_application_vendor(row, vendor_register.resolve_vendor(vendor, org["org_id"]))
        db.session.commit()
        app_id = row.id
        db.session.remove()
        return app_id


@pytest.fixture(scope="module")
def estate(live_server):
    tag = uuid.uuid4().hex[:6]
    org = create_fresh_org("platform_admin", org_admin=True, extra_roles=("solution_architect",))
    other = create_fresh_org("platform_admin", org_admin=True)
    old = "Old Supplier %s" % tag
    return {
        "org": org,
        "tag": tag,
        "admin": org["emails"]["platform_admin"],
        "limited": org["emails"]["solution_architect"],
        "old": old,
        "new": "Fresh Supplier %s" % tag,
        "limited_vendor": "Limited Supplier %s" % tag,
        "costed_name": "Costed System %s" % tag,
        "costed": _seed(org, "Costed System %s" % tag, vendor=old, cost=88000),
        "bare_name": "Bare System %s" % tag,
        "bare": _seed(org, "Bare System %s" % tag),
        "nocost_name": "No Cost System %s" % tag,
        "nocost": _seed(org, "No Cost System %s" % tag),
        "rival_name": "Rival System %s" % tag,
        "rival": _seed(other, "Rival System %s" % tag, vendor="Fresh Supplier %s" % tag, cost=5),
    }


def _person(browser, live_server, email):
    narrow = os.environ.get("WALK_MODE") == "narrow-dark"
    context = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 390, "height": 900} if narrow else {"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, email)
    return context, page


@pytest.fixture()
def admin(browser, live_server, estate):
    context, page = _person(browser, live_server, estate["admin"])
    yield page
    context.close()


@pytest.fixture()
def limited(browser, live_server, estate):
    context, page = _person(browser, live_server, estate["limited"])
    yield page
    context.close()


def _walk(page, name):
    folder = os.environ.get("WALK_DIR")
    if not folder:
        return
    os.makedirs(folder, exist_ok=True)
    if os.environ.get("WALK_MODE") == "narrow-dark":
        page.evaluate("() => document.documentElement.classList.add('dark')")
    page.screenshot(path=os.path.join(folder, "%s-%s.png" % (os.environ.get("WALK_MODE", "wide-light"), name)),
                    full_page=True)


def _open(page, live_server, path):
    return page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)


def _save(page):
    page.get_by_role("button", name="Save").first.click()
    page.wait_for_url(lambda url: "/edit" not in url, timeout=PAGE_TIMEOUT)


def _vendor_everywhere(page, live_server, estate, app_id, app_name, vendor, gone):
    """The vendor is the same supplier on the application, the Vendors page and the supplier's page."""
    _open(page, live_server, "/applications/%d?tab=vendors" % app_id)
    row = page.get_by_test_id("vendor-organisation-row")
    expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
    expect(row).to_contain_text(vendor)
    assert gone not in page.locator("main").inner_text()

    _open(page, live_server, "/applications/vendors")
    rows = page.get_by_test_id("vendor-row")
    expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    mine = rows.filter(has_text=vendor)
    expect(mine).to_have_count(1)
    expect(mine).to_contain_text("1 system")
    assert estate["rival_name"] not in page.locator("main").inner_text()

    mine.get_by_role("link", name="1 system").click()
    expect(page).to_have_url(re.compile(r"/applications/vendors/\d+"), timeout=PAGE_TIMEOUT)
    systems = page.locator("#vendor-systems")
    expect(systems).to_contain_text(app_name, timeout=PAGE_TIMEOUT)
    assert estate["rival_name"] not in systems.inner_text()
    systems.locator("ul").first.get_by_role("link", name=app_name).click()
    expect(page.get_by_role("heading", name=app_name).first).to_be_visible(timeout=PAGE_TIMEOUT)


def test_a_vendor_saved_on_the_editor_is_the_same_supplier_everywhere(admin, live_server, estate):
    _open(admin, live_server, "/applications/%d/edit" % estate["costed"])
    assert admin.input_value("#vendor_name") == estate["old"]
    expect(admin.locator("#total_cost_of_ownership")).to_be_visible(timeout=PAGE_TIMEOUT)
    assert float(admin.input_value("#total_cost_of_ownership")) == 88000
    admin.locator("#vendor_name").scroll_into_view_if_needed()
    _walk(admin, "editor-administrator")
    # Typed the way a person types it: lower case and loose spacing, for a supplier the register already holds.
    admin.fill("#vendor_name", "  %s  " % estate["new"].lower().replace(" ", "   "))
    admin.fill("#total_cost_of_ownership", "91000")
    _save(admin)
    _open(admin, live_server, "/applications/%d/edit" % estate["costed"])
    assert admin.input_value("#vendor_name") == estate["new"]
    _vendor_everywhere(admin, live_server, estate, estate["costed"], estate["costed_name"], estate["new"],
                       estate["old"])
    _walk(admin, "supplier")

    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    with create_app("testing").app_context():
        row = db.session.get(ApplicationComponent, estate["costed"])
        assert row.vendor_name == estate["new"]
        assert row.vendor_organization_id is not None
        assert row.total_cost_of_ownership == 91000
        db.session.remove()


def test_the_administrator_sees_the_cost_step_and_it_saves(admin, live_server, estate):
    _open(admin, live_server, "/applications/%d" % estate["bare"])
    step = admin.locator('#completeness-banner [data-testid="completeness-step"][data-step="Total cost of ownership"]')
    expect(step).to_be_visible(timeout=PAGE_TIMEOUT)
    step.get_by_role("link", name="Total cost of ownership").click()
    field = admin.locator("#total_cost_of_ownership")
    expect(field).to_be_visible(timeout=PAGE_TIMEOUT)
    field.fill("42000")
    _save(admin)
    _open(admin, live_server, "/applications/%d/edit" % estate["bare"])
    assert float(admin.input_value("#total_cost_of_ownership")) == 42000


def test_a_person_without_spend_authority_sees_no_cost_and_cannot_save_one(limited, live_server, estate):
    # The editor has no cost field and none of the recorded cost is on the page.
    _open(limited, live_server, "/applications/%d/edit" % estate["costed"])
    expect(limited.locator("#vendor_name")).to_be_visible(timeout=PAGE_TIMEOUT)
    assert limited.locator("#total_cost_of_ownership").count() == 0
    content = limited.content()
    assert "Total cost of ownership" not in limited.locator("form").first.inner_text()
    assert "88000" not in content and "91000" not in content
    _walk(limited, "editor-no-spend-authority")

    # The banner names the cost item but gives it no link and no place to set it.
    _open(limited, live_server, "/applications/%d" % estate["nocost"])
    step = limited.locator('#completeness-banner [data-testid="completeness-step"][data-step="Total cost of ownership"]')
    expect(step).to_be_visible(timeout=PAGE_TIMEOUT)
    assert step.locator("a").count() == 0, step.inner_html()
    assert "set in" not in step.inner_text()
    other = limited.locator('#completeness-banner [data-testid="completeness-step"][data-step="Business domain"]')
    assert other.locator("a").count() == 1
    _walk(limited, "banner-no-spend-authority")

    # A cost posted by hand is ignored; the vendor typed alongside it is saved as one supplier.
    _open(limited, live_server, "/applications/%d/edit" % estate["nocost"])
    limited.evaluate("""() => {
        const form = document.querySelector('form[method="post"], form[method="POST"]') || document.querySelector('form');
        const input = document.createElement('input');
        input.type = 'hidden'; input.name = 'total_cost_of_ownership'; input.value = '999999';
        form.appendChild(input);
    }""")
    limited.fill("#vendor_name", estate["limited_vendor"])
    _save(limited)
    _vendor_everywhere(limited, live_server, estate, estate["nocost"], estate["nocost_name"], estate["limited_vendor"],
                       estate["old"])

    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    with create_app("testing").app_context():
        row = db.session.get(ApplicationComponent, estate["nocost"])
        assert row.total_cost_of_ownership is None, row.total_cost_of_ownership
        assert row.vendor_name == estate["limited_vendor"] and row.vendor_organization_id is not None
        db.session.remove()


def test_another_organisations_system_stays_out_of_reach(admin, live_server, estate):
    _open(admin, live_server, "/applications/vendors")
    assert estate["rival_name"] not in admin.content()
    for path in ("/applications/%d", "/applications/%d/edit"):
        response = _open(admin, live_server, path % estate["rival"])
        assert response.status in (403, 404), (path, response.status)
        assert estate["rival_name"] not in admin.content()
