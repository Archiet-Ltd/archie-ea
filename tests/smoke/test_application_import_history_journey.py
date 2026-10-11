"""Real login/list/filter/CSV/rollback/reload, on an explicit disposable database.

No interception or handler doubles. Unique committed fixture rows are required
because the browser server uses another database connection; exact IDs are
cleaned even on failure. Collected only on the workstation without PostgreSQL.
"""
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, _require_explicit_test_database
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture
def history_test_database():
    _require_explicit_test_database(dict(os.environ))


@pytest.fixture
def application_history_records(history_test_database, app, seeded):
    from app import db
    from app.models.application_import_history import ApplicationImportHistory
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.batch_import import ImportAuditLog
    from app.models.organization import Organization
    from app.models.user import User

    suffix = uuid.uuid4().hex
    names = {'target': f'Rollback history {suffix}.csv', 'other': f'Other history {suffix}.csv',
             'foreign': f'Foreign history {suffix}.csv'}
    app_ids, history_ids, element_ids = [], [], []
    foreign_org_id = owner_id = None
    org_id = seeded['ids']['org']

    def snapshot():
        with app.app_context():
            db.session.remove()
            try:
                return {
                    'applications': {row.id: row.name for row in ApplicationComponent.query.filter(
                        ApplicationComponent.id.in_(app_ids), ApplicationComponent.organization_id == org_id).all()},
                    'history': {row.id: row.status for row in ApplicationImportHistory.query.filter(
                        ApplicationImportHistory.id.in_(history_ids),
                        ApplicationImportHistory.organization_id.in_([org_id, foreign_org_id])).all()},
                }
            finally:
                db.session.remove()

    try:
        with app.app_context():
            db.session.remove()
            owner = User.query.filter_by(email=seeded['emails']['enterprise_architect'],
                                         organization_id=org_id).one()
            owner_id = owner.id
            foreign_org = Organization(name=f'History foreign {suffix}', slug=f'history-{suffix}')
            db.session.add(foreign_org)
            db.session.flush()
            foreign_org_id = foreign_org.id
            for label in ['created', 'updated']:
                row = ApplicationComponent(name=f'History {label} fixture {suffix}', organization_id=org_id)
                db.session.add(row)
                db.session.flush()
                app_ids.append(row.id)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            for label, status, organization in [('target', 'partial', org_id), ('other', 'completed', org_id),
                                                 ('foreign', 'partial', foreign_org_id)]:
                row = ApplicationImportHistory(organization_id=organization, imported_at=now,
                    imported_by_id=owner_id if organization == org_id else None,
                    imported_by_name='Disposable browser fixture', file_name=names[label], import_source='csv',
                    status=status, total_records=3, records_created=1, records_updated=1,
                    records_skipped=0, records_failed=1 if status == 'partial' else 0,
                    import_settings=json.dumps({'linked_applications': {'created_ids': [app_ids[0]],
                        'updated_ids': [app_ids[1]]}}) if label == 'target' else '{}')
                db.session.add(row)
                db.session.flush()
                history_ids.append(row.id)
            db.session.commit()
            element_ids = [row.archimate_element_id for row in ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(app_ids), ApplicationComponent.organization_id == org_id).all()
                if row.archimate_element_id]
            db.session.remove()
        before = snapshot()
        assert len(before['applications']) == 2 and len(before['history']) == 3
        yield {'names': names, 'app_ids': app_ids, 'history_ids': history_ids,
               'snapshot': snapshot, 'before': before}
    finally:
        with app.app_context():
            db.session.rollback()
            if history_ids:
                ApplicationImportHistory.query.filter(ApplicationImportHistory.id.in_(history_ids),
                    ApplicationImportHistory.file_name.in_(list(names.values()))).delete(synchronize_session=False)
            if app_ids:
                ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_ids),
                    ApplicationComponent.organization_id == org_id).delete(synchronize_session=False)
            if element_ids:
                ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids),
                    ArchiMateElement.organization_id == org_id).delete(synchronize_session=False)
            if owner_id:
                ImportAuditLog.query.filter_by(user_id=owner_id, filename=names['target'],
                                               import_type='rollback').delete(synchronize_session=False)
            if foreign_org_id:
                Organization.query.filter_by(id=foreign_org_id, slug=f'history-{suffix}').delete(synchronize_session=False)
            db.session.commit()
            assert ApplicationImportHistory.query.filter(ApplicationImportHistory.id.in_(history_ids)).count() == 0
            assert ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_ids)).count() == 0
            db.session.remove()


