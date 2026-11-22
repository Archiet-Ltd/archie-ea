"""Verifier write-path (ENT-V): authentication, scope, org isolation, verdicts.

Proves the security-critical behaviour before the feature can deploy:
- no key / bad key -> 401
- wrong scope -> 403, key bound to another org -> 403 (ENT-V-5, ENT-V-8)
- a posted requirement is stored Proposed, source_kind assistant, scoped to the
  verifier org (ENT-V-1)
- a Failed then Verified verdict flips the verified-on-current-commit flag
  (ENT-V-6, ENT-V-7)
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _issue(db_session, org_id, scopes="verifier:write", label="test-verifier"):
    from app.models.verifier_api_key import VerifierApiKey

    key, secret = VerifierApiKey.issue(organization_id=org_id, label=label, scopes=scopes)
    db_session.add(key)
    db_session.commit()
    return key, secret


def _auth(secret):
    return {"Authorization": f"Bearer {secret}"}


def test_no_key_is_401(client):
    r = client.post("/api/v1/verifier/requirements", json={"name": "x"})
    assert r.status_code == 401


def test_bad_key_is_401(client):
    r = client.post(
        "/api/v1/verifier/requirements", json={"name": "x"}, headers=_auth("evk_not_real")
    )
    assert r.status_code == 401


def test_wrong_scope_is_403(client, db_session, make_org, app):
    org = make_org("ifiok")
    app.config["IFIOK_VERIFIER_ORG_ID"] = org.id
    _, secret = _issue(db_session, org.id, scopes="verifier:readonly")
    r = client.post("/api/v1/verifier/requirements", json={"name": "x"}, headers=_auth(secret))
    assert r.status_code == 403


def test_key_for_other_org_is_403(client, db_session, make_org, app):
    verifier_org = make_org("ifiok")
    other_org = make_org("other")
    app.config["IFIOK_VERIFIER_ORG_ID"] = verifier_org.id
    _, secret = _issue(db_session, other_org.id)  # bound to the wrong org
    r = client.post("/api/v1/verifier/requirements", json={"name": "x"}, headers=_auth(secret))
    assert r.status_code == 403


def test_post_requirement_is_proposed_and_scoped(client, db_session, make_org, app):
    org = make_org("ifiok")
    app.config["IFIOK_VERIFIER_ORG_ID"] = org.id
    _, secret = _issue(db_session, org.id)

    r = client.post(
        "/api/v1/verifier/requirements",
        json={
            "ears_sentence": "When a user signs in, the system shall show the dashboard.",
            "stakeholder": "CTO",
            "screen": "dashboard",
        },
        headers=_auth(secret),
    )
    assert r.status_code == 201
    data = r.get_json()["data"]
    assert data["status"] == "Proposed"
    assert data["verified"] is False

    from app.models.models import ArchiMateElement

    el = db_session.get(ArchiMateElement, data["id"])
    assert el is not None
    assert el.organization_id == org.id
    assert el.source_kind == "assistant"
    assert el.type == "Requirement"


def test_verdict_flow_and_verified_flag(client, db_session, make_org, app):
    org = make_org("ifiok")
    app.config["IFIOK_VERIFIER_ORG_ID"] = org.id
    _, secret = _issue(db_session, org.id)

    r = client.post(
        "/api/v1/verifier/requirements",
        json={"ears_sentence": "The system shall record a verdict."},
        headers=_auth(secret),
    )
    req_id = r.get_json()["data"]["id"]

    failed = client.post(
        "/api/v1/verifier/verdicts",
        json={
            "requirement_element_id": req_id,
            "status": "Failed",
            "command": "pytest -q",
            "result_line": "1 failed",
            "unmet_parts": ["criterion a"],
        },
        headers=_auth(secret),
    )
    assert failed.status_code == 201
    assert failed.get_json()["data"]["requirement"]["verified"] is False

    verified = client.post(
        "/api/v1/verifier/verdicts",
        json={
            "requirement_element_id": req_id,
            "status": "Verified",
            "command": "pytest -q",
            "result_line": "1 passed",
        },
        headers=_auth(secret),
    )
    assert verified.status_code == 201
    assert verified.get_json()["data"]["requirement"]["verified"] is True


def test_person_confirms_requirement(client, db_session, make_org, app, login_as):
    """ENT-V-2: a signed-in person flips a Proposed requirement to Confirmed."""
    import uuid

    from app.models.user import User

    org = make_org("ifiok")
    app.config["IFIOK_VERIFIER_ORG_ID"] = org.id
    _, secret = _issue(db_session, org.id)

    r = client.post(
        "/api/v1/verifier/requirements",
        json={"ears_sentence": "The system shall be confirmable."},
        headers=_auth(secret),
    )
    req_id = r.get_json()["data"]["id"]

    user = User(
        email=f"po-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Product",
        last_name="Owner",
        organization_id=org.id,
        enterprise_role="viewer",
        confirmed=True,
    )
    db_session.add(user)
    db_session.commit()
    login_as(client, user)

    rc = client.post(
        f"/enterprise/requirements/{req_id}/confirm", json={"decision": "confirm"}
    )
    assert rc.status_code == 200, rc.get_data(as_text=True)
    assert rc.get_json()["data"]["status"] == "Confirmed"

    from app.models.models import ArchiMateElement

    assert db_session.get(ArchiMateElement, req_id).status == "Confirmed"


def test_api_key_cannot_confirm(client, db_session, make_org, app):
    """ENT-V-2: no key can confirm — the confirm route is session-only, so a
    Bearer key gets 401 (and the requirement stays Proposed)."""
    org = make_org("ifiok")
    app.config["IFIOK_VERIFIER_ORG_ID"] = org.id
    _, secret = _issue(db_session, org.id)
    r = client.post(
        "/api/v1/verifier/requirements",
        json={"ears_sentence": "The system shall stay proposed."},
        headers=_auth(secret),
    )
    req_id = r.get_json()["data"]["id"]

    rc = client.post(
        f"/enterprise/requirements/{req_id}/confirm",
        json={"decision": "confirm"},
        headers=_auth(secret),
    )
    assert rc.status_code in (401, 403)
    from app.models.models import ArchiMateElement

    assert db_session.get(ArchiMateElement, req_id).status == "Proposed"


def _issue_builder(db_session, org_id):
    return _issue(db_session, org_id, scopes="builder:claim", label="test-builder")


def _post_requirement(client, secret, sentence="The system shall ship."):
    r = client.post(
        "/api/v1/verifier/requirements", json={"ears_sentence": sentence}, headers=_auth(secret)
    )
    return r.get_json()["data"]["id"]


def _confirm_as_person(client, db_session, login_as, org_id, req_id):
    import uuid

    from app.models.user import User

    user = User(
        email=f"po-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Product",
        last_name="Owner",
        organization_id=org_id,
        enterprise_role="viewer",
        confirmed=True,
    )
    db_session.add(user)
    db_session.commit()
    login_as(client, user)
    rc = client.post(f"/enterprise/requirements/{req_id}/confirm", json={"decision": "confirm"})
    assert rc.status_code == 200, rc.get_data(as_text=True)


def test_builder_claims_and_delivers(client, db_session, make_org, app, login_as):
    """ENT-V-3/4: a builder claims a Confirmed requirement and reports a delivery."""
    org = make_org("ifiok")
    app.config["IFIOK_VERIFIER_ORG_ID"] = org.id
    _, vsecret = _issue(db_session, org.id)
    req_id = _post_requirement(client, vsecret)
    _confirm_as_person(client, db_session, login_as, org.id, req_id)

    _, bsecret = _issue_builder(db_session, org.id)
    rc = client.post(
        "/api/v1/verifier/work-packages",
        json={"requirement_element_id": req_id},
        headers=_auth(bsecret),
    )
    assert rc.status_code == 201, rc.get_data(as_text=True)
    task_id = rc.get_json()["data"]["task_id"]
    assert rc.get_json()["data"]["status"] == "Claimed"

    rd = client.post(
        f"/api/v1/verifier/work-packages/{task_id}/deliver",
        json={"commit": "abc1234", "result_line": "1 passed"},
        headers=_auth(bsecret),
    )
    assert rd.status_code == 200, rd.get_data(as_text=True)
    assert rd.get_json()["data"]["status"] == "Delivered, awaiting verification"


def test_cannot_claim_unconfirmed_requirement(client, db_session, make_org, app):
    org = make_org("ifiok")
    app.config["IFIOK_VERIFIER_ORG_ID"] = org.id
    _, vsecret = _issue(db_session, org.id)
    req_id = _post_requirement(client, vsecret)  # still Proposed
    _, bsecret = _issue_builder(db_session, org.id)
    rc = client.post(
        "/api/v1/verifier/work-packages",
        json={"requirement_element_id": req_id},
        headers=_auth(bsecret),
    )
    assert rc.status_code == 409


def test_builder_key_cannot_set_verdict(client, db_session, make_org, app):
    """ENT-V-5: only the verifier scope may set a verdict; a builder key -> 403."""
    org = make_org("ifiok")
    app.config["IFIOK_VERIFIER_ORG_ID"] = org.id
    _, bsecret = _issue_builder(db_session, org.id)
    rc = client.post(
        "/api/v1/verifier/verdicts",
        json={"requirement_element_id": 1, "status": "Verified"},
        headers=_auth(bsecret),
    )
    assert rc.status_code == 403


def test_failed_verdict_reopens_task(client, db_session, make_org, app, login_as):
    """ENT-V-6: a Failed verdict reopens the builder's task with the unmet parts."""
    org = make_org("ifiok")
    app.config["IFIOK_VERIFIER_ORG_ID"] = org.id
    _, vsecret = _issue(db_session, org.id)
    req_id = _post_requirement(client, vsecret)
    _confirm_as_person(client, db_session, login_as, org.id, req_id)

    _, bsecret = _issue_builder(db_session, org.id)
    rc = client.post(
        "/api/v1/verifier/work-packages",
        json={"requirement_element_id": req_id},
        headers=_auth(bsecret),
    )
    task_id = rc.get_json()["data"]["task_id"]
    client.post(
        f"/api/v1/verifier/work-packages/{task_id}/deliver",
        json={"commit": "abc"},
        headers=_auth(bsecret),
    )

    client.post(
        "/api/v1/verifier/verdicts",
        json={"requirement_element_id": req_id, "status": "Failed", "unmet_parts": ["criterion x"]},
        headers=_auth(vsecret),
    )
    from app.models.requirement_task import RequirementTask

    task = db_session.get(RequirementTask, task_id)
    assert task.status == "Reopened, awaiting delivery"
    assert task.unmet_parts == ["criterion x"]
