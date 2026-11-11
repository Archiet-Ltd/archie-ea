"""An enterprise architect with a severity-critical medium-priority gap, a plain gap, an unrated gap, a closed gap
and a plateau transition reads the same count for each level and for "unrated" on the register, both gap APIs, the
planning report and the roadmap preview. The four levels plus "unrated" equal the recorded gaps on each, and a
second organisation's gap shows nowhere."""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]
LEVELS = ("critical", "high", "medium", "low", "unrated")


def _frame(page, name, viewport, theme):
    out = os.environ.get("GAP_LEVELS_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    page.screenshot(path="%s/%s-%d-%s.png" % (out, name, viewport["width"], theme), full_page=True)


@pytest.fixture(scope="module")
def estate(live_server):
    from app import create_app, db
    from app.models.archimate_core import ArchitectureModel
    from app.models.implementation_migration import GAP_KIND_PLATEAU_TRANSITION, Gap, Plateau
    from sqlalchemy import text

    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    tag = uuid.uuid4().hex[:6]
    out = {"mine": mine, "theirs": theirs, "tag": tag, "gaps": {}}
    with create_app("testing").app_context():
        architecture = ArchitectureModel(name="Levels architecture %s" % tag, organization_id=mine["org_id"])
        db.session.add(architecture)
        db.session.flush()
        as_is, to_be = (Plateau(name="%s %s" % (n, tag), organization_id=mine["org_id"],
                                architecture_id=architecture.id, sequence_order=o)
                        for n, o in (("As-is", 1), ("To-be", 2)))
        db.session.add_all([as_is, to_be])
        db.session.flush()
        specs = [("crit", "medium", "critical", {}), ("plain", "medium", "medium", {}),
                 ("unrated", "high", "low", {}), ("closed", "low", "low", {"resolution_status": "closed"}),
                 ("plateau", "critical", "critical", {"gap_kind": GAP_KIND_PLATEAU_TRANSITION,
                                                      "originating_plateau_id": as_is.id,
                                                      "target_plateau_id": to_be.id})]
        for key, priority, severity, extra in specs:
            gap = Gap(organization_id=mine["org_id"], name="Agree %s %s" % (key, tag), gap_type="coverage",
                      priority=priority, severity=severity, **extra)
            db.session.add(gap)
            db.session.flush()
            out["gaps"][key] = (gap.id, gap.name)
        foreign = Gap(organization_id=theirs["org_id"], name="Agree foreign %s" % tag, gap_type="coverage",
                      priority="high", severity="high")
        db.session.add(foreign)
        db.session.flush()
        out["foreign"] = foreign.name
        db.session.execute(text("UPDATE gaps SET severity = NULL WHERE id = :i"), {"i": out["gaps"]["unrated"][0]})
        db.session.commit()
        db.session.remove()
    return out


def _names_in(text, estate):
    return {k for k, (_, name) in estate["gaps"].items() if name in text}


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_each_level_and_unrated_count_the_same_on_every_surface(browser, live_server, estate, viewport, theme):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, estate["mine"]["emails"]["enterprise_architect"])
    page.evaluate("localStorage.setItem('theme', '%s')" % theme)
    expected = {"critical": {"crit"}, "high": set(), "medium": {"plain"}, "low": {"closed"}, "unrated": {"unrated"}}
    try:
        # The register, one filter at a time, as a person reaches it.
        for level in LEVELS:
            page.goto(live_server + "/enterprise/implementation/gap-analysis?severity=%s" % level,
                      wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            body = page.locator("body").inner_text()
            assert _names_in(body, estate) == expected[level], ("register", level)
            assert estate["foreign"] not in body
            if level in ("critical", "unrated"):
                expect(page.get_by_test_id("gap-severity-filter")).to_contain_text(level.capitalize())
                _frame(page, "register-%s" % level, viewport, theme)
        page.goto(live_server + "/enterprise/implementation/gap-analysis", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        everything = page.locator("body").inner_text()
        recorded = _names_in(everything, estate)
        assert recorded == {"crit", "plain", "unrated", "closed"}, recorded  # the plateau transition is not a gap here
        assert sum(len(v) for v in expected.values()) == len(recorded)

        # Both gap APIs, by priority and by severity.
        for level in LEVELS:
            for query in ("priority", "severity"):
                status, body = api(page, "GET", "/implementation/api/gaps?%s=%s&limit=100" % (query, level))
                assert status == 200, body
                assert _names_in(str(body), estate) == expected[level], ("implementation", query, level)
                assert body["total"] == len(expected[level])
                assert estate["foreign"] not in str(body)
            status, body = api(page, "GET", "/api/roadmap/gaps?priority=%s" % level)
            assert status == 200, body
            assert _names_in(str(body), estate) == expected[level], ("roadmap", level)
            assert estate["foreign"] not in str(body)

        # The implementation report counts by the same rating.
        status, body = api(page, "GET", "/implementation/reports/generate")
        assert status == 200, body
        summary = body["report"]["summary"]
        assert summary["gaps_by_priority"] == {"critical": 1, "medium": 1, "low": 1, "unrated": 1}
        assert summary["total_gaps"] == len(recorded)

        # The roadmap preview, opened from the page with three gaps ticked.
        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for key in ("crit", "plain", "unrated"):
            gap_id, name = estate["gaps"][key]
            status, body = api(page, "POST", "/capability-map/api/roadmap/gaps/%d/work-packages" % gap_id,
                               {"name": "Close %s %s" % (name, theme), "start_date": "2027-01-01",
                                "target_date": "2027-06-30"})
            assert status in (200, 201), body
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for key in ("crit", "plain", "unrated"):
            page.get_by_label("Select Close %s %s" % (estate["gaps"][key][1], theme)).check(timeout=PAGE_TIMEOUT)
        with page.expect_response(lambda r: "roadmap/ai-generate" in r.url) as seen:
            page.get_by_role("button", name=re.compile("Generate roadmap")).click()
        assert seen.value.status == 200
        dialog = page.get_by_role("dialog", name="Roadmap preview")
        expect(dialog).to_contain_text("placed in a plateau by its severity", timeout=PAGE_TIMEOUT)
        expect(dialog.get_by_text("Plateau 3 — Long Term")).to_be_visible(timeout=PAGE_TIMEOUT)
        _frame(page, "preview", viewport, theme)
        for key, rating, plateau in (("crit", "Critical", "Plateau 1"), ("plain", "Medium", "Plateau 2"),
                                     ("unrated", "Unrated", "Plateau 3")):
            card = dialog.locator(
                'xpath=.//p[normalize-space()="Resolve: %s"]/ancestor::div[contains(., "Target Plateau")][2]'
                % estate["gaps"][key][1])
            text = card.inner_text()
            assert rating.lower() in text.lower() and plateau in text, (key, text)
        if viewport["width"] < 500:
            title = dialog.get_by_role("heading", name="Roadmap preview").bounding_box()
            description = dialog.get_by_text("placed in a plateau by its severity").bounding_box()
            assert description["y"] >= title["y"] + title["height"] - 1, (title, description)
    finally:
        context.close()
