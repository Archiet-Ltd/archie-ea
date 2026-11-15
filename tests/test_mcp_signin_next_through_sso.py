"""mcp01 / QA-042: the page a person was heading for survives a single-sign-on sign-in, and an outside
address in next never does."""
from __future__ import annotations

import pytest

from tests.test_sso_cross_org_domain_claim_per_org import (
    _PASSWORD, _anonymous_client, _configure_sso, _domain, _make_user, _org_admin, _post_login, _session_user_id,
)

NEXT = "/oauth/authorize?client_id=abc&state=1"


def _org_with_sso(db_session, make_org, label):
    org = make_org(label)
    domain = _domain(label)
    config = _configure_sso(db_session, org, domain)
    user = _make_user(db_session, org, "person@" + domain, password=_PASSWORD)
    db_session.commit()
    return org, config, user


def test_password_sign_in_for_an_sso_domain_keeps_next_for_the_way_back(app, db_session, make_org):
    _org, _config, user = _org_with_sso(db_session, make_org, "keepnext")
    client = _anonymous_client(app)
    resp = client.post("/account/login?next=/oauth/authorize%3Fclient_id%3Dabc%26state%3D1",
                       data={"email": user.email, "password": _PASSWORD, "remember_me": ""})
    assert "/auth/sso/initiate" in resp.headers["Location"]
    with client.session_transaction() as sess:
        assert sess.get("_post_login_next") == NEXT


def _finish(app, org, config, user, stored_next):
    from app.modules.auth import sso_routes

    claims = {"email": user.email, "sub": "sub-1"}
    with app.test_request_context("/auth/sso/callback/oidc"):
        from flask import session

        if stored_next is not None:
            session["_post_login_next"] = stored_next
        resp = sso_routes._finish_sso(config, org, claims, test_mode=False)
        return resp, dict(session)


def test_finishing_an_sso_sign_in_returns_to_the_stored_page(app, db_session, make_org):
    org, config, user = _org_with_sso(db_session, make_org, "finish")
    resp, _ = _finish(app, org, config, user, NEXT)
    assert resp.status_code == 302 and resp.headers["Location"].endswith(NEXT)


@pytest.mark.parametrize("outside", ["https://evil.example/x", "//evil.example/x", "/\evil.example"])
def test_an_outside_address_stored_as_next_is_refused(app, db_session, make_org, outside):
    org, config, user = _org_with_sso(db_session, make_org, "outside")
    resp, _ = _finish(app, org, config, user, outside)
    assert "evil.example" not in resp.headers["Location"]
    assert "/dashboard" in resp.headers["Location"]


def test_an_administrator_keeps_next_through_the_two_factor_step(app, db_session, make_org):
    org, config, user = _org_with_sso(db_session, make_org, "mfa")
    admin = _org_admin(db_session, org)
    admin.email = "admin@" + config.email_domain
    db_session.commit()
    resp, kept = _finish(app, org, config, admin, NEXT)
    assert "/account/mfa-challenge" in resp.headers["Location"]
    assert kept.get("_mfa_pending_next") == NEXT
