"""Switching on "Require two-factor sign-in" does what the Team page says.

The people: the founder who signed up (inside the first week, no authenticator yet) and
a teammate in the same workspace who does not administer it.
Goal one: the founder switches the requirement on from the Team page and is taken to set
up at once, with no "Remind me later".
Goal two: after signing out, the founder's next sign-in lands on set-up, while the
teammate still signs in to a workspace with content.
Goal three: once the founder has set up, the Team page offers to stop requiring it, and
the founder reaches the workspace.
"""

import re

import pytest
from playwright.sync_api import expect

from .test_mfa_first_week_journey import (  # noqa: F401 - the module's fixtures and helpers
    SHORT,
    _assert_workspace_opened,
    _enrol,
    _sign_up,
    journey,
)
from .test_mfa_first_week_members_journey import _add_teammate
from .test_mfa_first_week_members_journey import _sign_in as _sign_in_here

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

_STATE = {}


def _sign_in(j, email):
    """Sign in as someone else. The page the last person was on is closed first, so a
    request it still has in flight cannot hand its cookie back after the sign-out."""
    j.page.goto("about:blank")
    _sign_in_here(j, email)
    j.page.wait_for_load_state("load")


def test_01_switching_it_on_takes_the_founder_to_set_up_with_no_later(journey, app):
    j, page = journey, journey.page
    _STATE["owner"] = _sign_up(j, "require")
    _STATE["teammate"] = _add_teammate(app, _STATE["owner"], "plain", None)
    page.goto(j.base + "/admin/team", wait_until="domcontentloaded")
    toggle = page.get_by_test_id("team-two-factor-toggle")
    expect(toggle).to_be_visible(timeout=SHORT)
    j.shot("require-team-control")
    toggle.click()
    page.wait_for_url(re.compile(r".*/account/mfa-enrol.*"), timeout=60000)
    j.shot("require-set-up")
    text = j.main_text()
    assert "Remind me later" not in text, text[:300]
    assert page.locator("#setup-key").inner_text().strip()


def test_02_the_teammate_keeps_the_week_and_the_founders_next_sign_in_lands_on_set_up(journey):
    j, page = journey, journey.page
    _sign_in(j, _STATE["teammate"])
    _assert_workspace_opened(j, "teammate while the requirement is on")
    page.goto(j.base + "/dashboard/overview", wait_until="domcontentloaded")
    expect(page.get_by_test_id("two-factor-banner")).to_be_visible(timeout=SHORT)
    assert "/account/mfa-enrol" not in page.url
    _sign_in(j, _STATE["owner"])
    page.wait_for_url(re.compile(r".*/account/mfa-(enrol|challenge).*"), timeout=60000)
    expect(page.locator("#setup-key")).to_be_visible(timeout=60000)
    j.shot("require-sign-in-set-up")
    text = j.main_text()
    assert "Set up two-factor authentication" in text and "Remind me later" not in text, text[:300]
    assert page.locator("#setup-key").inner_text().strip()


def test_03_after_set_up_the_founder_reaches_the_workspace_and_can_stop_requiring_it(journey):
    j, page = journey, journey.page
    _enrol(j, r".*/(dashboard/overview|account/welcome|admin/team)/?(\?.*)?$")
    page.goto(j.base + "/admin/team", wait_until="domcontentloaded")
    toggle = page.get_by_test_id("team-two-factor-toggle")
    expect(toggle).to_have_text(re.compile("Stop requiring two-factor"), timeout=SHORT)
    j.shot("require-team-control-on")
