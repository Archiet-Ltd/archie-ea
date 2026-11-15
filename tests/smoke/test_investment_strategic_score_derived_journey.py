"""An enterprise architect reads where to invest, and every capability has a strategic score.

Goal: "I see a strategic score for each capability. Where nobody recorded how important it is, the
score is worked out from what the model holds (the criticality of its applications, its links to
goals, the value streams it serves, its open risks) and says so, with its evidence. Where the
model holds nothing to work from, the row says which fact to record and links to where."

The organisation is seeded with linked data and a second organisation whose capability, application
and goal must never reach the first one's scores. A second journey reads the demonstration
organisation's own seed and requires that no capability is left unscored without a way to score it.
"""
import os
import pathlib
import re
import tempfile
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.fresh_org import create_fresh_org, sign_in

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGES = ("/strategic/investment-matrix", "/architecture/investment-priorities")
SHOTS = pathlib.Path(os.environ.get("SMOKE_SHOT_DIR") or tempfile.mkdtemp(prefix="strategic-score-"))
DEMO_ADMIN = "demo@lantern-quay.example.com"


def _seed_org(org_id, tag):
    """Five capabilities for one organisation; returns their names."""
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.motivation import Goal
    from app.models.models import ArchiMateRelationship
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.models.unified_capability import (
        CapabilityValueStreamMapping,
        UnifiedCapability,
        ValueStream,
        ValueStreamStage,
    )
    from app.services.archimate_backbone import create_backbone_element

    names = {
        "recorded": "Run payroll %s" % tag,
        "by_apps": "Forecast demand %s" % tag,
        "by_goals": "Shape pricing %s" % tag,
        "by_stream": "Pack orders %s" % tag,
        "nothing": "Plan events %s" % tag,
    }
    elements = {}

    def capability(key, importance):
        element = create_backbone_element(
            element_type="Capability", layer="strategy", name=names[key], organization_id=org_id)
        cap = UnifiedCapability(
            name=names[key], organization_id=org_id, strategic_importance=importance,
            archimate_element_id=element.id, level=1)
        db.session.add(cap)
        db.session.flush()
        elements[key] = element
        return cap

    recorded = capability("recorded", "high")
    by_apps = capability("by_apps", None)
    capability("by_goals", None)
    by_stream = capability("by_stream", None)
    # Created the way the capabilities page creates one, so that page lists it too.
    db.session.add(BusinessCapability(name=names["nothing"], organization_id=org_id))
    db.session.flush()

    # Applications: the recorded capability is supported by a critical one, which must not
    # override the rating somebody recorded; the unrated one by a critical and a low one.
    for cap, label, criticality in (
        (recorded, "Payroll suite", "Critical"),
        (by_apps, "Demand engine", "High"),
        (by_apps, "Spreadsheet", "Low"),
    ):
        application = ApplicationComponent(
            name="%s %s" % (label, tag), organization_id=org_id, business_criticality=criticality)
        db.session.add(application)
        db.session.flush()
        db.session.add(UnifiedApplicationCapabilityMapping(
            unified_capability_id=cap.id, application_component_id=application.id,
            support_level="full", coverage_percentage=80, is_active=True))

    for goal_name in ("Grow margin %s", "Win repeat buyers %s"):
        goal_el = create_backbone_element(
            element_type="Goal", layer="motivation", name=goal_name % tag, organization_id=org_id)
        db.session.add(Goal(name=goal_name % tag, organization_id=org_id,
                            archimate_element_id=goal_el.id))
        db.session.add(ArchiMateRelationship(
            source_id=elements["by_goals"].id, target_id=goal_el.id, type="realization",
            organization_id=org_id))

    stream = ValueStream(name="Order to delivery %s" % tag, code="O2D", organization_id=org_id)
    db.session.add(stream)
    db.session.flush()
    stage = ValueStreamStage(name="Pack the order", value_stream_id=stream.id, stage_order=1,
                             organization_id=org_id)
    db.session.add(stage)
    db.session.flush()
    db.session.add(CapabilityValueStreamMapping(
        value_stream_id=stream.id, capability_id=by_stream.id, value_stream_stage_id=stage.id,
        organization_id=org_id))
    db.session.commit()
    return names


@pytest.fixture(scope="module")
def two_orgs(live_server):
    from app import create_app

    tag = uuid.uuid4().hex[:6]
    mine = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    app = create_app("testing")
    with app.app_context():
        names = _seed_org(mine["org_id"], "m" + tag)
        foreign = _seed_org(other["org_id"], "f" + tag)
    return {"mine": mine, "names": names, "foreign": foreign}


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=False)


def _row(page, name):
    return page.get_by_test_id("capability-row").filter(has_text=name)


def _cell(page, name, field):
    row = _row(page, name)
    expect(row).to_have_count(1)
    return row.get_by_test_id("cell-" + field)


def _read(page, names):
    out = {key: {"strategic": _cell(page, name, "strategic").inner_text().strip(),
                 "total": _cell(page, name, "total").inner_text().strip(),
                 "priority": _row(page, name).locator("td").nth(8).inner_text().strip()}
           for key, name in names.items()}
    out["average"] = page.get_by_test_id("average-strategic-score").inner_text().strip()
    out["critical"] = page.get_by_test_id("critical-priorities").inner_text().strip()
    return out


