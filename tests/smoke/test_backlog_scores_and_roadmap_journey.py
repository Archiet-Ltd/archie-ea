"""Backlog scores, recommendations on a phone, domain counts, plateau order, singular counts and applications under
review, as the demo Administrator sees them.

The signed-in person is the Administrator of the demonstration company. One step for each defect a walk of the
preview found: a backlog requirement with no recorded inputs is not given a score, a recommendation reads whole on
a phone, the capability roadmap counts the domains Capability Health lists, the critical path lists plateaus in date
order, a count of one reads in the singular, and an application in its sunset stage appears under review. Each step
is run at 1440 wide in the light theme and at 390 wide in the dark theme.
"""
import os
import re
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
WALK = os.environ.get("SMOKE_WALK_DIR")
VIEWS = [("light", 1440, 900), ("dark", 390, 844)]


@pytest.fixture(scope="module")
def demo(live_server, app):
    """The demonstration organisation, seeded the way the product's own seed does."""
    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            from app import db
            from app.commands.seed_demo_company import seed_demo_company
            from app.models.organization import Organization
            from app.models.user import User

            seed_demo_company()
            org = Organization.query.filter_by(slug="lantern-quay").one()
            admin = User.query.filter_by(email=DEMO_EMAIL, organization_id=org.id).one()
            admin.password = PASSWORD
            admin.is_org_admin = True
            db.session.commit()
            # reference domains no capability of this organisation records, as a shared database holds
            from app.models.unified_capability import BusinessDomain

            codes = ["ZQ%d" % n for n in range(1, 4)]
            for code in codes:
                if not BusinessDomain.query.filter_by(code=code).first():
                    db.session.add(BusinessDomain(code=code, name="Unused reference domain %s" % code))
            db.session.commit()
            made = {"org_id": org.id, "domain_codes": codes}
            db.session.remove()
        yield made
    finally:
        with app.app_context():
            from app import db
            from app.models.unified_capability import BusinessDomain

            BusinessDomain.query.filter(BusinessDomain.code.in_(["ZQ1", "ZQ2", "ZQ3"])).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous


def _clear_scores(app, org_id):
    """Take back every score input the journey recorded, for this organisation's requirements only."""
    with app.app_context():
        from app import db
        from app.models.models import Requirement

        Requirement.query.filter(Requirement.organization_id == org_id).update(
            {"business_value": None, "time_criticality": None, "risk_reduction": None, "job_size": None,
             "reach": None, "impact": None, "confidence": None, "story_points": None},
            synchronize_session=False)
        db.session.commit()
        db.session.remove()


@pytest.fixture(autouse=True)
def no_scores_recorded(app, demo):
    _clear_scores(app, demo["org_id"])
    yield
    _clear_scores(app, demo["org_id"])


@pytest.fixture(params=VIEWS, ids=["1440-light", "390-dark"])
def view(request, browser, live_server, demo):
    scheme, width, height = request.param
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, live_server, DEMO_EMAIL)
    yield page, scheme, width
    context.close()


def _open(page, base, path, ready, scheme):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector(ready, timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    if scheme == "dark":
        page.evaluate("() => document.documentElement.classList.add('dark')")


def _shot(page, name, scheme, width):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(800)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, scheme, width)), full_page=True)


def _scores(page, tab, testid):
    page.get_by_role("tab", name=tab).click()
    panel = page.locator("[role='tabpanel']:visible")
    panel.locator("[data-testid='%s']" % testid).first.wait_for(state="visible")
    return panel, panel.locator("[data-testid='%s']" % testid).all_inner_texts()


def _row_named(page, name):
    """The row for one requirement. The tables re-rank as inputs are typed, so a row is found by its name, never by position."""
    return page.locator("[role='tabpanel']:visible tbody tr").filter(
        has=page.locator("td:first-child", has_text=re.compile(r"^\s*%s\s*$" % re.escape(name))))


