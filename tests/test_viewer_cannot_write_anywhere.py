"""A person who may not change the model cannot change it through any write route.

The first guard covered ``/api/`` only. This file walks every route that accepts POST, PUT, PATCH or
DELETE, inside or outside ``/api/``, and holds the write guard, the AI chat and the forms to one
answer for the same person and record. Every isolation case uses two real organisations.
"""
from __future__ import annotations

import re
import uuid

import pytest
from test_viewer_cannot_write_through_the_api import (
    WRITE_METHODS,
    _application,
    _member,
    _org,
    _table_counts as _all_table_counts,
)

import app._bootstrap.write_access as write_access

# Read by name so that, where a list does not exist yet, the tests below fail on behaviour.
READ_ONLY_ALLOWED = write_access.READ_ONLY_ALLOWED
READ_ONLY_ALLOWED_PAGES = getattr(write_access, "READ_ONLY_ALLOWED_PAGES", {})
ANSWERS_FOR_ITSELF = getattr(write_access, "ANSWERS_FOR_ITSELF", {})
ALLOWED = {**READ_ONLY_ALLOWED, **READ_ONLY_ALLOWED_PAGES}
SAMPLE_EVERY = 25


def _url(rule: str) -> str:
    """A path that matches ``rule``: a listed choice for ``any(...)``, 1 for numbers, a word otherwise."""

    def fill(match):
        converter = match.group(1) or "string"
        if converter.startswith("any("):
            return converter[4:].split(")")[0].split(",")[0].strip()
        converter = converter.split("(")[0]
        if converter in ("int", "float", "number"):
            return "1"
        return "00000000-0000-0000-0000-000000000001" if converter == "uuid" else "x"

    return re.sub(r"<(?:(any\([^)]*\)|[^:>]+):)?[^>]+>", fill, rule)


def _guard_answer(app, user, method, rule):
    """What the write guard says to ``user`` for this route: None (let through) or its refusal."""
    from flask import g

    hook = next(f for f in app.before_request_funcs[None] if f.__name__ == "_refuse_api_write_without_permission")
    with app.test_request_context(_url(rule), method=method, json={}):
        g._login_user = user
        g.current_org_id = user.organization_id
        return hook()


def _all_write_routes(app):
    routes = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        for method in sorted(rule.methods & set(WRITE_METHODS)):
            routes.append((method, rule.rule, rule.endpoint))
    return sorted(routes)


def _table_counts(db_session):
    """Row counts of every table the model lives in; the browser-error log grows on any refusal."""
    counts = _all_table_counts(db_session)
    counts.pop("error_events", None)
    return counts


@pytest.fixture
def two_orgs(db_session):
    from app.services.billing_plans import set_contract_plan

    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    for org in (org_a, org_b):
        set_contract_plan(org, "enterprise", None)
    db_session.commit()
    _application(db_session, org_a, "Ledger A")
    _application(db_session, org_b, "Ledger B")
    return org_a, org_b


def _viewers(db_session, org):
    return {
        "organisation viewer": _member(db_session, org, "viewer"),
        "platform viewer": _member(db_session, org, "viewer", platform_role="Viewer"),
    }


def _name_of(db_session, application_id):
    """The stored name, read without the ambient organisation filter (the other organisation's row too)."""
    from sqlalchemy import text

    from app.models.application_portfolio import ApplicationComponent

    table = ApplicationComponent.__table__.name
    return db_session.execute(text(f"SELECT name FROM {table} WHERE id = :id"), {"id": application_id}).scalar()


def _with_persona(db_session, user, persona):
    user.enterprise_role = persona
    db_session.commit()
    return user


# -- the three routes the review named ---------------------------------------------------------


