"""Actual manual form submission and PostgreSQL persistence, without HTTP doubles."""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_manual_import_create_reload_and_merge(browser, live_server, seeded):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    # live_server refuses implicit or different test/server database URLs.
    verifier = create_app('testing')
    marker = 'QA manual import ' + uuid.uuid4().hex
    code = 'QA-' + uuid.uuid4().hex
    org_id = seeded['ids']['org']
    page = browser.new_page()
    errors, console_errors, posts = [], [], []
    endpoint = live_server + '/applications/import-manual'
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: console_errors.append(message.text)
            if message.type == 'error' else None)
    page.on('request', lambda request: posts.append(request.url)
            if request.method == 'POST' and request.url == endpoint else None)

    def persisted():
        with verifier.app_context():
            db.session.remove()
            assert db.engine.dialect.name == 'postgresql'
            rows = ApplicationComponent.query.filter_by(name=marker, organization_id=org_id).all()
            assert len(rows) == 1
            row = rows[0]
            element = ArchiMateElement.query.filter_by(
                id=row.archimate_element_id, organization_id=org_id).one()
            return dict(id=row.id, code=row.application_code, kind=row.component_type,
                        status=row.deployment_status, mirror=element.id)

    def submit(kind, expected_created, expected_updated):
        page.get_by_test_id('btn-import').click()
        dialog = page.get_by_role('dialog', name='Import Applications', exact=True)
        expect(dialog).to_be_visible()
        dialog.get_by_role('button', name='Manual entry tab', exact=True).click()
        dialog.get_by_role('button', name='Add manual entry row', exact=True).click()
        entry = dialog.locator('#manual-entry-tbody tr')
        expect(entry).to_have_count(1)
        entry.get_by_placeholder('APP ID', exact=True).fill(code)
        entry.get_by_placeholder('Application Name *', exact=True).fill(marker)
        entry.get_by_placeholder('Type', exact=True).fill(kind)
        entry.locator('select[name="deployment_status"]').select_option('design')
        expect(dialog.locator('#auto-map-after-import-manual')).not_to_be_checked()
        dialog.locator('#duplicate-mode-manual').select_option('update')
        with page.expect_response(lambda response: response.url == endpoint
                                  and response.request.method == 'POST') as saved:
            dialog.get_by_role('button', name='Import manual entries', exact=True).click()
        response = saved.value
        assert response.status == 200, response.text()
        result = response.json()
        assert result['success'] is True
        assert result['created'] == expected_created
        assert result['updated'] == expected_updated
        assert result['failed'] == 0, result
        assert response.request.headers.get('x-csrftoken') or response.request.headers.get('x-csrf-token')
        # Saved data and truthful counts must remain available before the user
        # acknowledges; a transient toast followed by reload is insufficient.
        outcome = page.get_by_role('dialog', name='Import saved', exact=True)
        expect(outcome).to_be_visible()
        expect(outcome.get_by_text(f'Created: {expected_created}', exact=True)).to_be_visible()
        expect(outcome.get_by_text(f'Updated: {expected_updated}', exact=True)).to_be_visible()
        expect(outcome.get_by_text('Auto-mapping was not requested.', exact=True)).to_be_visible()
        expect(page.locator('#application-import-modal [aria-label="Import manual entries"]')).to_be_disabled()
        assert persisted()['kind'] == kind
        expect(outcome).to_be_visible()
        # Observe the application's acknowledged refresh before independently
        # reloading, so the two navigations cannot race with ERR_ABORTED.
        with page.expect_navigation(wait_until='domcontentloaded', timeout=PAGE_TIMEOUT) as refreshed:
            outcome.get_by_role('button', name='Done — refresh applications', exact=True).click()
        assert refreshed.value is not None and refreshed.value.status == 200
        expect(dialog).not_to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200

    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        assert page.goto(live_server + '/applications/', timeout=PAGE_TIMEOUT).status == 200
        submit('ERP', 1, 0)
        created = persisted()
        assert created['code'] == code
        assert created['kind'] == 'ERP'
        assert created['status'] == 'design'
        submit('CRM', 0, 1)
        merged = persisted()
        assert merged == dict(created, kind='CRM')
        assert posts == [endpoint, endpoint], 'Each visible submission must write exactly once'
        assert errors == [], errors
        assert console_errors == [], console_errors
    finally:
        try:
            # Remove only this test's exact unique name and tenant, even after a
            # browser assertion fails. Never use application-wide cleanup.
            with verifier.app_context():
                db.session.remove()
                rows = ApplicationComponent.query.filter_by(name=marker, organization_id=org_id).all()
                ids = [row.id for row in rows]
                mirrors = [row.archimate_element_id for row in rows if row.archimate_element_id]
                if ids:
                    ApplicationComponent.query.filter(
                        ApplicationComponent.id.in_(ids), ApplicationComponent.organization_id == org_id
                    ).delete(synchronize_session=False)
                    ArchiMateElement.query.filter(
                        ArchiMateElement.id.in_(mirrors), ArchiMateElement.organization_id == org_id
                    ).delete(synchronize_session=False)
                    db.session.commit()
                assert ApplicationComponent.query.filter_by(name=marker, organization_id=org_id).count() == 0
                db.session.remove()
        finally:
            page.close()
            assert errors == [], errors
            assert console_errors == [], console_errors
