"""Chat routes that write the model read the permission matrix.

Every route in the AI chat blueprints that creates or edits an application, a
relationship (including application-to-capability mappings) or ArchiMate
elements is refused to a caller the matrix does not allow, with nothing
written, and the answer is per organisation: an organisation that revokes a
cell does not change the answer in another organisation.

Routes that only write chat history, feedback, vendors, solutions or diagrams
are outside this file.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_session")

D = "/ai-chat/data"
C = "/ai-chat/chat"


def _user(db_session, org, org_role, label):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    platform = "Viewer" if org_role == "viewer" else "User"
    user = User(
        email=f"cmw-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
        role=Role.query.filter_by(name=platform).first(),
    )
    user.password = "Test!12345"
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, org_role)
    db_session.flush()
    return user


def _records(db_session, org):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.models.unified_capability import UnifiedCapability

    tag = uuid.uuid4().hex[:6]
    app_row = ApplicationComponent(
        name=f"CMW app {tag}", description="original", business_domain="original",
        organization_id=org.id,
    )
    cap = UnifiedCapability(name=f"CMW cap {tag}", organization_id=org.id)
    spare = UnifiedCapability(name=f"CMW spare {tag}", organization_id=org.id)
    db_session.add_all([app_row, cap, spare])
    db_session.flush()
    mapping = UnifiedApplicationCapabilityMapping(
        unified_capability_id=cap.id, application_component_id=app_row.id,
        support_level="partial", coverage_percentage=10,
    )
    db_session.add(mapping)
    db_session.flush()
    return {"tag": tag, "app": app_row.id, "cap": cap.id, "spare": spare.id,
            "mapping": mapping.id, "org": org.id}


def _scalar(db_session, sql, **params):
    # Raw SQL so the reading user's organisation cannot hide a row.
    return db_session.execute(text(sql), params).scalar()


def _spec(db_session, recs):
    """name -> (method, url, body, snapshot callable, action, object type)."""
    a, t = recs["app"], recs["tag"]
    app_count = (
        "select count(*) from application_components "
        f"where name = :n and organization_id = {recs['org']}"
    )
    return {
        "create-application": (
            "post", f"{D}/create-application", {"name": f"Made {t}"},
            lambda: _scalar(db_session, app_count, n=f"Made {t}"),
            "create", "application"),
        "update-application": (
            "put", f"{D}/update-application/{a}", {"description": "changed"},
            lambda: _scalar(db_session, "select description from application_components where id = :i", i=a),
            "edit", "application"),
        "bulk-create-applications": (
            "post", f"{D}/bulk-create-applications", {"applications": [{"name": f"Bulk {t}"}]},
            lambda: _scalar(db_session, app_count, n=f"Bulk {t}"),
            "create", "application"),
        "bulk-update-applications": (
            "post", f"{D}/bulk-update-applications",
            {"updates": [{"application_id": a, "update_data": {"business_domain": "changed"}}]},
            lambda: _scalar(db_session, "select business_domain from application_components where id = :i", i=a),
            "edit", "application"),
        "create-capability-mapping": (
            "post", f"{D}/create-capability-mapping",
            {"unified_capability_id": recs["spare"], "application_component_id": a},
            lambda: _scalar(db_session, "select count(*) from unified_application_capability_mapping where application_component_id = :i", i=a),
            "create", "relationship"),
        "update-capability-mapping": (
            "put", f"{D}/update-capability-mapping/{recs['mapping']}", {"coverage_percentage": 77},
            lambda: _scalar(db_session, "select coverage_percentage from unified_application_capability_mapping where id = :i", i=recs["mapping"]),
            "edit", "relationship"),
        "save-insights": (
            "post", f"{C}/save-insights",
            {"application_id": a, "insights": {"description": "changed"}},
            lambda: _scalar(db_session, "select description from application_components where id = :i", i=a),
            "edit", "application"),
    }


DB_ROUTES = ["create-application", "update-application", "bulk-create-applications",
             "bulk-update-applications", "create-capability-mapping",
             "update-capability-mapping", "save-insights"]

# chat commands that call the mapper service when they write
SERVICE_ROUTES = {
    "generate-archimate": f"{C}/generate-archimate",
    "map-apqc": f"{C}/map-apqc",
    "bulk-process": f"{C}/bulk-process",
    "apply-archimate": "/ai-chat/apply-archimate",
    "apply-apqc": "/ai-chat/apply-apqc",
}


@pytest.fixture(autouse=True)
def _csrf_off(monkeypatch):
    from app.utils import csrf_helper

    monkeypatch.setattr(csrf_helper, "validate_csrf", lambda *_a, **_k: None)


@pytest.fixture
def world(db_session, make_org):
    a, b = make_org("cmw-a"), make_org("cmw-b")
    w = {
        "a": a, "b": b,
        "viewer": _user(db_session, a, "viewer", "viewer"),
        "architect": _user(db_session, a, "architect", "architect"),
        "b_architect": _user(db_session, b, "architect", "barchitect"),
        "recs_a": _records(db_session, a),
        "recs_b": _records(db_session, b),
    }
    db_session.commit()
    return w


def _call(client, login_as, user, method, url, body):
    login_as(client, user)
    return getattr(client, method)(url, json=body, headers={"X-CSRFToken": "test"})


def _deny(db_session, org, action, obj, role="architect"):
    from app.core.auth.permissions import set_grant

    set_grant(org.id, "role", role, obj, action, False)
    db_session.commit()


@pytest.mark.parametrize("name", DB_ROUTES)
def test_viewer_is_refused_and_nothing_is_written(client, login_as, db_session, world, name):
    method, url, body, snap, _act, _obj = _spec(db_session, world["recs_a"])[name]
    before = snap()
    resp = _call(client, login_as, world["viewer"], method, url, body)
    assert resp.status_code == 403, resp.get_data(as_text=True)[:200]
    assert snap() == before


@pytest.mark.parametrize("name", DB_ROUTES)
def test_matrix_answer_is_per_organisation(client, login_as, db_session, world, name):
    """Organisation A revokes the cell: A's architect is refused and nothing is
    written; organisation B's architect, on B's own records, is not refused."""
    _m, _u, _b, _s, action, obj = _spec(db_session, world["recs_a"])[name]
    _deny(db_session, world["a"], action, obj)

    method, url, body, snap, _a, _o = _spec(db_session, world["recs_a"])[name]
    before = snap()
    refused = _call(client, login_as, world["architect"], method, url, body)
    assert refused.status_code == 403, refused.get_data(as_text=True)[:200]
    assert snap() == before

    method, url, body, _s, _a, _o = _spec(db_session, world["recs_b"])[name]
    allowed = _call(client, login_as, world["b_architect"], method, url, body)
    assert allowed.status_code != 403, allowed.get_data(as_text=True)[:200]

    # and B's architect pointed at A's record changes nothing there
    method, url, body, snap_a, _a, _o = _spec(db_session, world["recs_a"])[name]
    _call(client, login_as, world["b_architect"], method, url, body)
    assert snap_a() == before


@pytest.fixture
def mapper_calls(monkeypatch):
    from app.modules.applications.services.application_architecture_mapper import (
        ApplicationArchitectureMapperService as S,
    )

    calls = []

    def recorder(name):
        def fn(*_a, **_k):
            calls.append(name)
            if name == "map_apqc_from_codes":
                return []
            return {"elements_created": 0, "elements": []}
        return staticmethod(fn)

    for name in ("generate_archimate_for_application", "map_apqc_from_codes", "bulk_auto_map"):
        monkeypatch.setattr(S, name, recorder(name))

    from app.modules.architecture.services.architect_workflow_service import (
        ArchitectWorkflowService as W,
    )

    def workflow(name):
        def fn(*_a, **_k):
            calls.append(name)
            return {"success": True}
        return fn

    for name in ("apply_archimate_elements", "apply_apqc_mappings"):
        monkeypatch.setattr(W, name, workflow(name))
    return calls


def _service_body(name, recs):
    body = {"application_id": recs["app"]}
    if name in ("generate-archimate", "map-apqc"):
        body["preview_only"] = False
    if name == "apply-archimate":
        body["elements"] = []
    if name == "apply-apqc":
        body["mappings"] = []
    return body


@pytest.mark.parametrize("name", list(SERVICE_ROUTES))
def test_viewer_cannot_run_a_model_writing_chat_command(
        client, login_as, db_session, world, mapper_calls, name):
    resp = _call(client, login_as, world["viewer"], "post", SERVICE_ROUTES[name],
                 _service_body(name, world["recs_a"]))
    assert resp.status_code == 403, resp.get_data(as_text=True)[:200]
    assert mapper_calls == []


@pytest.mark.parametrize("name", list(SERVICE_ROUTES))
def test_chat_command_gate_is_per_organisation(
        client, login_as, db_session, world, mapper_calls, name):
    _deny(db_session, world["a"], "create", "relationship")
    refused = _call(client, login_as, world["architect"], "post", SERVICE_ROUTES[name],
                    _service_body(name, world["recs_a"]))
    assert refused.status_code == 403, refused.get_data(as_text=True)[:200]
    assert mapper_calls == []
    allowed = _call(client, login_as, world["b_architect"], "post", SERVICE_ROUTES[name],
                    _service_body(name, world["recs_b"]))
    assert allowed.status_code != 403, allowed.get_data(as_text=True)[:200]


@pytest.mark.parametrize("name", ["generate-archimate", "map-apqc"])
def test_viewer_preview_writes_nothing_and_stays_allowed(
        client, login_as, db_session, world, mapper_calls, name):
    """A preview is read-only, so a viewer's preview behaves as before."""
    resp = _call(client, login_as, world["viewer"], "post", SERVICE_ROUTES[name],
                 {"application_id": world["recs_a"]["app"], "preview_only": True})
    assert resp.status_code != 403
    assert "generate_archimate_for_application" not in mapper_calls
    assert "map_apqc_from_codes" not in mapper_calls


def test_viewer_bulk_process_with_every_write_switched_off_stays_allowed(
        client, login_as, db_session, world, mapper_calls):
    resp = _call(client, login_as, world["viewer"], "post", f"{C}/bulk-process",
                 {"map_processes": False})
    assert resp.status_code != 403
