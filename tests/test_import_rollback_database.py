"""Rollback against actual PostgreSQL rows inside shared rollback-only transactions."""
import json
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.skipif(
    not os.environ.get('TEST_DATABASE_URL'),
    reason='Rollback integration requires an explicit disposable PostgreSQL TEST_DATABASE_URL',
)


@pytest.fixture
def batch(db_session, make_org):
    from app.models.application_import_history import ApplicationImportHistory
    from app.models.application_portfolio import ApplicationComponent
    from app.models.apqc_process import APQCProcess, ProcessApplicationMapping
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability
    from app.models.user import Permission, Role, User

    assert db_session.get_bind().dialect.name == 'postgresql'
    assert db_session.get_bind().in_transaction()
    org, foreign_org = make_org('rollback-owner'), make_org('rollback-foreign')
    suffix = uuid.uuid4().hex
    writer = Role(name='Rollback writer ' + suffix, permissions=Permission.GENERAL)
    admin_role = Role(name='Rollback admin ' + suffix, permissions=Permission.ADMINISTER)
    db_session.add_all([writer, admin_role])

    def make_user(label, role, organization):
        row = User(email=f'rollback-{label}-{suffix}@example.com', role=role,
                   organization_id=organization.id, confirmed=True, enterprise_role='enterprise_architect')
        db_session.add(row)
        db_session.flush()
        return row

    owner = make_user('owner', writer, org)
    colleague = make_user('colleague', writer, org)
    administrator = make_user('admin', admin_role, org)
    foreign_user = make_user('foreign', writer, foreign_org)
    applications = []
    mapping_ids = {'capabilities': [], 'processes': []}
    for index, organization in enumerate((org, org, foreign_org)):
        application = ApplicationComponent(name=f'Rollback synthetic {index} {suffix}',
                                           organization_id=organization.id)
        capability = UnifiedCapability(name=f'Rollback capability {index} {suffix}',
                                       organization_id=organization.id, scope='tenant')
        process = APQCProcess(process_code=suffix[:12] + str(index), process_name=f'Rollback process {index}')
        db_session.add_all([application, capability, process])
        db_session.flush()
        cap_mapping = UnifiedApplicationCapabilityMapping(application_component_id=application.id,
                                                          unified_capability_id=capability.id,
                                                          organization_id=organization.id)
        proc_mapping = ProcessApplicationMapping(application_id=application.id, apqc_process_id=process.id)
        db_session.add_all([cap_mapping, proc_mapping])
        db_session.flush()
        applications.append(application)
        mapping_ids['capabilities'].append(cap_mapping.id)
        mapping_ids['processes'].append(proc_mapping.id)
    created, updated, foreign = applications
    history = ApplicationImportHistory(
        organization_id=org.id, imported_by_id=owner.id, imported_by_name='Synthetic rollback owner',
        imported_at=datetime.now(timezone.utc).replace(tzinfo=None), import_source='manual',
        file_name='synthetic-rollback.csv', status='completed', records_created=1, records_updated=1,
        import_settings=json.dumps({'linked_applications': {'created_ids': [created.id], 'updated_ids': [updated.id]}}),
    )
    db_session.add(history)
    db_session.flush()
    return SimpleNamespace(owner=owner, colleague=colleague, admin=administrator, foreign_user=foreign_user,
                           history=history, history_id=history.id, org_id=org.id, foreign_org_id=foreign_org.id,
                           created=created, updated=updated, foreign=foreign,
                           app_ids=[row.id for row in applications],
                           element_ids=[row.archimate_element_id for row in applications], mapping_ids=mapping_ids)


def snapshot(db_session, batch):
    """Read only exact synthetic fixture IDs, including rows outside request tenant."""
    from sqlalchemy import select
    from app.models.application_import_history import ApplicationImportHistory
    from app.models.application_portfolio import ApplicationComponent
    from app.models.apqc_process import ProcessApplicationMapping
    from app.models.archimate_core import ArchiMateElement
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    result = {}
    for name, model, identifiers in [
        ('history', ApplicationImportHistory, [batch.history_id]),
        ('applications', ApplicationComponent, batch.app_ids),
        ('elements', ArchiMateElement, batch.element_ids),
        ('capabilities', UnifiedApplicationCapabilityMapping, batch.mapping_ids['capabilities']),
        ('processes', ProcessApplicationMapping, batch.mapping_ids['processes']),
    ]:
        table = model.__table__
        result[name] = [dict(row) for row in db_session.execute(
            select(table).where(table.c.id.in_(identifiers)).order_by(table.c.id)
        ).mappings()]
    return result


