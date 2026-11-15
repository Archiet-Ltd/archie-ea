"""F500-090 red contracts: actual synchronous handler and active service methods.

Database/query and AI provider boundaries are explicit in-memory doubles. These
tests demonstrate selected-record, option, commit and response behavior, NOT
PostgreSQL constraints, tenant middleware, real providers or live exploitation.
No application factory, production database or provider is initialized.
"""
import ast
from collections import Counter
from datetime import datetime
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from flask import Flask, current_app, jsonify, request

ROOT = Path(__file__).resolve().parents[1]


class Column:
    """Only the query operations used at this boundary; unsupported calls fail."""
    def __init__(self, name):
        self.name = name

    def isnot(self, value):
        return lambda row: getattr(row, self.name) is not value

    def in_(self, values):
        return lambda row: getattr(row, self.name) in values

    def desc(self):
        return self.name


class Query:
    def __init__(self, rows, model=None):
        self.rows = list(rows)
        self.model = model

    def filter(self, *predicates):
        return Query(row for row in self.rows if all(predicate(row) for predicate in predicates))

    def filter_by(self, **values):
        if self.model is not None:
            from sqlalchemy import select
            # Real ORM expression construction rejects nonexistent attributes
            # before an in-memory query can pretend they are valid columns.
            select(self.model).filter_by(**values)
        return Query(row for row in self.rows if all(getattr(row, key) == value for key, value in values.items()))

    def order_by(self, column):
        return Query(sorted(self.rows, key=lambda row: getattr(row, column), reverse=True))

    def limit(self, count):
        return Query(self.rows[:count])

    def all(self):
        return self.rows

    def first(self):
        return next(iter(self.rows), None)


class Session:
    def __init__(self):
        self.pending = []
        self.persisted = []
        self.commits = 0

    def add(self, row):
        self.pending.append(row)

    def commit(self):
        self.persisted.extend(self.pending)
        self.pending.clear()
        self.commits += 1

    def rollback(self):
        self.pending.clear()


def mapping_model(category, session, real_model):
    class Rows:
        def __get__(self, instance, owner):
            return Query((row for row in session.persisted + session.pending if row.category == category), real_model)

    class Mapping:
        query = Rows()

        def __new__(cls, **values):
            row = real_model(**values)
            row.category = category
            return row

    return Mapping


def support_writer(session):
    """Capability support is recorded by the support module: this double takes its place at that boundary.

    It accepts only what the module accepts - the application's organisation, the application and the
    capability - and counts one capability write per call."""
    def record(organization_id, application_id, capability_id, kind='realization', facts=None, actor_id=None):
        assert (organization_id, application_id) == (7, 10), (organization_id, application_id)
        row = SimpleNamespace(category='capability', application_component_id=application_id,
                              unified_capability_id=capability_id, **(facts or {}))
        session.add(row)
        return row
    return record


def actual_service(session, rows):
    """Compile exact active methods without importing constructor dependencies."""
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.apqc_process import ProcessApplicationMapping
    path = ROOT / 'app/services/ai_import_service.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'AIImportService')
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef)
                and node.name in {'bulk_ai_analyze', 'create_ai_mappings'}]
    assert len(cls.body) == 2
    namespace = dict(db=SimpleNamespace(session=session), logger=logging.getLogger(__name__),
        ApplicationComponent=SimpleNamespace(query=Query(rows),
            imported_capabilities=Column('imported_capabilities'), created_at=Column('created_at'), id=Column('id')),
        UnifiedApplicationCapabilityMapping=mapping_model('capability', session, UnifiedApplicationCapabilityMapping),
        record_support_for_records=support_writer(session),
        ProcessApplicationMapping=mapping_model('process', session, ProcessApplicationMapping))
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
    service = namespace['AIImportService']()
    service.write_requests = []
    writer = service.create_ai_mappings

    def recorded_writer(**values):
        service.write_requests.append(values)
        return writer(**values)

    service.create_ai_mappings = recorded_writer

    def analyze(identifier):
        return SimpleNamespace(application_id=identifier, application_name='Synthetic selected application',
            capability_mappings=[{'capability_id': 501, 'confidence_score': .9}],
            process_mappings=[{'process_id': 601, 'similarity_score': .9}],
            archimate_elements=[{'name': 'Synthetic element', 'type': 'ApplicationService', 'layer': 'Application'}],
            avg_capability_confidence=.9, avg_process_confidence=.9,
            processing_time_ms=1, ai_models_used=['deterministic-fixture'], warnings=[])

    def create_element(data, created_by):
        row = SimpleNamespace(category='archimate', **data)
        session.add(row)
        return row

    service.analyze_application_for_ai_mapping = analyze
    service._get_archimate_service = lambda: SimpleNamespace(create_element_from_dict=create_element)
    return service


