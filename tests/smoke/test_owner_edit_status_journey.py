"""An application owner opens their edit page on the status stored, and saving leaves it.

The application manager owns an application stored as `staging`, another stored as
`Production` (an older release stored it with a capital) and a third stored with a
word that is not on the list. Each opens with that status chosen; saving after
changing only the description keeps the stored status; the word off the list is
offered as itself. A second organisation's owner cannot open the page by id.
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from . import fresh_org
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PERSONA = "application_manager"


def _shot(page, tmp_path, name, dark=False):
    folder = Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    page.evaluate("d => document.documentElement.classList.toggle('dark', d)", dark)
    width = page.viewport_size["width"]
    page.screenshot(path=str(folder / f"{name}-{width}-{'dark' if dark else 'light'}.png"),
                    full_page=True)
    page.evaluate("document.documentElement.classList.remove('dark')")


def _seed(org, word):
    from app import create_app, db
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        row = ApplicationComponent(
            name="Owned %s %s" % (word.strip().lower(), uuid.uuid4().hex[:6]),
            organization_id=org["org_id"], description="Old description")
        db.session.add(row)
        db.session.flush()
        # Stored as an older release stored it: the model refuses these words now.
        db.session.execute(
            db.text("UPDATE application_components SET deployment_status = :w WHERE id = :i"),
            {"w": word, "i": row.id})
        db.session.add(ApplicationOwner(
            application_id=row.id, user_id=org["user_ids"][PERSONA],
            organization_id=org["org_id"]))
        db.session.commit()
        ident = row.id
        db.session.remove()
    return ident


def _shots(page, tmp_path, name):
    """The screen at 1440 wide in the light theme and at 390 wide in the dark one."""
    page.set_viewport_size({"width": 1440, "height": 900})
    _shot(page, tmp_path, name)
    page.set_viewport_size({"width": 390, "height": 844})
    _shot(page, tmp_path, name, dark=True)
    page.set_viewport_size({"width": 1440, "height": 900})


def _stored(app_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        row = db.session.get(ApplicationComponent, app_id)
        out = (row.deployment_status, row.description)
        db.session.remove()
    return out


@pytest.fixture
def orgs():
    return [fresh_org.create_fresh_org(PERSONA, org_admin=False) for _ in range(2)]


@pytest.mark.parametrize("word, chosen, label", [
    ("staging", "staging", "Staging"),
    ("Production", "production", "Production"),
    ("Mystery", "Mystery", "Mystery"),
])
def test_the_owner_edit_page_opens_on_the_stored_status_and_a_save_keeps_it(
    page, live_server, orgs, tmp_path, word, chosen, label
):
    org_a, org_b = orgs
    app_id = _seed(org_a, word)
    other_id = _seed(org_b, "staging")
    url = "%s/my-applications/app/%d/edit" % (live_server, app_id)
    fresh_org.sign_in(page, live_server, org_a["emails"][PERSONA])
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    select = page.locator("#deployment_status")
    expect(select).to_have_value(chosen)
    assert label in select.locator("option:checked").inner_text()
    options = [o.strip() for o in select.locator("option").all_inner_texts()]
    assert options[:2] == ["Not set", "Design"] and "Decommissioned" in options, options
    _shots(page, tmp_path, "owner-edit-before-%s" % chosen)

    page.locator("textarea[name=description]").fill("Changed by its owner")
    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name=re.compile("Save")).first.click()
    assert "/edit" not in page.url, page.url
    assert "Application updated." in page.locator("body").inner_text()
    status, description = _stored(app_id)
    assert description == "Changed by its owner"
    assert status is not None and status.lower() == chosen.lower(), status
    page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    expect(page.locator("#deployment_status")).to_have_value(chosen)
    _shots(page, tmp_path, "owner-edit-after-%s" % chosen)

    # The other organisation's application is not reachable by id from this one.
    response = page.goto("%s/my-applications/app/%d/edit" % (live_server, other_id),
                         wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert response.status == 404, response.status
