"""Focused rollback boundary checks; pure policy tests require no database."""
import ast
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


def policy():
    """Execute actual policy functions without bootstrapping unrelated blueprints."""
    source = Path(__file__).resolve().parents[1] / 'app/modules/applications/routes/import_export_routes.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    names = {'_rollback_metadata', '_rollback_policy', 'rollback_import_eligibility'}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(functions) == 3, 'Rollback policy boundary is missing'
    namespace = {'json': json, 'datetime': datetime, 'timedelta': timedelta, 'timezone': timezone}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


def history(settings=None, details=None, **overrides):
    values = dict(imported_by_id=4, status='completed', imported_at=datetime(2026, 9, 5),
                  import_settings=json.dumps(settings) if settings is not None else None,
                  error_details=json.dumps(details) if details is not None else None)
    values.update(overrides)
    return SimpleNamespace(**values)


def user(identifier=4, admin=False):
    return SimpleNamespace(id=identifier, is_admin=lambda: admin)


@pytest.mark.parametrize('settings,details', [
    ({'application_ids': [11]}, None),
    ({'linked_applications': {'created_ids': [11], 'updated_ids': [12]}}, None),
    ({}, {'errors': [], 'linked_applications': {'created_ids': [11], 'updated_ids': [12]}}),
])
def test_legitimate_created_only_representations(settings, details):
    result = policy()['_rollback_policy'](history(settings, details), user(), datetime(2026, 9, 6))
    assert result == [11]


@pytest.mark.parametrize('settings,details', [
    ({'application_ids': [True]}, None), ({'application_ids': ['11']}, None),
    ({'application_ids': [-1]}, None), ({'application_ids': 11}, None),
    ([], None), ({'linked_applications': []}, None),
    ({'linked_applications': {'created_ids': [11], 'updated_ids': [11]}}, None),
    ({'application_ids': [11]}, {'linked_applications': {'created_ids': [12]}}),
    ({'application_ids': [11]}, {'linked_applications': {'created_ids': [], 'updated_ids': [12]}}),
])
def test_invalid_or_conflicting_metadata_rejected(settings, details):
    with pytest.raises(ValueError):
        policy()['_rollback_policy'](history(settings, details), user(), datetime(2026, 9, 6))


@pytest.mark.parametrize('owner', [None, 99])
def test_non_admin_cannot_bypass_ownership(owner):
    with pytest.raises(PermissionError):
        policy()['_rollback_policy'](history({'application_ids': [11]}, imported_by_id=owner),
                                     user(), datetime(2026, 9, 6))


# R3-1 (PR 428 round 4): ``is_admin()`` used to be judged globally here --
# ANY holder of the Permission.ADMINISTER flag could roll back ANY
# organisation's import, not just their own, because the check never
# looked at which organisation was active. ``_rollback_policy`` now calls
# ``is_active_org_admin(user)``, which needs a real Flask-Login user, a
# real active organisation (``g.current_org_id``) and ``rbac_service`` --
# none of which the plain ``SimpleNamespace`` double above can represent,
# so the admin-override half of the old test above (now moved here) needs
# the real app/db fixtures instead of staying a "pure, no database" check.
@pytest.mark.parametrize('owner', [None, 99])
def test_active_org_admin_can_override_ownership_but_a_mere_global_flag_cannot(
    app, db_session, make_org, owner
):
    from app.middleware.tenant_decorators import is_active_org_admin
    from app.models.user import Permission, Role, User

    org = make_org(f"r3-1-rollback-{uuid.uuid4().hex[:8]}")
    admin_role = Role.query.filter(
        Role.permissions.op("&")(Permission.ADMINISTER) == Permission.ADMINISTER
    ).first()

    real_admin = User(
        email=f"r3-1-rollback-admin-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Org",
        last_name="Admin",
        organization_id=org.id,
        confirmed=True,
        role=admin_role,
    )
    real_admin.password = uuid.uuid4().hex
    db_session.add(real_admin)
    db_session.flush()

    with app.test_request_context("/"):
        from flask import g
        from flask_login import login_user

        login_user(real_admin)
        g.current_org_id = org.id

        assert is_active_org_admin(real_admin) is True, (
            "fixture setup: a genuine Administrator of their own, active "
            "organisation must be judged an active-org admin"
        )
        assert policy()["_rollback_policy"](
            history({"application_ids": [11]}, imported_by_id=owner),
            real_admin,
            datetime(2026, 9, 6),
        ) == [11], "a genuine active-org admin must still be able to roll back any import in their own org"

        # The global ADMINISTER flag alone, with NO active-org standing
        # (g.current_org_id pointed at a different organisation this user
        # has no relationship to), must no longer stand in for it.
        other_org = make_org(f"r3-1-rollback-other-{uuid.uuid4().hex[:8]}")
        g.current_org_id = other_org.id
        assert is_active_org_admin(real_admin) is False, (
            "an Administrator switched to an organisation they have no "
            "standing in must not be judged an active-org admin there"
        )
        with pytest.raises(PermissionError):
            policy()["_rollback_policy"](
                history({"application_ids": [11]}, imported_by_id=owner),
                real_admin,
                datetime(2026, 9, 6),
            )


