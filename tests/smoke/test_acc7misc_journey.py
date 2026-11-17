"""The overview figures, roadmap wording, domains and the remaining small defects, as the demo Administrator sees them.

The signed-in person is the Administrator of the demonstration company. One step for each defect a walk of the
live site found: the risk assessment page offers the button its empty list points at, the overview's year
card and run cost chart say what they count, the change history is not filled with updates that changed
nothing, a connector-derived application is not the one the no-owner card names first, Capability Health groups
by the domains the roadmap counts, the roadmap files a reached plateau under Completed and names no package as
its own blocker, the portfolio shows a zero as plain text under an evidence footer, and no link inside text on the
application list depends on colour alone. Each step is run at 1440 wide in the light theme and at 390 wide in the
dark theme.
"""
import os
import re
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
WALK = os.environ.get("ACC7MISC_WALK_DIR")
VIEWS = [("light", 1440, 900), ("dark", 390, 844)]
DERIVED_NAME = "Aardvark Repository Import"
RECORDED_NAME = "Zulu Ledger Recorded By Hand"


@pytest.fixture(scope="module")
def demo(live_server, app):
    """The demonstration organisation, seeded the way the product's own seed does, with one application a
    connector derived from a repository and named so that it sorts before every other."""
    from app import db

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    made = {}
    try:
        with app.app_context():
            from app.commands.seed_demo_company import seed_demo_company
            from app.models.application_portfolio import ApplicationComponent, VendorContract
            from app.models.archimate_core import ArchiMateElement
            from app.models.organization import Organization
            from app.models.vendor.vendor_organization import VendorOrganization
            from app.models.user import User

            seed_demo_company()
            org = Organization.query.filter_by(slug="lantern-quay").one()
            admin = User.query.filter_by(email=DEMO_EMAIL, organization_id=org.id).one()
            admin.password = PASSWORD
            # the Administrator of the organisation, who may read the spend figures
            admin.is_org_admin = True
            element = ArchiMateElement(name=DERIVED_NAME, type="Application", layer="application",
                                       organization_id=org.id, source_kind="connector")
            db.session.add(element)
            db.session.flush()
            row = ApplicationComponent(name=DERIVED_NAME, organization_id=org.id, archimate_element_id=element.id,
                                       component_type="application")
            db.session.add(row)
            recorded = ApplicationComponent(name=RECORDED_NAME, organization_id=org.id, component_type="application")
            db.session.add(recorded)
            vendor = VendorOrganization(name="Acc7 Vendor %s" % uuid.uuid4().hex[:6])
            db.session.add(vendor)
            db.session.flush()
            contract = VendorContract(
                vendor_id=vendor.id, organization_id=org.id, contract_name="Acc7 agreement", status="active",
                contract_number="ACC7-%s" % uuid.uuid4().hex[:6], annual_cost=120000,
                start_date=date.today() - timedelta(days=100))
            db.session.add(contract)
            db.session.commit()
            made = {"contract_id": contract.id, "vendor_id": vendor.id, "org_id": org.id, "element_id": element.id, "application_id": row.id,
                    "recorded_id": recorded.id}
            db.session.remove()
        yield made
    finally:
        with app.app_context():
            from app.models.application_portfolio import ApplicationComponent, VendorContract
            from app.models.archimate_core import ArchiMateElement
            from app.models.vendor.vendor_organization import VendorOrganization

            if made:
                VendorContract.query.filter_by(id=made["contract_id"]).delete(synchronize_session=False)
                VendorOrganization.query.filter_by(id=made["vendor_id"]).delete(synchronize_session=False)
                ApplicationComponent.query.filter(
                    ApplicationComponent.id.in_([made["application_id"], made["recorded_id"]])
                ).delete(synchronize_session=False)
                ArchiMateElement.query.filter_by(id=made["element_id"]).delete(synchronize_session=False)
                db.session.commit()
            db.session.remove()
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous


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


