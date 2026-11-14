"""Journey, in a browser: a plainly worded change in the chat, and what the application page shows.

An architect asks "Change the description of <application> to: <text>" and is shown the
save card at once, with no refusal first. After Save the application's Overview shows that
description; an application with none says so and links to the form that adds one. A new
owner recorded from the page reads as assigned now, not at the time of the owner it replaced,
and a name that matches nobody in the workspace is answered in a sentence. A second
organisation's application of the same name is never touched.
"""

import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from . import fresh_org
from .chat_ask import open_chat, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ARCHITECT = "enterprise_architect"
NEW_DESCRIPTION = "Runs tenant billing and month-end close for finance."
SEED_OWNER = "Seeded Owner"

# Opt-in walk screenshots: off unless a directory is named.
WALK = os.environ.get("CHAT_WALK_DIR")


def _shot(page, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _seed(org_id, name):
    """An application with no description and an owner recorded two hours ago, and a second
    application with no description."""
    from app import create_app, db
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        row = ApplicationComponent(name=name, organization_id=org_id, description=None,
                                   deployment_status="development", lifecycle_status="operational")
        db.session.add(row)
        db.session.flush()
        long_ago = datetime.utcnow() - timedelta(hours=2)
        db.session.add(ApplicationOwner(
            application_id=row.id, organization_id=org_id, ownership_type="primary",
            person_name=SEED_OWNER, assigned_at=long_ago, created_at=long_ago))
        db.session.commit()
        out = {"app_id": row.id}
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
    for _ in range(2):
        org = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
        org.update(_seed(org["org_id"], name))
        org["name"] = name
        out.append(org)
    return out


def test_plain_request_is_shown_at_once_and_the_overview_shows_what_was_saved(page, live_server, orgs):
    mine, theirs = orgs
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, live_server, mine["emails"][ARCHITECT])

    # Before anything is saved the Overview says there is no description, and links to adding one.
    page.goto("%s/applications/%d" % (live_server, mine["app_id"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    facts = page.get_by_test_id("application-description-facts")
    expect(facts).to_contain_text("No description yet")
    _shot(page, "overview-no-description-1440")
    facts.get_by_role("link", name="Add one").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#edit-description")).to_be_visible()

    # The plain request gets the save card, never a refusal.
    open_chat(page, live_server)
    card = _send(page, "Change the description of %s to: %s" % (mine["name"], NEW_DESCRIPTION))
    expect(card.get_by_test_id("application-change-save")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(card).to_contain_text("Change the description of %s" % mine["name"])
    expect(card).to_contain_text(NEW_DESCRIPTION)
    assert "no direct write" not in card.inner_text().lower()
    assert _description(mine["app_id"]) is None
    _shot(page, "chat-plain-request-1440")

    card.get_by_test_id("application-change-save").click()
    expect(card).to_contain_text("Saved: the description of %s is changed" % mine["name"], timeout=PAGE_TIMEOUT)
    assert _description(mine["app_id"]) == NEW_DESCRIPTION
    card.get_by_test_id("application-change-open").click()
    page.wait_for_load_state("networkidle")
    assert re.search(r"/applications/%d" % mine["app_id"], page.url), page.url
    expect(page.get_by_test_id("application-description-facts")).to_contain_text(NEW_DESCRIPTION)
    _shot(page, "overview-description-1440")

    # The same name in the other organisation was not touched.
    assert _description(theirs["app_id"]) is None

    # Phone width, dark theme: the Overview still shows it.
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("document.documentElement.classList.add('dark')")
    expect(page.get_by_test_id("application-description-facts")).to_contain_text(NEW_DESCRIPTION)
    page.get_by_test_id("application-description").scroll_into_view_if_needed()
    _shot(page, "overview-description-dark-390")


def test_new_owner_is_assigned_now_and_a_name_nobody_matches_is_answered(page, live_server, orgs):
    mine, _theirs = orgs
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, live_server, mine["emails"][ARCHITECT])
    page.goto("%s/applications/%d" % (live_server, mine["app_id"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    owner_block = page.get_by_test_id("application-owner")
    expect(owner_block).to_contain_text(SEED_OWNER)

    # A name that matches nobody: a sentence, not silence.
    picker = page.locator("[data-owner-picker]").first
    picker.locator("[data-owner-search]").fill("Zzqx Nobody")
    expect(picker.locator("[data-owner-results]")).to_contain_text(
        "Nobody in this workspace matches 'Zzqx Nobody'. Record someone without an account below.",
        timeout=PAGE_TIMEOUT)
    _shot(page, "owner-picker-no-match-1440")

    # Record someone without an account: the page then reads that they were assigned now.
    picker.locator("[data-person-name]").fill("Newly Recorded")
    picker.locator("[data-person-save]").click()
    page.wait_for_load_state("networkidle")
    owner_block = page.get_by_test_id("application-owner")
    expect(owner_block).to_contain_text("Newly Recorded", timeout=PAGE_TIMEOUT)
    assigned = owner_block.locator("[data-owner-assigned]").first.inner_text()
    stamp = re.search(r"Assigned (\d{2} \w{3} \d{4}, \d{2}:\d{2})", assigned)
    assert stamp, assigned
    shown = datetime.strptime(stamp.group(1), "%d %b %Y, %H:%M")
    assert abs(datetime.utcnow() - shown) < timedelta(minutes=10), (
        "the owner reads as assigned %s, not just now" % assigned)
    _shot(page, "owner-assigned-now-1440")
