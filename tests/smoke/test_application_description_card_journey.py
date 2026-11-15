"""The Overview's description card names itself once and shows the description.

An application manager opens an application in their organisation that has a description.
The card is headed "Description" and the word is not repeated as a field label inside it,
at 1440 wide and at 390 wide. The text is on the page, and an application in another
organisation with a different description never appears.
"""

import os
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


def _seed(org, description):
    from app import create_app, db
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        row = ApplicationComponent(
            name="Described %s" % uuid.uuid4().hex[:6], organization_id=org["org_id"],
            description=description)
        db.session.add(row)
        db.session.flush()
        db.session.add(ApplicationOwner(
            application_id=row.id, organization_id=org["org_id"],
            ownership_type="primary", person_name="Ada Byron", person_role="Head of Billing"))
        db.session.commit()
        ident = row.id
        db.session.remove()
    return ident


@pytest.fixture
def orgs():
    return [fresh_org.create_fresh_org(PERSONA, org_admin=False) for _ in range(2)]


@pytest.mark.parametrize("width, height, dark", [(1440, 900, False), (390, 844, True)])
def test_the_description_card_says_description_once(page, live_server, orgs, tmp_path, width, height, dark):
    org_a, org_b = orgs
    app_id = _seed(org_a, "Runs tenant billing and month-end close.")
    _seed(org_b, "Other organisation secret text.")
    fresh_org.sign_in(page, live_server, org_a["emails"][PERSONA])
    page.set_viewport_size({"width": width, "height": height})
    page.goto("%s/applications/%d" % (live_server, app_id), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    card = page.get_by_test_id("application-description")
    expect(card).to_contain_text("Runs tenant billing and month-end close.")
    assert card.inner_text().lower().count("description") == 1, card.inner_text()
    assert "Other organisation secret text" not in page.locator("body").inner_text()
    _shot(page, tmp_path, "overview-description", dark=dark)
