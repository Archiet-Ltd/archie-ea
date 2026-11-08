"""A CTO whose estate came in from GitHub sees where each application really came from.

The person: a CTO who connected GitHub and approved two repositories as applications. The goals:
the application list and the recommendations name the connector as the source of those two
(never "entered by the team"), a repository nobody has promoted is not listed as an application and
is not asked for an owner, and an application typed in by hand still reads as entered by hand.

The organisation holds linked records (an owned hand-entered application, two approved repositories
and one repository nobody has promoted); a second organisation holds a repository of its own whose
name must never appear.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org, *, approved, repository, typed, capability="Shared Capability"):
    from app import create_app, db
    from app.connectors import github
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.business_capabilities import BusinessCapability
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        org_id, owner_id = org["org_id"], org["user_ids"]["cto"]

        def application(name, owned=False):
            row = ApplicationComponent(name=name, organization_id=org_id, lifecycle_status="planning")
            db.session.add(row)
            db.session.commit()
            if owned:
                db.session.add(ApplicationOwner(user_id=owner_id, application_id=row.id,
                                                organization_id=org_id, ownership_type="primary"))
                db.session.commit()
            return row

        application(typed, owned=True)
        served = BusinessCapability(name=capability, level=1, organization_id=org_id)
        db.session.add(served)
        db.session.commit()
        for name in approved + [repository]:
            row = application(name)
            if name == repository:
                row.component_type = github.REPOSITORY_TYPE
            github._finish_application(
                org_id, row.id,
                {"repository": "acme/%s" % name.lower().replace(" ", "-"), "commit": "abc123def456",
                 "evidence_file": "Dockerfile", "owners": []},
                owner_id)
            db.session.commit()
            if name in approved:
                db.session.add(ApplicationCapabilityMapping(
                    application_component_id=row.id, business_capability_id=served.id,
                    organization_id=org_id, coverage_percentage=80))
                db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def estate(live_server):
    tag = uuid.uuid4().hex[:6]
    org = create_fresh_org("cto", org_admin=False)
    other = create_fresh_org("cto", org_admin=False)
    names = {
        "approved": ["Repo Alpha %s" % tag, "Repo Beta %s" % tag],
        "repository": "Shared Docs %s" % tag,
        "typed": "Typed Ledger %s" % tag,
        "rival": "Rival Repo %s" % tag,
    }
    names["capability"] = "Order Handling %s" % tag
    _seed(org, approved=names["approved"], repository=names["repository"], typed=names["typed"],
          capability=names["capability"])
    _seed(other, approved=[names["rival"], "Rival Second %s" % tag], repository="Rival Docs %s" % tag,
          typed="Rival Typed %s" % tag, capability="Rival Capability %s" % tag)
    return {"email": org["emails"]["cto"], **names}


@pytest.fixture()
def cto(browser, live_server, estate):
    narrow = os.environ.get("WALK_MODE") == "narrow-dark"
    context = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 390, "height": 900} if narrow else {"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, estate["email"])
    yield page
    context.close()


def _walk(page, name):
    folder = os.environ.get("WALK_DIR")
    if not folder:
        return
    os.makedirs(folder, exist_ok=True)
    if os.environ.get("WALK_MODE") == "narrow-dark":
        page.evaluate("() => document.documentElement.classList.add('dark')")
    source = page.locator('[data-testid="result-footer-source"]:visible').filter(has_text="connector").first
    if source.count():
        source.scroll_into_view_if_needed()
    page.screenshot(path=os.path.join(folder, "%s-%s.png" % (os.environ.get("WALK_MODE", "wide-light"), name)))


def _row(page, text):
    return page.locator('[data-footer-for]:visible, [data-testid^="mobile-card-"]:visible').filter(has_text=text)


def test_application_list_names_the_connector_as_source(cto, live_server, estate):
    cto.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    for name in estate["approved"]:
        source = _row(cto, name).get_by_test_id("result-footer-source")
        expect(source.first).to_be_visible(timeout=PAGE_TIMEOUT)
        text = source.first.inner_text()
        assert "synced by a connector" in text.lower(), text
        assert "GitHub: acme/" in text, text
        assert "abc123" not in text and "Dockerfile" not in text, text
        assert "entered by the team" not in text.lower(), text
    typed = _row(cto, estate["typed"]).get_by_test_id("result-footer-source").first
    assert "entered by the team" in typed.inner_text().lower(), typed.inner_text()
    assert estate["rival"] not in cto.content()
    _walk(cto, "applications")


def test_unpromoted_repository_is_not_an_application_and_asks_no_owner(cto, live_server, estate):
    cto.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    listed = cto.locator('tr:visible, [data-testid^="mobile-card-"]:visible').filter(has_text=estate["repository"])
    if os.environ.get("WALK_MODE") == "narrow-dark":
        # The phone card has no type line; the repository still shows with its connector source.
        expect(listed.first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "synced by a connector" in listed.first.inner_text().lower()
        # The card says what the record is, so it is not taken for an application.
        assert listed.first.get_by_text("Repository", exact=True).count() == 1
        for name in estate["approved"]:
            assert _row(cto, name).get_by_text("Repository", exact=True).count() == 0
    else:
        expect(listed.filter(has_text="Repository").first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert listed.filter(has_text="Application").count() == 0
    cto.goto(live_server + "/recommendations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    body = cto.locator("main").inner_text()
    assert "Name an owner for %s" % estate["repository"] not in body, body[:500]
    assert estate["rival"] not in cto.content()


def test_recommendations_name_the_connector_as_source(cto, live_server, estate):
    cto.goto(live_server + "/recommendations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    for name in estate["approved"]:
        row = cto.locator("li, tr, article, div").filter(has_text="Name an owner for %s" % name).last
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        source = row.get_by_test_id("result-footer-source").first
        assert "synced by a connector" in source.inner_text().lower(), source.inner_text()
        assert "entered by the team" not in source.inner_text().lower(), source.inner_text()
        link = row.get_by_role("link", name=name).first
        link.click()
        cto.wait_for_url(lambda url: "/applications/" in url, timeout=PAGE_TIMEOUT)
        expect(cto.locator("main")).to_contain_text(name, timeout=PAGE_TIMEOUT)
        cto.goto(live_server + "/recommendations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _walk(cto, "recommendations")


def test_opportunities_name_the_connector_as_source(cto, live_server, estate):
    cto.goto(live_server + "/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = cto.locator("tr:visible, li:visible, article:visible").filter(has_text=estate["capability"]).first
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    text = row.inner_text()
    for name in estate["approved"]:
        assert name in text, text
    source = row.get_by_test_id("result-footer-source").first.inner_text().lower()
    for name in estate["approved"]:
        assert "%s: synced by a connector from github: acme/" % name.lower() in source, source
    assert "abc123" not in source and "dockerfile" not in source, source
    # Only the capability, which a person did type in, is called hand-entered; the applications are not.
    by_hand = [line for line in cto.locator("main").inner_text().lower().splitlines() if "entered by hand" in line]
    for line in by_hand:
        assert not any(name.lower() in line for name in estate["approved"]), line
    assert estate["rival"] not in cto.content()
    _walk(cto, "opportunities")