def test_a_viewer_cannot_bulk_delete_quick_update_or_add_a_requirement(client, db_session, login_as, two_orgs):
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = two_orgs
    viewer = _member(db_session, org_a, "viewer")
    mine = ApplicationComponent.query.filter_by(organization_id=org_a.id).first()
    theirs = ApplicationComponent.query.filter_by(organization_id=org_b.id).first()
    mine_id, theirs_id = mine.id, theirs.id
    before = _table_counts(db_session)

    attempts = [
        ("/dashboard/applications/bulk-delete", {"ids": [mine_id]}),
        (f"/dashboard/applications/{mine_id}/quick-update", {"name": "Viewer renamed"}),
        ("/ai-chat/data/create-requirement", {"name": "Viewer requirement"}),
    ]
    for path, body in attempts:
        login_as(client, viewer)
        response = client.post(path, json=body)
        assert response.status_code == 403, (path, response.status_code, response.get_data(as_text=True)[:200])
        assert "read-only" in response.get_data(as_text=True), path

    db_session.expire_all()
    assert _name_of(db_session, mine_id) == "Ledger A"
    assert _name_of(db_session, theirs_id) == "Ledger B"
    assert _table_counts(db_session) == before


def test_an_editor_still_updates_an_application_through_the_quick_update(client, db_session, login_as, two_orgs):
    from app.models.application_portfolio import ApplicationComponent

    org_a, _ = two_orgs
    editor = _member(db_session, org_a, "architect")
    mine_id = ApplicationComponent.query.filter_by(organization_id=org_a.id).first().id

    login_as(client, editor)
    response = client.post(f"/dashboard/applications/{mine_id}/quick-update", json={"name": "Editor renamed"})

    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    db_session.expire_all()
    assert _name_of(db_session, mine_id) == "Editor renamed"