def test_exact_seven_day_window_and_aware_utc():
    check = policy()['_rollback_policy']
    row = history({'application_ids': [11]})
    assert check(row, user(), datetime(2026, 9, 12, tzinfo=timezone.utc)) == [11]
    for now in [datetime(2026, 9, 12, 0, 0, 0, 1), datetime(2026, 9, 4)]:
        with pytest.raises(ValueError):
            check(row, user(), now)


@pytest.mark.parametrize('raw', ['{', 'null', '11', '{"application_ids":[11],"application_ids":[12]}'])
def test_malformed_and_duplicate_key_metadata_rejected(raw):
    with pytest.raises(ValueError):
        policy()['_rollback_policy'](history(import_settings=raw), user(), datetime(2026, 9, 6))


def test_equivalent_duplicate_encodings_and_error_list_are_compatible():
    boundary = policy()
    row = history({'application_ids': [11, 11], 'linked_applications': {'created_ids': [11], 'updated_ids': [12]}},
                  ['An unrelated row failed'])
    assert boundary['_rollback_policy'](row, user(), datetime(2026, 9, 6)) == [11]
    row.imported_at = None
    assert not boundary['rollback_import_eligibility'](row, user())['can_rollback']


@pytest.mark.parametrize('status', ['rolled_back', 'pending', 'failed', None])
def test_unsupported_states_rejected(status):
    with pytest.raises(ValueError):
        policy()['_rollback_policy'](history({'application_ids': [11]}, status=status),
                                     user(), datetime(2026, 9, 6))


def test_partial_import_eligible_but_update_only_is_not():
    boundary = policy()
    assert boundary['_rollback_policy'](history({'application_ids': [11]}, status='partial'),
                                         user(), datetime(2026, 9, 6)) == [11]
    result = boundary['rollback_import_eligibility'](
        history({'linked_applications': {'created_ids': [], 'updated_ids': [12]}}),
        user(), datetime(2026, 9, 6))
    assert result['can_rollback'] is False
    assert result['rollback_created_count'] == 0
    assert result['rollback_reason']


@pytest.fixture
def rollback_route(monkeypatch):
    """Actual handler against observable query doubles; PostgreSQL tests are separate."""
    import sys
    from flask import Flask, current_app, g, jsonify

    class Column:
        def __init__(self, name):
            self.name = name

        def __eq__(self, value):
            return lambda row: getattr(row, self.name) == value

        def in_(self, values):
            return lambda row: getattr(row, self.name) in values

        def notin_(self, values):
            return lambda row: getattr(row, self.name) not in values

    mutations = []

    class Query:
        def __init__(self, rows, name, filters=()):
            self.rows, self.name, self.filters = rows, name, filters

        def filter(self, *predicates):
            return Query(self.rows, self.name, self.filters + predicates)

        def filter_by(self, **kwargs):
            return self.filter(*(Column(key) == value for key, value in kwargs.items()))

        def populate_existing(self):
            return self

        def with_for_update(self):
            return self

        def all(self):
            return [row for row in self.rows if all(predicate(row) for predicate in self.filters)]

        def first(self):
            return next(iter(self.all()), None)

        def get(self, identifier):
            return next((row for row in self.rows if row.id == identifier), None)

        def delete(self, **kwargs):
            targets = self.all()
            mutations.append((self.name, [row.id for row in targets]))
            self.rows[:] = [row for row in self.rows if row not in targets]
            return len(targets)

    data = {name: [] for name in ['history', 'applications', 'capabilities', 'processes', 'elements']}
    modules = [
        ('application_import_history', 'ApplicationImportHistory', 'history'),
        ('application_portfolio', 'ApplicationComponent', 'applications'),
        ('unified_application_capability_mapping', 'UnifiedApplicationCapabilityMapping', 'capabilities'),
        ('apqc_process', 'ProcessApplicationMapping', 'processes'),
        ('archimate_core', 'ArchiMateElement', 'elements'),
    ]
    columns = SimpleNamespace(**{name: Column(name) for name in
                               ['id', 'organization_id', 'archimate_element_id', 'application_component_id', 'application_id']})
    for module, cls_name, key in modules:
        values = dict(query=Query(data[key], key), id=columns.id,
                      organization_id=columns.organization_id)
        if key == 'capabilities':
            values['application_component_id'] = columns.application_component_id
        if key == 'processes':
            values['application_id'] = columns.application_id
        if key == 'applications':
            values['__table__'] = SimpleNamespace(c=columns)
        monkeypatch.setitem(sys.modules, 'app.models.' + module,
                            SimpleNamespace(**{cls_name: SimpleNamespace(**values)}))
    monkeypatch.setitem(sys.modules, 'app.models.batch_import',
                        SimpleNamespace(ImportAuditLog=lambda **kwargs: SimpleNamespace(**kwargs)))

    class Select:
        def where(self, *predicates):
            self.predicates = predicates
            return self

    session = SimpleNamespace(
        commit=lambda: mutations.append(('commit', [])),
        rollback=lambda: mutations.append(('rollback', [])),
        add=lambda row: None,
        execute=lambda query: SimpleNamespace(scalars=lambda: [
            row.archimate_element_id for row in data['applications']
            if all(predicate(row) for predicate in query.predicates)]),
    )
    boundary = policy()
    boundary.update(db=SimpleNamespace(session=session, select=lambda *args: Select()),
                    current_user=user(), current_app=current_app, jsonify=jsonify)
    source = Path(__file__).resolve().parents[1] / 'app/modules/applications/routes/import_export_routes.py'
    handler = next(node for node in ast.parse(source.read_text(encoding='utf-8')).body
                   if isinstance(node, ast.FunctionDef) and node.name == 'rollback_import')
    handler.decorator_list = []
    exec(compile(ast.Module(body=[handler], type_ignores=[]), str(source), 'exec'), boundary)
    application = Flask(__name__)

    def call(org=1, actor=None):
        boundary['current_user'] = actor or user()
        with application.test_request_context('/applications/rollback-import/10', method='POST'):
            g.current_org_id = org
            response, status = boundary['rollback_import'](10)
            return status, response.get_json()

    row = history({'application_ids': [11]}, imported_at=datetime.now(timezone.utc),
                  id=10, organization_id=1, file_name='synthetic.csv', imported_by_name='Fixture owner')
    data['history'].append(row)
    data['applications'].append(SimpleNamespace(id=11, organization_id=1, name='Created', archimate_element_id=21))
    data['elements'].append(SimpleNamespace(id=21, organization_id=1))
    data['capabilities'].append(SimpleNamespace(id=31, application_component_id=11))
    data['processes'].append(SimpleNamespace(id=41, application_id=11))
    return call, data, mutations


