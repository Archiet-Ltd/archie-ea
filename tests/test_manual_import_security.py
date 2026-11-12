"""Exercise the real manual HTTP boundary without starting the database.

The database sentinel deliberately fails if invalid input reaches persistence.
PostgreSQL persistence and tenant assertions live in test_manual_import_database.
"""
import ast
import json
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType

import pytest
from flask import Flask, current_app, g, jsonify, request
from flask_login import LoginManager, current_user, login_required


def boundary_namespace(unified=False):
    source = Path(__file__).resolve().parents[1] / (
        'app/modules/applications/routes/import_sophisticated_routes.py' if unified
        else 'app/application_mgmt/import_routes.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and (node.name == 'import_manual_applications' or node.name.startswith('_manual_import_'))]
    for node in nodes:
        if node.name == 'import_manual_applications':
            node.decorator_list = [ast.Name(id='login_required', ctx=ast.Load())]
    namespace = dict(request=request, jsonify=jsonify, current_user=current_user, current_app=current_app,
                     login_required=login_required, datetime=datetime, json=json)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 str(source), 'exec'), namespace)
    return namespace


@pytest.fixture(params=[False, True], ids=['legacy', 'unified'])
def boundary(monkeypatch, request):
    namespace = boundary_namespace(request.param)
    class NoDatabase:
        def __getattr__(self, name):
            raise RuntimeError('Invalid request reached database: ' + name)

    namespace['db'] = SimpleNamespace(session=NoDatabase())
    namespace['DuplicateDetector'] = NoDatabase()
    history_module = ModuleType('app.models.application_import_history')
    history_module.ApplicationImportHistory = lambda **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, 'app.models.application_import_history', history_module)
    application = Flask(__name__)
    application.secret_key = 'manual-import-test-only'
    application.config['BOUNDARY_NAMESPACE'] = namespace
    application.config['UNIFIED_BOUNDARY'] = request.param
    application.config['IMPORT_PATH'] = ('/applications/import-manual' if request.param
                                          else '/dashboard/applications/import-manual')
    manager = LoginManager(application)
    manager.user_loader(lambda identifier: SimpleNamespace(id=1, is_authenticated=True, email='test@example.com'))
    manager.unauthorized_handler(lambda: (jsonify(error='Authentication required'), 401))
    @application.before_request
    def tenant():
        g.current_org_id = application.config.get('TEST_ORG_ID', 11)
    application.add_url_rule(application.config['IMPORT_PATH'],
                             view_func=namespace['import_manual_applications'], methods=['POST'])
    client = application.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = '1'
        session['_fresh'] = True
    return client, application


@pytest.mark.parametrize('field,value', [
    ('organization_id', 22), ('id', 900001), ('archimate_element_id', 44),
    ('created_at', '2026-01-01'), ('updated_at', '2026-01-01'),
    ('deleted_at', '2026-01-01'), ('lock_version', 20),
    ('organization', {'id': 22}), ('vendor_id', 22), ('__dict__', {'id': 20}),
    ('vendor_product_id', 22), ('created_by', 'actor'), ('deleted_by', 1),
    ('discovered_by_ai', True), ('last_assessed', '2026-01-01'), ('provenance', {}),
])
@pytest.mark.parametrize('mode', ['merge', 'skip', 'duplicate', 'update'])
def test_server_owned_and_unsupported_fields_rejected_before_writes(boundary, field, value, mode):
    client, _ = boundary
    response = client.post(boundary[1].config['IMPORT_PATH'], json={
        'duplicate_mode': mode, 'applications': [{'name': 'Owned application', field: value}]})
    assert response.status_code == 400
    assert response.get_json()['error']


@pytest.mark.parametrize('payload', [
    None, [], 'text', 42, {}, {'applications': None}, {'applications': {}},
    {'applications': 'text'}, {'applications': [None]}, {'applications': [[]]},
    {'applications': [{'name': ['nested']}]}, {'applications': [{'name': True}]},
    {'applications': [{'name': ' '}]}, {'applications': [{'name': 'A', 'description': {}}]},
    {'applications': [{'name': 'A', 'component_type': ['x']}]},
    {'applications': [{'name': 'A'}], 'duplicate_mode': []},
    {'applications': [{'name': 'A'}], 'duplicate_mode': 'unexpected'},
    {'applications': [{'name': 'Valid'}, {'name': 'Bad', 'organization_id': 22}]},
    {'applications': [{'name': 'A', 'application_code': 'A', 'app_id': 'B'}]},
    {'applications': [{'name': 'A' * 257}]},
])
def test_malformed_batch_rejected_before_any_write(boundary, payload):
    import json
    response = boundary[0].post(boundary[1].config['IMPORT_PATH'],
                                data=json.dumps(payload), content_type='application/json')
    assert response.status_code == 400
    assert response.get_json()['error']


def test_missing_tenant_cannot_enter_unscoped_writer(boundary):
    client, application = boundary
    application.config['TEST_ORG_ID'] = None
    response = client.post(application.config['IMPORT_PATH'], json={'applications': [{'name': 'A'}]})
    assert response.status_code == 403


