"""D-10 (PR 424 review): the SCIM token cap holds under concurrent issues.

Real commits on separate connections, so this file does not use the
rolled-back ``db_session`` fixture (which would put both threads on one
connection) and cleans up after itself.
"""

from __future__ import annotations

import threading
import time
import uuid

from sqlalchemy import text


def test_d10_two_concurrent_token_issues_for_an_organisation_with_one_admit_exactly_one(app, monkeypatch):
    from app import db
    from app.models.organization import Organization
    from app.services import provisioning_service

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org = Organization(name=f"Tokens {suffix}", slug=f"tokens-{suffix}")
        db.session.add(org)
        db.session.flush()
        org_id = org.id
        provisioning_service.issue_scim_token(org_id, None)  # the one it already has
        db.session.commit()

    reached = threading.Event()
    outcomes = {}
    real = provisioning_service.secrets.token_urlsafe

    def slow(n=None):
        if threading.current_thread().name == "issuer-1":
            reached.set()
            time.sleep(1.0)  # the second issue is now at the lock
        return real(n)

    monkeypatch.setattr(provisioning_service.secrets, "token_urlsafe", slow)

    def issue(name):
        with app.app_context():
            try:
                provisioning_service.issue_scim_token(org_id, None)
                outcomes[name] = "issued"
            except provisioning_service.TokenLimitReached:
                db.session.rollback()
                outcomes[name] = "refused"
            finally:
                db.session.remove()

    first = threading.Thread(target=issue, args=("first",), name="issuer-1")
    second = threading.Thread(target=issue, args=("second",), name="issuer-2")
    try:
        first.start()
        reached.wait(10)
        second.start()
        first.join(30)
        second.join(30)
        with app.app_context():
            active = db.session.execute(
                text("SELECT count(*) FROM scim_tokens WHERE organization_id = :o AND revoked_at IS NULL"),
                {"o": org_id},
            ).scalar_one()
        assert sorted(outcomes.values()) == ["issued", "refused"], outcomes
        assert active == 2
    finally:
        with app.app_context():
            db.session.execute(text("DELETE FROM scim_tokens WHERE organization_id = :o"), {"o": org_id})
            db.session.execute(text("DELETE FROM soc2_audit_log WHERE organization_id = :o"), {"o": org_id})
            db.session.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org_id})
            db.session.commit()