def test_every_capability_is_scored_or_says_what_to_record(browser, live_server, two_orgs):
    names, foreign = two_orgs["names"], two_orgs["foreign"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    sign_in(page, live_server, two_orgs["mine"]["emails"]["enterprise_architect"])

    seen = {}
    for path in PAGES:
        page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_row(page, names["recorded"])).to_have_count(1)
        expect(page.locator("body")).not_to_contain_text(foreign["recorded"])
        expect(page.locator("body")).not_to_contain_text(foreign["nothing"])
        seen[path] = _read(page, names)
        rows = seen[path]

        # A recorded rating always wins over what the applications say.
        assert rows["recorded"]["strategic"].startswith("20/25"), rows["recorded"]
        assert "derived" not in rows["recorded"]["strategic"].lower()
        # Each unrated capability with facts is scored from them and says so.
        assert rows["by_apps"]["strategic"].startswith("20/25"), rows["by_apps"]
        assert "derived" in rows["by_apps"]["strategic"].lower()
        assert "criticality of the 2 applications that support it" in rows["by_apps"]["strategic"]
        assert rows["by_goals"]["strategic"].startswith("20/25"), rows["by_goals"]
        assert "2 goals" in rows["by_goals"]["strategic"]
        assert rows["by_stream"]["strategic"].startswith("15/25"), rows["by_stream"]
        assert "value stream" in rows["by_stream"]["strategic"]
        # The total and the priority follow the derived score: no capability with a score reads Not scored.
        for key in ("recorded", "by_apps", "by_goals", "by_stream"):
            assert "Not scored" not in rows[key]["total"], key
            assert rows[key]["priority"] != "NOT SCORED", key
        # A capability with nothing to derive from names the fact to record.
        assert rows["nothing"]["strategic"].startswith("Not scored")
        assert "Record" in rows["nothing"]["strategic"]
        # The average counts the four scored capabilities (20, 20, 20, 15), not five.
        assert "18.8/25" in rows["average"], rows["average"]
        assert "4 of 5" in rows["average"], rows["average"]
        assert "3 worked out from the model" in rows["average"], rows["average"]

    assert seen[PAGES[0]] == seen[PAGES[1]]

    # The evidence of a derived score: the applications it rests on, a low confidence, the source and a date.
    page.goto(live_server + PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    footer = _row(page, names["by_apps"]).get_by_test_id("capability-footer")
    text = footer.inner_text()
    assert "Demand engine" in text and "Spreadsheet" in text
    assert re.search(r"Strategic score 20/25 worked out from", text), text
    assert re.search(r"low", text, re.I)
    assert "Strategic importance: not recorded" in text
    assert re.search(r"\b20\d\d\b", text)
    # Every evidence link lands on a page with content.
    hrefs = footer.locator("a").evaluate_all("els => els.map(e => e.getAttribute('href'))")
    assert hrefs
    for href in {h for h in hrefs if h and h.startswith("/")}:
        landing = context.new_page()
        landing.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert len(landing.locator("main, body").first.inner_text().strip()) > 120, href
        landing.close()
    _shot(page, "derived-score", 1440, "light")

    # The step on an unscored row lands on the page where importance is recorded.
    step = _row(page, names["nothing"]).get_by_test_id("strategic-step")
    expect(step).to_have_count(1)
    href = step.get_attribute("href")
    assert href
    step.click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_text(names["nothing"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
    context.close()

    # The same page at phone width, dark.
    phone = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844},
                                color_scheme="dark")
    small = phone.new_page()
    sign_in(small, live_server, two_orgs["mine"]["emails"]["enterprise_architect"])
    small.goto(live_server + PAGES[1], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = _row(small, names["by_apps"])
    expect(row).to_have_count(1)
    row.scroll_into_view_if_needed()
    assert "20/25" in row.get_by_test_id("phone-strategic").inner_text()
    assert "derived" in row.get_by_test_id("phone-strategic").inner_text().lower()
    assert small.evaluate("document.documentElement.scrollWidth") <= 391
    _shot(small, "derived-score", 390, "dark")
    phone.close()


def test_demo_organisation_leaves_no_capability_unscored_without_a_step(browser, live_server):
    from app import create_app
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with create_app("testing").app_context():
            seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    seen = {}
    for path in PAGES:
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        page = context.new_page()
        _login(page, live_server, DEMO_ADMIN)
        page.goto(live_server + path, timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("capability-row")
        expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        # Show every row: the table pages at 25.
        page.get_by_test_id("per-page-selector").select_option("100")
        count = rows.count()
        assert count >= 20
        cells = []
        for index in range(count):
            row = rows.nth(index)
            strategic = row.get_by_test_id("cell-strategic").inner_text().strip()
            if strategic.startswith("Not scored"):
                # Only a capability with nothing to work from is left, and it says what to record, with a link.
                assert row.get_by_test_id("strategic-step").count() == 1, strategic
                assert row.get_by_test_id("strategic-step").get_attribute("href")
            else:
                assert re.match(r"\d+/25", strategic), strategic
            cells.append((row.get_by_test_id("cell-name").inner_text().split("\n")[0], strategic.split("\n")[0]))
        seen[path] = (sorted(cells), page.get_by_test_id("average-strategic-score").inner_text().strip(),
                      page.get_by_test_id("critical-priorities").inner_text().strip())
        unscored = [c for c in cells if c[1].startswith("Not scored")]
        # Thirteen of 24 capabilities were unscored before the scores were worked out from the model.
        assert len(unscored) < 13, unscored
        _shot(page, "demo-matrix", 1440, "light")
        context.close()
    assert seen[PAGES[0]] == seen[PAGES[1]]