def test_unauthenticated_request_denied(boundary):
    client, _ = boundary
    with client.session_transaction() as session:
        session.clear()
    response = client.post(boundary[1].config['IMPORT_PATH'], json={'applications': [{'name': 'A'}]})
    assert response.status_code == 401


def test_real_parser_preserves_manual_grid_fields_and_import_description():
    parse = boundary_namespace()['_manual_import_payload']
    rows, mode = parse({'applications': [{
        'app_id': ' APP-1 ', 'name': ' Accounts ', 'component_type': ' ERP ',
        'deployment_status': ' planned ', 'description': ' Source description ',
    }], 'duplicate_mode': 'update'})
    assert mode == 'merge'
    assert rows == [{'application_code': 'APP-1', 'name': 'Accounts', 'component_type': 'ERP',
                     'deployment_status': 'design', 'description': 'Source description'}]


def test_blank_optional_fields_preserve_merge_values():
    rows, mode = boundary_namespace()['_manual_import_payload']({
        'applications': [{'name': 'A', 'description': ' ', 'component_type': None}]})
    assert rows == [{'name': 'A'}]
    assert mode == 'merge'


@pytest.mark.parametrize('content_type,body', [('application/json', '{'), ('text/plain', '{}')])
def test_invalid_json_or_media_type_rejected(boundary, content_type, body):
    response = boundary[0].post(boundary[1].config['IMPORT_PATH'],
                                data=body, content_type=content_type)
    assert response.status_code == 400
    assert response.get_json()['error']


@pytest.mark.parametrize('field,value', [('user_count', []), ('user_count', True),
    ('user_count', 1.5), ('user_count', 2147483648), ('license_cost', 'nan'),
    ('license_cost', 'inf'), ('encryption_at_rest', 'maybe'), ('implementation_date', {})])
def test_rich_wrong_types_rejected_before_write(boundary, field, value):
    response = boundary[0].post(boundary[1].config['IMPORT_PATH'], json={
        'applications': [{'name': 'A', field: value}]})
    assert response.status_code == 400


def test_rich_parser_preserves_typed_fields_and_date_format():
    from app.utils.manual_application_import import validate_manual_application_import
    rows, mode, date_format = validate_manual_application_import({
        'date_format': 'dmy', 'applications': [{'name': 'A', 'user_count': '12',
        'license_cost': 100.25, 'encryption_at_rest': 'false', 'implementation_date': '05/09/2026'}],
    }, rich=True)
    assert rows == [{'name': 'A', 'user_count': 12, 'license_cost': 100.25,
                     'encryption_at_rest': False, 'implementation_date': '05/09/2026'}]
    assert (mode, date_format) == ('merge', 'dmy')


@pytest.mark.parametrize('mode', ['merge', 'update', 'skip', 'duplicate'])
@pytest.mark.parametrize('fail_audit', [False, True])
def test_same_batch_duplicate_provenance_with_persistence_double(boundary, monkeypatch, mode, fail_audit):
    """Actual writer/audit logic; double covers flush identity, not ORM tenancy."""
    import json
    client, application = boundary
    namespace = application.config['BOUNDARY_NAMESPACE']
    histories, applications = [], []

    class Application(SimpleNamespace):
        id = None
        query = SimpleNamespace(all=lambda: list(applications))

    def history(**values):
        values.setdefault('imported_at', datetime.now())
        row = SimpleNamespace(**values)
        histories.append(row)
        return row

    class Session:
        def add(self, row):
            if isinstance(row, Application):
                applications.append(row)

        def flush(self):
            for index, row in enumerate(applications, start=1):
                row.id = index

        commit = flush

        def rollback(self):
            applications.clear()
            histories.clear()

    monkeypatch.setattr(sys.modules['app.models.application_import_history'], 'ApplicationImportHistory', history)
    namespace['ApplicationComponent'] = Application
    namespace['db'] = SimpleNamespace(session=Session())
    if application.config['UNIFIED_BOUNDARY']:
        namespace['DuplicateDetector'] = SimpleNamespace(
            preload_existing_apps=lambda: {}, find_existing_app=lambda *args: None)
        namespace['DATE_FIELDS'] = set()
        namespace['clean_import_data'] = lambda data: data
        def audit_record(**values):
            if fail_audit:
                raise RuntimeError('Synthetic audit write failure')
            return SimpleNamespace(**values)
        namespace['ImportSessionLog'] = audit_record
    response = client.post(application.config['IMPORT_PATH'], json={
        'applications': [{'name': 'A', 'description': 'First'}, {'name': 'A', 'description': 'Second'}],
        'duplicate_mode': mode})
    if fail_audit and application.config['UNIFIED_BOUNDARY']:
        assert response.status_code == 500
        assert response.get_json()['success'] is False
        assert applications == []
        assert histories == []
        return
    assert response.status_code == 200, response.get_json()
    assert histories, 'Successful manual import must persist history'
    links = json.loads(histories[0].import_settings)['linked_applications']
    assert links['created_ids'] == ([1, 2] if mode == 'duplicate' else [1])
    assert links['updated_ids'] == []
    assert [row.description for row in applications] == (
        ['First', 'Second'] if mode == 'duplicate' else ['First'] if mode == 'skip' else ['Second'])
