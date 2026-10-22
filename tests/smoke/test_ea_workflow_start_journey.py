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


def _modal_is_gone(page):
    """No part of the start-workflow modal (root, backdrop, panel) is left over the page."""
    state = page.evaluate(
        """() => {
            const root = document.getElementById('startWorkflowModal');
            const visible = (el) => {
                const s = getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
            };
            const parts = root ? [root, ...root.querySelectorAll('.modal-backdrop, .modal-panel, div')] : [];
            return {
                visibleParts: parts.filter(visible).length,
                bodyLocked: document.body.classList.contains('overflow-hidden'),
                inertLeft: document.querySelectorAll('[inert]').length,
            };
        }"""
    )
    return state


def test_start_workflow_modal_leaves_page_clickable_after_every_close(browser, live_server, seeded):
    """A person opens the start-workflow modal, sees the steps of the workflow they
    chose, closes it (Cancel, Escape, backdrop click, or by starting it) and must be
    able to click the page underneath again. A backdrop left behind swallows the click."""
    _ensure_workflow_defaults_seeded()

    page = browser.new_page()
    problems = []
    page.on('console', lambda m: problems.append(m.text) if m.type == 'error' else None)
    page.on('pageerror', lambda e: problems.append(str(e)))
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        assert page.goto(live_server + '/ea-workflows', timeout=PAGE_TIMEOUT).status == 200

        modal = page.locator('#startWorkflowModal')
        card_start = page.locator('[data-scenario-card="ARCH_REVIEW"] button')
        other_start = page.locator('[data-scenario-card="GAP_REMEDIATION"] button')
        expect(card_start).to_be_visible(timeout=PAGE_TIMEOUT)

        def open_modal(button, title):
            button.click(timeout=5000)
            expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
            expect(modal.locator('#startWorkflowTitle')).to_have_text(title)
            # The right-hand panel shows what will happen for the chosen workflow.
            expect(modal.get_by_text('What will happen')).to_be_visible()
            steps = modal.locator('#stepPreviewList > div')
            expect(steps.first).to_be_visible()
            assert steps.count() >= 3, 'step preview is not filled for the chosen workflow'
            expect(modal.locator('#stepPreviewOutputsList span').first).to_be_visible()
            expect(modal.get_by_role('button', name='Cancel', exact=True)).to_be_visible()

        def assert_closed_and_page_clickable(route):
            expect(modal).to_be_hidden(timeout=5000)
            state = _modal_is_gone(page)
            assert state['visibleParts'] == 0, f'{route}: modal pieces still on screen: {state}'
            assert not state['bodyLocked'], f'{route}: page scroll still locked: {state}'
            assert state['inertLeft'] == 0, f'{route}: page still inert: {state}'
            # The element underneath takes a real click and does its job: it opens
            # the modal for a different workflow.
            other_start.click(timeout=5000)
            expect(modal.locator('#startWorkflowTitle')).not_to_have_text('Architecture Review')
            expect(modal).to_be_visible(timeout=5000)
            page.keyboard.press('Escape')
            expect(modal).to_be_hidden(timeout=5000)

        title = page.evaluate("WORKFLOW_DATA['ARCH_REVIEW'].label")

        # 1. Cancel
        open_modal(card_start, title)
        modal.get_by_role('button', name='Cancel', exact=True).click()
        assert_closed_and_page_clickable('cancel')

        # 2. Escape
        open_modal(card_start, title)
        page.keyboard.press('Escape')
        assert_closed_and_page_clickable('escape')

        # 3. Backdrop click (the dimmed area outside the dialog panel)
        open_modal(card_start, title)
        page.mouse.click(5, 5)
        assert_closed_and_page_clickable('backdrop')

        # 4. Start: the modal gives way to the new instance, with nothing left over it.
        open_modal(card_start, title)
        modal.locator('#field_application_id').select_option(str(seeded['ids']['application']))
        page.locator('#startWorkflowBtn').click()
        page.wait_for_url('**/ea-workflows/instance/*', timeout=PAGE_TIMEOUT)
        assert page.locator('#startWorkflowModal').count() == 0 or \
            not page.locator('#startWorkflowModal').is_visible()
        page.go_back(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state('load', timeout=PAGE_TIMEOUT)
        expect(card_start).to_be_visible(timeout=PAGE_TIMEOUT)
        card_start.click(timeout=5000)
        expect(modal).to_be_visible(timeout=5000)

        assert problems == [], f'console errors: {problems}'
    finally:
        page.close()
