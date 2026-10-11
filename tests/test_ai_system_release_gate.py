"""The AI system register's release gate: who may write its state, how
concurrent changes serialise, reclassification, owners and browser behaviour.

Uses the shared fixtures in tests/conftest.py (db_session rolls back); the one
concurrency test commits real rows and removes them again.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest

from app.modules.enterprise_genome.services.ai_system_classification import (
    ENTITY_ASSESSMENT,
    PROPKEY_RELEASE_STATE,
    PROPKEY_RISK_CLASS,
    accept_assessment,
    accept_classification,
    get_classification,
    get_release_state,
    mark_released,
    propose_assessment,
    propose_classification,
    reject_classification,
)
from app.modules.enterprise_genome.services.ai_systems_seed import register_ai_system

BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def _user(db_session, org, role_name, label):
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        pytest.skip(f"no {role_name} role seeded")
    u = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name=label,
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    u.password = uuid.uuid4().hex
    db_session.add(u)
    db_session.flush()
    return u


def _system(db_session, org, name="Gate System"):
    el = register_ai_system(
        db_session, org.id, name=f"{name} {uuid.uuid4().hex[:6]}",
        provider="anthropic", model_id="claude-opus-5",
    )
    db_session.flush()
    return el


def _classify(db_session, el, org, tier):
    a = propose_classification(
        element=el, proposed_risk_class=tier,
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    return a


def _accept_class(db_session, el, org, tier):
    a = _classify(db_session, el, org, tier)
    assert accept_classification(a.id, org.id) is True
    db_session.flush()
    return a


def _assess(db_session, el, org):
    a = propose_assessment(element=el, organization_id=org.id, proposed_by_user_id=None)
    db_session.flush()
    return a


def _flashes(client):
    with client.session_transaction() as s:
        return [m for _cat, m in s.get("_flashes", [])]


# --- The gate is the only writer of its own state ----------------------------


@pytest.mark.parametrize("role", ["Viewer", "Architect"])
def test_element_update_api_cannot_write_gate_keys(
    app, db_session, make_org, client, login_as, role
):
    from app.models import ApplicationComponent
    from app.models.models import ArchiMateElement

    org = make_org("gk")
    el = _system(db_session, org)
    _accept_class(db_session, el, org, "high")
    assert mark_released(el, org.id)["success"] is False
    host = ApplicationComponent(name=f"Host {uuid.uuid4().hex[:6]}")
    host.organization_id = org.id
    db_session.add(host)
    db_session.flush()
    user = _user(db_session, org, role, role.lower())
    login_as(client, user)
    client.put(
        f"/dashboard/api/applications/{host.id}/architecture/elements/{el.id}",
        json={"properties": {PROPKEY_RELEASE_STATE: "released",
                             PROPKEY_RISK_CLASS: "minimal"}},
    )
    db_session.expire_all()
    el2 = db_session.get(ArchiMateElement, el.id)
    assert get_release_state(el2) != "released"
    assert get_classification(el2) == "high"


def test_property_service_refuses_gate_keys_from_other_writers(db_session, make_org):
    from app.modules.architecture_assistant.property_service import PropertyService

    org = make_org("gp")
    el = _system(db_session, org)
    svc = PropertyService()
    with pytest.raises(ValueError):
        svc.set_element_property(el, PROPKEY_RELEASE_STATE, "released", source="user")
    # Batch writes drop the reserved keys and still apply the others.
    svc.merge_element_properties(
        el, {PROPKEY_RISK_CLASS: "minimal", "lifecycle": "Production"}, source="user"
    )
    assert get_classification(el) is None
    assert get_release_state(el) == "not set"


def test_gate_still_writes_its_own_keys(db_session, make_org):
    org = make_org("gw")
    el = _system(db_session, org)
    _accept_class(db_session, el, org, "minimal")
    assert get_classification(el) == "minimal"
    assert mark_released(el, org.id)["success"] is True
    assert get_release_state(el) == "released"


# --- Serialising release and classification acceptance -----------------------


def test_approved_classification_not_yet_applied_counts_as_pending(db_session, make_org):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.enterprise_genome.services.ai_system_classification import (
        REFUSAL_PENDING_CLASSIFICATION,
        release_refusal_reason,
    )

    org = make_org("gq")
    el = _system(db_session, org)
    a = _classify(db_session, el, org, "high")
    # The queue has claimed the row but has not yet dispatched it.
    a.status = ApprovalStatus.APPROVED
    a.approved_at = datetime.utcnow()
    db_session.flush()
    assert release_refusal_reason(el, org.id) == REFUSAL_PENDING_CLASSIFICATION
    assert mark_released(el, org.id)["success"] is False


def test_release_racing_classification_acceptance_keeps_the_classification(app):
    """The queue commits its claim, then an administrator's release reads the
    gate, then the queue dispatches. The accepted class must survive and the
    system must not end up released."""
    from app import db
    from app.models.ai_chat_crud_approval import (
        AIChatApprovalAuditLog,
        AIChatCRUDApproval,
        ApprovalStatus,
    )
    from app.models.models import ArchiMateElement
    from app.models.organization import Organization

    with app.app_context():
        sfx = uuid.uuid4().hex[:8]
        org = Organization(name=f"Race {sfx}", slug=f"race-{sfx}")
        db.session.add(org)
        db.session.flush()
        el = register_ai_system(db.session, org.id, name=f"Race {sfx}",
                                provider="anthropic", model_id="claude-opus-5")
        a = propose_classification(element=el, proposed_risk_class="high",
                                   organization_id=org.id, proposed_by_user_id=None)
        db.session.commit()
        org_id, el_id, a_id = org.id, el.id, a.id
        AIChatCRUDApproval.query.filter_by(id=a_id).update(
            {"status": ApprovalStatus.APPROVED, "approved_at": datetime.utcnow()})
        db.session.commit()
    try:
        with app.app_context():
            el_a = ArchiMateElement.query.filter_by(id=el_id, organization_id=org_id).first()
            # Release runs after the claim and before the dispatch: it is refused.
            assert mark_released(el_a, org_id)["success"] is False
            db.session.rollback()
            with app.app_context():
                assert accept_classification(a_id, org_id) is True
                db.session.commit()
        with app.app_context():
            el_f = ArchiMateElement.query.filter_by(id=el_id, organization_id=org_id).first()
            assert get_classification(el_f) == "high"
            assert get_release_state(el_f) != "released"
    finally:
        with app.app_context():
            AIChatApprovalAuditLog.query.filter_by(approval_id=a_id).delete()
            AIChatCRUDApproval.query.filter_by(id=a_id).delete()
            ArchiMateElement.query.filter_by(id=el_id).delete()
            Organization.query.filter_by(id=org_id).delete()
            db.session.commit()


def test_release_and_acceptance_lock_the_element_row(db_session, make_org, monkeypatch):
    """Both write paths reload the element with a row lock before reading the
    gate or writing a property."""
    from sqlalchemy.orm import Query

    org = make_org("gl")
    el = _system(db_session, org)
    locked = []
    real = Query.with_for_update

    def spy(self, *a, **kw):
        locked.append(True)
        return real(self, *a, **kw)

    monkeypatch.setattr(Query, "with_for_update", spy)
    mark_released(el, org.id)
    assert locked, "release did not lock the element row"
    locked.clear()
    a = _classify(db_session, el, org, "minimal")
    accept_classification(a.id, org.id)
    assert locked, "classification acceptance did not lock the element row"


# --- Reclassification --------------------------------------------------------


def test_second_classification_proposal_can_be_recorded(db_session, make_org):
    org = make_org("rc1")
    el = _system(db_session, org)
    _accept_class(db_session, el, org, "minimal")
    second = _classify(db_session, el, org, "high")
    assert second.id is not None
    assert accept_classification(second.id, org.id) is True
    assert get_classification(el) == "high"


def test_rejected_classification_can_be_proposed_again(db_session, make_org):
    org = make_org("rc2")
    el = _system(db_session, org)
    a = _classify(db_session, el, org, "high")
    assert reject_classification(a.id, org.id) is True
    again = _classify(db_session, el, org, "high")
    assert accept_classification(again.id, org.id) is True
    assert get_classification(el) == "high"


def test_reclassify_released_system_through_route_and_queue_resets_to_draft(
    app, db_session, make_org, client, login_as, tenant_ctx
):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org = make_org("rc3")
    el = _system(db_session, org)
    arch = _user(db_session, org, "Architect", "arch")
    reviewer = _user(db_session, org, "Architect", "rev")
    _accept_class(db_session, el, org, "minimal")
    assert mark_released(el, org.id)["success"] is True

    login_as(client, arch)
    r = client.post(f"/genome/ai-systems/{el.id}/classify", data={"risk_class": "high"})
    assert r.status_code == 302
    pending = AIChatCRUDApproval.query.filter_by(
        entity_type="ai_system_classification", entity_id=el.id,
        organization_id=org.id).order_by(AIChatCRUDApproval.id.desc()).first()
    assert pending is not None and json.loads(pending.operation_payload)[
        "proposed_risk_class"] == "high"
    with tenant_ctx(org.id):
        res = AIChatApprovalService(user_id=reviewer.id).approve_and_execute(pending.id)
    assert res.get("success") is True, res
    db_session.refresh(el)
    assert get_classification(el) == "high"
    assert get_release_state(el) == "draft"


def test_classification_refusal_flashes_the_real_reason(
    app, db_session, make_org, client, login_as
):
    org = make_org("rc4")
    el = _system(db_session, org)
    arch = _user(db_session, org, "Architect", "arch")
    login_as(client, arch)
    client.post(f"/genome/ai-systems/{el.id}/classify", data={"risk_class": "high"})
    client.get("/genome/ai-systems")  # drain the first (empty) flash set
    login_as(client, arch)
    r = client.post(f"/genome/ai-systems/{el.id}/classify", data={"risk_class": "limited"})
    assert r.status_code == 302
    msgs = _flashes(client)
    assert msgs and "already" in msgs[0].lower() and "review" in msgs[0].lower()


# --- The unacceptable tier ---------------------------------------------------


def test_unacceptable_system_cannot_be_released(db_session, make_org):
    org = make_org("un1")
    el = _system(db_session, org)
    _accept_class(db_session, el, org, "unacceptable")
    res = mark_released(el, org.id)
    assert res["success"] is False
    assert "prohibited" in res["reason"].lower() or "unacceptable" in res["reason"].lower()
    assert get_release_state(el) != "released"


def test_accepting_unacceptable_resets_released_system_to_draft(db_session, make_org):
    org = make_org("un2")
    el = _system(db_session, org)
    assert mark_released(el, org.id)["success"] is True
    _accept_class(db_session, el, org, "unacceptable")
    assert get_release_state(el) == "draft"


# --- Owners: one service for every element type ------------------------------


def test_owner_service_validates_ownership_type_and_org(db_session, make_org):
    from app.services.element_ownership_service import (
        CrossOrganisationElementOwner,
        set_element_owner,
    )

    org = make_org("ow1")
    other = make_org("ow2")
    el = _system(db_session, org)
    owner = _user(db_session, org, "Architect", "own")
    stranger = _user(db_session, other, "Architect", "str")
    with pytest.raises(ValueError):
        set_element_owner(element_type="ai_system", element_id=el.id, user_id=owner.id,
                          organization_id=org.id, ownership_type="overlord")
    with pytest.raises(CrossOrganisationElementOwner):
        set_element_owner(element_type="ai_system", element_id=el.id, user_id=stranger.id,
                          organization_id=org.id)
    rec = set_element_owner(element_type="ai_system", element_id=el.id, user_id=owner.id,
                            organization_id=org.id)
    assert rec.ownership_type == "primary"


def test_owner_route_rejects_unknown_ownership_type(
    app, db_session, make_org, client, login_as
):
    org = make_org("ow3")
    el = _system(db_session, org)
    arch = _user(db_session, org, "Architect", "arch")
    login_as(client, arch)
    r = client.post(f"/genome/api/ai-systems/{el.id}/owners",
                    json={"user_id": arch.id, "ownership_type": "overlord"})
    assert r.status_code == 400


def test_new_primary_owner_replaces_the_old_one(app, db_session, make_org, client, login_as):
    from app.models.application_owner import ApplicationOwner

    org = make_org("ow4")
    el = _system(db_session, org)
    first = _user(db_session, org, "Architect", "first")
    second = _user(db_session, org, "Architect", "second")
    for u in (first, second):
        login_as(client, first)
        r = client.post(f"/genome/api/ai-systems/{el.id}/owners", json={"user_id": u.id})
        assert r.status_code == 201
    rows = ApplicationOwner.query.filter_by(
        element_type="ai_system", element_id=el.id, ownership_type="primary").all()
    assert [r.user_id for r in rows] == [second.id]


def test_capability_owner_service_is_the_same_implementation():
    from app.services import capability_ownership_service as cap
    from app.services import element_ownership_service as gen

    assert cap.set_capability_owner.__module__ == "app.services.capability_ownership_service"
    assert gen.set_element_owner.__module__ == "app.services.element_ownership_service"
    import inspect
    assert "set_element_owner" in inspect.getsource(cap.set_capability_owner)


def test_register_page_has_an_owner_picker(app, db_session, make_org, client, login_as):
    org = make_org("ow5")
    el = _system(db_session, org)
    arch = _user(db_session, org, "Architect", "arch")
    login_as(client, arch)
    body = client.get("/genome/ai-systems").get_data(as_text=True)
    assert f"/genome/api/ai-systems/{el.id}/owners" in body
    assert 'data-owner-picker' in body


# --- Browser behaviour and element type --------------------------------------


def test_browser_refusal_redirects_with_flash_not_json(
    app, db_session, make_org, client, login_as
):
    org = make_org("br1")
    el = _system(db_session, org)
    admin = _user(db_session, org, "Administrator", "bradmin")
    _accept_class(db_session, el, org, "high")
    login_as(client, admin)
    r = client.post(f"/genome/ai-systems/{el.id}/release",
                    headers={"Accept": BROWSER_ACCEPT})
    assert r.status_code == 302
    assert "application/json" not in (r.headers.get("Content-Type") or "")
    assert any("assessment" in m for m in _flashes(client))


def test_json_client_still_gets_json_refusal(app, db_session, make_org, client, login_as):
    org = make_org("br2")
    el = _system(db_session, org)
    admin = _user(db_session, org, "Administrator", "bradmin")
    _accept_class(db_session, el, org, "high")
    login_as(client, admin)
    r = client.post(f"/genome/ai-systems/{el.id}/release",
                    headers={"Accept": "application/json"})
    assert r.status_code == 409
    assert r.get_json()["success"] is False


def test_routes_only_act_on_ai_system_elements(app, db_session, make_org, client, login_as):
    from app.models.models import ArchiMateElement

    org = make_org("ty1")
    other_el = ArchiMateElement(name="Plain capability", type="Capability",
                                layer="strategy", organization_id=org.id)
    db_session.add(other_el)
    db_session.flush()
    admin = _user(db_session, org, "Administrator", "tyadmin")
    for method, url, kw in [
        ("post", f"/genome/ai-systems/{other_el.id}/release", {}),
        ("post", f"/genome/ai-systems/{other_el.id}/assess", {}),
        ("post", f"/genome/ai-systems/{other_el.id}/classify", {"data": {"risk_class": "high"}}),
        ("get", f"/genome/api/ai-systems/{other_el.id}/owners", {}),
        ("post", f"/genome/api/ai-systems/{other_el.id}/owners", {"json": {"user_id": admin.id}}),
    ]:
        login_as(client, admin)
        r = getattr(client, method)(url, **kw)
        assert r.status_code == 404, (url, r.status_code)
    assert get_release_state(other_el) == "not set"


def test_attach_assessment_shown_only_when_it_can_work(
    app, db_session, make_org, client, login_as
):
    org = make_org("at1")
    pending_sys = _system(db_session, org, "Pending")
    ready_sys = _system(db_session, org, "Ready")
    _classify(db_session, pending_sys, org, "high")  # still under review
    _accept_class(db_session, ready_sys, org, "high")  # needs an assessment
    arch = _user(db_session, org, "Architect", "arch")
    login_as(client, arch)
    body = client.get("/genome/ai-systems").get_data(as_text=True)
    assess_pending = f"/genome/ai-systems/{pending_sys.id}/assess"
    assess_ready = f"/genome/ai-systems/{ready_sys.id}/assess"
    assert assess_ready in body
    assert assess_pending not in body


def test_assess_refusal_flashes_the_reason(app, db_session, make_org, client, login_as):
    org = make_org("at2")
    el = _system(db_session, org)
    arch = _user(db_session, org, "Architect", "arch")
    login_as(client, arch)
    r = client.post(f"/genome/ai-systems/{el.id}/assess")
    assert r.status_code == 302
    msgs = _flashes(client)
    assert msgs and "no accepted risk class" in msgs[0]


def test_emitter_source_has_no_misindented_line():
    import pathlib
    import re

    src = pathlib.Path("app/modules/enterprise_genome/emit/ai_systems_register.py").read_text()
    for line in src.splitlines():
        assert not re.match(r"^f'<td class=\"px-3 py-2 text-sm\">\{_cell\(s\.get\(\"data_sens", line)
    lines = [l for l in src.splitlines() if "_cell(s.get(\"data_sensitivity\"))" in l]
    assert lines and all(l.startswith("            ") for l in lines)
    # Neighbouring lines share one indent.
    for l in src.splitlines():
        if "_classify_cell(s)" in l or "_release_cell(s)" in l:
            assert l.startswith("            f'") and not l.startswith("             ")
