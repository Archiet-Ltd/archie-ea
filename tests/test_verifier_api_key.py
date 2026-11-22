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
