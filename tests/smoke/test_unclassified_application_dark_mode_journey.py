"""A portfolio manager reads the applications list in dark mode and opens an
unclassified application to classify it.

The applications list is the surface where the dark-mode token work and the
carried main changes meet on one page: the newer list tells a person which
applications are not yet classified and, when they may edit, offers the way
in; the dark-mode tokens must keep that text legible while dark mode is on.
The journey clicks the real theme toggle, reads the classification prompt in
dark mode and follows it to the edit page, and checks the screen stays dark
there.
"""

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login, _visit, page  # noqa: F401
from .test_header_search_and_theme import _contrast_ratio, _parse_rgb, _relative_luminance

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _contrast_against_surface(page, locator):
    """WCAG contrast of an element's text colour against the surface behind
    it (the nearest opaque ancestor), exactly as the browser renders it."""
    text = locator.evaluate("el => window.getComputedStyle(el).color")
    background = locator.evaluate(
        """el => {
            let node = el;
            while (node) {
                const c = window.getComputedStyle(node).backgroundColor;
                if (c !== 'transparent' && c !== 'rgba(0, 0, 0, 0)') {
                    return c;
                }
                node = node.parentElement;
            }
            return 'rgb(0, 0, 0)';
        }"""
    )
    return _contrast_ratio(
        _relative_luminance(*_parse_rgb(text)),
        _relative_luminance(*_parse_rgb(background)),
    )


def test_unclassified_application_is_legible_and_reachable_in_dark_mode(page, live_server, seeded):
    """The seeded application has no component type, so the list offers the
    person who may edit a route in: "Not set: open to classify".  With dark
    mode on that prompt must still read at 4.5:1, and clicking it must land
    on the edit page with content, dark mode intact."""
    _login(page, live_server, seeded["emails"]["application_manager"])
    app_id = seeded["ids"]["application"]

    _visit(page, live_server, "/applications/")

    # The merged list renders the new, plain-language column headers and the
    # classification affordance for an unclassified application.
    page.locator("th", has_text="ArchiMate").first.wait_for(state="visible", timeout=PAGE_TIMEOUT)

    classify = page.locator('[data-testid="app-type-%d"]' % app_id)
    classify.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    label = classify.inner_text().strip()
    assert "Not set" in label and "classify" in label, (
        "the unclassified application did not render the classification prompt; got %r" % label
    )

    row = page.locator("tr", has=classify)
    stated = row.locator("span", has_text="Linked").count() + row.locator(
        "span", has_text="Not linked"
    ).count()
    assert stated >= 1, (
        "the row did not state the ArchiMate linkage in plain words (Linked or Not linked)"
    )

    # Dark mode goes on through the theme toggle, the way a person does it.
    page.locator("#user-menu-btn").click()
    page.wait_for_timeout(400)
    dark_item = page.locator('button[role="menuitemcheckbox"]')
    dark_item.click()
    page.wait_for_timeout(400)
    assert page.evaluate("() => document.documentElement.classList.contains('dark')"), (
        "the dark class did not apply after clicking the Dark theme toggle"
    )

    # The classification prompt stays legible on the dark surface.
    ratio = _contrast_against_surface(page, classify)
    assert ratio >= 4.5, (
        "the classification prompt text measured %.2f:1 on the dark page; needs 4.5:1" % ratio
    )

    # Following the prompt lands on the application edit page, still dark.
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        classify.click()
    page.wait_for_timeout(800)
    assert page.url.endswith("/applications/%d/edit" % app_id), (
        "the classification prompt went to %r instead of the edit page" % page.url
    )
    assert "Edit Application" in page.inner_text("body"), (
        "the edit page rendered without its heading"
    )
    assert page.locator("#name").count() == 1, "the edit form has no name field"
    assert page.evaluate("() => document.documentElement.classList.contains('dark')"), (
        "dark mode was lost when the person followed the classification prompt"
    )


# ---------------------------------------------------------------------------
# Colour contrast on the six templates the dark-mode token pull request changes
# ---------------------------------------------------------------------------
#
# The pull request body claims "0 failing nodes", but axe still reports
# colour-contrast failures, in light and dark, on six templates the pull
# request itself edits: the capability-map hierarchy, the product roadmap, the
# stakeholder map, the ArchiMate roadmap, the strategic roadmap and the account
# page's own-details panel.  Each page paints text that misses WCAG AA 4.5:1 —
# raw Tailwind palette colours written from page JavaScript, hardcoded hex fills
# in an SVG canvas, a primary-tint-with-info-text token mismatch, or
# opacity-reduced token text.
#
# These assertions reach each page as a signed-in persona, in an organisation
# seeded with the linked data the page needs to render real content (a
# capability tree with coverage, a product-roadmap board with epics and sprints,
# stakeholders linked to a solution, work packages on the roadmaps), toggle the
# real theme control, and run axe-core's colour-contrast rule.  A page shown in
# its empty state would hide exactly the recoloured elements this change fixes,
# so the data is seeded first and its presence is asserted before the audit.
#
# axe is run with the full WCAG 2.2 AA tag set (the shared TAGS list every axe
# run under tests/smoke uses — a static test enforces that) and the
# colour-contrast violation is read out of the result, so the assertion is "zero
# colour-contrast failing nodes" without narrowing the audited standard.

