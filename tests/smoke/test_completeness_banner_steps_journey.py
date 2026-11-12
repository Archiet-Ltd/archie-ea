"""Every item the completeness banner names has a step that sets it.

The person: a startup CTO who opens an application that was imported with almost nothing recorded.
The goals: each item the banner and the fact sheet's missing list name links to a page that has the
field (or the card) that sets it, with that field focused; setting one field per step makes the
banner shrink by exactly that item; saving the full editor returns to the tab the person came from;
and an owner named only as text is not an owner: the owner gap stays open and offers "Record as
owner", while an owner record closes it.

A second organisation holds an application of its own; none of it may reach the first organisation.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# What the person looks for on the page a step lands on: the field's id and the label beside it.
EDITOR_FIELDS = {
    "Business domain": ("business_domain", "Business Domain"),
    "Business criticality": ("criticality", "Business Criticality"),
    "Lifecycle": ("lifecycle_status", "Lifecycle Status"),
    "Total cost of ownership": ("total_cost_of_ownership", "Total cost of ownership"),
    "Technology stack": ("technology_stack", "Technology Stack"),
    "Hosting / deployment": ("deployment_model", "Hosting model"),
    "Data classification": ("data_classification", "Data classification"),
    "Vendor": ("vendor_name", "Vendor"),
    "Description": ("description", "Description"),
}

# The value a person types or picks for each field, in the order the journey sets them.
VALUES = {
    "Business domain": ("fill", "Retail banking"),
    "Technology stack": ("fill", "Python, PostgreSQL"),
    "Hosting / deployment": ("select", "cloud"),
    "Data classification": ("select", "confidential"),
    "Total cost of ownership": ("fill", "120000"),
    "Vendor": ("fill", "Acme Software"),
    "Business criticality": ("select", "High"),
    "Lifecycle": ("select", "2.1 strategic"),
    "Description": ("fill", "Takes card payments for the shop."),
}


def _seed(org, name, *, legacy_owner=None):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    with create_app("testing").app_context():
        row = ApplicationComponent(name=name, organization_id=org["org_id"])
        for column in ("business_domain", "business_criticality", "lifecycle_status", "technology_stack",
                       "deployment_model", "data_classification", "vendor_name", "description",
                       "total_cost_of_ownership"):
            setattr(row, column, None)
        row.legacy_application_owner = legacy_owner
        db.session.add(row)
        db.session.commit()
        app_id = row.id
        db.session.remove()
        return app_id


@pytest.fixture(scope="module")
def estate(live_server):
    tag = uuid.uuid4().hex[:6]
    org = create_fresh_org("cto", org_admin=False)
    other = create_fresh_org("cto", org_admin=False)
    names = {"bare_name": "Payments Hub %s" % tag, "rival_name": "Rival Ledger %s" % tag}
    return {
        "email": org["emails"]["cto"],
        "bare": _seed(org, names["bare_name"], legacy_owner="Pat Legacy"),
        "tab": _seed(org, "Tab Service %s" % tag),
        "rival_id": _seed(other, names["rival_name"]),
        "org": org,
        "tag": tag,
        **names,
    }


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
    page.screenshot(path=os.path.join(folder, "%s-%s.png" % (os.environ.get("WALK_MODE", "wide-light"), name)))


def _open(page, live_server, path):
    page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)


def _steps(page):
    """The banner's items, by name, each with the address its link goes to."""
    rows = page.locator('#completeness-banner [data-testid="completeness-step"]')
    expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    return {r.get_attribute("data-step"): r.locator("a").first.get_attribute("href") for r in rows.all()}


def _focused_id(page):
    return page.evaluate("() => document.activeElement && document.activeElement.id")


def _set(page, label):
    field, _ = EDITOR_FIELDS[label]
    how, value = VALUES[label]
    if how == "fill":
        page.fill("#" + field, value)
    else:
        page.select_option("#" + field, value)


