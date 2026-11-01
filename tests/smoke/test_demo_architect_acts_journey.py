"""The demo architect can act, and the catalogues behind every result are loaded.

Before this change every demo architect -- the primary demo user and the
sixteen owner accounts the seed creates for it, bryn.businessarch among them
-- was seeded on the read-only Viewer platform role. Score portfolio failed
with a generic "try again" message that hid the real refusal, Run detection
and Place with a rationale left the page for a raw PERMISSION_DENIED screen,
and the APQC process catalogue held 0 rows on a fresh install so the options
analysis process picker had nothing to offer. This journey, signed in as the
demo architect on a freshly seeded demo organisation, proves all four now
work; a second check, signed in as the one deliberately read-only Viewer in
a second organisation, proves the refusal that role still gets is reported
in plain words rather than hidden behind a generic error.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"


@pytest.fixture(scope="module")
def demo(live_server, app):
    """Seed the demo company and its vendor/APQC mappings the way the
    product's own seed does it, twice, to prove the seed is idempotent, and
    report the row counts the brief asks for."""
    from app import db

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            from app.commands.seed_demo_company import seed_demo_company
            from app.commands.seed_demo_mappings import seed_demo_mappings
            from app.models.apqc_process import APQCProcess
            from app.models.application_rationalization import (
                ApplicationRationalizationScore,
            )
            from app.models.organization import Organization
            from app.models.tech_radar import TechRadarEntry
            from app.models.user import User

            def counts():
                return {
                    "apqc_processes": APQCProcess.query.count(),
                    "application_rationalization_scores": (
                        ApplicationRationalizationScore.query.count()
                    ),
                    "tech_radar_entries": TechRadarEntry.query.count(),
                }

            before = counts()

            seed_demo_company()
            org_id = Organization.query.filter_by(slug="lantern-quay").one().id
            seed_demo_mappings(org_id=org_id)
            after_first = counts()

            # Idempotent: a second run of both commands changes nothing.
            seed_demo_company()
            seed_demo_mappings(org_id=org_id)
            after_second = counts()

            architect = User.query.filter_by(email=DEMO_EMAIL, organization_id=org_id).one()
            architect.password = PASSWORD
            reader = User.query.filter_by(
                email="drew.dataanalyst@lantern-quay.example.com", organization_id=org_id,
            ).one()
            reader.password = PASSWORD
            db.session.commit()

            out = {
                "org_id": org_id,
                "before": before,
                "after_first": after_first,
                "after_second": after_second,
                "architect_role": architect.role.name if architect.role else None,
                "reader_role": reader.role.name if reader.role else None,
            }
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return out


def test_seed_loads_the_catalogues_and_is_idempotent(demo):
    """The seed loads the APQC catalogue and scores the portfolio; a second
    run creates nothing new. The "before" counts are reported for the
    record rather than asserted at zero: this suite shares one persistent
    test database (tests/conftest.py), so an earlier test in the same
    session may already have seeded the catalogue -- a fresh install's
    actual zero start is shown directly in the build report."""
    assert demo["after_first"]["apqc_processes"] > 0, demo["after_first"]
    assert demo["after_first"]["application_rationalization_scores"] > 0, demo["after_first"]
    assert demo["after_second"] == demo["after_first"], (
        "re-running the seed changed the counts: %r -> %r"
        % (demo["after_first"], demo["after_second"])
    )
    # The demo architect may write; the one deliberate reader may not.
    assert demo["architect_role"] == "Architect", demo
    assert demo["reader_role"] == "Viewer", demo


def test_demo_architect_scores_portfolio_already_scored_on_open(page, live_server, demo):
    """The portfolio opens already scored (from the seed), and the demo
    architect can score it again without a refusal."""
    base = live_server
    sign_in(page, base, DEMO_EMAIL)

    page.goto(base + "/applications/rationalization", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    scored_value = page.locator("[data-testid='stat-scored-value']").inner_text()
    assert scored_value.strip() and not scored_value.strip().startswith("0 of"), (
        "the portfolio did not open already scored: %r" % scored_value
    )

    # Score again, from the "More actions" menu the already-scored state shows.
    page.locator("[data-testid='rationalization-more']").click()
    page.get_by_role("menuitem", name="Score again").click()
    page.wait_for_timeout(1500)
    error = page.locator("[data-testid='score-error']")
    assert not error.is_visible(), (
        "scoring refused the demo architect: %r" % (error.inner_text() if error.count() else None)
    )


def test_demo_architect_runs_detection_and_sees_a_result(page, live_server, demo):
    """Run detection succeeds for the demo architect and the page reports a
    real result -- found groups, or the honest empty state -- never a
    refusal."""
    base = live_server
    sign_in(page, base, DEMO_EMAIL)

    page.goto(base + "/applications/rationalization", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator("[data-testid='rationalization-more']").click()
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.locator("[data-testid='run-detection-menu-form'] button[type=submit]").click()

    body_text = page.inner_text("body")
    assert "PERMISSION_DENIED" not in body_text and "Platform admin" not in body_text, (
        "run detection refused the demo architect"
    )
    assert "detection finished" in body_text.lower(), (
        "no result was reported after running detection: %r" % body_text[:400]
    )


def test_demo_architect_places_a_technology_on_the_radar(page, live_server, demo):
    """The demo architect opens the radar, places (or moves) one technology
    with a rationale, and sees it reflected on the radar afterwards."""
    base = live_server
    sign_in(page, base, DEMO_EMAIL)

    page.goto(base + "/technology/radar/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # Every row's "Place with a rationale" / "Edit rationale" link sits
    # behind that row's own "Actions for ..." menu, closed by default -- open
    # the first row's menu, then take whichever such link that opened makes
    # visible (the table may re-sort client-side after the trigger is first
    # located, so re-deriving the row from the trigger is not reliable).
    page.get_by_role("button", name=re.compile("^Actions for ")).first.click()
    place_link = page.locator("[data-testid^='radar-place-']:visible").first
    place_link.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    element_testid = place_link.get_attribute("data-testid")
    element_id = element_testid.rsplit("-", 1)[-1]
    place_link.wait_for(state="visible", timeout=PAGE_TIMEOUT)

    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        place_link.click()
    assert "PERMISSION_DENIED" not in page.inner_text("body")
    assert "/place" in page.url

    page.select_option("#radar-ring-input", "adopt")
    page.fill("#radar-rationale-input", "Placed by the demo architect for the seed05 journey.")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.locator("[data-testid='radar-classify-%s']" % element_id).click()

    assert "moved to" in page.inner_text("body").lower() or "/technology/radar" in page.url
    # The radar index reflects the change: the ring badge for this element
    # now shows a real ring instead of "Not placed".
    page.goto(base + "/technology/radar/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    ring_cell = page.locator("[data-testid='radar-ring-%s']" % element_id)
    assert ring_cell.count() > 0 and ring_cell.inner_text().strip() != "Not placed", (
        "the placed technology still shows 'Not placed' as if nothing happened"
    )


def test_options_analysis_process_tab_lists_processes_and_enables_discover(page, live_server, demo):
    """In options analysis, the APQC process tab lists real processes (the
    catalogue the seed now loads) and Discover becomes enabled once one is
    picked."""
    base = live_server
    sign_in(page, base, DEMO_EMAIL)

    page.goto(base + "/dashboard/vendor-analysis/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("tab", name="APQC Processes").click()

    l1 = page.get_by_role("listbox", name="L1 APQC processes")
    checkbox = l1.locator("input[type='checkbox']").first
    # The L1 list loads via its own fetch after the tab switch; wait for a
    # real checkbox to appear rather than judging the list the instant the
    # loading placeholder is still showing.
    expect(checkbox).to_be_attached(timeout=PAGE_TIMEOUT)
    assert "No APQC processes found" not in l1.inner_text(), (
        "the process picker has no processes to offer"
    )
    checkbox.check()

    discover = page.get_by_role("button", name="Discover Matching Vendors")
    expect(discover).to_be_enabled(timeout=PAGE_TIMEOUT)


def test_viewer_in_a_second_organisation_sees_the_plain_refusal(page, live_server):
    """A Viewer-role person in a fresh, unrelated organisation is refused
    Score portfolio in plain words -- never 'Try again in a moment', which
    hides the real reason."""
    ids = create_fresh_org("enterprise_architect", platform_role="Viewer")
    from app import create_app, db

    with create_app("testing").app_context():
        from app.models.application_portfolio import ApplicationComponent

        db.session.add(ApplicationComponent(
            name="Launch Org Application", organization_id=ids["org_id"],
            lifecycle_status="2.1 strategic",
        ))
        db.session.commit()
        db.session.remove()

    base = live_server
    sign_in(page, base, ids["emails"]["enterprise_architect"])

    page.goto(base + "/applications/rationalization", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator("[data-testid='score-portfolio']").click()
    error = page.locator("[data-testid='score-error']")
    error.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    message = error.inner_text().strip()
    assert message and message != "Scoring did not finish. Try again in a moment.", (
        "the refusal was hidden behind the generic message: %r" % message
    )
    assert "role" in message.lower(), "the message does not say it is about the person's role: %r" % message
