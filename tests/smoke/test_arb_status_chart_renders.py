"""The ARB dashboard's "Review status" donut must actually render when there
is data to chart -- not just carry a <canvas> tag in the HTML.

Found 11 Sep 2026 in a visual-design pass on production: with exactly one
governed review item, the "Review status" card rendered as a large blank
card with only the legend numbers (Pending/Approved/Rejected, all reading 0)
floating in its bottom-right corner -- no visible donut at all. The existing
source-level tests in tests/test_arb_shell.py only assert the canvas element
is present in the returned HTML string, which passed throughout: the bug was
never in the markup, it was that the chart-building <script> lives in
extra_head_js (rendered inside <head>, before <body> -- and #arbStatusChart
-- exist), so `document.getElementById('arbStatusChart')` returned null on
every page load and the script bailed out silently with no console error.
Fixed by wrapping the chart construction in a DOMContentLoaded listener in
app/templates/arb/dashboard.html. This test proves the fix by checking for
an actual Chart.js instance attached to the canvas, which a source-only
HTML-string test cannot see.
"""
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture
def one_governed_review(seeded):
    from app import create_app, db
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.architecture_review_board import ARBReviewCycle, ARBReviewItem
    from app.models.transformation_decision import ARBSubjectEvidenceSnapshot
    from app.models.user import User
    from datetime import datetime, timezone

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org_id = seeded["ids"]["org"]
        submitter = User.query.filter_by(email=seeded["emails"]["solution_architect"]).one()

        # archie_validate_arb_cycle_membership() expects a review-projection
        # row that only the real submission service normally populates; a
        # hand-built ORM fixture can't satisfy it without replaying that
        # workflow. Bypassing triggers for every insert in this transaction,
        # same pattern tests/smoke/test_typed_arb_governance_journey.py's
        # `governed` fixture already uses for synthetic rows -- must be set
        # before the first insert, since SET LOCAL only affects statements
        # issued after it within the current transaction.
        db.session.execute(db.text("SET LOCAL session_replication_role = replica"))  # session-replication-role-ok: archie_validate_arb_cycle_membership() expects a review-projection row only the real submission service normally populates; a hand-built ORM fixture can't satisfy it without replaying that workflow

        adr = ArchitectureDecision(
            organization_id=org_id,
            decision_id="AD-chart-smoke-%s" % suffix,
            title="Chart smoke ADR %s" % suffix,
            status="proposed",
            context="A governed choice needs evidence.",
            decision="Adopt the governed option.",
            rationale="It is testable.",
            consequences="Conditions must be verified.",
            created_by_id=submitter.id,
        )
        db.session.add(adr)
        db.session.flush()

        # ck_arb_review_cycle_shape requires subject_evidence_snapshot_id
        # NOT NULL for a non-historical 'adr' cycle.
        snapshot = ARBSubjectEvidenceSnapshot(
            organization_id=org_id,
            subject_type="adr",
            subject_id=adr.id,
            adr_id=adr.id,
            schema_version=1,
            policy_version="adr-arb-r2",
            captured_by_id=submitter.id,
            captured_at=datetime.now(timezone.utc),
            payload={"title": adr.title},
            citations={"linked_resources": []},
            content_hash="0" * 64,
        )
        db.session.add(snapshot)
        db.session.flush()

        cycle = ARBReviewCycle(
            organization_id=org_id,
            subject_type="adr",
            subject_id=adr.id,
            adr_id=adr.id,
            subject_evidence_snapshot_id=snapshot.id,
            review_number="CHART-SMOKE-%s" % suffix,
            cycle_number=1,
            status="submitted",
        )
        db.session.add(cycle)
        db.session.flush()

        review = ARBReviewItem(
            organization_id=org_id,
            review_number="CHART-SMOKE-ITEM-%s" % suffix,
            title="Chart smoke review %s" % suffix,
            review_type="architecture_change",
            subject_type="adr",
            subject_id=adr.id,
            adr_id=adr.id,
            subject_evidence_snapshot_id=snapshot.id,
            review_cycle_id=cycle.id,
            status="submitted",
            submitter_id=submitter.id,
        )
        db.session.add(review)
        db.session.commit()
        ids = {"adr_id": adr.id, "cycle_id": cycle.id, "review_id": review.id, "snapshot_id": snapshot.id}

    yield ids

    # No cleanup: ARBReviewCycle/ARBReviewItem are guarded append-only by a DB
    # trigger, and ARBSubjectEvidenceSnapshot by a before_delete event in
    # transformation_decision.py -- both real safeguards against rewriting
    # governance history, which a synthetic fixture row is correctly not
    # exempt from. The reference fixture (test_typed_arb_governance_journey.py's
    # `governed`) leaves its rows in place for the same reason. Each run uses
    # a fresh uuid suffix, so rows never collide across test runs.


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").dispatch_event("click")
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def test_status_chart_gets_a_real_chartjs_instance(browser, live_server, seeded, one_governed_review):
    # `page` is intentionally not requested here: it is not defined in this
    # package's conftest.py, so it silently resolves to pytest-playwright's
    # plugin-provided fixture instead of this repo's own `browser` fixture --
    # two different Playwright driver setups whose async/sync loop handling
    # conflicts and fails every test with "Playwright Sync API inside the
    # asyncio loop". Building the page from `browser` directly (the pattern
    # every other file in this package uses) avoids the plugin fixture.
    page = browser.new_page()
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/arb/", wait_until="networkidle", timeout=PAGE_TIMEOUT)

    canvas = page.locator("#arbStatusChart")
    assert canvas.count() == 1, "the chart canvas is missing from the page entirely"

    has_instance = page.evaluate(
        "() => { const c = document.getElementById('arbStatusChart'); "
        "return !!(window.Chart && window.Chart.getChart && window.Chart.getChart(c)); }"
    )
    assert has_instance, (
        "no Chart.js instance is attached to #arbStatusChart -- the canvas "
        "exists in the HTML but the chart-building script never ran against "
        "it (the extra_head_js/<head>-vs-<body> timing bug), so the card "
        "renders as dead whitespace instead of the review-status donut"
    )