def test_every_banner_item_links_to_the_field_that_sets_it(cto, live_server, estate):
    _open(cto, live_server, "/applications/%d" % estate["bare"])
    steps = _steps(cto)
    assert set(steps) >= set(EDITOR_FIELDS) | {"Owner"}, sorted(steps)
    _walk(cto, "banner")
    for label, href in steps.items():
        assert href, label
        cto.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if label == "Owner":
            card = cto.locator("#ownership")
            expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
            expect(card.locator("[data-owner-search]")).to_be_visible()
            assert "Pat Legacy" in card.inner_text(), card.inner_text()
            continue
        field, text = EDITOR_FIELDS[label]
        expect(cto.locator("#" + field)).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(cto.locator("label[for=%s]" % field)).to_contain_text(text)
        cto.wait_for_function("id => document.activeElement && document.activeElement.id === id", arg=field,
                              timeout=PAGE_TIMEOUT)
        assert _focused_id(cto) == field, label


def test_the_fact_sheet_list_links_to_the_same_places(cto, live_server, estate):
    _open(cto, live_server, "/applications/%d/fact-sheet" % estate["bare"])
    strip = cto.locator("#missing-record")
    expect(strip).to_be_visible(timeout=PAGE_TIMEOUT)
    links = {r.get_attribute("data-step"): r.locator("a").first.get_attribute("href")
             for r in strip.locator('[data-testid="completeness-step"]').all()}
    assert set(links) >= set(EDITOR_FIELDS) | {"Owner"}, sorted(links)
    for label, (field, _) in EDITOR_FIELDS.items():
        cto.goto(live_server + links[label], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(cto.locator("#" + field)).to_be_visible(timeout=PAGE_TIMEOUT)
    _walk(cto, "fact-sheet")


def test_setting_one_field_per_step_shrinks_the_banner(cto, live_server, estate):
    page_path = "/applications/%d" % estate["bare"]
    _open(cto, live_server, page_path)
    steps = _steps(cto)
    for label in ("Business domain", "Technology stack", "Hosting / deployment", "Data classification"):
        before = set(steps)
        assert label in before
        cto.goto(live_server + steps[label], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _set(cto, label)
        cto.get_by_role("button", name="Save").first.click()
        cto.wait_for_url(lambda url: "/edit" not in url, timeout=PAGE_TIMEOUT)
        _open(cto, live_server, page_path)
        steps = _steps(cto)
        assert set(steps) == before - {label}, (label, sorted(before - set(steps)), sorted(set(steps) - before))
    text = cto.locator("#completeness-banner").inner_text()
    assert "Retail banking" not in text and "Hosting" not in text
    _walk(cto, "banner-after")
    # The saved values are on the editor the person reopens.
    cto.goto(live_server + "/applications/%d/edit" % estate["bare"], wait_until="domcontentloaded",
             timeout=PAGE_TIMEOUT)
    assert cto.input_value("#business_domain") == "Retail banking"
    assert cto.input_value("#deployment_model") == "cloud"
    assert cto.input_value("#data_classification") == "confidential"
    assert cto.input_value("#technology_stack") == "Python, PostgreSQL"


def test_the_rest_are_set_from_the_fact_sheet_list_and_leave_it(cto, live_server, estate):
    sheet = "/applications/%d/fact-sheet" % estate["bare"]
    _open(cto, live_server, sheet)
    strip = cto.locator("#missing-record")
    expect(strip).to_be_visible(timeout=PAGE_TIMEOUT)
    for label in ("Total cost of ownership", "Vendor", "Business criticality", "Lifecycle", "Description"):
        links = {r.get_attribute("data-step"): r.locator("a").first.get_attribute("href")
                 for r in cto.locator('#missing-record [data-testid="completeness-step"]').all()}
        assert label in links, (label, sorted(links))
        cto.goto(live_server + links[label], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _set(cto, label)
        cto.get_by_role("button", name="Save").first.click()
        cto.wait_for_url(lambda url: "/edit" not in url, timeout=PAGE_TIMEOUT)
        _open(cto, live_server, sheet)
        remaining = {r.get_attribute("data-step")
                     for r in cto.locator('#missing-record [data-testid="completeness-step"]').all()}
        assert label not in remaining, label
    # Only the owner is left, and it is the one a person sets on the Ownership card.
    left = [r.get_attribute("data-step")
            for r in cto.locator('#missing-record [data-testid="completeness-step"]').all()]
    assert left == ["Owner"], left
    assert "120,000" in cto.locator("main").inner_text() or "120000" in cto.locator("main").inner_text()


def test_saving_the_editor_returns_to_the_tab_the_person_came_from(cto, live_server, estate):
    path = "/applications/%d" % estate["tab"]
    _open(cto, live_server, path + "?tab=technology")
    cto.get_by_role("link", name="Edit Technology Details").first.click()
    cto.wait_for_url(lambda url: "/edit" in url, timeout=PAGE_TIMEOUT)
    cto.fill("#technology_stack", "Go, Redis")
    cto.get_by_role("button", name="Save").first.click()
    cto.wait_for_url(lambda url: "/edit" not in url, timeout=PAGE_TIMEOUT)
    assert "tab=technology" in cto.url, cto.url
    # A step in the banner, followed from the Cost tab, returns to the Cost tab.
    _open(cto, live_server, path + "?tab=cost")
    steps = _steps(cto)
    cto.goto(live_server + steps["Business domain"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    cto.fill("#business_domain", "Logistics")
    cto.get_by_role("button", name="Save").first.click()
    cto.wait_for_url(lambda url: "/edit" not in url, timeout=PAGE_TIMEOUT)
    assert "tab=cost" in cto.url, cto.url
    # From no tab, it lands where it always did.
    _open(cto, live_server, "/applications/%d/edit" % estate["tab"])
    cto.get_by_role("button", name="Save").first.click()
    cto.wait_for_url(lambda url: "/edit" not in url, timeout=PAGE_TIMEOUT)
    assert "tab=architecture" in cto.url, cto.url


def test_another_organisations_application_stays_out_of_reach(cto, live_server, estate):
    _open(cto, live_server, "/applications/%d" % estate["bare"])
    assert estate["rival_name"] not in cto.content()
    for path in ("/applications/%d", "/applications/%d/edit", "/applications/%d/fact-sheet"):
        response = cto.goto(live_server + path % estate["rival_id"], wait_until="domcontentloaded",
                            timeout=PAGE_TIMEOUT)
        assert response.status in (403, 404), (path, response.status)
        assert estate["rival_name"] not in cto.content()


def _import(org, name, owners_text=None, record=False):
    """An application the way a connector leaves it, owned by text only or by a record."""
    from app import create_app, db
    from app.connectors import github
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    with create_app("testing").app_context():
        row = ApplicationComponent(name=name, organization_id=org["org_id"], lifecycle_status="planning")
        db.session.add(row)
        db.session.commit()
        github._finish_application(
            org["org_id"], row.id,
            {"repository": "acme/%s" % name.replace(" ", "-").lower(), "commit": "abc123def456",
             "evidence_file": "CODEOWNERS", "owners": [], "group": "Orders", "language": "Python"},
            org["user_ids"]["cto"])
        row.legacy_application_owner = owners_text
        if record:
            db.session.add(ApplicationOwner(user_id=org["user_ids"]["cto"], application_id=row.id,
                                            organization_id=org["org_id"], ownership_type="primary"))
        db.session.commit()
        app_id = row.id
        db.session.remove()
        return app_id


def test_an_owner_named_only_as_text_is_not_an_owner(cto, live_server, estate):
    text_only = _import(estate["org"], "Text Owned %s" % estate["tag"], owners_text="Sam Textowner")
    recorded = _import(estate["org"], "Record Owned %s" % estate["tag"], owners_text="Sam Textowner", record=True)

    _open(cto, live_server, "/applications/%d" % text_only)
    card = cto.get_by_test_id("imported-needs-card")
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    owner = card.get_by_test_id("need-owner")
    assert "Record as owner" in owner.inner_text(), owner.inner_text()
    assert "Sam Textowner" in owner.inner_text() and "text only" in owner.inner_text()
    _walk(cto, "owner-gap")
    owner.get_by_role("link", name="Record as owner").click()
    expect(cto.locator("#ownership [data-owner-search]")).to_be_visible(timeout=PAGE_TIMEOUT)

    _open(cto, live_server, "/applications/%d" % recorded)
    card = cto.get_by_test_id("imported-needs-card")
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    assert card.get_by_test_id("need-owner").count() == 0, card.inner_text()