def test_backlog_scores_are_worked_out_from_recorded_inputs_or_say_they_are_not(view, live_server):
    page, scheme, width = view
    _open(page, live_server, "/backlog/prioritisation", "[role='tab']", scheme)
    page.wait_for_selector("text=Not yet prioritised")
    _, wsjf = _scores(page, "WSJF", "wsjf-score")
    assert wsjf, "the WSJF tab lists no requirement"
    assert all(t.startswith("Not worked out") for t in wsjf), "a score appears for requirements with no recorded inputs: %r" % wsjf
    _, rice = _scores(page, "RICE", "rice-score")
    assert rice and all(t.startswith("Not worked out") for t in rice), rice
    _shot(page, "backlog-not-worked-out", scheme, width)

    # record the four inputs on the first requirement, save, and the score is worked out after a reload
    page.get_by_role("tab", name="WSJF").click()
    name = page.locator("[role='tabpanel']:visible tbody tr").first.locator("td").first.inner_text().strip()
    row = _row_named(page, name)
    for label, value in (("Business value", "8"), ("Time criticality", "5"), ("Risk reduction", "3"), ("Job size", "4")):
        row.get_by_label(label).fill(value)
    row.get_by_role("button", name="Save").click()
    page.wait_for_function(
        "() => [...document.querySelectorAll(\"[data-testid='wsjf-score']\")].some(e => e.textContent.trim() === '4.00')")
    _open(page, live_server, "/backlog/prioritisation", "[role='tab']", scheme)
    page.get_by_role("tab", name="WSJF").click()
    first = page.locator("[role='tabpanel']:visible tbody tr").first
    assert name in first.inner_text()
    assert first.locator("[data-testid='wsjf-score']").inner_text().strip() == "4.00", first.inner_text()
    others = page.locator("[role='tabpanel']:visible tbody tr [data-testid='wsjf-score']").all_inner_texts()[1:]
    assert others and all(t.startswith("Not worked out") for t in others), others
    _shot(page, "backlog-worked-out", scheme, width)

    # RICE needs story points too: reach 200 x impact 3 x 50% / 5 points = 60.00
    page.get_by_role("tab", name="RICE").click()
    row = _row_named(page, name)
    for label, value in (("Reach", "200"), ("Impact", "3"), ("Confidence", "50"), ("Story points", "5")):
        row.get_by_label(label).fill(value)
    row.get_by_role("button", name="Save").click()
    page.wait_for_function(
        "() => [...document.querySelectorAll(\"[data-testid='rice-score']\")].some(e => e.textContent.trim() === '60.00')")
    _open(page, live_server, "/backlog/prioritisation", "[role='tab']", scheme)
    page.get_by_role("tab", name="RICE").click()
    top = page.locator("[role='tabpanel']:visible tbody tr [data-testid='rice-score']").first
    assert top.inner_text().strip() == "60.00"


def test_each_recommendation_reads_whole_on_a_phone(view, live_server):
    page, scheme, width = view
    _open(page, live_server, "/recommendations", "[data-testid='recommendation-row']", scheme)
    assert page.locator("[data-testid='recommendation-row']").count() >= 5
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"
    clipped = page.evaluate(
        """(width) => [...document.querySelectorAll("[data-testid='recommendation-row']")].flatMap(row =>
            [...row.querySelectorAll("[data-testid='recommendation-title'], [data-testid='recommendation-reason'], [data-testid='recommendation-record']")]
              .filter(e => { const r = e.getBoundingClientRect(); return r.right > width + 1 || r.left < -1 || e.scrollWidth > e.clientWidth + 1; })
              .map(e => e.textContent.trim().slice(0, 60)))""", width)
    assert clipped == [], "text runs past the edge of the screen: %r" % clipped
    # the card holding the table does not hide any of it either
    hidden = page.evaluate(
        """() => { const t = document.querySelector("[data-testid='recommendations-table']");
          const out = []; for (let n = t; n && n !== document.body; n = n.parentElement) {
            const s = getComputedStyle(n);
            if (n.scrollWidth > n.clientWidth + 1 && s.overflowX !== 'visible') out.push((n.className || n.tagName).toString().slice(0, 60) + ' ' + n.scrollWidth + '>' + n.clientWidth); }
          return out; }""")
    assert hidden == [], hidden
    _shot(page, "recommendations-whole", scheme, width)