def test_status_chart_segments_are_not_all_black(browser, live_server, seeded, one_governed_review):
    """17 Sep 2026: `hsl(var(--warning))` handed to Chart.js as a JS string
    literal (var() only resolves in real CSS) made @kurkle/color fail to
    parse it SILENTLY -- no console error -- and every segment fell back to
    Chart.js's default fill, black. A source scan cannot see this; only the
    resolved, rendered chart colours can. This is the browser-driven proof."""
    page = browser.new_page()
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/arb/", wait_until="networkidle", timeout=PAGE_TIMEOUT)

    colors = page.evaluate(
        "() => { const c = document.getElementById('arbStatusChart'); "
        "const chart = window.Chart.getChart(c); "
        "return chart.data.datasets[0].backgroundColor; }"
    )
    assert colors, "chart carries no resolved backgroundColor array"
    assert len(colors) == 4

    def _is_black_or_unparsed(value):
        v = (value or "").strip().lower()
        return v in ("", "#000000", "#000", "rgb(0, 0, 0)", "rgba(0, 0, 0, 1)") or "var(" in v

    assert not any(_is_black_or_unparsed(c) for c in colors), (
        "chart segment resolved to black/unparsed -- reproduces the silent "
        "@kurkle/color parse failure: %r" % colors
    )
    # Every segment must be a genuinely distinct colour -- four copies of the
    # same value would still look broken even if none of them is black.
    assert len(set(colors)) == 4, "chart segments are not visually distinct: %r" % colors


def test_status_chart_canvas_height_is_capped(browser, live_server, seeded, one_governed_review):
    """17 Sep 2026: canvas `height="160"` was inert under
    `responsive: true` + the doughnut default `maintainAspectRatio: true`,
    which sizes the canvas as a square as tall as its wide flex-1 container
    -- ~700px on a 1440px-wide screen. Fixed by capping the wrapper height
    AND setting `maintainAspectRatio: false`; this asserts the rendered
    result, not just that both source changes are present."""
    page = browser.new_page()
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/arb/", wait_until="networkidle", timeout=PAGE_TIMEOUT)

    box = page.locator("#arbStatusChart").bounding_box()
    assert box is not None
    assert box["height"] <= 240, "chart canvas is oversized: %r" % box
