"""An enterprise architect decides where to invest next, and the matrix scores each capability from the model.

Goal: "I see which capabilities to fund first, and every figure comes from what
the model records: importance, linked objectives, linked risks, linked work."
The organisation is seeded with linked data (a domain and a child under it, a
capability with no rating, applications with risks, a costed work package, and
a second organisation whose capability, risk and cost must never appear).
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.fresh_org import create_fresh_org, sign_in

PAGE_TIMEOUT = 30000
PAGES = ("/strategic/investment-matrix", "/architecture/investment-priorities")
FIELDS = ("domain", "strategic", "risk", "total", "investment")


def _seed_org(app_ctx, org_id, tag, *, costed):
    """Four capabilities for one organisation; returns their names."""
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.motivation import Goal
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.models.unified_capability import BusinessDomain, UnifiedCapability
    from app.models.models import ArchiMateRelationship
    from app.services.archimate_backbone import create_backbone_element

    names = {
        "top": "Serve customers %s" % tag,
        "child": "Answer calls %s" % tag,
        "plain": "Keep records %s" % tag,
        "unrated": "Plan events %s" % tag,
        "domain": "Customer domain %s" % tag,
    }
    domain = BusinessDomain(code=("D%s" % uuid.uuid4().hex[:8]), name=names["domain"])
    db.session.add(domain)
    db.session.flush()

    def capability(key, importance, **extra):
        element = create_backbone_element(
            element_type="Capability", layer="strategy", name=names[key], organization_id=org_id)
        cap = UnifiedCapability(
            name=names[key], organization_id=org_id, strategic_importance=importance,
            archimate_element_id=element.id, **extra)
        db.session.add(cap)
        db.session.flush()
        return cap, element

    top, top_el = capability("top", "critical", domain_id=domain.id, level=1)
    child, child_el = capability("child", "high", parent_capability_id=top.id, level=2)
    plain, plain_el = capability("plain", "medium", level=1)
    unrated, _ = capability("unrated", None, level=1)

    for cap_el, goal_name in ((top_el, "Grow revenue %s"), (child_el, "Cut call waits %s")):
        goal_el = create_backbone_element(
            element_type="Goal", layer="motivation", name=goal_name % tag, organization_id=org_id)
        db.session.add(Goal(name=goal_name % tag, organization_id=org_id,
                            archimate_element_id=goal_el.id))
        db.session.add(ArchiMateRelationship(
            source_id=cap_el.id, target_id=goal_el.id, type="realization",
            organization_id=org_id))

    for cap, label, likelihood, impact in ((top, "Billing", 5, 4), (child, "Telephony", 2, 3)):
        application = ApplicationComponent(name="%s %s" % (label, tag), organization_id=org_id)
        db.session.add(application)
        db.session.flush()
        db.session.add(UnifiedApplicationCapabilityMapping(
            unified_capability_id=cap.id, application_component_id=application.id,
            support_level="full", coverage_percentage=80, is_active=True))
        risk = Risk(title="%s outage %s" % (label, tag), likelihood=likelihood, impact=impact,
                    organization_id=org_id)
        db.session.add(risk)
        db.session.flush()
        db.session.add(RiskEntityLink(
            risk_id=risk.id, entity_type="application", entity_id=application.id,
            organization_id=org_id))

    if costed:
        db.session.add(UnifiedWorkPackage(
            name="Replace billing %s" % tag, capability_id=top.id, estimated_cost=120000.0,
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
        names = _seed_org(app, mine["org_id"], "m" + tag, costed=True)
        foreign = _seed_org(app, other["org_id"], "f" + tag, costed=True)
    return {"mine": mine, "names": names, "foreign": foreign}


def _row(page, name):
    return page.get_by_test_id("capability-row").filter(has_text=name)


def _cells(page, name):
    row = _row(page, name)
    expect(row).to_have_count(1)
    return {f: row.get_by_test_id("cell-" + f).inner_text().strip() for f in FIELDS}


@pytest.mark.smoke
@pytest.mark.journey
def test_matrix_scores_each_capability_from_the_model_and_both_pages_agree(browser, live_server, two_orgs):
    names, foreign = two_orgs["names"], two_orgs["foreign"]
    page = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900}).new_page()
    sign_in(page, live_server, two_orgs["mine"]["emails"]["enterprise_architect"])

    seen = {}
    for path in PAGES:
        page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_row(page, names["top"])).to_have_count(1)
        body = page.locator("body")
        expect(body).not_to_contain_text(foreign["top"])
        expect(body).not_to_contain_text("Unknown")
        seen[path] = {key: _cells(page, names[key]) for key in ("top", "child", "plain", "unrated")}

        rows = seen[path]
        # The domain is the capability's own, or the one it inherits from its parent.
        assert names["domain"] in rows["top"]["domain"]
        assert names["domain"] in rows["child"]["domain"]
        assert "Unknown" not in rows["plain"]["domain"] + rows["unrated"]["domain"]
        # Strategic score follows importance; an unrated capability is not scored.
        assert rows["top"]["strategic"].startswith("25/25")
        assert rows["child"]["strategic"].startswith("20/25")
        assert rows["plain"]["strategic"].startswith("15/25")
        assert rows["unrated"]["strategic"].startswith("Not scored")
        assert rows["unrated"]["total"].startswith("Not scored")
        # Risk follows the risks linked through the applications that run the capability.
        assert rows["top"]["risk"].startswith("20/25")
        assert rows["child"]["risk"].startswith("6/25")
        assert "No risks linked" in rows["plain"]["risk"]
        assert len({r["risk"].split(" ")[0] for r in rows.values()}) >= 2
        assert len({r["strategic"].split(" ")[0] for r in rows.values()}) >= 3
        # Investment comes from linked work and says so; otherwise it is not estimated.
        assert "120.0k" in rows["top"]["investment"] and "work package" in rows["top"]["investment"].lower()
        assert "Not estimated" in rows["plain"]["investment"]
        assert "100.0k" not in body.inner_text() and "240.0k" not in body.inner_text()
        # The total sums real figures only and says how many capabilities it covers.
        total = page.get_by_test_id("total-investment").inner_text()
        assert "120.0k" in total
        assert re.search(r"from 1 of 4 capabilities", total), total

    assert seen[PAGES[0]] == seen[PAGES[1]]


@pytest.mark.smoke
@pytest.mark.journey
def test_matrix_keeps_every_field_reachable_at_phone_width(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844}).new_page()
    sign_in(page, live_server, two_orgs["mine"]["emails"]["enterprise_architect"])
    for path in PAGES:
        page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_row(page, names["top"])).to_have_count(1)
        scroller = page.get_by_test_id("matrix-scroll")
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        assert scroller.evaluate("e => e.scrollWidth > e.clientWidth")
        row = _row(page, names["top"])
        name_cell = row.get_by_test_id("cell-name")
        for field in FIELDS + ("actions",):
            cell = row.get_by_test_id("cell-" + field)
            row.evaluate("e => e.scrollIntoView({block: 'center', inline: 'nearest'})")
            # Scroll the cell to sit just right of the fixed name column, then it must
            # be wholly inside the card and not covered by anything.
            scroller.evaluate(
                "(e, args) => { const c = e.querySelector(args.sel);"
                " const n = e.querySelector(args.name);"
                " e.scrollLeft = c.getBoundingClientRect().left - e.getBoundingClientRect().left"
                " + e.scrollLeft - n.getBoundingClientRect().width; }",
                {"sel": '[data-testid="capability-row"]:has([data-testid="cell-name"]) [data-testid="cell-%s"]' % field,
                 "name": '[data-testid="cell-name"]'})
            box, frame = cell.bounding_box(), scroller.bounding_box()
            assert box["x"] >= frame["x"] - 1, field
            assert box["x"] + box["width"] <= frame["x"] + frame["width"] + 1, field
            covered = cell.evaluate(
                "e => { const r = e.getBoundingClientRect();"
                " const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);"
                " return !e.contains(top); }")
            assert not covered, "%s is hidden behind another column" % field
        # The name stays in view while the other columns scroll past it.
        scroller.evaluate("e => { e.scrollLeft = e.scrollWidth }")
        name_box, frame = name_cell.bounding_box(), scroller.bounding_box()
        assert name_box["x"] >= frame["x"] - 1 and name_box["x"] < frame["x"] + frame["width"]
        assert names["top"][:8] in name_cell.inner_text()