@pytest.fixture
def boundary(monkeypatch, app):
    # `app` forces the real Flask app factory to run first, which imports
    # every model module and configures the full SQLAlchemy mapper registry.
    # Without it, a bare `from app.models.unified_application_capability_mapping
    # import ...` below is the first thing to touch any mapper, and
    # configuring one mapper configures the whole registry - including an
    # unrelated, already-broken ARB mapper - so an isolated run of this file
    # failed with a mapper-configuration error that has nothing to do with
    # auto-mapping.
    session = Session()
    # An older just-updated import and a newer unrelated record, same tenant.
    rows = [SimpleNamespace(id=10, name='Imported existing record', created_at=datetime(2020, 1, 1),
                            imported_capabilities='Recorded capability', organization_id=7),
            SimpleNamespace(id=99, name='Unrelated newer record', created_at=datetime(2026, 1, 1),
                            imported_capabilities='Recorded capability', organization_id=7)]
    service = actual_service(session, rows)
    provider = ModuleType('app.services.llm_service')
    provider.LLMService = SimpleNamespace(is_available=lambda: True, configuration_status=lambda: {'ready': True})
    service_module = ModuleType('app.services.ai_import_service')
    service_module.get_ai_import_service = lambda: service
    monkeypatch.setitem(sys.modules, provider.__name__, provider)
    monkeypatch.setitem(sys.modules, service_module.__name__, service_module)
    path = ROOT / 'app/modules/applications/routes/auto_mapping_routes.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    handler = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'comprehensive_auto_map')
    handler.decorator_list = []  # Auth/rate/audit wiring is explicitly outside this unit boundary.
    namespace = dict(request=request, jsonify=jsonify, current_app=current_app,
                     current_user=SimpleNamespace(is_authenticated=True, email='fixture@example.invalid'),
                     db=SimpleNamespace(session=session))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[])), str(path), 'exec'), namespace)
    application = Flask(__name__)

    def post(**payload):
        with application.test_request_context('/applications/api/comprehensive-auto-map', method='POST', json=payload):
            response = application.make_response(namespace['comprehensive_auto_map']())
            assert response.status_code == 200, response.get_json()
            return response.get_json()

    return SimpleNamespace(post=post, service=service, session=session,
                           handler=namespace['comprehensive_auto_map'])


@pytest.mark.parametrize('options', [{'auto_create': False}, {'auto_create': True, 'preview_mode': True}])
def test_preview_does_not_commit_any_mapping(boundary, options):
    boundary.post(max_applications=1, **options)
    assert boundary.service.write_requests == [], 'Preview reached the actual writer before acceptance'
    assert boundary.session.persisted == [], 'Preview persisted mappings before any acceptance'
    assert boundary.session.commits == 0, 'Preview must not own a write transaction'


def test_explicit_import_ids_select_updated_import_not_newest_unrelated_record(boundary):
    result = boundary.post(application_ids=[10], max_applications=1, auto_create=True)
    assert [row['application_id'] for row in result['applications']] == [10]
    assert {call['application_id'] for call in boundary.service.write_requests} <= {10}


@pytest.mark.parametrize('flag,category', [
    ('map_capabilities', 'capability_mappings'), ('map_processes', 'process_mappings'), ('generate_archimate', 'archimate_elements'),
])
def test_disabled_category_does_not_reach_writer(boundary, flag, category):
    options = dict(map_capabilities=True, map_processes=True, generate_archimate=True)
    options[flag] = False
    boundary.post(max_applications=1, auto_create=True, **options)
    assert not any(call.get(category) for call in boundary.service.write_requests), boundary.service.write_requests