def test_handler_legitimate_control_deletes_validated_created_targets(rollback_route):
    call, data, mutations = rollback_route
    status, result = call()
    assert status == 200, result
    assert result['deleted'] == dict(applications=1, capability_mappings=1,
                                     process_mappings=1, archimate_elements=1)
    assert data['history'][0].status == 'rolled_back'
    assert ('applications', [11]) in mutations
    previous = list(mutations)
    status, result = call()
    assert status == 400, result
    assert mutations == previous


@pytest.mark.parametrize('invalid_id', [12, 999])
def test_handler_rejects_mixed_foreign_or_missing_before_any_delete(rollback_route, invalid_id):
    call, data, mutations = rollback_route
    data['applications'].append(SimpleNamespace(id=12, organization_id=2, name='Foreign', archimate_element_id=None))
    data['capabilities'].append(SimpleNamespace(id=32, application_component_id=12))
    data['processes'].append(SimpleNamespace(id=42, application_id=12))
    data['history'][0].import_settings = json.dumps({'application_ids': [11, invalid_id]})
    status, result = call()
    assert status == 400, result
    assert mutations == []
    assert data['history'][0].status == 'completed'


@pytest.mark.parametrize('survivor_org', [1, 2])
def test_handler_preserves_shared_element_and_updated_application(rollback_route, survivor_org):
    call, data, mutations = rollback_route
    data['history'][0].import_settings = json.dumps({'linked_applications': {'created_ids': [11], 'updated_ids': [12]}})
    data['applications'].append(SimpleNamespace(id=12, organization_id=survivor_org, name='Updated', archimate_element_id=21))
    data['capabilities'].append(SimpleNamespace(id=32, application_component_id=12))
    status, result = call()
    assert status == 200, result
    assert result['deleted']['archimate_elements'] == 0
    assert [row.id for row in data['applications']] == [12]
    assert [row.id for row in data['elements']] == [21]
    assert [row.id for row in data['capabilities']] == [32]


@pytest.mark.parametrize('case,expected', [('foreign_history', 404), ('foreign_element', 400),
                                        ('missing_tenant', 403), ('wrong_owner', 403)])
def test_handler_authorization_failures_are_non_mutating(rollback_route, case, expected):
    call, data, mutations = rollback_route
    if case == 'foreign_history':
        data['history'][0].organization_id = 2
    if case == 'foreign_element':
        data['elements'][0].organization_id = 2
    status, result = call(org=None if case == 'missing_tenant' else 1,
                          actor=user(99) if case == 'wrong_owner' else user())
    assert status == expected, result
    assert mutations == []
