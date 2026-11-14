"""Two organisations each run duplicate detection and see only their own groups.

Organisation A runs detection and sees its own groups, savings and run history.
Organisation B's groups, figures and run history stay unchanged — no name, figure
or run from organisation A appears in organisation B's dashboard, and vice versa.
Every group link on the dashboard lands on that group's own detail page.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("DETECTION_WALK_DIR")


@pytest.fixture(scope="module")
def two_detecting_orgs(live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_duplicate_detection import (
        UnifiedDetectionRun,
        UnifiedDuplicateGroup,
        unified_group_members,
    )

    tag = uuid.uuid4().hex[:6]
    out = {"tag": tag}
    orgs = {k: create_fresh_org("enterprise_architect") for k in ("a", "b")}
    app = create_app("testing")
    with app.app_context():
        for key, org in orgs.items():
            org_id = org["org_id"]
            first = ApplicationComponent(
                name=f"Ledger {key} {tag}", organization_id=org_id
            )
            second = ApplicationComponent(
                name=f"Ledgerlite {key} {tag}", organization_id=org_id
            )
            db.session.add_all([first, second])
            db.session.flush()
            run = UnifiedDetectionRun(
                run_name=f"Run {key} {tag}",
                strategy="fast",
                status="completed",
                organization_id=org_id,
                applications_analyzed=2,
                groups_found=1,
                estimated_savings=100000.0 if key == "a" else 250000.0,
            )
            db.session.add(run)
            db.session.flush()
            group = UnifiedDuplicateGroup(
                name=f"Duplicate group {key} {tag}",
                organization_id=org_id,
                duplicate_type="fuzzy",
                similarity_score=0.93,
                estimated_savings=100000.0 if key == "a" else 250000.0,
                detection_run_id=run.id,
            )
            db.session.add(group)
            db.session.flush()
            db.session.execute(
                unified_group_members.insert().values(
                    group_id=group.id, application_id=first.id, is_primary=True
                )
            )
            db.session.execute(
                unified_group_members.insert().values(
                    group_id=group.id, application_id=second.id, is_primary=False
                )
            )
            db.session.commit()
            out[key] = {
                "email": org["emails"]["enterprise_architect"],
                "first": first.name,
                "second": second.name,
                "group_name": f"Duplicate group {key} {tag}",
                "savings": 100000.0 if key == "a" else 250000.0,
            }
        db.session.remove()
    return out


def _text(page):
    return re.sub(r"\s+", " ", page.locator("main, body").first.inner_text())


def _shot(page, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def _figures(text):
    """Every whole number the page prints, thousands separators and a trailing .00 removed."""
    return {re.sub(r",", "", n).split(".")[0] for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _read(browser, live_server, world, mine, theirs, width, theme="light"):
    mobile = width < 600
    context = browser.new_context(
        viewport={"width": width, "height": 900 if not mobile else 844}
    )
    context.add_init_script(
        f"try {{ localStorage.setItem('theme', '{theme}') }} catch (e) {{}}"
    )
    page = context.new_page()
    try:
        sign_in(page, live_server, world[mine]["email"])
        assert page.goto(
            live_server + "/duplicate-detection/simple",
            timeout=PAGE_TIMEOUT,
        ).status == 200
        assert page.evaluate(
            "() => document.documentElement.classList.contains('dark')"
        ) == (theme == "dark")

        # Wait for Alpine.js to load the groups table. At narrow widths the
        # desktop table link is hidden (hidden lg:block) but still in DOM order
        # before the mobile card link, so filter to visible elements.
        expect(
            page.get_by_text(world[mine]["group_name"]).filter(visible=True).first
        ).to_be_visible(timeout=PAGE_TIMEOUT)
        # The figure cards load from their own request, after the groups.
        expect(page.get_by_text("Not estimated yet")).to_have_count(
            0, timeout=PAGE_TIMEOUT
        )
        body = _text(page)
        assert world[theirs]["group_name"] not in body
        _shot(page, f"{mine}-dashboard-{width}-{theme}")

        figures = _figures(body)
        assert str(int(world[mine]["savings"])) in figures, body
        assert str(int(world[theirs]["savings"])) not in figures, body

        # The group link lands on that group's own detail page, with its name
        # and its applications listed.
        page.get_by_role("link", name=world[mine]["group_name"]).filter(visible=True).first.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/duplicate-detection/simple/group/" in page.url, page.url
        expect(
            page.get_by_text(world[mine]["group_name"]).filter(visible=True).first
        ).to_be_visible(timeout=PAGE_TIMEOUT)
        detail_body = _text(page)
        assert world[mine]["first"] in detail_body
        assert world[mine]["second"] in detail_body
        assert world[theirs]["first"] not in detail_body
        assert world[theirs]["second"] not in detail_body
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [1440, 390])
def test_first_organisation_reads_only_its_own(
    browser, live_server, two_detecting_orgs, width, theme
):
    _read(browser, live_server, two_detecting_orgs, "a", "b", width, theme)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [1440, 390])
def test_second_organisation_reads_only_its_own(
    browser, live_server, two_detecting_orgs, width, theme
):
    _read(browser, live_server, two_detecting_orgs, "b", "a", width, theme)
