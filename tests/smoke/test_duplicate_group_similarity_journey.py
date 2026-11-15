"""A group's similarity is the same figure on the duplicate list and on its own page, and the page says how it was worked out.

Goal: "I run Find possible duplicates, open the list of groups and then one group, and read the same similarity in both
places, with a sentence telling me how it was worked out." The figure is the one the pair measured, not the threshold I
chose; a second organisation's earlier group is still there, with its own figure, afterwards.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .cost_withheld_support import open_page, shot, shown_text
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIZES = [(1440, "light"), (390, "dark")]
FIRST, SECOND = "Monelytics", "Monelytics (usage, quotas, plans, billing)"
EARLIER_SCORE = 0.77


def _seed(tag_names, org_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    with create_app("testing").app_context():
        rows = [ApplicationComponent(name=n, organization_id=org_id, lifecycle_status="operational")
                for n in tag_names]
        db.session.add_all(rows)
        db.session.commit()
        return [r.id for r in rows]


def _earlier_group(app_ids):
    from app import create_app, db
    from app.models.unified_duplicate_detection import (
        UnifiedDetectionRun, UnifiedDuplicateGroup, unified_group_members)

    with create_app("testing").app_context():
        run = UnifiedDetectionRun(run_name="Earlier run", strategy="fast", status="completed",
                                  groups_found=1, applications_analyzed=2)
        db.session.add(run)
        db.session.flush()
        group = UnifiedDuplicateGroup(name="Earlier pair", similarity_score=EARLIER_SCORE, similarity_threshold=0.6,
                                      detection_run_id=run.id, status="pending", duplicate_type="fuzzy",
                                      match_details={"pair_count": 1})
        db.session.add(group)
        db.session.flush()
        for app_id in app_ids:
            db.session.execute(unified_group_members.insert().values(group_id=group.id, application_id=app_id))
        db.session.commit()
        return group.id


def _drop(app_ids):
    """Remove the groups, runs and applications this journey made, by their application ids."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_duplicate_detection import (
        UnifiedDetectionRun, UnifiedDuplicateGroup, unified_group_members)

    with create_app("testing").app_context():
        group_ids = [r[0] for r in db.session.execute(
            unified_group_members.select().with_only_columns(unified_group_members.c.group_id).where(
                unified_group_members.c.application_id.in_(app_ids)))]
        run_ids = [r[0] for r in db.session.execute(
            db.select(UnifiedDuplicateGroup.detection_run_id).where(UnifiedDuplicateGroup.id.in_(group_ids)))]
        db.session.execute(unified_group_members.delete().where(unified_group_members.c.group_id.in_(group_ids)))
        UnifiedDuplicateGroup.query.filter(UnifiedDuplicateGroup.id.in_(group_ids)).delete(synchronize_session=False)
        UnifiedDetectionRun.query.filter(UnifiedDetectionRun.id.in_(run_ids)).delete(synchronize_session=False)
        ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_ids)).delete(synchronize_session=False)
        db.session.commit()


@pytest.fixture(scope="module")
def world(live_server):
    mine = create_fresh_org("solution_architect")
    theirs = create_fresh_org("solution_architect")
    mine["app_ids"] = _seed([FIRST, SECOND], mine["org_id"])
    theirs["app_ids"] = _seed(["Payroll Hub", "Payroll Hub Copy"], theirs["org_id"])
    theirs["group_id"] = _earlier_group(theirs["app_ids"])
    yield mine, theirs
    _drop(mine["app_ids"] + theirs["app_ids"])


def _percent(text):
    found = re.findall(r"(\d+)%", text)
    assert found, text
    return found


