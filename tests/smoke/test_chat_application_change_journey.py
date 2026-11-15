"""Journey, in a browser: correcting an application from the AI chat.

An architect types "set <application>'s description to ..." in the chat, sees what
will change, presses Save and finds the application page showing it. They ask
which capabilities the application supports and get the same ones the page lists,
each a link that opens its own page. They record an owner who has no account and
the chat no longer lists the application as having no owner. A viewer asking for
the same change is refused in words and the page keeps its description. A second
organisation's application of the same name is never touched.
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from . import fresh_org
from .chat_ask import open_chat, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ARCHITECT = "enterprise_architect"
NEW_DESCRIPTION = "Runs tenant billing and month-end close for finance."

# Opt-in walk screenshots: off unless a directory is named.
WALK = os.environ.get("CHAT_WALK_DIR")


def _shot(page, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _seed(org_id, name, with_viewer=False):
    """An application with two mapped capabilities (one in each mapping store), a
    second application with no owner, and optionally a viewer in the organisation."""
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        row = ApplicationComponent(
            name=name, organization_id=org_id, description="Old description",
            deployment_status="development", lifecycle_status="operational",
        )
        orphan = ApplicationComponent(
            name="Orphan %s" % uuid.uuid4().hex[:5], organization_id=org_id,
            description="Nobody owns this", deployment_status="production",
        )
        db.session.add_all([row, orphan])
        db.session.flush()
        payments = BusinessCapability(name="Take payments %s" % uuid.uuid4().hex[:4], organization_id=org_id)
        onboarding = BusinessCapability(name="Onboard customers %s" % uuid.uuid4().hex[:4], organization_id=org_id)
        db.session.add_all([payments, onboarding])
        db.session.flush()
        db.session.add(ApplicationCapabilityMapping(
            application_component_id=row.id, business_capability_id=payments.id,
            organization_id=org_id, support_level="primary"))
        twin = UnifiedCapability.query.filter_by(
            organization_id=org_id, source_table="business_capability", source_id=str(onboarding.id)).one()
        db.session.add(UnifiedApplicationCapabilityMapping(
            application_component_id=row.id, unified_capability_id=twin.id))
        out = {"app_id": row.id, "orphan": orphan.name, "capabilities": [payments.name, onboarding.name]}
        if with_viewer:
            viewer = User(email="viewer.%s@example.com" % uuid.uuid4().hex[:8], first_name="Vera",
                          last_name="Viewer", organization_id=org_id, confirmed=True,
                          enterprise_role=ARCHITECT)
            viewer.role = Role.query.filter_by(name="Viewer").one()
            viewer.password = "SmokeJourney!2026"
            db.session.add(viewer)
            out["viewer_email"] = viewer.email
        db.session.commit()
        db.session.remove()
    return out


def _description(app_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        value = db.session.get(ApplicationComponent, app_id).description
        db.session.remove()
    return value


def _send(page, text):
    """Type *text* in the chat the way a person does and return the newest answer card."""
    before = page.get_by_test_id("estate-answer").count()
    box = page.locator("#user-input")
    box.fill(text)
    box.press("Enter")
    answer = page.get_by_test_id("estate-answer")
    expect(answer).to_have_count(before + 1, timeout=PAGE_TIMEOUT)
    return answer.nth(before)


@pytest.fixture
def orgs():
    name = "Ifiok %s" % uuid.uuid4().hex[:5]
    out = []
    for index in range(2):
        org = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
        org.update(_seed(org["org_id"], name, with_viewer=(index == 0)))
        org["name"] = name
        out.append(org)
    return out


def test_architect_changes_an_application_from_the_chat_and_the_page_shows_it(page, live_server, orgs):
    mine, theirs = orgs
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, live_server, mine["emails"][ARCHITECT])
    open_chat(page, live_server)

    # Asked for, shown, not yet saved.
    preview = _send(page, "set %s's description to %s" % (mine["name"], NEW_DESCRIPTION))
    expect(preview).to_contain_text("Change the description of %s" % mine["name"])
    expect(preview).to_contain_text("Old description")
    expect(preview).to_contain_text(NEW_DESCRIPTION)
    expect(preview).to_contain_text("Nothing is saved until you press Save")
    assert _description(mine["app_id"]) == "Old description"
    _shot(page, "chat-change-preview-1440")

    # Saved, said so, with the link to the page.
    preview.get_by_test_id("application-change-save").click()
    expect(preview).to_contain_text("Saved: the description of %s is changed" % mine["name"], timeout=PAGE_TIMEOUT)
    expect(preview).to_contain_text(NEW_DESCRIPTION)
    assert _description(mine["app_id"]) == NEW_DESCRIPTION
    _shot(page, "chat-change-saved-1440")
    preview.get_by_test_id("application-change-open").click()
    page.wait_for_load_state("networkidle")
    assert re.search(r"/applications/%d" % mine["app_id"], page.url), page.url
    expect(page.locator("main")).to_contain_text(NEW_DESCRIPTION)
    _shot(page, "application-page-after-chat-change-1440")

    # The same name in the other organisation was not touched.
    assert _description(theirs["app_id"]) == "Old description"

    # Which capabilities does it support: the same ones the page lists, each a working link.
    open_chat(page, live_server)
    answer = _send(page, "Which capabilities does %s support?" % mine["name"])
    for capability in mine["capabilities"]:
        expect(answer).to_contain_text(capability)
    links = answer.get_by_test_id("catalogue-open")
    expect(links).to_have_count(2)
    _shot(page, "chat-capabilities-1440")
    hrefs = [links.nth(i).get_attribute("href") for i in range(2)]
    names = [links.nth(i).inner_text().strip() for i in range(2)]
    for href, name in zip(hrefs, names):
        page.goto(live_server + href, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text(name)
    page.goto("%s/applications/%d?tab=capabilities" % (live_server, mine["app_id"]),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)
    for capability in mine["capabilities"]:
        expect(page.locator("main")).to_contain_text(capability)
    expect(page.locator("main")).to_contain_text(re.compile(r"Mapped Capabilities\s*2"))

    # An owner outside the workspace: recorded from the chat, shown on the page, and not
    # listed again as an application with no owner.
    open_chat(page, live_server)
    owner = _send(page, "set %s's business owner to Ifiok Etim" % mine["name"])
    owner.get_by_test_id("application-change-save").click()
    expect(owner).to_contain_text("Saved: the business owner", timeout=PAGE_TIMEOUT)
    page.goto("%s/applications/%d" % (live_server, mine["app_id"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    body = page.locator("main")
    expect(body).to_contain_text("Ifiok Etim")
    expect(body).to_contain_text("No account in this workspace")
    _shot(page, "application-owner-without-account-1440")
    open_chat(page, live_server)
    missing = _send(page, "Which applications have no owner?")
    expect(missing).to_contain_text(mine["orphan"])
    assert mine["name"] not in missing.inner_text(), "an application with a recorded owner is listed as ownerless"

    # Phone width, dark theme: the preview still reads and its buttons are reachable.
    page.set_viewport_size({"width": 390, "height": 844})
    open_chat(page, live_server)
    page.evaluate("document.documentElement.classList.add('dark')")
    narrow = _send(page, "set %s's version to 3.1" % mine["name"])
    narrow.get_by_test_id("application-change-save").scroll_into_view_if_needed()
    expect(narrow.get_by_test_id("application-change-save")).to_be_visible()
    expect(narrow.get_by_test_id("application-change-cancel")).to_be_visible()
    _shot(page, "chat-change-preview-dark-390")
    narrow.get_by_test_id("application-change-cancel").click()
    expect(narrow).to_contain_text("Cancelled. Nothing was changed.")


def test_a_viewer_asking_for_the_same_change_is_refused_in_words(page, live_server, orgs):
    mine, _theirs = orgs
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, live_server, mine["viewer_email"])
    open_chat(page, live_server)
    refused = _send(page, "set %s's description to %s" % (mine["name"], NEW_DESCRIPTION))
    expect(refused).to_contain_text("I have not changed anything")
    expect(refused).to_contain_text("can look at applications but not edit them")
    assert refused.get_by_test_id("application-change-save").count() == 0
    _shot(page, "chat-change-viewer-refused-1440")
    assert _description(mine["app_id"]) == "Old description"
    page.goto("%s/applications/%d" % (live_server, mine["app_id"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).to_contain_text("Old description")