def test_response_counts_match_distinct_committed_categories(boundary):
    # Isolate actual handler aggregation using a persistence receipt double.
    # This proves response arithmetic, not that current ORM writes succeed.
    proposals = {'total_analyzed': 1, 'capability_mappings_found': 1, 'process_mappings_found': 2,
        'archimate_elements_generated': 0, 'high_confidence_mappings': 3,
        'processing_stats': {'avg_processing_time_ms': 1, 'ai_models_used': ['deterministic-fixture']},
        'applications': [{'application_id': 10, 'capability_mappings': [{'capability_id': 501, 'confidence_score': .9}],
            'process_mappings': [{'process_id': 601, 'similarity_score': .9}, {'process_id': 602, 'similarity_score': .9}]}]}
    boundary.service.bulk_ai_analyze = lambda **kwargs: proposals
    boundary.service.create_ai_mappings = lambda **kwargs: {
        'capability_mappings_created': 1, 'process_mappings_created': 2,
        'archimate_elements_created': 0, 'errors': []}
    result = boundary.post(max_applications=1, auto_create=True)
    assert {key: result[key] for key in ('capability_mappings_created', 'process_mappings_created', 'archimate_elements_created')} == {
        'capability_mappings_created': 1, 'process_mappings_created': 2, 'archimate_elements_created': 0}


def test_active_writer_uses_real_model_query_and_constructor_fields(boundary):
    result = boundary.service.create_ai_mappings(application_id=10,
        capability_mappings=[{'capability_id': 501}],
        process_mappings=[{'process_id': 601}, {'process_id': 602}],
        archimate_elements=[{'name': 'Synthetic element', 'type': 'ApplicationService'}])
    assert result['errors'] == [], result['errors']
    # Current internal commit ownership is not a desired architectural contract.
    rows = boundary.session.persisted + boundary.session.pending
    assert Counter(row.category for row in rows) == {'capability': 1, 'process': 2, 'archimate': 1}
    assert result == {'capability_mappings_created': 1, 'process_mappings_created': 2,
                      'archimate_elements_created': 1, 'errors': []}


def test_empty_candidate_set_positive_control_has_no_writes(boundary):
    result = boundary.post(max_applications=0, auto_create=False)
    assert result['applications'] == []
    assert boundary.session.persisted == []
    assert boundary.session.commits == 0


def test_postgresql_explicit_scope_uses_imported_ids(db_session, make_org, tenant_ctx, boundary):
    """Real ORM selection; shared transaction rolls back all synthetic sources.

    Provider suggestions remain deterministic and below the requested threshold,
    so this test measures selection, not mapping persistence or provider behavior.
    """
    from app.models.application_portfolio import ApplicationComponent

    assert db_session.get_bind().dialect.name == 'postgresql'
    assert db_session.get_bind().in_transaction()
    owner, other = make_org('mapping-owner'), make_org('mapping-other')
    old = ApplicationComponent(name='Synthetic updated import', organization_id=owner.id,
        imported_capabilities='Recorded capability', created_at=datetime(2020, 1, 1))
    newer = ApplicationComponent(name='Synthetic unrelated existing', organization_id=owner.id,
        imported_capabilities='Recorded capability', created_at=datetime(2026, 1, 1))
    foreign = ApplicationComponent(name='Synthetic foreign existing', organization_id=other.id,
        imported_capabilities='Recorded capability', created_at=datetime(2026, 2, 1))
    db_session.add_all([old, newer, foreign])
    db_session.flush()
    boundary.service.bulk_ai_analyze.__func__.__globals__['ApplicationComponent'] = ApplicationComponent
    # Keep the shared application's real tenant/session context. Only the JSON
    # input is substituted; opening the unit fixture's Flask app would bypass it.
    boundary.handler.__globals__['request'] = SimpleNamespace(get_json=lambda: {
        'application_ids': [old.id], 'max_applications': 1,
        'confidence_threshold': 1.0, 'auto_create': False})
    with tenant_ctx(owner.id):
        result = boundary.handler().get_json()
    identifiers = [row['application_id'] for row in result['applications']]
    assert foreign.id not in identifiers, 'Foreign source entered ordinary tenant-scoped selection'
    assert identifiers == [old.id], 'Explicit imported scope was replaced by newest eligible source'

