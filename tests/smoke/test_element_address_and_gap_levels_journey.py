"""An enterprise architect opens an element from a global search hit and from a workflow page and sees that element;
selects gaps on the roadmap and sees the generate preview place a gap rated critical by its severity in the nearest
plateau; and finds every gap under exactly one level in the gap register. A second organisation's rows stay out."""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]


def _frame(page, name, viewport, theme):
    out = os.environ.get("ELEMENT_ADDRESS_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    page.screenshot(path="%s/%s-%d-%s.png" % (out, name, viewport["width"], theme), full_page=True)


@pytest.fixture(scope="module")
def estate(live_server):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import Gap
    from sqlalchemy import text

    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    tag = uuid.uuid4().hex[:6]
    out = {"mine": mine, "theirs": theirs, "tag": tag, "gaps": {}}
    with create_app("testing").app_context():
        for key, org in (("mine", mine), ("theirs", theirs)):
            el = ArchiMateElement(name="Handover pack %s %s" % (key, tag), type="Deliverable",
                                  layer="implementation", organization_id=org["org_id"])
            db.session.add(el)
            db.session.flush()
            out["element_" + key] = (el.id, el.name)
        specs = [("sevcrit", "medium", "critical"), ("plain", "medium", "medium"),
                 ("nosev", "high", "low"), ("sevhigh", "medium", "high")]
        for key, priority, severity in specs:
            gap = Gap(organization_id=mine["org_id"], name="Level %s %s" % (key, tag), gap_type="coverage",
                      priority=priority, severity=severity)
            db.session.add(gap)
            db.session.flush()
            out["gaps"][key] = (gap.id, gap.name)
        foreign = Gap(organization_id=theirs["org_id"], name="Level foreign %s" % tag, gap_type="coverage",
                      priority="high", severity="high")
        db.session.add(foreign)
        db.session.flush()
        db.session.execute(text("UPDATE gaps SET severity = NULL WHERE id = :i"), {"i": out["gaps"]["nosev"][0]})
        db.session.commit()
        db.session.remove()
    return out


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_search_hit_opens_the_element_and_the_register_levels_add_up(browser, live_server, estate, viewport, theme):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, estate["mine"]["emails"]["enterprise_architect"])
    page.evaluate("localStorage.setItem('theme', '%s')" % theme)
    try:
        _, mine_name = estate["element_mine"]
        status, body = api(page, "GET", "/api/sidebar/search?q=%s" % ("Handover pack mine " + estate["tag"]).replace(" ", "%20"))
        assert status == 200, body
        hits = body.get("results") or body.get("data") or []
        hit = next(h for h in hits if h["name"] == mine_name)
        assert "Handover pack theirs" not in str(body)
        page.goto(live_server + hit["url"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=mine_name).first).to_be_visible(timeout=PAGE_TIMEOUT)
        _frame(page, "search-hit", viewport, theme)

        mine_id, _ = estate["element_mine"]
        page.goto(live_server + "/architecture/implementation/ArchiMateElement/%d?ae=1" % mine_id,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=mine_name).first).to_be_visible(timeout=PAGE_TIMEOUT)

        foreign_id, _ = estate["element_theirs"]
        other = page.goto(live_server + "/architecture/implementation/element/%d" % foreign_id,
                          wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert other.status == 404

        by_level = {}
        for level in ("critical", "high", "medium", "low", "unrated"):
            status, body = api(page, "GET", "/implementation/api/gaps?priority=%s&limit=100" % level)
            assert status == 200, (level, body)
            text = str(body)
            by_level[level] = {k for k, (_, name) in estate["gaps"].items() if name in text}
            assert "Level foreign" not in text
            status, second = api(page, "GET", "/api/roadmap/gaps?priority=%s" % level)
            assert status == 200, (level, second)
            assert {k for k, (_, name) in estate["gaps"].items() if name in str(second)} == by_level[level], level
        assert by_level["critical"] == {"sevcrit"}, by_level
        assert by_level["unrated"] == {"nosev"}, by_level
        assert "sevhigh" in by_level["high"] and "sevhigh" not in by_level["medium"], by_level
        assert sorted(k for ks in by_level.values() for k in ks) == sorted(estate["gaps"]), by_level
    finally:
        context.close()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_generate_preview_puts_a_severity_critical_gap_in_the_nearest_plateau(browser, live_server, estate, viewport, theme):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, estate["mine"]["emails"]["enterprise_architect"])
    page.evaluate("localStorage.setItem('theme', '%s')" % theme)
    try:
        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for key in ("sevcrit", "plain"):
            gap_id, name = estate["gaps"][key]
            status, body = api(page, "POST", "/capability-map/api/roadmap/gaps/%d/work-packages" % gap_id,
                               {"name": "Close %s %s" % (name, theme), "start_date": "2027-01-01", "target_date": "2027-06-30"})
            assert status in (200, 201), body
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for key in ("sevcrit", "plain"):
            _, name = estate["gaps"][key]
            page.get_by_label("Select Close %s %s" % (name, theme)).check(timeout=PAGE_TIMEOUT)
        with page.expect_response(lambda r: "roadmap/ai-generate" in r.url) as seen:
            page.get_by_role("button", name=re.compile("AI Generate")).click()
        assert seen.value.status == 200
        dialog = page.locator("#rdm-ai-generate-modal")
        expect(dialog.get_by_text("Plateau 1 — Immediate")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(dialog.get_by_text("Plateau 2 — Near Term")).to_be_visible()
        _frame(page, "generate-preview", viewport, theme)
        for key, plateau in (("sevcrit", "Plateau 1"), ("plain", "Plateau 2")):
            block = dialog.locator(
                'xpath=.//p[normalize-space()="Resolve: %s"]/ancestor::div[contains(., "Target Plateau")][1]'
                % estate["gaps"][key][1])
            assert plateau in block.inner_text(), key
        critical = dialog.locator(
            'xpath=.//p[normalize-space()="Resolve: %s"]/ancestor::div[contains(., "Target Plateau")][2]'
            % estate["gaps"]["sevcrit"][1])
        assert "Critical" in critical.inner_text()
    finally:
        context.close()
