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
    lines = [line for line in src.splitlines() if "_cell(s.get(\"data_sensitivity\"))" in line]
    assert lines and all(line.startswith("            ") for line in lines)
    # Neighbouring lines share one indent.
    for line in src.splitlines():
        if "_classify_cell(s)" in line or "_release_cell(s)" in line:
            assert line.startswith("            f'") and not line.startswith("             ")


# --- H-1: an assessment is tied to the classification it was made against ----


def _tick():
    """Keep successive utcnow() accepted_at values strictly ordered."""
    import time

    time.sleep(0.01)


def test_assessment_payload_records_the_classification_it_was_made_against(db_session, make_org):
    """Proposing an assessment records the id of the accepted classification it
    is made against, so an assessment can never open the gate for a later
    classification."""
    org = make_org("h1a")
    el = _system(db_session, org)
    classification = _accept_class(db_session, el, org, "high")
    assessment = _assess(db_session, el, org)
    payload = json.loads(assessment.operation_payload)
    assert payload["classification_approval_id"] == classification.id


def test_stale_pending_assessment_does_not_open_the_gate_after_reclassification(db_session, make_org):
    """An assessment proposed under an earlier high classification, left pending
    through a reclassification (high, minimal, high) and accepted after the new
    high classification, must not open the release gate: it was not made against
    the current classification."""
    from app.modules.enterprise_genome.services.ai_system_classification import (
        has_accepted_assessment,
    )

    org = make_org("h1b")
    el = _system(db_session, org)
    _accept_class(db_session, el, org, "high")
    stale = _assess(db_session, el, org)
    _tick()
    _accept_class(db_session, el, org, "minimal")  # supersedes the pending assessment
    _tick()
    _accept_class(db_session, el, org, "high")
    _tick()
    accept_assessment(stale.id, org.id)  # accepted late must still not count
    result = mark_released(el, org.id)
    assert result["success"] is False, "a stale assessment opened the gate"
    assert has_accepted_assessment(el, org.id) is False


def test_accepting_a_classification_rejects_pending_assessments(db_session, make_org):
    """Accepting a new classification supersedes every still-pending assessment
    for the element: the old examination no longer describes the new class."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    org = make_org("h1c")
    el = _system(db_session, org)
    _accept_class(db_session, el, org, "high")
    stale = _assess(db_session, el, org)
    db_session.flush()
    db_session.expire_all()
    assert db_session.get(AIChatCRUDApproval, stale.id).status == ApprovalStatus.PENDING
    _accept_class(db_session, el, org, "minimal")
    db_session.expire_all()
    row = db_session.get(AIChatCRUDApproval, stale.id)
    assert row.status == ApprovalStatus.REJECTED
    assert row.rejected_reason == "superseded by a new classification"


def test_reclassification_rejects_only_this_orgs_pending_assessments(db_session, make_org):
    """Accepting a classification in organisation A rejects A's pending
    assessments but never B's: each assessment names its own organisation's
    classification approval, and B's gate keeps working."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    org_a = make_org("h1d-a")
    org_b = make_org("h1d-b")
    el_a = _system(db_session, org_a)
    el_b = _system(db_session, org_b)
    class_a = _accept_class(db_session, el_a, org_a, "high")
    class_b = _accept_class(db_session, el_b, org_b, "high")
    pending_a = _assess(db_session, el_a, org_a)
    pending_b = _assess(db_session, el_b, org_b)
    # Each assessment names its own organisation's classification approval.
    assert json.loads(pending_a.operation_payload)["classification_approval_id"] == class_a.id
    assert json.loads(pending_b.operation_payload)["classification_approval_id"] == class_b.id
    # A reclassifies; B does not.
    _accept_class(db_session, el_a, org_a, "minimal")
    db_session.expire_all()
    row_a = db_session.get(AIChatCRUDApproval, pending_a.id)
    row_b = db_session.get(AIChatCRUDApproval, pending_b.id)
    assert row_a.status == ApprovalStatus.REJECTED
    assert row_a.rejected_reason == "superseded by a new classification"
    assert row_b.status == ApprovalStatus.PENDING
    # B's gate is unaffected and can still release through its own assessment.
    _tick()
    accept_assessment(pending_b.id, org_b.id)
    assert mark_released(el_b, org_b.id)["success"] is True


# --- H-2: the register readers trust only values the gate wrote --------------