def test_capability_roadmap_counts_the_domains_capability_health_lists(view, live_server):
    page, scheme, width = view
    _open(page, live_server, "/capability-roadmap", "main", scheme)
    stat = page.locator("main").inner_text()
    domains = int(re.search(r"Domains\s*\n?\s*(\d+)", stat).group(1))
    _shot(page, "roadmap-domains", scheme, width)
    _open(page, live_server, "/strategic/capability-health", "text=Domain Health Index", scheme)
    rows = page.locator("[data-slot='card']", has_text="Domain Health Index").locator(".space-y-6 > div")
    listed = len(rows.all_inner_texts())
    assert listed >= 2, "the domain index lists %d domains" % listed
    assert domains == listed, "the roadmap counts %d domains, Capability Health lists %d" % (domains, listed)
    _shot(page, "capability-health-domains", scheme, width)


def test_critical_path_lists_plateaus_in_date_order(view, live_server, demo):
    page, scheme, width = view
    _open(page, live_server, "/genome/roadmap/", "[data-testid='critical-path-plateau']", scheme)
    shown = [b.locator("span").first.inner_text().strip() for b in page.locator("[data-testid='critical-path-plateau']").all()]
    from app import create_app
    from app.models.implementation_migration import Plateau

    with create_app("testing").app_context():
        dates = {p.name: p.target_date for p in Plateau.query.filter_by(organization_id=demo["org_id"])}
    assert len(shown) >= 4 and all(dates.get(n) for n in shown), (shown, dates)
    assert [dates[n] for n in shown] == sorted(dates[n] for n in shown), "plateaus out of date order: %r" % [
        (n, str(dates[n])) for n in shown]
    assert shown.index("Transition State 1") < shown.index("Future State")
    _shot(page, "critical-path-order", scheme, width)


def test_a_count_of_one_reads_in_the_singular(view, live_server):
    page, scheme, width = view
    bad = re.compile(r"(?<![\d.,])1 (apps|solutions|reqs|requirements)\b")
    _open(page, live_server, "/strategic/capability-health", "text=Domain Health Index", scheme)
    health = page.locator("main").inner_text()
    assert not bad.search(health), bad.search(health).group(0)
    assert re.search(r"(?<![\d.,])1 app\b", health), "no capability records exactly one application, so the singular is not exercised"
    _shot(page, "singular-health", scheme, width)
    _open(page, live_server, "/strategic/investment-matrix", "main", scheme)
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("[data-testid='cell-strategic']")
    matrix = page.locator("main").inner_text()
    assert not bad.search(matrix), bad.search(matrix).group(0)
    _shot(page, "singular-matrix", scheme, width)


def test_applications_in_their_sunset_stage_appear_under_review(view, live_server):
    page, scheme, width = view
    _open(page, live_server, "/recommendations", "[data-testid='recommendation-row']", scheme)
    retire = [t for t in page.locator("[data-testid='recommendation-title']").all_inner_texts()
              if t.startswith("Plan the retirement of ")]
    assert retire, "no recommendation asks for a retirement plan"
    names = [t[len("Plan the retirement of "):].strip() for t in retire]
    _open(page, live_server, "/enterprise/analysis/ai-architecture", "main", scheme)
    section = page.locator("main").inner_text()
    assert "Portfolio Up to Date" not in section, section
    review = page.locator("div.rounded-lg", has_text="Applications Under Review").last
    for name in names:
        assert name in review.inner_text(), "%s is in its sunset stage and is not under review" % name
    # the card figure is the number of applications under review, the same as the list beneath it
    listed = review.get_by_role("link").count()
    card = page.locator("[data-testid='ai-analysis-apps-review']").inner_text()
    assert re.search(r"\b%d\b" % listed, card), (card, listed)
    _shot(page, "under-review", scheme, width)
    link = review.get_by_role("link", name=names[0])
    link.click()
    page.wait_for_load_state("domcontentloaded")
    assert names[0] in page.locator("main").inner_text()


