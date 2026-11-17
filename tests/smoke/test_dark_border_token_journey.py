"""In dark mode a bare border draws in the border token, not Tailwind's light grey.

The goal, as an Enterprise Architect working in dark mode: "I see no bright lines
that are not part of the design". Runs against the demonstration organisation's
own seed, on the overview, a record's page and that record's Edit dialog.
"""
import os
import pathlib
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("DARK_BORDER_SHOTS", "/tmp/dark-border-shots"))
DEMO_USER = "demo@lantern-quay.example.com"
ARCHITECT = "dark.border.architect@example.com"
PREFLIGHT_GREY = "rgb(229, 231, 235)"
RECORD = "Sensor Calibration"

# Every visible element under `root` with a border drawn on some side, with that side's colour.
_BORDERED = """(root) => {
  const out = [];
  for (const el of root.querySelectorAll('*')) {
    const box = el.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none') continue;
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
      if (parseFloat(s['border' + side + 'Width']) > 0 && s['border' + side + 'Style'] !== 'none') {
        out.push({side, colour: s['border' + side + 'Color'], text: (el.innerText || el.className || el.tagName).trim().slice(0, 60)});
      }
    }
  }
  return out;
}"""


@pytest.fixture(scope="module")
def demo_architect(live_server):
    """The demonstration organisation, seeded by the product's own seed, and an architect in it."""
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.user import Role, User

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    app = create_app("testing")
    try:
        with app.app_context():
            seed_demo_company()
            org_id = User.query.filter_by(email=DEMO_USER).one().organization_id
            if not User.query.filter_by(email=ARCHITECT).first():
                Role.insert_roles()
                user = User(
                    email=ARCHITECT, first_name="Dark", last_name="Architect", organization_id=org_id,
                    enterprise_role="enterprise_architect", confirmed=True,
                    role=Role.query.filter_by(name="Viewer").first(),
                )
                user.password = PASSWORD
                db.session.add(user)
                db.session.commit()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return ARCHITECT


def _assert_signed_in_as_enterprise_architect(email):
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "enterprise_architect"
        assert not user.is_platform_admin
        assert not user.is_org_admin


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-1440-dark.png" % name)), full_page=True)


def _offenders(page, root_selector):
    found = page.locator(root_selector).first.evaluate(_BORDERED)
    return sorted({"%s border on %r" % (f["side"], f["text"]) for f in found if f["colour"] == PREFLIGHT_GREY})


def test_dark_mode_draws_no_preflight_grey_border(browser, live_server, demo_architect):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    try:
        _login(page, live_server, demo_architect)
        failures = {}

        page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text("run cost", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        assert page.evaluate("document.documentElement.classList.contains('dark')")
        _shot(page, "overview")
        failures["overview"] = _offenders(page, "main")

        # The record is opened from the Strategy list, as a person would.
        page.goto(live_server + "/architecture/strategy/Capability?search=Sensor", timeout=PAGE_TIMEOUT)
        row = page.locator("tr", has_text=RECORD).first
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        row.get_by_role("link", name="View").click()
        expect(page.get_by_role("heading", name=RECORD).first).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        _shot(page, "record")
        failures["record"] = _offenders(page, "main")

        page.locator("main").get_by_text(re.compile("^Edit")).first.click(timeout=5000)
        dialog = page.get_by_role("dialog")
        expect(dialog.first).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, "record-edit")
        failures["edit dialog"] = _offenders(page, "[role=dialog]")

        failures = {screen: found for screen, found in failures.items() if found}
        assert not failures, "bright lines in dark mode: %s" % failures
    finally:
        context.close()
