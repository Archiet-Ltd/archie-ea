"""EA Workflows: start a real workflow instance against a real entity, drive it
through its actual approval state machine via the rendered UI, and confirm the
state change persists after a reload.

Wave 5 identified real user-facing CRUD in app/main/routes_ea_workflows.py
(dashboard, definitions, approvals, a two-panel "Start Workflow" modal posting
to /api/ea-workflows/start, resume/cancel/reject) with no journey coverage,
because the start-workflow payload's target-entity shape wasn't traced. Traced
here: ARCH_REVIEW is application-scoped (requires context.application_id, an
ApplicationComponent id) and its step sequence
(resolve_context -> completeness_audit -> relationship_gaps ->
review_relationships[approval] -> quality_assessment -> review_findings[approval]
-> commit_changes) is the only seeded workflow whose state machine exercises
BOTH a start and a real approve/resume transition without extra required
context beyond a single application.

EAWorkflowDefinition rows are global reference data (not tenant-scoped, see
tests/test_ea_workflows_journeys.py) but are not auto-seeded at boot -- the
dashboard's WORKFLOW_DATA is empty until something calls
EAWorkflowEngine.seed_default_workflows(). This journey seeds them directly
(idempotent upsert) rather than depending on another test file's ordering.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _ensure_workflow_defaults_seeded():
    from app import create_app, db  # noqa: F401  (db import keeps app context bound)
    from app.services.ea_workflow_engine import EAWorkflowEngine

    app = create_app("testing")
    with app.app_context():
        engine = EAWorkflowEngine()
        engine.seed_default_workflows()


def test_arch_review_workflow_start_approve_persists(browser, live_server, seeded):
    _ensure_workflow_defaults_seeded()

    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])

        # ---- Start: real application, real modal, real POST ----
        response = page.goto(live_server + '/ea-workflows?start=ARCH_REVIEW', timeout=PAGE_TIMEOUT)
        assert response.status == 200

        modal = page.locator('#startWorkflowModal')
        expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)

        app_select = modal.locator('#field_application_id')
        expect(app_select).to_be_visible(timeout=PAGE_TIMEOUT)
        app_select.select_option(str(seeded['ids']['application']))

        # The submit control must be the top-most element at its own centre: if
        # the backdrop is laid over the panel it silently swallows the click and
        # the workflow never starts, which is what a person experiences as a
        # dead "Start Workflow" button.
        topmost_is_start = page.evaluate(
            """() => {
                const btn = document.getElementById('startWorkflowBtn');
                const r = btn.getBoundingClientRect();
                const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                return btn.contains(hit);
            }"""
        )
        assert topmost_is_start, (
            "the start-workflow backdrop is covering its own submit button, so the "
            "click never reaches it"
        )

        page.locator('#startWorkflowBtn').click()

        # The submit handler navigates to /ea-workflows/instance/<id> on success.
        page.wait_for_url('**/ea-workflows/instance/*', timeout=PAGE_TIMEOUT)
        instance_url = page.url

        # ---- Confirm it appears in the dashboard ----
        page.goto(live_server + '/ea-workflows', timeout=PAGE_TIMEOUT)
        recent = page.locator('#recent-activity a[href*="/ea-workflows/instance/"]').first
        expect(recent).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- Drive the real approval state machine ----
        page.goto(instance_url, timeout=PAGE_TIMEOUT)
        status_badge = page.locator('[role="status"]').first

        def _wait_for_status(predicate, timeout_ms=30000):
            """Poll via reload: steps run in a background thread server-side,
            so the UI status only updates on a fresh load."""
            deadline_polls = timeout_ms // 1000
            for _ in range(deadline_polls):
                text = status_badge.inner_text(timeout=PAGE_TIMEOUT).strip()
                if predicate(text):
                    return text
                page.wait_for_timeout(1000)
                page.reload(timeout=PAGE_TIMEOUT)
            return status_badge.inner_text(timeout=PAGE_TIMEOUT).strip()

        first_status = _wait_for_status(lambda t: t not in ('', 'Analysis Running'))
        assert first_status == 'Awaiting Your Review', (
            f"ARCH_REVIEW did not reach its first approval gate; status was {first_status!r}"
        )

        # Approve step 1 (review_relationships) via the real button.
        page.get_by_role('button', name='Approve', exact=True).first.click()
        page.wait_for_timeout(1500)
        page.reload(timeout=PAGE_TIMEOUT)

        second_status = _wait_for_status(lambda t: t not in ('', 'Analysis Running'))
        assert second_status in ('Awaiting Your Review', 'Review Complete'), (
            f"ARCH_REVIEW stalled after first approval; status was {second_status!r}"
        )

        if second_status == 'Awaiting Your Review':
            page.get_by_role('button', name='Approve', exact=True).first.click()
            page.wait_for_timeout(1500)
            page.reload(timeout=PAGE_TIMEOUT)
            second_status = _wait_for_status(lambda t: t not in ('', 'Analysis Running'))

        assert second_status == 'Review Complete', (
            f"ARCH_REVIEW did not complete after all approvals; final status was {second_status!r}"
        )

        # ---- Confirm the state change persisted after a hard reload ----
        page.reload(timeout=PAGE_TIMEOUT)
        expect(status_badge).to_have_text('Review Complete', timeout=PAGE_TIMEOUT)
    finally:
        page.close()