def test_a_recorded_one_and_a_reach_of_zero_are_kept_after_a_reload(view, live_server):
    page, scheme, width = view
    _open(page, live_server, "/backlog/prioritisation", "[role='tab']", scheme)
    page.wait_for_selector("text=Not yet prioritised")
    page.get_by_role("tab", name="WSJF").click()
    name = page.locator("[role='tabpanel']:visible tbody tr").first.locator("td").first.inner_text().strip()
    row = _row_named(page, name)
    for label in ("Business value", "Time criticality", "Risk reduction", "Job size"):
        row.get_by_label(label).fill("1")
    row.get_by_role("button", name="Save").click()
    page.wait_for_function(
        "() => [...document.querySelectorAll(\"[data-testid='wsjf-score']\")].some(e => e.textContent.trim() === '3.00')")
    _open(page, live_server, "/backlog/prioritisation", "[role='tab']", scheme)
    page.get_by_role("tab", name="WSJF").click()
    first = _row_named(page, name)
    assert first.locator("[data-testid='wsjf-score']").inner_text().strip() == "3.00", first.inner_text()
    for label in ("Business value", "Time criticality", "Risk reduction", "Job size"):
        assert first.get_by_label(label).input_value() == "1", label
    # the score is the first thing after the name, so it is on screen at 390 wide
    box = first.locator("[data-testid='wsjf-score']").bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, box
    _shot(page, "backlog-recorded-ones", scheme, width)

    page.get_by_role("tab", name="RICE").click()
    name = page.locator("[role='tabpanel']:visible tbody tr").first.locator("td").first.inner_text().strip()
    row = _row_named(page, name)
    for label, value in (("Reach", "0"), ("Impact", "3"), ("Confidence", "80"), ("Story points", "2")):
        row.get_by_label(label).fill(value)
    # the score shows on screen as the inputs are typed, so wait for the save itself before reloading
    with page.expect_response(lambda r: "/rice" in r.url and r.request.method == "PATCH") as saved:
        row.get_by_role("button", name="Save").click()
    assert saved.value.status == 200
    _open(page, live_server, "/backlog/prioritisation", "[role='tab']", scheme)
    page.get_by_role("tab", name="RICE").click()
    recorded = _row_named(page, name)
    assert recorded.get_by_label("Reach").input_value() == "0"
    assert recorded.locator("[data-testid='rice-score']").inner_text().strip() == "0.00"
    box = recorded.locator("[data-testid='rice-score']").bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, box
    _shot(page, "backlog-recorded-reach-zero", scheme, width)


def test_the_roadmap_domains_card_says_why_when_no_capability_is_assessed(view, live_server):
    page, scheme, width = view
    org = create_fresh_org("enterprise_architect")
    page.context.clear_cookies()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])
    _open(page, live_server, "/capability-roadmap", "main", scheme)
    card = page.locator("[data-slot='card']", has_text="Domains").last
    basis = card.locator("[data-testid='roadmap-domains-basis']")
    assert "No capabilities are recorded yet" in basis.inner_text()
    assert not re.search(r"Domains\s*\n?\s*0\b", page.locator("main").inner_text())
    box = basis.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, box
    _shot(page, "roadmap-domains-empty", scheme, width)


def test_clearing_a_recorded_score_empties_its_inputs_after_a_reload(view, live_server):
    page, scheme, width = view
    _open(page, live_server, "/backlog/prioritisation", "[role='tab']", scheme)
    page.wait_for_selector("text=Not yet prioritised")
    page.get_by_role("tab", name="WSJF").click()
    name = page.locator("[role='tabpanel']:visible tbody tr").first.locator("td").first.inner_text().strip()
    row = _row_named(page, name)
    for label in ("Business value", "Time criticality", "Risk reduction", "Job size"):
        row.get_by_label(label).fill("1")
    with page.expect_response(lambda r: "/wsjf" in r.url and r.request.method == "PATCH") as saved:
        row.get_by_role("button", name="Save").click()
    assert saved.value.status == 200
    with page.expect_response(lambda r: "/wsjf" in r.url and r.request.method == "PATCH") as cleared:
        _row_named(page, name).get_by_role("button", name="Clear WSJF inputs").click()
    assert cleared.value.status == 200
    _open(page, live_server, "/backlog/prioritisation", "[role='tab']", scheme)
    page.get_by_role("tab", name="WSJF").click()
    after = _row_named(page, name)
    assert after.locator("[data-testid='wsjf-score']").inner_text().strip().startswith("Not worked out")
    for label in ("Business value", "Time criticality", "Risk reduction", "Job size"):
        assert after.get_by_label(label).input_value() == "", label
    _shot(page, "backlog-cleared", scheme, width)