@pytest.mark.parametrize("width,scheme", SIZES)
def test_the_list_and_the_group_page_show_the_pair_figure_not_the_threshold(
        browser, live_server, world, width, scheme):
    mine, theirs = world
    context, page = open_page(browser, live_server, mine["emails"]["solution_architect"], width, scheme)
    try:
        page.goto(live_server + "/applications/rationalization", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.select_option("#detection-threshold", "0.55")
        page.get_by_test_id("run-detection-form").get_by_role("button", name="Run detection").click()
        page.wait_for_url(re.compile(r"/applications/rationalization/?$"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("rationalization-last-run")).to_contain_text("1 group", timeout=PAGE_TIMEOUT)
        shot(page, "similarity-rationalisation", width, scheme)

        # The list of groups: the row for this pair, with its similarity cell.
        page.get_by_role("link", name="Review groups").first.click()
        link = page.locator("a[href*='/duplicate-detection/simple/group/']:visible").first
        expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
        row = link.locator("xpath=ancestor::*[.//*[normalize-space(text())='Similarity'] or self::tr][1]")
        listed = row.inner_text()
        assert "62%" in listed and "55%" not in listed, listed
        shot(page, "similarity-list", width, scheme)

        # Following the link lands on the group with the same figure and the way it was worked out.
        link.click()
        page.wait_for_url(re.compile(r"/duplicate-detection/simple/group/\d+"), timeout=PAGE_TIMEOUT)
        card = page.locator("main").get_by_text("Overall Similarity").locator("xpath=..")
        expect(card).to_contain_text("62%", timeout=PAGE_TIMEOUT)
        expect(card.get_by_test_id("similarity-basis")).to_have_text("Average of 1 pair")
        text = shown_text(page)
        assert FIRST in text and SECOND in text
        assert "Aggregate match score" not in text
        shot(page, "similarity-group", width, scheme)
    finally:
        context.close()


def test_the_other_organisations_earlier_group_is_untouched(browser, live_server, world):
    mine, theirs = world
    context, page = open_page(browser, live_server, theirs["emails"]["solution_architect"], 1440, "light")
    try:
        page.goto(live_server + "/duplicate-detection/simple/group/%d" % theirs["group_id"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card = page.locator("main").get_by_text("Overall Similarity").locator("xpath=..")
        expect(card).to_contain_text("77%", timeout=PAGE_TIMEOUT)
        expect(card.get_by_test_id("similarity-basis")).to_have_text("Average of 1 pair")
        assert "Payroll Hub" in shown_text(page)
        # and nothing of the first organisation's applications appears in it
        assert FIRST not in shown_text(page)
    finally:
        context.close()


def _dashboard_data(org_id, app_ids, groups_found, kinds):
    """A completed run of the organisation with one two-application group per kind.

    ``kinds`` pairs a duplicate type with the similarity the row stores. Returns the run id."""
    from app import create_app, db
    from app.models.unified_duplicate_detection import (
        UnifiedDetectionRun, UnifiedDuplicateGroup, unified_group_members)

    with create_app("testing").app_context():
        run = UnifiedDetectionRun(run_name="Dashboard run", strategy="hybrid", status="completed",
                                  groups_found=groups_found, applications_analyzed=len(app_ids),
                                  organization_id=org_id)
        db.session.add(run)
        db.session.flush()
        for (kind, stored), pair in zip(kinds, zip(app_ids[0::2], app_ids[1::2])):
            group = UnifiedDuplicateGroup(name="%s pair" % kind, similarity_score=stored, similarity_threshold=0.6,
                                          detection_run_id=run.id, status="pending", duplicate_type=kind,
                                          match_details={"pair_count": 1})
            db.session.add(group)
            db.session.flush()
            for app_id in pair:
                db.session.execute(unified_group_members.insert().values(group_id=group.id, application_id=app_id))
        db.session.commit()
        return run.id


@pytest.fixture(scope="module")
def dashboard_world(live_server):
    mine = create_fresh_org("solution_architect")
    theirs = create_fresh_org("solution_architect")
    mine["app_ids"] = _seed(["Ledger Core", "Ledger Core", "Rota Tool", "Rota Tool Plus"], mine["org_id"])
    theirs["app_ids"] = _seed(["Payroll Hub", "Payroll Hub Copy", "Claims Desk", "Claims Desk Copy",
                               "Audit Log", "Audit Log Copy"], theirs["org_id"])
    # an older exact group holds the 0.0 default; the other organisation runs last with bigger figures
    mine["run_id"] = _dashboard_data(mine["org_id"], mine["app_ids"], 2, [("exact", 0.0), ("fuzzy", 0.7)])
    theirs["run_id"] = _dashboard_data(theirs["org_id"], theirs["app_ids"], 3,
                                       [("fuzzy", 0.9), ("fuzzy", 0.95), ("fuzzy", 0.5)])
    yield mine, theirs
    _drop(mine["app_ids"] + theirs["app_ids"])


def _card(page, label):
    return page.locator("p", has_text=re.compile("^%s$" % label)).first.locator("xpath=../..")


@pytest.mark.parametrize("width,scheme", SIZES)
def test_the_dashboard_cards_show_my_organisation_and_never_the_others(
        browser, live_server, dashboard_world, width, scheme):
    mine, theirs = dashboard_world
    for org, groups, high, found in ((mine, "2", "1", "2 duplicate groups"), (theirs, "3", "2", "3 duplicate groups")):
        context, page = open_page(browser, live_server, org["emails"]["solution_architect"], width, scheme)
        try:
            page.goto(live_server + "/duplicate-detection/simple", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(_card(page, "Duplicate Groups")).to_contain_text(groups, timeout=PAGE_TIMEOUT)
            expect(_card(page, "High Priority")).to_contain_text(high, timeout=PAGE_TIMEOUT)
            expect(_card(page, "Last Detection")).to_contain_text("Last run found " + found, timeout=PAGE_TIMEOUT)
            shot(page, "dashboard-cards-%s" % ("mine" if org is mine else "theirs"), width, scheme)
            # the same figures reach the group list underneath
            expect(page.locator("a[href*='/duplicate-detection/simple/group/']:visible").first).to_be_visible(
                timeout=PAGE_TIMEOUT)
            listed = page.eval_on_selector_all(
                "a[href*='/duplicate-detection/simple/group/']",
                "els => [...new Set(els.filter(e => e.offsetParent).map(e => e.getAttribute('href')))].length")
            assert str(listed) == groups, listed
            if org is mine:
                assert "Payroll Hub" not in shown_text(page)
        finally:
            context.close()


def test_the_dashboard_statistics_name_my_run_and_an_older_exact_group_reads_as_a_full_match(
        browser, live_server, dashboard_world):
    mine, theirs = dashboard_world
    context, page = open_page(browser, live_server, mine["emails"]["solution_architect"], 1440, "light")
    try:
        page.goto(live_server + "/duplicate-detection/simple", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        stats = page.evaluate("fetch('/duplicate-detection/simple/api/statistics').then(r => r.json())")
        assert stats["latest_run"]["id"] == mine["run_id"] != theirs["run_id"]
        exact = page.locator("a[href*='/duplicate-detection/simple/group/']:visible", has_text="Exact").first
        row = exact.locator("xpath=ancestor::tr[1]")
        expect(row).to_contain_text("100%", timeout=PAGE_TIMEOUT)
        assert "0%" not in row.inner_text().replace("100%", "")
        exact.click()
        page.wait_for_url(re.compile(r"/duplicate-detection/simple/group/\d+"), timeout=PAGE_TIMEOUT)
        card = page.locator("main").get_by_text("Overall Similarity").locator("xpath=..")
        expect(card).to_contain_text("100%", timeout=PAGE_TIMEOUT)
        expect(card.get_by_test_id("similarity-basis")).to_have_text("Names match exactly")
    finally:
        context.close()