def test_a_viewer_who_posts_a_form_is_told_why_in_a_page(client, db_session, login_as, two_orgs):
    org_a, _ = two_orgs
    viewer = _member(db_session, org_a, "viewer")
    login_as(client, viewer)

    response = client.post(
        "/dashboard/applications/bulk-delete",
        data={"ids": "1"},
        headers={"Accept": "text/html"},
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 403
    assert "<html" in body.lower()
    assert "Your role is read-only, so it cannot make this change." in body


# -- every write route --------------------------------------------------------------------------


def test_the_url_map_has_write_routes_outside_api_to_walk(app):
    outside = [r for r in _all_write_routes(app) if "/api/" not in r[1]]
    assert len(outside) > 500


@pytest.mark.parametrize("kind", ["organisation viewer", "platform viewer"])
def test_the_guard_refuses_a_viewer_on_every_write_route(app, db_session, two_orgs, kind):
    viewer = _viewers(db_session, two_orgs[0])[kind]
    routes = [r for r in _all_write_routes(app) if r[2] not in ALLOWED]

    from app.core.auth.permissions import READ_ONLY_CODE

    leaking = []
    for method, rule, _endpoint in routes:
        answer = _guard_answer(app, viewer, method, rule)
        if answer is None or answer[1] != 403 or READ_ONLY_CODE not in answer[0].get_data(as_text=True):
            leaking.append(f"{method} {rule}")
    assert not leaking, f"{len(leaking)} of {len(routes)} write routes would not refuse a Viewer:\n" + "\n".join(
        sorted(leaking)
    )


@pytest.mark.parametrize("org_role", ["architect", "org_admin"])
def test_an_editor_and_an_administrator_are_not_turned_away_on_any_write_route(app, db_session, two_orgs, org_role):
    person = _member(db_session, two_orgs[0], org_role)

    turned_away = sorted(
        f"{m} {rule}"
        for m, rule, _ in _all_write_routes(app)
        if _guard_answer(app, person, m, rule) is not None
    )
    assert not turned_away, turned_away


@pytest.mark.parametrize("kind", ["organisation viewer", "platform viewer"])
def test_a_sample_of_real_writes_outside_api_by_a_viewer_is_refused_and_changes_nothing(
    app, client, db_session, login_as, two_orgs, kind
):
    viewer = _viewers(db_session, two_orgs[0])[kind]
    routes = [r for r in _all_write_routes(app) if "/api/" not in r[1] and r[2] not in ALLOWED][::SAMPLE_EVERY]
    assert len(routes) > 20
    before = _table_counts(db_session)

    leaking = []
    for method, rule, endpoint in routes:
        login_as(client, viewer)
        code = client.open(_url(rule), method=method, json={}).status_code
        if code != 403 and endpoint not in ANSWERS_FOR_ITSELF:
            leaking.append(f"{method} {rule} -> {code}")
    assert not leaking, f"{len(leaking)} of {len(routes)} sampled routes did not refuse a Viewer:\n" + "\n".join(leaking)
    assert _table_counts(db_session) == before


# -- the allow-list ------------------------------------------------------------------------------


def test_the_allow_list_names_a_real_route_and_a_true_reason_for_every_entry(app):
    known = {endpoint: rule for _, rule, endpoint in _all_write_routes(app)}
    for endpoint, reason in ALLOWED.items():
        assert endpoint in known, f"{endpoint} is no longer a write route"
        assert len(reason) > 20, endpoint
    for endpoint in READ_ONLY_ALLOWED:
        assert "/api/" in known[endpoint], f"{endpoint} is not under /api/"
    for endpoint in READ_ONLY_ALLOWED_PAGES:
        assert "/api/" not in known[endpoint], f"{endpoint} is under /api/"
    for endpoint in ANSWERS_FOR_ITSELF:
        assert endpoint in known, endpoint


def test_the_impact_endpoint_stores_nothing(client, db_session, login_as, two_orgs):
    from app.models import ArchiMateElement
    from app.models.traceability import ImpactAnalysisResult

    org_a, _ = two_orgs
    viewer = _member(db_session, org_a, "viewer")
    element = ArchiMateElement(name="Impact subject", type="ApplicationComponent", layer="Application", organization_id=org_a.id)
    db_session.add(element)
    db_session.commit()
    element_id = element.id
    stored_before = ImpactAnalysisResult.query.count()

    login_as(client, viewer)
    response = client.post("/api/v1/impact/analyze", json={"element_id": element_id, "scenario": "retirement"})

    assert response.status_code != 403, response.get_data(as_text=True)[:300]
    db_session.expire_all()
    assert ImpactAnalysisResult.query.count() == stored_before


def test_a_viewer_still_sets_their_scope_and_reports_a_browser_error(client, db_session, login_as, two_orgs):
    org_a, _ = two_orgs
    viewer = _member(db_session, org_a, "viewer")

    login_as(client, viewer)
    scope = client.post("/api/bu-scope", json={"bu_id": None})
    login_as(client, viewer)
    error = client.post("/api/client-error", json={"message": "script failed", "url": "/dashboard/overview"})
    login_as(client, viewer)
    report = client.post("/api/csp-report", json={"csp-report": {"blocked-uri": "inline"}})
    login_as(client, viewer)
    sign_in = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong"})

    for name, response in (("bu-scope", scope), ("client-error", error), ("csp-report", report), ("login", sign_in)):
        assert response.status_code != 403 or "read-only" not in response.get_data(as_text=True), (
            name,
            response.status_code,
            response.get_data(as_text=True)[:200],
        )


# -- one rule for the guard and the AI chat -------------------------------------------------------


def _people(db_session, org):
    editor = _member(db_session, org, "architect")
    admin = _member(db_session, org, "org_admin")
    org_viewer = _member(db_session, org, "viewer")
    platform_viewer = _member(db_session, org, "viewer", platform_role="Viewer")
    persona_viewer = _with_persona(db_session, _member(db_session, org, "viewer"), "cto")
    return {
        "editor": editor,
        "org_admin": admin,
        "org viewer": org_viewer,
        "platform viewer": platform_viewer,
        "viewer with a persona": persona_viewer,
    }


def test_the_guard_and_the_chat_answer_the_same_for_every_person(app, db_session, two_orgs):
    from app.core.auth.permissions import may_write
    from app.modules.ai_chat.tools.executor import user_can_write

    answers = {}
    for label, person in _people(db_session, two_orgs[0]).items():
        answers[label] = (may_write(person), user_can_write(person.id))

    assert all(guard == chat for guard, chat in answers.values()), answers
    assert answers["editor"] == (True, True)
    assert answers["org_admin"] == (True, True)
    assert answers["org viewer"] == (False, False)
    assert answers["platform viewer"] == (False, False)
    assert answers["viewer with a persona"] == (False, False)


def test_a_persona_on_a_viewer_does_not_open_the_application_api(client, db_session, login_as, two_orgs):
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = two_orgs
    target = ApplicationComponent.query.filter_by(organization_id=org_a.id).first()
    target_id = target.id
    other_id = ApplicationComponent.query.filter_by(organization_id=org_b.id).first().id
    before = _table_counts(db_session)

    for persona in ("cto", "arb_member", "solution_architect", "portfolio_manager"):
        person = _with_persona(db_session, _member(db_session, org_a, "viewer"), persona)
        before = _table_counts(db_session)
        login_as(client, person)
        assert client.post("/api/v1/applications/", json={"name": f"{persona} made"}).status_code == 403, persona
        login_as(client, person)
        assert client.put(f"/api/v1/applications/{target_id}", json={"name": f"{persona} renamed"}).status_code == 403
        login_as(client, person)
        assert client.delete(f"/api/v1/applications/{target_id}").status_code == 403, persona
        assert _table_counts(db_session) == before, persona

    db_session.expire_all()
    assert _name_of(db_session, target_id) == "Ledger A"
    assert _name_of(db_session, other_id) == "Ledger B"


def _tool_call(name, arguments):
    from app.modules.ai_chat.tools.executor import ToolCall

    return ToolCall(id=uuid.uuid4().hex, name=name, arguments=arguments)


def _procurement_person(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"proc-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Pro",
        last_name="Curement",
        organization_id=org.id,
        role=Role.query.filter_by(name="User").first(),
        enterprise_role="procurement",
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    db_session.commit()
    return user


def test_the_chat_refuses_a_status_change_the_form_refuses_and_an_editor_succeeds_in_both(
    app, client, db_session, login_as, two_orgs, tenant_ctx
):
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.ai_chat.tools.executor import ToolExecutor

    org_a, _ = two_orgs
    procurement = _procurement_person(db_session, org_a)
    editor = _member(db_session, org_a, "architect")
    target = ApplicationComponent.query.filter_by(organization_id=org_a.id).first()
    target.deployment_status = "planned"
    db_session.commit()
    target_id, target_name = target.id, target.name

    # The form's own answer for this person: the record's edit permission, the one the chat now asks.
    login_as(client, procurement)
    link = client.post(f"/applications/{target_id}/requirements/add", data={"requirement_id": "1"})
    assert link.status_code == 403

    def run_as(user, status):
        with tenant_ctx(org_a.id):
            return ToolExecutor(user.id).execute(
                _tool_call("update_application_status", {"application_name": target_name, "new_status": status})
            )

    refused = run_as(procurement, "testing")
    assert refused.get("success") is False, refused
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, target_id).deployment_status == "planned"

    allowed = run_as(editor, "testing")
    assert allowed.get("success") is True, allowed
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, target_id).deployment_status == "testing"


