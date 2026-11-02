"""Every person gets the same first week, not only the person who started the workspace.

The people: the founder who signed up, a teammate added to that workspace who has never
signed in, and a teammate whose first week ended eight days ago.
Goal one: the teammate signs in, finds the workspace open with its content and a banner
that names the last day to set up two-factor sign-in, and the banner's link lands on a
set-up page that says how long is left.
Goal two: the teammate whose week is over signs in and is taken to set up before anything
else, is told plainly that the week is over (never that they are not the owner), sets it
up, and arrives in the workspace.
"""

import re
import uuid
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .test_mfa_first_week_journey import (  # noqa: F401 - the module's fixtures and helpers
    PASSWORD,
    SHORT,
    _assert_workspace_opened,
    _enrol,
    _sign_up,
    journey,
    week_server,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

_STATE = {}


def _add_teammate(app, owner_email, label, signed_up_days_ago):
    """A teammate in the founder's workspace, made the way the team page makes one."""
    from app import db
    from app.models import Role
    from app.models.org_role import OrgRole
    from app.models.user import User

    with app.app_context():
        owner = User.query.filter_by(email=owner_email).one()
        Role.insert_roles()
        email = "%s.%s@gmail.com" % (label, uuid.uuid4().hex[:8])
        teammate = User(first_name=label.title(), last_name="Teammate", email=email, password=PASSWORD,
                        confirmed=True, organization_id=owner.organization_id,
                        role=Role.query.filter_by(name="Viewer").first())
        if signed_up_days_ago is not None:
            teammate.signup_at = datetime.utcnow() - timedelta(days=signed_up_days_ago)
        db.session.add(teammate)
        db.session.flush()
        OrgRole.set_role(owner.organization_id, teammate.id, "viewer", granted_by_id=owner.id)
        db.session.commit()
        return email


def _sign_in(j, email):
    page = j.page
    page.context.clear_cookies()
    page.goto(j.base + "/account/login", wait_until="domcontentloaded")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()


def test_01_a_teammate_signs_in_inside_the_week_and_finds_the_workspace_with_a_dated_banner(journey, app):
    j, page = journey, journey.page
    _STATE["owner"] = _sign_up(j, "members")
    _STATE["inside"] = _add_teammate(app, _STATE["owner"], "inside", None)
    _sign_in(j, _STATE["inside"])
    _assert_workspace_opened(j, "teammate inside the week")
    page.goto(j.base + "/dashboard/overview", wait_until="domcontentloaded")
    banner = page.get_by_test_id("two-factor-banner")
    expect(banner).to_be_visible(timeout=SHORT)
    j.shot("members-banner")
    ends = (datetime.utcnow() + timedelta(days=7)).strftime("%-d %B %Y")
    text = banner.inner_text()
    assert "Set up two-factor sign-in by" in text and ends in text, text
    assert "owner" not in text.lower()


def test_02_the_banner_link_lands_on_a_set_up_page_with_content(journey):
    j, page = journey, journey.page
    page.get_by_test_id("two-factor-banner").get_by_role("link", name="Set it up").click()
    page.wait_for_url(re.compile(r".*/account/mfa-enrol.*"), timeout=60000)
    j.shot("members-set-up-page")
    assert re.search(r"Remind me later \(\d days? left\)", j.main_text()), j.main_text()[:300]
    assert page.locator("#setup-key").inner_text().strip()


def test_03_a_teammate_whose_week_is_over_is_taken_to_set_up_first_and_then_arrives(journey, app):
    j, page = journey, journey.page
    _STATE["late"] = _add_teammate(app, _STATE["owner"], "late", 8)
    _sign_in(j, _STATE["late"])
    page.wait_for_url(re.compile(r".*/account/mfa-(enrol|challenge).*"), timeout=60000)
    j.shot("members-week-over")
    text = j.main_text().replace("’", "'")
    # Sign-in itself stops at set-up: the person has no code to enter yet.
    assert "Set up two-factor authentication" in text, text[:300]
    assert "sole owner" not in text and "not the owner" not in text
    assert "Remind me later" not in text
    assert "Overview" not in text
    _enrol(j, r".*/(dashboard/overview|account/welcome)/?(\?.*)?$")
    _assert_workspace_opened(j, "teammate after setting up")


def test_04_that_teammate_is_now_asked_for_a_code_at_the_next_sign_in(journey):
    j, page = journey, journey.page
    _sign_in(j, _STATE["late"])
    page.wait_for_url(re.compile(r".*/account/mfa-challenge.*"), timeout=60000)
    assert "Enter your verification code" in j.main_text()
    j.shot("members-asks-for-code")
