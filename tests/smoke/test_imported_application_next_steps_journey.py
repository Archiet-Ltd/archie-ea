"""An application a connector imported says what it still needs and offers the step that fills it.

The person: a CTO whose estate was read from GitHub. One imported application has no owner, no
capability, only the general type and the starting status; another imported application is complete;
a third was typed in by hand. The goals: the list and the application's own page name each missing
field for the imported application only, every name links to the step that fills it, a suggestion
appears only where the repository gives a source for it and names that source, and filling one field
shows it saved and takes it off the list of what is still needed.

A second organisation holds an imported application of its own, with the same group and a capability
of the same name; none of it may reach the first organisation's pages.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

CAPABILITY = "Order Handling"


def _import(org, name, repository, owners, *, owned=False, typed=None, status="planning", mapped=None):
    """An application the way a connector leaves it: created with the starting status, then finished."""
    from app import db
    from app.connectors import github
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    org_id = org["org_id"]
    row = ApplicationComponent(name=name, organization_id=org_id, lifecycle_status=status)
    db.session.add(row)
    db.session.commit()
    github._finish_application(
        org_id, row.id,
        {"repository": repository, "commit": "abc123def456", "evidence_file": "CODEOWNERS",
         "owners": owners, "group": CAPABILITY, "language": "Python"},
        org["user_ids"]["cto"])
    if typed:
        row.component_type = typed
    if owned:
        db.session.add(ApplicationOwner(user_id=org["user_ids"]["cto"], application_id=row.id,
                                        organization_id=org_id, ownership_type="primary"))
    if mapped is not None:
        db.session.add(ApplicationCapabilityMapping(
            application_component_id=row.id, business_capability_id=mapped.id,
            organization_id=org_id, coverage_percentage=80))
    db.session.commit()
    return row.id


def _seed(org, tag, gap_name, full_name, hand_name):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.user import Role, User

    with create_app("testing").app_context():
        org_id = org["org_id"]
        capability = BusinessCapability(name=CAPABILITY, level=1, organization_id=org_id)
        db.session.add(capability)
        db.session.commit()
        owner_email = "codeowner.%s@example.com" % tag
        ids = {"gap": _import(org, gap_name, "acme/gap-%s" % tag, [owner_email, "@platform-team"])}
        ids["full"] = _import(org, full_name, "acme/full-%s" % tag, [], owned=True, typed="service",
                              status="2.2 tactical", mapped=capability)
        hand = ApplicationComponent(name=hand_name, organization_id=org_id, lifecycle_status="planning")
        db.session.add(hand)
        db.session.commit()
        # The code owner joins the organisation after the read, so the import could not name them.
        person = User(email=owner_email, first_name="Ada", last_name="Ledger%s" % tag,
                      organization_id=org_id, enterprise_role="architect", confirmed=True)
        person.role = Role.query.filter_by(name="Architect").one()
        person.password = "x" * 12 + "Aa1!"
        db.session.add(person)
        db.session.commit()
        db.session.remove()
        return ids


@pytest.fixture(scope="module")
def estate(live_server):
    tag = uuid.uuid4().hex[:6]
    org = create_fresh_org("cto", org_admin=False)
    other = create_fresh_org("cto", org_admin=False)
    names = {"gap": "Gap Service %s" % tag, "full": "Full Service %s" % tag, "hand": "Typed By Hand %s" % tag,
             "rival": "Rival Service %s" % tag, "tag": tag}
    ids = _seed(org, tag, names["gap"], names["full"], names["hand"])
    _seed(other, uuid.uuid4().hex[:6], names["rival"], "Rival Full %s" % tag, "Rival Hand %s" % tag)
    return {"email": org["emails"]["cto"], "ids": ids, "person": "Ada Ledger%s" % tag, **names}


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
    shown = page.locator('[data-testid^="imported-needs"]:visible').first
    if shown.count():
        shown.scroll_into_view_if_needed()
    page.screenshot(path=os.path.join(folder, "%s-%s.png" % (os.environ.get("WALK_MODE", "wide-light"), name)))


def _needs(page, app_id):
    return page.locator('[data-testid="imported-needs-%d"]:visible' % app_id)


def _open(page, live_server, path):
    page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)


def test_list_names_what_an_imported_application_still_needs(cto, live_server, estate):
    _open(cto, live_server, "/applications/")
    needs = _needs(cto, estate["ids"]["gap"])
    expect(needs.first).to_be_visible(timeout=PAGE_TIMEOUT)
    text = needs.first.inner_text()
    for field in ("Owner", "Capability", "Type", "Status"):
        assert field in text, text
    assert _needs(cto, estate["ids"]["full"]).count() == 0
    hand = cto.locator('tr:visible, [data-testid^="mobile-card-"]:visible').filter(has_text=estate["hand"]).first
    expect(hand).to_be_visible(timeout=PAGE_TIMEOUT)
    assert "still needs" not in hand.inner_text().lower()
    assert estate["rival"] not in cto.content()
    _walk(cto, "list")


def test_application_page_offers_each_step_with_its_source(cto, live_server, estate):
    gap = estate["ids"]["gap"]
    _open(cto, live_server, "/applications/%d" % gap)
    card = cto.get_by_test_id("imported-needs-card")
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    text = card.inner_text()
    for field in ("Owner", "Capability", "Type", "Status"):
        assert field in text, text
    owner = card.get_by_test_id("need-owner")
    assert estate["person"] in owner.inner_text(), owner.inner_text()
    assert "CODEOWNERS" in owner.inner_text() and "acme/gap-%s" % estate["tag"] in owner.inner_text(), owner.inner_text()
    assert "@platform-team" in owner.inner_text(), owner.inner_text()
    capability = card.get_by_test_id("need-capability")
    assert CAPABILITY in capability.inner_text() and "acme/gap-%s" % estate["tag"] in capability.inner_text()
    for field in ("type", "status"):
        assert "suggest" not in card.get_by_test_id("need-%s" % field).inner_text().lower()
    assert estate["rival"] not in cto.content()
    _walk(cto, "page")


def test_every_step_lands_on_the_place_that_fills_it(cto, live_server, estate):
    gap = estate["ids"]["gap"]
    for field, target in (("type", "#application_type"), ("status", "#lifecycle_status")):
        _open(cto, live_server, "/applications/%d" % gap)
        cto.get_by_test_id("imported-needs-card").get_by_test_id("need-%s" % field).get_by_role("link").first.click()
        cto.wait_for_url(lambda url: "/edit" in url, timeout=PAGE_TIMEOUT)
        expect(cto.locator(target)).to_be_visible(timeout=PAGE_TIMEOUT)
    _open(cto, live_server, "/applications/%d" % gap)
    cto.get_by_test_id("imported-needs-card").get_by_test_id("need-owner").get_by_role("link").first.click()
    expect(cto.locator("[data-owner-picker] [data-owner-search]")).to_be_visible(timeout=PAGE_TIMEOUT)
    _open(cto, live_server, "/applications/%d" % gap)
    cto.get_by_test_id("imported-needs-card").get_by_test_id("need-capability").get_by_role("button").first.click()
    dialog = cto.locator("#unified-mapping-modal")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(dialog).to_contain_text(estate["gap"], timeout=PAGE_TIMEOUT)
    expect(dialog).to_contain_text(CAPABILITY, timeout=PAGE_TIMEOUT)


def test_filling_a_field_saves_it_and_takes_it_off_the_list(cto, live_server, estate):
    gap = estate["ids"]["gap"]
    _open(cto, live_server, "/applications/%d" % gap)
    card = cto.get_by_test_id("imported-needs-card")
    card.get_by_test_id("need-owner").get_by_role("link").first.click()
    box = cto.locator("[data-owner-picker] [data-owner-search]")
    box.fill("Ledger%s" % estate["tag"])
    cto.locator("[data-owner-picker] [data-owner-results] button", has_text=estate["person"]).first.click()
    with cto.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        with cto.expect_response(lambda r: r.request.method == "POST" and "/owners" in r.url) as saved:
            cto.locator("[data-owner-picker] [data-owner-save]").click()
    assert saved.value.status == 201
    card = cto.get_by_test_id("imported-needs-card")
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    assert card.get_by_test_id("need-owner").count() == 0, card.inner_text()
    for field in ("capability", "type", "status"):
        assert card.get_by_test_id("need-%s" % field).count() == 1
    assert estate["person"] in cto.locator("main").inner_text()
    _open(cto, live_server, "/applications/")
    row_needs = _needs(cto, gap).first.inner_text()
    assert "Owner" not in row_needs and "Capability" in row_needs, row_needs
    _walk(cto, "after-owner")


def test_choosing_the_status_saves_it_and_leaves_the_other_steps(cto, live_server, estate):
    gap = estate["ids"]["gap"]
    _open(cto, live_server, "/applications/%d" % gap)
    cto.get_by_test_id("imported-needs-card").get_by_test_id("need-status").get_by_role("link").first.click()
    cto.wait_for_url(lambda url: "/edit" in url, timeout=PAGE_TIMEOUT)
    cto.select_option("#lifecycle_status", "2.2 tactical")
    cto.get_by_role("button", name="Save").first.click()
    cto.wait_for_url(lambda url: "/edit" not in url, timeout=PAGE_TIMEOUT)
    _open(cto, live_server, "/applications/%d" % gap)
    card = cto.get_by_test_id("imported-needs-card")
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    assert card.get_by_test_id("need-status").count() == 0, card.inner_text()
    assert card.get_by_test_id("need-capability").count() == 1
    assert card.get_by_test_id("need-type").count() == 1
    assert "Tactical" in cto.locator("main").inner_text()
    _open(cto, live_server, "/applications/")
    row_needs = _needs(cto, gap).first.inner_text()
    assert "Status" not in row_needs and "Type" in row_needs, row_needs