def test_classification_readers_ignore_verbatim_written_gate_keys(db_session, make_org):
    """An element whose acm_properties carry the gate keys with any other
    source (a verbatim writer) reads as unclassified, unreleased and
    assessable-invariant: the gate is the only authority on its own fields."""
    from app.models.models import ArchiMateElement

    org = make_org("h2a")
    forged = ArchiMateElement(
        name="Forged", type="ApplicationComponent", layer="application",
        organization_id=org.id,
        acm_properties={
            PROPKEY_RISK_CLASS: {"value": "high", "source": "user"},
            PROPKEY_RELEASE_STATE: {"value": "released", "source": "user"},
        },
    )
    db_session.add(forged)
    db_session.flush()
    assert get_classification(forged) is None
    assert get_release_state(forged) == "not set"


def test_element_created_with_gate_keys_then_registered_is_not_released_high(db_session, make_org):
    """An element created by a writer that stores acm_properties verbatim and
    later registered as an AI system by the seed's upsert-by-name must not
    enter the register released and high with no assessment."""
    from app.models.models import ArchiMateElement

    org = make_org("h2b")
    name = "Fraud Scoring Engine"          # a name the seed route registers
    pre = ArchiMateElement(
        name=name, type="ApplicationComponent", layer="Application",
        acm_source="journey",
        acm_properties={
            PROPKEY_RISK_CLASS: {"value": "high", "source": "user"},
            PROPKEY_RELEASE_STATE: {"value": "released", "source": "user"},
        },
    )
    pre.organization_id = org.id
    db_session.add(pre)
    db_session.flush()
    el = register_ai_system(db_session, org.id, name=name, provider="openai", model_id="gpt-4o")
    assert el.id == pre.id
    assert PROPKEY_RISK_CLASS not in (el.acm_properties or {})
    assert PROPKEY_RELEASE_STATE not in (el.acm_properties or {})
    assert get_release_state(el) == "not set"
    assert get_classification(el) is None


def test_journey_create_element_route_drops_gate_keys(
    app, db_session, make_org, client, login_as
):
    """The journey's create-element route writes acm_properties through the
    typed property writer, so the two gate keys cannot be stored verbatim and
    later read back as the gate's state."""
    from app.models.archimate_core import ArchitectureModel
    from app.models.models import ArchiMateElement
    from app.models.solution_models import Solution

    org = make_org("h2c")
    arch = _user(db_session, org, "Architect", "h2c")
    sol = Solution(name=f"Sol {uuid.uuid4().hex[:6]}")
    sol.organization_id = org.id
    sol.created_by_id = arch.id
    db_session.add(sol)
    db_session.flush()
    am = ArchitectureModel(name="m", solution_id=sol.id)
    am.organization_id = org.id
    db_session.add(am)
    db_session.flush()
    login_as(client, arch)
    r = client.post(f"/architecture-journey/{sol.id}/element", json={
        "name": "Fraud Scoring Engine", "type": "ApplicationComponent", "layer": "Application",
        "acm_properties": {PROPKEY_RISK_CLASS: {"value": "high"},
                           PROPKEY_RELEASE_STATE: {"value": "released"}}})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    db_session.expire_all()
    row = ArchiMateElement.query.filter_by(name="Fraud Scoring Engine",
                                           organization_id=org.id).first()
    assert row is not None, "route did not create the element"
    assert PROPKEY_RISK_CLASS not in (row.acm_properties or {})
    assert PROPKEY_RELEASE_STATE not in (row.acm_properties or {})


def test_route_chain_journey_create_then_seed_never_shows_released_high(
    app, db_session, make_org, client, login_as
):
    """Product routes only: an Architect creates 'Fraud Scoring Engine' in a
    journey with the two gate keys, then the seed route registers it as an AI
    system; the register must never list it released and high with no
    assessment."""
    from app.models.archimate_core import ArchitectureModel
    from app.models.models import ArchiMateElement
    from app.models.solution_models import Solution
    from app.modules.enterprise_genome.services.ai_systems_slice import build_ai_systems_slice

    org = make_org("h2d")
    arch = _user(db_session, org, "Architect", "h2d")
    sol = Solution(name=f"Sol {uuid.uuid4().hex[:6]}")
    sol.organization_id = org.id
    sol.created_by_id = arch.id
    db_session.add(sol)
    db_session.flush()
    am = ArchitectureModel(name="m", solution_id=sol.id)
    am.organization_id = org.id
    db_session.add(am)
    db_session.flush()
    login_as(client, arch)
    r = client.post(f"/architecture-journey/{sol.id}/element", json={
        "name": "Fraud Scoring Engine", "type": "ApplicationComponent", "layer": "Application",
        "acm_properties": {PROPKEY_RISK_CLASS: {"value": "high"},
                           PROPKEY_RELEASE_STATE: {"value": "released"}}})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    login_as(client, arch)
    client.post("/genome/ai-systems/seed")
    db_session.expire_all()
    el = ArchiMateElement.query.filter_by(name="Fraud Scoring Engine",
                                          organization_id=org.id).first()
    assert el is not None
    assert get_classification(el) is None
    assert get_release_state(el) == "not set"
    slice_data = build_ai_systems_slice(org.id, db_session)
    by_name = {s["name"]: s for s in slice_data["systems"]}
    assert "Fraud Scoring Engine" in by_name
    assert by_name["Fraud Scoring Engine"]["risk_class"] == "not set"
    assert by_name["Fraud Scoring Engine"]["release_state"] == "not set"
    assert by_name["Fraud Scoring Engine"]["release_blocked_reason"] is None