def test_risk_assessment_offers_the_button_its_empty_list_points_at(view, live_server):
    page, scheme, width = view
    _open(page, live_server, "/strategic/risk-assessment", "main", scheme)
    button = page.get_by_role("button", name="Mitigate Risk").first
    button.wait_for(state="visible")
    empty = page.locator("[data-testid='plans-created-here-empty']")
    if empty.count():
        assert "Mitigate Risk" in empty.inner_text()
    button.click()
    dialog = page.locator("#initiative-risk-mitigation-modal")
    dialog.wait_for(state="visible")
    assert "Create Risk Mitigation Plan" in dialog.inner_text()
    _shot(page, "item15-risk-assessment", scheme, width)


def test_overview_year_figure_says_what_it_counts_and_run_cost_draws_its_months(view, live_server, demo):
    page, scheme, width = view
    _open(page, live_server, "/dashboard/overview", "[data-testid='year-card']", scheme)
    card = page.locator("[data-testid='year-card']")
    figure = page.locator("[data-testid='year-value']")
    assert figure.count(), card.inner_text()
    assert re.search(r"\d", figure.inner_text())
    # the figure never stands alone: it names what it counts, or sits beside last year's, each labelled
    if not page.locator("[data-testid='year-last']").count():
        assert "added this year" in figure.inner_text(), figure.inner_text()
    cost = page.locator("[data-testid='monthly-cost-value']")
    cost.wait_for(state="visible")
    page.wait_for_timeout(1200)
    labels = page.locator("[data-testid='monthly-cost-value']").locator(
        "xpath=ancestor::*[@data-slot='card' or contains(@class,'card')][1]").locator("svg text").evaluate_all("els => els.map(e => e.textContent || '')")
    months = [m.strip() for m in labels if re.fullmatch(r"[A-Z][a-z]{2}", m.strip())]
    assert len(set(months)) >= 3, "the run cost chart draws %r though a contract began 100 days ago" % labels
    _shot(page, "item21-22-overview", scheme, width)


def _change_total(page):
    match = re.search(r"([\d,]+) changes?, newest first", page.locator("main").inner_text())
    assert match, page.locator("main").inner_text()[:500]
    return int(match.group(1).replace(",", ""))