@pytest.mark.parametrize('representation', ['legacy', 'settings', 'details'])
def test_created_only_rollback_and_repeated_request(batch, db_session, client, login_as, representation):
    linked = {'created_ids': [batch.app_ids[0]], 'updated_ids': [batch.app_ids[1]]}
    batch.history.import_settings = json.dumps(
        {'application_ids': linked['created_ids']} if representation == 'legacy' else
        {'linked_applications': linked} if representation == 'settings' else {})
    batch.history.error_details = json.dumps({'linked_applications': linked}) if representation == 'details' else None
    db_session.flush()
    before = snapshot(db_session, batch)
    login_as(client, batch.owner)
    response = client.post(f'/applications/rollback-import/{batch.history_id}')
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['deleted'] == dict(applications=1, capability_mappings=1,
                                                process_mappings=1, archimate_elements=1)
    after = snapshot(db_session, batch)
    for name in ('applications', 'elements', 'capabilities', 'processes'):
        assert after[name] == before[name][1:]
    assert after['history'][0]['status'] == 'rolled_back'
    login_as(client, batch.owner)
    repeated = client.post(f'/applications/rollback-import/{batch.history_id}')
    assert repeated.status_code == 400
    assert snapshot(db_session, batch) == after


@pytest.mark.parametrize('malicious', ['foreign_id', 'missing_id', 'foreign_element', 'conflict', 'overlap'])
def test_invalid_target_sets_are_atomic(batch, db_session, client, login_as, malicious):
    from sqlalchemy import update
    from app.models.application_portfolio import ApplicationComponent

    if malicious in ('foreign_id', 'missing_id'):
        identifier = batch.app_ids[2] if malicious == 'foreign_id' else 2147483647
        batch.history.import_settings = json.dumps({'application_ids': [batch.app_ids[0], identifier]})
    elif malicious == 'foreign_element':
        # Explicit corrupted association fixture, never another organization's real data.
        table = ApplicationComponent.__table__
        db_session.execute(update(table).where(table.c.id == batch.app_ids[0]).values(
            archimate_element_id=batch.element_ids[2]))
    elif malicious == 'conflict':
        batch.history.error_details = json.dumps({'linked_applications': {'created_ids': [batch.app_ids[2]]}})
    else:
        batch.history.import_settings = json.dumps({'linked_applications': {
            'created_ids': [batch.app_ids[0]], 'updated_ids': [batch.app_ids[0]]}})
    db_session.flush()
    before = snapshot(db_session, batch)
    login_as(client, batch.owner)
    response = client.post(f'/applications/rollback-import/{batch.history_id}')
    assert response.status_code == 400, response.get_json()
    assert snapshot(db_session, batch) == before


def test_callable_admin_and_history_tenant_boundary(batch, db_session, client, login_as):
    before = snapshot(db_session, batch)
    assert batch.colleague.is_admin() is False
    login_as(client, batch.colleague)
    denied = client.post(f'/applications/rollback-import/{batch.history_id}')
    assert denied.status_code == 403
    assert snapshot(db_session, batch) == before
    # The same history object remains in the identity map when tenant switches.
    login_as(client, batch.foreign_user)
    foreign = client.post(f'/applications/rollback-import/{batch.history_id}')
    assert foreign.status_code == 404
    assert snapshot(db_session, batch) == before
    login_as(client, batch.admin)
    allowed = client.post(f'/applications/rollback-import/{batch.history_id}')
    assert allowed.status_code == 200, allowed.get_json()


@pytest.mark.parametrize('survivor', ['updated', 'foreign'])
def test_shared_element_survives(batch, db_session, client, login_as, survivor):
    from sqlalchemy import update
    from app.models.application_portfolio import ApplicationComponent

    table = ApplicationComponent.__table__
    survivor_id = batch.app_ids[1] if survivor == 'updated' else batch.app_ids[2]
    db_session.execute(update(table).where(table.c.id == survivor_id).values(archimate_element_id=batch.element_ids[0]))
    before = snapshot(db_session, batch)
    login_as(client, batch.owner)
    response = client.post(f'/applications/rollback-import/{batch.history_id}')
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['deleted']['archimate_elements'] == 0
    after = snapshot(db_session, batch)
    assert after['elements'] == before['elements']
    assert after['applications'] == before['applications'][1:]