# --- L-1: classification proposals serialise on the element row --------------


def test_concurrent_classification_proposals_record_at_most_one(app):
    """Two concurrent proposals for the same system: the pending check is
    serialised on the element row, so at most one proposal is recorded."""
    import threading

    from app import db
    from app.models.ai_chat_crud_approval import (
        AIChatApprovalAuditLog,
        AIChatCRUDApproval,
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
        db.session.commit()
        org_id, el_id = org.id, el.id
    recorded = []
    barrier = threading.Barrier(2)

    def propose(tier):
        with app.app_context():
            el = db.session.get(ArchiMateElement, el_id)
            barrier.wait(5)
            try:
                a = propose_classification(element=el, proposed_risk_class=tier,
                                           organization_id=org_id, proposed_by_user_id=None)
                db.session.commit()
                recorded.append(a.id)
            except ValueError:
                db.session.rollback()

    ts = [threading.Thread(target=propose, args=(t,)) for t in ("minimal", "high")]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    try:
        assert len(recorded) <= 1, "two pending classification proposals for one system"
    finally:
        with app.app_context():
            approval_ids = [r.id for r in AIChatCRUDApproval.query.filter_by(
                entity_id=el_id, organization_id=org_id).all()]
            if approval_ids:
                AIChatApprovalAuditLog.query.filter(
                    AIChatApprovalAuditLog.approval_id.in_(approval_ids)
                ).delete(synchronize_session=False)
                AIChatCRUDApproval.query.filter(
                    AIChatCRUDApproval.id.in_(approval_ids)
                ).delete(synchronize_session=False)
            ArchiMateElement.query.filter_by(id=el_id).delete()
            db.session.commit()


# --- L-4: the register shows actions only to roles the routes admit ----------


def test_viewer_sees_no_write_controls(app, db_session, make_org, client, login_as):
    """A Viewer can read the register but must not be shown the Release,
    Classify, Attach-assessment or owner-assignment controls, all of which the
    routes would refuse with 403."""
    org = make_org("l4a")
    el = _system(db_session, org)
    _accept_class(db_session, el, org, "high")  # would show every control
    viewer = _user(db_session, org, "Viewer", "l4viewer")
    login_as(client, viewer)
    body = client.get("/genome/ai-systems").get_data(as_text=True)
    assert f"/genome/ai-systems/{el.id}/release" not in body
    assert f"/genome/ai-systems/{el.id}/classify" not in body
    assert f"/genome/ai-systems/{el.id}/assess" not in body
    assert f"/genome/api/ai-systems/{el.id}/owners" not in body


def test_architect_sees_classify_but_not_release(app, db_session, make_org, client, login_as):
    """An Architect (general permission, not org admin) sees the Classify form
    on a releasable system, but not the Release form, which is limited to
    administrators."""
    org = make_org("l4b")
    el = _system(db_session, org)
    _accept_class(db_session, el, org, "minimal")  # releasable: Release WOULD render for an admin
    arch = _user(db_session, org, "Architect", "l4arch")
    login_as(client, arch)
    body = client.get("/genome/ai-systems").get_data(as_text=True)
    assert f"/genome/ai-systems/{el.id}/classify" in body
    assert f"/genome/ai-systems/{el.id}/release" not in body
    assert f"/genome/api/ai-systems/{el.id}/owners" in body


def test_release_control_shown_only_to_administrators(app, db_session, make_org, client, login_as):
    """The Release form renders for an administrator of the organisation (whose
    release POST would be admitted) and for nobody else."""
    org = make_org("l4c")
    el = _system(db_session, org)
    _accept_class(db_session, el, org, "minimal")  # releasable, no refusal reason
    admin = _user(db_session, org, "Administrator", "l4admin")
    login_as(client, admin)
    body = client.get("/genome/ai-systems").get_data(as_text=True)
    assert f"/genome/ai-systems/{el.id}/release" in body, "administrator lost Release"
    arch = _user(db_session, org, "Architect", "l4arch")
    login_as(client, arch)
    body = client.get("/genome/ai-systems").get_data(as_text=True)
    assert f"/genome/ai-systems/{el.id}/release" not in body, "Architect sees Release"