# -- reads that also save -------------------------------------------------------------------------


def _vendor_option(db_session, org, creator):
    from app.models.business_capability import BusinessCapability
    from app.models.vendor_analysis import OptionsAnalysis, VendorOption

    capability = BusinessCapability(name=f"Cap {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(capability)
    db_session.flush()
    analysis = OptionsAnalysis(
        name="Ranking analysis", capability_id=capability.id, created_by_id=creator.id, organization_id=org.id
    )
    db_session.add(analysis)
    db_session.flush()
    option = VendorOption(analysis_id=analysis.id, vendor_name="Acme", total_score=61.0, organization_id=org.id)
    db_session.add(option)
    db_session.commit()
    return option.id


def test_reading_the_vendor_ranking_stores_it_only_for_a_person_who_may_write(client, db_session, login_as, two_orgs):
    from app.models.vendor_analysis import VendorOption

    org_a, _ = two_orgs
    viewer = _member(db_session, org_a, "viewer")
    editor = _member(db_session, org_a, "architect")
    option_id = _vendor_option(db_session, org_a, editor)

    login_as(client, viewer)
    seen = client.get("/api/vendors/ranking")
    assert seen.status_code == 200, seen.get_data(as_text=True)[:300]
    assert [row["vendor_name"] for row in seen.get_json()["ranking"]] == ["Acme"]
    assert seen.get_json()["ranking"][0]["rank"] == 1
    db_session.expire_all()
    assert db_session.get(VendorOption, option_id).ranking is None

    login_as(client, editor)
    assert client.get("/api/vendors/ranking").status_code == 200
    db_session.expire_all()
    assert db_session.get(VendorOption, option_id).ranking == 1


def test_a_viewer_cannot_start_a_batch_import_stream_and_its_owner_still_can(client, db_session, login_as, two_orgs):
    from app.models.batch_import import BatchImportJob, BatchJobStatus

    org_a, _ = two_orgs
    viewer = _member(db_session, org_a, "viewer")
    editor = _member(db_session, org_a, "architect")
    jobs = {}
    for key, owner in (("viewer", viewer), ("editor", editor)):
        job = BatchImportJob(
            user_id=owner.id, name=f"Job {key}", filename="apps.csv", status=BatchJobStatus.PAUSED,
        )
        db_session.add(job)
        db_session.commit()
        jobs[key] = job.id

    login_as(client, viewer)
    refused = client.get(f"/api/batch-import/jobs/{jobs['viewer']}/stream")
    assert refused.status_code == 403, refused.status_code
    assert "read-only" in refused.get_data(as_text=True)
    db_session.expire_all()
    assert db_session.get(BatchImportJob, jobs["viewer"]).status == BatchJobStatus.PAUSED

    login_as(client, editor)
    opened = client.get(f"/api/batch-import/jobs/{jobs['editor']}/stream")
    assert opened.status_code == 200


# -- a governance persona on a viewer --------------------------------------------------------------


@pytest.mark.parametrize("persona", ["cto", "arb_member", "solution_architect", "portfolio_manager"])
def test_a_persona_on_a_viewer_cannot_delete_or_rename_applications_outside_the_api(
    client, db_session, login_as, two_orgs, persona
):
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = two_orgs
    person = _with_persona(db_session, _member(db_session, org_a, "viewer"), persona)
    mine_id = ApplicationComponent.query.filter_by(organization_id=org_a.id).first().id
    theirs_id = ApplicationComponent.query.filter_by(organization_id=org_b.id).first().id
    before = _table_counts(db_session)

    login_as(client, person)
    deleted = client.post("/dashboard/applications/bulk-delete", json={"ids": [mine_id, theirs_id]})
    login_as(client, person)
    renamed = client.post(f"/dashboard/applications/{mine_id}/quick-update", json={"name": f"{persona} renamed"})

    assert (deleted.status_code, renamed.status_code) == (403, 403), (
        deleted.get_data(as_text=True)[:200],
        renamed.get_data(as_text=True)[:200],
    )
    assert "read-only" in deleted.get_data(as_text=True)
    db_session.expire_all()
    assert _name_of(db_session, mine_id) == "Ledger A"
    assert _name_of(db_session, theirs_id) == "Ledger B"
    assert _table_counts(db_session) == before


def test_a_solution_architect_on_a_viewer_still_records_a_process_owner_the_persona_permits(
    client, db_session, login_as, two_orgs
):
    from app.models.application_owner import ApplicationOwner
    from app.models.process_data import BusinessProcess

    org_a, org_b = two_orgs
    person = _with_persona(db_session, _member(db_session, org_a, "viewer"), "solution_architect")
    owner = _member(db_session, org_a, "architect")
    process = BusinessProcess(name="Handle returns", organization_id=org_a.id, level=2)
    db_session.add(process)
    db_session.commit()
    process_id, owner_id = process.id, owner.id

    login_as(client, person)
    recorded = client.post(f"/architecture/business-process/{process_id}/owners", json={"user_id": owner_id})

    assert recorded.status_code == 201, recorded.get_data(as_text=True)[:300]
    db_session.expire_all()
    assert ApplicationOwner.query.filter_by(user_id=owner_id).count() >= 1


def test_the_overview_save_refuses_the_procurement_person_the_chat_refuses_and_admits_an_editor(
    client, db_session, login_as, two_orgs
):
    from app.models.application_portfolio import ApplicationComponent

    org_a, _ = two_orgs
    procurement = _procurement_person(db_session, org_a)
    editor = _member(db_session, org_a, "architect")
    target = ApplicationComponent.query.filter_by(organization_id=org_a.id).first()
    target.description = "Pays suppliers"
    db_session.commit()
    target_id = target.id

    login_as(client, procurement)
    refused = client.post(f"/applications/{target_id}/overview-update", data={"description": "Changed by procurement"})
    assert refused.status_code == 403
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, target_id).description == "Pays suppliers"

    login_as(client, editor)
    saved = client.post(f"/applications/{target_id}/overview-update", data={"description": "Pays suppliers weekly"})
    assert saved.status_code in (200, 302), saved.status_code
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, target_id).description == "Pays suppliers weekly"
