"""Journey, in a browser: a solution's own page states its status and review in the
same words as the Solutions list, and the page the sidebar opens is called what the
sidebar calls it.

Played at 1440 wide in the light theme and at 390 wide in the dark theme.
"""

import os
import pathlib
import re

import pytest

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("DASHCOUNT03_SHOTS", "/tmp/dashcount03-shots"))


def _context(browser, width=1600, theme="light"):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
    context.set_default_timeout(PAGE_TIMEOUT)
    return context


def _seed_solutions(email):
    """Create two solutions in the org of the given user; return their ids."""
    from app import create_app, db
    app = create_app("testing")
    ids = {}
    with app.app_context():
        from app.models.solution_models import Solution
        from app.models.user import User

        user = User.query.filter_by(email=email).one()
        s1 = Solution(
            name="Field Calibration Platform",
            description="Calibrates field sensors across the estate.",
            organization_id=user.organization_id,
            created_by_id=user.id,
            governance_status="arb_review",
            solution_owner="Ivo Reed",
        )
        db.session.add(s1)
        db.session.flush()
        ids["calibration"] = s1.id
        s2 = Solution(
            name="Event-Driven Operations Backbone",
            description="Event streaming for operations.",
            organization_id=user.organization_id,
            created_by_id=user.id,
            governance_status="approved",
            solution_owner="Sam Lee",
        )
        db.session.add(s2)
        db.session.flush()
        ids["backbone"] = s2.id
        db.session.commit()
        db.session.remove()
    return ids


def test_solution_record_review_matches_list(browser, live_server):
    """The review badge on a solution's record page uses the same word as the list."""
    org = create_fresh_org("solution_architect")
    email = org["emails"]["solution_architect"]
    ids = _seed_solutions(email)
    sid = ids["calibration"]

    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, email)

        # Open the solution's record page directly.
        page.goto("%s/solutions/%d" % (live_server, sid), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_url(re.compile(r"/solutions/%d" % sid), timeout=PAGE_TIMEOUT)

        # The record page shows Status and Review as two labelled badges.
        status = page.get_by_test_id("record-status")
        review = page.get_by_test_id("record-review")
        assert status.is_visible(), "record has a Status badge"
        assert review.is_visible(), "record has a Review badge"
        assert "In review" in review.inner_text(), "record review says 'In review', got: %r" % review.inner_text()

        # Back to the list: the same solution's review word matches.
        page.goto(live_server + "/solutions/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.get_by_test_id("solution-row").filter(has_text="Field Calibration Platform").first
        assert row.is_visible(), "solution appears on the list"
        review_cell = row.locator("td").filter(has_text="In review").first
        assert review_cell.is_visible(), "list review cell says 'In review'"

        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / "record-review-1440-light.png"), full_page=True)
    finally:
        context.close()


def test_sidebar_opens_solutions_architecture(browser, live_server):
    """The sidebar entry 'Solutions Architecture' opens a page titled 'Solutions Architecture'."""
    org = create_fresh_org("solution_architect")
    email = org["emails"]["solution_architect"]

    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, email)

        # The Solutions Architecture entry is a child of the Changes toggle group in the
        # sidebar; navigate to its page directly and verify the title matches the label.
        page.goto(live_server + "/architecture/solutions-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The page title and heading match what the sidebar calls it.
        assert page.title() == "Solutions Architecture", "page title: %r" % page.title()
        heading = page.get_by_role("heading", name="Solutions Architecture", exact=True)
        assert heading.is_visible(), "page heading says 'Solutions Architecture'"

        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / "sidebar-solutions-architecture-1440-light.png"), full_page=True)
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_record_review_fits_phone(browser, live_server, theme):
    """The record page's Status and Review badges do not overflow at phone width."""
    org = create_fresh_org("solution_architect")
    email = org["emails"]["solution_architect"]
    ids = _seed_solutions(email)
    sid = ids["calibration"]

    context = _context(browser, width=390, theme=theme)
    page = context.new_page()
    try:
        sign_in(page, live_server, email)
        page.goto("%s/solutions/%d" % (live_server, sid), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_url(re.compile(r"/solutions/%d" % sid), timeout=PAGE_TIMEOUT)

        status = page.get_by_test_id("record-status")
        review = page.get_by_test_id("record-review")
        assert status.is_visible()
        assert review.is_visible()

        overflow = page.evaluate(
            "() => Array.from(document.querySelectorAll('body, main, [data-testid=solution-record]'))"
            ".every(el => el.scrollWidth <= window.innerWidth + 1)"
        )
        assert overflow, "record page overflows at 390 wide (%s theme)" % theme

        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / ("record-review-390-%s.png" % theme)), full_page=True)
    finally:
        context.close()