import datetime

from .conftest import PASSWORD  # noqa: F401  (re-exported for symmetry with the file above)
from .test_accessibility_audit import TAGS

# One representative persona per page.  Every page below is @login_required
# only (no role gate — test_authorisation_matrix.py), so any seeded archetype
# reaches it; the persona chosen is the one whose job the page serves.
CONTRAST_PAGES = [
    ("business_architect", "capability hierarchy", "/capability-map/hierarchy",
     '[data-testid="capability-legend"]'),
    ("portfolio_manager", "product roadmap", "/product-roadmap",
     "text=Sprint Timeline"),
    ("business_architect", "stakeholder map", "__stakeholder_map__",
     "#stk-svg .stk-node"),
    ("enterprise_architect", "archimate roadmap", "/archimate-roadmap",
     None),
    ("enterprise_architect", "strategic roadmap", "/strategic/roadmap",
     None),
    ("platform_admin", "account details", "/account/manage",
     '[data-testid="active-organization-badge"]'),
]


def _seed_contrast_content(org_id, solution_id, application_id):
    """Create the linked rows the six pages read, so each renders real content
    rather than an empty state.  Returns the stakeholder count created so the
    caller can assert the map actually rendered nodes."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.adm_kanban import KanbanBoard
        from app.models.models import Requirement
        from app.models.solution_stakeholder import (
            SolutionStakeholder,
            SolutionStakeholderMapping,
            StakeholderAttitude,
            StakeholderType,
        )
        from app.models.sprint import Sprint, SprintStatus
        from app.models.unified_application_capability_mapping import (
            UnifiedApplicationCapabilityMapping,
        )
        from app.models.unified_capability import BusinessDomain, UnifiedCapability
        from app.models.unified_work_package import UnifiedWorkPackage

        tag = "c884"

        # Idempotent: the smoke database persists between runs, so a second run
        # against the same organisation must not duplicate rows (or collide on a
        # globally-unique key). If this organisation already has the tree, the
        # content is already present.
        existing = UnifiedCapability.query.filter_by(
            organization_id=org_id, level=1).filter(
            UnifiedCapability.name.like("Customer Management %")).count()
        if existing:
            return 5

        # ── Capability hierarchy: a three-level tree with domains, types and a
        #    mapped application so the coverage legend and dots render.
        #    business_domains.code is globally unique, so key it on the org.
        domain = BusinessDomain(code=("D%d" % org_id)[:10], name="Operations %s" % tag)
        db.session.add(domain)
        db.session.flush()
        root = UnifiedCapability(
            name="Customer Management %s" % tag, code="CUST84-%s" % org_id, level=1,
            capability_type="strategic", category="core",
            domain_id=domain.id, organization_id=org_id,
        )
        db.session.add(root)
        db.session.flush()
        child = UnifiedCapability(
            name="Relationship Management %s" % tag, code="CRM84-%s" % org_id, level=2,
            capability_type="operational", category="differentiating",
            domain_id=domain.id, organization_id=org_id, parent_capability_id=root.id,
        )
        db.session.add(child)
        db.session.flush()
        grandchild = UnifiedCapability(
            name="Lead Capture %s" % tag, code="LEAD84-%s" % org_id, level=3,
            capability_type="supporting", category="supporting",
            domain_id=domain.id, organization_id=org_id, parent_capability_id=child.id,
        )
        db.session.add(grandchild)
        db.session.flush()
        # Coverage: map the seeded application to the child capability so the
        # legend renders and a coverage dot paints.
        db.session.add(UnifiedApplicationCapabilityMapping(
            unified_capability_id=child.id, application_component_id=application_id,
            support_level="full", coverage_percentage=80,
        ))

        # ── Product roadmap: an epic per horizon, each with stories so points
        #    and a progress bar render, plus a sprint so the sprint strip paints.
        for horizon in ("now", "next", "later"):
            epic = Requirement(
                title="%s epic %s" % (horizon.title(), tag),
                requirement_type="epic", horizon=horizon, organization_id=org_id,
            )
            db.session.add(epic)
            db.session.flush()
            db.session.add_all([
                Requirement(title="Done story %s %s" % (horizon, tag),
                            requirement_type="story", epic_id=epic.id,
                            story_points=5, dod_complete=True, organization_id=org_id),
                Requirement(title="Open story %s %s" % (horizon, tag),
                            requirement_type="story", epic_id=epic.id,
                            story_points=3, dod_complete=False, organization_id=org_id),
            ])
        # The user who creates the board is any seeded user in this org.
        from app.models.user import User

        board_owner = User.query.filter_by(organization_id=org_id).first()
        board = KanbanBoard(
            name="Delivery board %s" % tag, organization_id=org_id,
            created_by_id=board_owner.id,
        )
        db.session.add(board)
        db.session.flush()
        today = datetime.date.today()
        db.session.add_all([
            Sprint(name="Sprint A %s" % tag, board_id=board.id,
                   status=SprintStatus.ACTIVE, start_date=today,
                   end_date=today + datetime.timedelta(days=14)),
            Sprint(name="Sprint B %s" % tag, board_id=board.id,
                   status=SprintStatus.PLANNING, start_date=today + datetime.timedelta(days=15),
                   end_date=today + datetime.timedelta(days=28)),
        ])

        # ── Stakeholder map: one stakeholder per attitude, linked to the seeded
        #    solution, so every attitude fill paints.
        attitudes = [
            StakeholderAttitude.CHAMPION, StakeholderAttitude.SUPPORTER,
            StakeholderAttitude.NEUTRAL, StakeholderAttitude.SKEPTIC,
            StakeholderAttitude.BLOCKER,
        ]
        for index, attitude in enumerate(attitudes):
            stakeholder = SolutionStakeholder(
                name="Stakeholder %s %s" % (attitude.value, tag),
                stakeholder_type=StakeholderType.INDIVIDUAL,
                influence_level=1 + (index % 5), interest_level=1 + ((index + 2) % 5),
                attitude=attitude, organization_id=org_id,
            )
            db.session.add(stakeholder)
            db.session.flush()
            db.session.add(SolutionStakeholderMapping(
                stakeholder_id=stakeholder.id, solution_id=solution_id,
            ))

        # ── Both roadmaps read the same unified work-package store: rows across
        #    the layers and statuses so the timeline bars and status badges
        #    (including the 'Planned' badge this change recolours) render.
        statuses = ["Planned", "In Progress", "Completed", "On Hold"]
        layers = ["implementation", "business", "application", "technology"]
        for index, (status, layer) in enumerate(zip(statuses, layers)):
            db.session.add(UnifiedWorkPackage(
                name="Work package %d %s" % (index, tag),
                description="Roadmap contrast fixture.",
                layer=layer, status=status,
                start_date=datetime.datetime(2026, 1 + index, 1),
                end_date=datetime.datetime(2026, 6 + index, 1),
                priority="high", risk_level="medium", progress_percentage=index * 20,
                business_capability=child.name, organization_id=org_id,
            ))

        db.session.commit()
        return len(attitudes)


def _color_contrast_nodes(axe, page):
    """Number of colour-contrast failing nodes axe reports on the current page.

    Runs the shared WCAG 2.2 AA tag set (every axe run under tests/smoke uses
    exactly TAGS; test_accessibility_audit enforces it) and reads the
    colour-contrast violation out of the result."""
    report = axe.run(page, options={"runOnly": {"type": "tag", "values": TAGS}})
    data = report.response if hasattr(report, "response") else report
    nodes = 0
    targets = []
    for violation in data.get("violations", []):
        if violation.get("id") != "color-contrast":
            continue
        for node in violation.get("nodes", []):
            nodes += 1
            target = node.get("target") or []
            targets.append(" > ".join(str(part) for part in target))
    return nodes, targets


def _enable_dark_theme(page):
    """Turn dark mode on through the real user-menu toggle."""
    page.locator("#user-menu-btn").click()
    page.wait_for_timeout(300)
    page.locator('button[role="menuitemcheckbox"]').click()
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.documentElement.classList.contains('dark')"), (
        "dark mode did not apply after clicking the theme toggle"
    )


def _reach_contrast_page(page, base, path, content_selector, solution_id):
    """Load one of the six pages as the signed-in persona and wait for its real
    content.  The stakeholder map is driven through its real solution picker."""
    from .conftest import PAGE_TIMEOUT

    if path == "__stakeholder_map__":
        page.goto(base + "/stakeholders/map", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)
        try:
            page.eval_on_selector_all(
                "[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
        except Exception:
            pass
        # Pick the seeded solution the way a person does; the canvas then loads.
        page.select_option("select[aria-label='Select a solution...']", str(solution_id))
        page.wait_for_timeout(1500)
    else:
        page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)
        try:
            page.eval_on_selector_all(
                "[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
        except Exception:
            pass
    if content_selector:
        page.locator(content_selector).first.wait_for(state="attached", timeout=PAGE_TIMEOUT)


import importlib

import pytest as _pytest

AXE_MODULE = "axe_playwright_python.sync_playwright"
AXE_PACKAGE = "axe-playwright-python"


@_pytest.fixture(scope="module")
def axe_module(browser):
    """The axe-playwright-python module, or a hard failure — never a skip.

    Depends on ``browser`` so the tier's own skip (no browser) keeps winning;
    once a real browser exists, a missing audit library is a defect, not an
    environment gap, exactly as test_accessibility_audit.py arranges it."""
    try:
        return importlib.import_module(AXE_MODULE)
    except ImportError as exc:  # pragma: no cover - environment gap
        _pytest.fail(
            "The colour-contrast audit could not run: %s is not installed (%s). "
            "Install it with: pip install -r requirements.txt" % (AXE_PACKAGE, exc),
            pytrace=False)


@_pytest.fixture(scope="module")
def axe_engine(axe_module, browser, live_server):
    """Confirm the bundled axe-core carries the colour-contrast rule before any
    assertion reads a clean result from it."""
    from .conftest import PAGE_TIMEOUT

    ctx = browser.new_context()
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.evaluate(axe_module.Axe().axe_script)
        rules = page.evaluate("axe.getRules().map(r => r.ruleId)")
    finally:
        ctx.close()
    assert "color-contrast" in rules, (
        "the bundled axe-core does not carry the colour-contrast rule, so a "
        "clean result would say nothing")
    return rules


@_pytest.fixture(scope="module")
def contrast_content(seeded):
    """Seed the linked content the six pages need, once for the module."""
    created = _seed_contrast_content(
        seeded["ids"]["org"], seeded["ids"]["solution"], seeded["ids"]["application"],
    )
    assert created == 5, "expected five stakeholders (one per attitude) to be seeded"
    return seeded


def test_six_changed_pages_have_zero_colour_contrast_failures_in_both_themes(
    axe_module, axe_engine, browser, live_server, contrast_content,
):
    """axe reports zero colour-contrast failing nodes on each of the six pages
    the dark-mode pull request changes, in light and dark, at 1440 and 390.

    Red at head 69a04354 (the pages still paint raw-palette text, hardcoded hex
    SVG fills and a primary-tint/info-text mismatch); green after the recolour.
    """
    from .conftest import PAGE_TIMEOUT

    seeded = contrast_content
    axe = axe_module.Axe()
    failures = []
    observed = {}
    for width in (1440, 390):
        for archetype, label, path, content_selector in CONTRAST_PAGES:
            ctx = browser.new_context(viewport={"width": width, "height": 900})
            ctx.set_default_timeout(PAGE_TIMEOUT)
            ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
            page = ctx.new_page()
            try:
                _login(page, live_server, seeded["emails"][archetype])
                for theme in ("light", "dark"):
                    _reach_contrast_page(
                        page, live_server, path, content_selector,
                        seeded["ids"]["solution"])
                    if theme == "dark":
                        _enable_dark_theme(page)
                        # The stakeholder canvas is painted once on load; reload
                        # the picture under the dark theme so axe reads the dark
                        # colours, not the light ones captured before the toggle.
                        if path == "__stakeholder_map__":
                            page.select_option(
                                "select[aria-label='Select a solution...']",
                                str(seeded["ids"]["solution"]))
                            page.wait_for_timeout(1200)
                    page.wait_for_timeout(400)
                    nodes, targets = _color_contrast_nodes(axe, page)
                    key = "%s @ %dpx %s" % (label, width, theme)
                    observed[key] = nodes
                    if nodes:
                        failures.append(
                            "%s: %d colour-contrast failing node(s); targets: %s"
                            % (key, nodes, "; ".join(targets[:12]) or "unavailable"))
            finally:
                ctx.close()

    print("\n[contrast] colour-contrast failing nodes per page/width/theme:")
    for key in sorted(observed):
        print("[contrast]   %-44s %d" % (key, observed[key]))

    assert not failures, (
        "%d page/theme combination(s) still fail axe colour-contrast "
        "(must be 0 on all six pages, both themes, both widths):\n  %s"
        % (len(failures), "\n  ".join(failures)))