def _open_changes(page, base, scheme):
    page.goto(base + "/intelligence/history/changes", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector("[data-testid='change-row'], [data-testid='change-card']", state="attached",
                           timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    if scheme == "dark":
        page.evaluate("() => document.documentElement.classList.add('dark')")


def test_scoring_a_solution_again_adds_no_update_that_says_nothing(view, live_server, demo):
    page, scheme, width = view
    from app import create_app
    from app.models.solution_models import Solution

    from .fresh_org import api

    _open(page, live_server, "/dashboard/overview", "[data-testid='year-card']", scheme)
    with create_app("testing").app_context():
        ids = [s.id for s in Solution.query.filter_by(organization_id=demo["org_id"])]
    assert ids, "the demonstration organisation holds no solution"
    for solution_id in ids:
        status, _ = api(page, "POST", "/solutions/%d/api/blueprint-scores/recalculate" % solution_id, {})
        assert status == 200, status
    _open_changes(page, live_server, scheme)
    before = _change_total(page)
    for solution_id in ids:
        status, _ = api(page, "POST", "/solutions/%d/api/blueprint-scores/recalculate" % solution_id, {})
        assert status == 200, status
    _open_changes(page, live_server, scheme)
    assert _change_total(page) == before, "scoring the solutions again added changes to the history"
    _shot(page, "item23-changes", scheme, width)


def test_unowned_card_does_not_open_with_the_derived_application(view, live_server):
    page, scheme, width = view
    _open(page, live_server, "/dashboard/overview", "[data-testid='year-card']", scheme)
    card = page.locator("main").inner_text()
    match = re.search(r"([^\n]+?) (?:has no owner|and \d+ others? have no owner)", card)
    assert match, card[:800]
    assert match.group(1).startswith(RECORDED_NAME), match.group(0)
    _shot(page, "item24-unowned", scheme, width)


def test_capability_health_groups_by_the_domains_the_roadmap_counts(view, live_server):
    page, scheme, width = view
    _open(page, live_server, "/capability-roadmap", "main", scheme)
    page.wait_for_load_state("networkidle")
    stat = page.locator("main").inner_text()
    domains = int(re.search(r"Domains\s*\n?\s*(\d+)", stat).group(1))
    _open(page, live_server, "/strategic/capability-health", "text=Domain Health Index", scheme)
    rows = page.locator("[data-slot='card']", has_text="Domain Health Index").locator(".space-y-6 > div")
    counts = [int(re.search(r"\((\d+) caps\)", r).group(1)) for r in rows.all_inner_texts()]
    assert counts, "the domain index lists no domain"
    assert len(counts) <= domains, "%d domains listed, the roadmap counts %d" % (len(counts), domains)
    assert max(counts) > 1, "every domain holds one capability: %r" % counts
    _shot(page, "item25-capability-health", scheme, width)


def test_roadmap_files_a_reached_plateau_under_completed_and_names_no_package_its_own_blocker(view, live_server):
    page, scheme, width = view
    _open(page, live_server, "/genome/roadmap/", "[data-testid='roadmap-column']", scheme)
    planned = page.locator("[data-testid='roadmap-column'][data-column='planned']")
    completed = page.locator("[data-testid='roadmap-column'][data-column='completed']")
    assert "Baseline Architecture" not in planned.inner_text()
    assert "Baseline Architecture" in completed.inner_text()
    assert "No work package is planned on this plateau yet" not in completed.inner_text()
    for block in page.locator("[data-testid='critical-path-plateau']").all():
        blocking = block.locator("[data-testid='critical-path-blocking']")
        if not blocking.count():
            continue
        chain = block.locator("[data-testid='critical-path-chain'] span.inline-flex, "
                              "[data-testid='critical-path-chain'] > *").all_inner_texts()
        names = [n.strip() for n in chain if n.strip() and n.strip() != "→"]
        text = blocking.inner_text()
        if len(names) == 1:
            assert not text.startswith("Blocking:"), text
            assert "sets when the plateau is ready" in text, text
    _shot(page, "item26-27-roadmap", scheme, width)


def test_portfolio_shows_a_zero_as_text_under_an_evidence_footer(view, live_server):
    page, scheme, width = view
    _open(page, live_server, "/portfolio/", "[data-testid='portfolio-footer']", scheme)
    footer = page.locator("[data-testid='portfolio-footer']")
    text = footer.inner_text()
    assert "Initiatives shown" in text and "Serving no objective" in text, text
    zero = page.get_by_text("0 initiatives serving no objective")
    if zero.count():
        assert page.get_by_role("link", name="0 initiatives serving no objective").count() == 0
    # a figure that is a link opens a page with content
    link = page.locator("#objective-filter a").first
    if link.count() and "serving no objective" in link.inner_text():
        link.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("main table tbody tr").count() >= 1
    _shot(page, "item28-portfolio", scheme, width)


def test_application_list_links_in_text_are_not_told_apart_by_colour_alone(view, live_server):
    page, scheme, width = view
    _open(page, live_server, "/applications/", "main", scheme)
    import axe_playwright_python

    source = (Path(axe_playwright_python.__file__).parent / "axe.min.js").read_text(encoding="utf-8")
    page.add_script_tag(content=source)
    found = page.evaluate(
        """async () => { const r = await axe.run(document, {runOnly: {type: 'rule', values: ['link-in-text-block']},
          resultTypes: ['violations']}); return r.violations.flatMap(v => v.nodes.map(n => n.target.join(' '))); }""")
    assert found == [], found
    _shot(page, "item30-applications", scheme, width)