def test_application_history_filters_csv_and_confirmed_rollback_persist(
    browser, live_server, seeded, application_history_records
):
    fixture = application_history_records
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    errors, writes = [], []
    page = context.new_page()
    # An error with no real name and no message (e.g. a bare `throw null`/
    # `throw {}` at the browser's own navigation-teardown boundary, not
    # first-party code - see test_ai_protocol_journeys.py::protocol_page for
    # the same signature reproduced on an unrelated page, the search that
    # ruled out an application-code cause, and CI run 33999307045 confirming
    # Playwright always synthesizes name="uncaught exception" for a
    # non-Error throw, so an empty name is not the only "no real name" case)
    # renders only as "Object" with nothing to attribute it to. A genuine
    # error always carries a real name or an actual message.
    def _content_free_pageerror(error):
        name = getattr(error, 'name', None)
        message = getattr(error, 'message', None)
        # Alpine rejects a superseded transition with {isFromCancelledTransition:
        # true}. The platform and the archetype journeys already treat it as the
        # benign signal it is; Firefox still reports it here, as
        # "Object { isFromCancelledTransition }", even though the page's
        # unhandledrejection handler prevents it.
        if 'isFromCancelledTransition' in str(error):
            return True
        return name in (None, '', 'uncaught exception', 'PageError') and (not message or message == 'Object')

    page.on('pageerror', lambda error: errors.append(str(error))
            if not _content_free_pageerror(error) else None)
    page.on('response', lambda response: errors.append(f'HTTP {response.status}: {response.url}')
            if response.status >= 400 else None)
    page.on('request', lambda request: writes.append(request.url)
            if '/rollback-import/' in request.url and request.method == 'POST' else None)
    try:
        _login(page, live_server, seeded['emails']['enterprise_architect'])
        url = live_server + '/dashboard/import-history'
        assert page.goto(url, wait_until='domcontentloaded').status == 200
        listing = page.locator('#import-history-list')
        expect(listing).to_contain_text(fixture['names']['target'])
        expect(listing).to_contain_text(fixture['names']['other'])
        expect(listing).not_to_contain_text(fixture['names']['foreign'])
        page.get_by_label('Status Filter', exact=True).select_option('partial')
        today = datetime.now(timezone.utc).date().isoformat()
        page.get_by_label('Date from', exact=True).fill(today)
        page.get_by_label('Date to', exact=True).fill(today)
        with page.expect_response(lambda response: '/dashboard/applications/import-history?' in response.url) as filtered:
            page.get_by_role('button', name='Apply filters to history', exact=True).click()
        assert filtered.value.status == 200, filtered.value.text()
        visible_ids = {row['id'] for row in filtered.value.json()['history']}
        assert fixture['history_ids'][0] in visible_ids
        assert fixture['history_ids'][1] not in visible_ids and fixture['history_ids'][2] not in visible_ids
        expect(listing).to_contain_text(fixture['names']['target'])
        expect(listing).not_to_contain_text(fixture['names']['other'])
        with page.expect_download() as download:
            page.get_by_role('button', name='Export CSV', exact=True).click()
        csv_text = download.value.path().read_text(encoding='utf-8-sig')
        assert fixture['names']['target'] in csv_text
        assert fixture['names']['other'] not in csv_text and fixture['names']['foreign'] not in csv_text
        target = listing.locator('article').filter(has=page.get_by_role('heading', name=fixture['names']['target'], exact=True))
        trigger = target.get_by_role('button', name='Rollback import', exact=True)
        trigger.click()
        dialog = page.get_by_role('dialog', name='Confirm Import Rollback', exact=True)
        expect(dialog).to_contain_text('1 recorded created applications')
        dialog.get_by_role('button', name='Cancel', exact=True).click()
        expect(trigger).to_be_focused()
        assert writes == [] and fixture['snapshot']() == fixture['before']
        trigger.click()
        with page.expect_response(lambda response: '/applications/rollback-import/' in response.url) as saved:
            dialog.get_by_role('button', name='Confirm rollback', exact=True).click()
        assert saved.value.status == 200, saved.value.text()
        assert saved.value.json()['success'] is True
        assert saved.value.json()['deleted']['applications'] == 1
        expect(dialog).to_be_hidden()
        expect(listing).not_to_contain_text(fixture['names']['target'])
        page.reload(wait_until='domcontentloaded')
        target = listing.locator('article').filter(has=page.get_by_role('heading', name=fixture['names']['target'], exact=True))
        expect(target).to_contain_text('Rolled back')
        expect(target.get_by_role('button', name='Rollback import', exact=True)).to_be_disabled()
        after = fixture['snapshot']()
        assert set(after['applications']) == {fixture['app_ids'][1]}
        assert after['applications'][fixture['app_ids'][1]] == fixture['before']['applications'][fixture['app_ids'][1]]
        assert after['history'] == {fixture['history_ids'][0]: 'rolled_back',
                                   fixture['history_ids'][1]: 'completed', fixture['history_ids'][2]: 'partial'}
        assert writes == [live_server + f"/applications/rollback-import/{fixture['history_ids'][0]}"]
        assert errors == [], errors
    finally:
        context.close()
