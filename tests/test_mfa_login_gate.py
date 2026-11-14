"""The password login route gates an administrator on MFA (R1-B12 PR 2,
TB-0144/PB-0100): /account/login never mints a real session for an
administrator until a TOTP code is verified, whether that is a first-time
enrolment or an already-enrolled sign-in.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pyotp
import pytest

pytestmark = pytest.mark.usefixtures("db_session")

_PASSWORD = "Str0ng!Passw0rd"

# Everyone who has not enrolled has a first week; these tests are about the
# sign-in after it, so the people they create started theirs eight days ago.
_WEEK_OVER = datetime.utcnow() - timedelta(days=8)


def _azure_identity():
    """One tid+oid pair, and the (userinfo, external_id) shape the nOAuth
    fix (app/auth/sso.py) requires to resolve-or-link an Azure account:
    either a pre-existing external_id = "{tid}:{oid}" match, or a
    configured AZURE_AD_TENANT_ID whose value equals the claim's own tid.
    Lead ruling (board note 939): these tests must drive one of those two
    real paths, not the now-correctly-refused "any unverified email claim
    links any existing account" shape they used before the fix."""
    tid = f"tid-{uuid.uuid4().hex[:8]}"
    oid = f"oid-{uuid.uuid4().hex[:8]}"
    return tid, oid, f"{tid}:{oid}"


def _make_admin(db_session, org, *, mfa_enabled=False, mfa_secret=None):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"mfa-login-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=role,
    )
    user.password = _PASSWORD
    user.mfa_enabled = mfa_enabled
    user.mfa_secret = mfa_secret
    user.signup_at = _WEEK_OVER
    db_session.add(user)
    db_session.commit()
    return user


def _make_plain_user(db_session, org):
    from app.models.user import User

    user = User(
        email=f"plain-login-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True,
    )
    user.password = _PASSWORD
    db_session.add(user)
    db_session.commit()
    return user


def test_a_plain_user_logs_in_without_any_mfa_step(app, db_session, make_org):
    org = make_org("mfa-gate-plain")
    user = _make_plain_user(db_session, org)

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": user.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" not in resp.headers.get("Location", "")


def test_an_unenrolled_administrator_is_sent_to_enrol_not_logged_in(app, db_session, make_org):
    org = make_org("mfa-gate-enrol")
    admin = _make_admin(db_session, org, mfa_enabled=False)

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" in resp.headers.get("Location", "")

    challenge = client.get("/account/mfa-challenge")
    assert challenge.status_code == 200
    assert b"Set up two-factor authentication" in challenge.data or b"setup key" in challenge.data.lower()


def test_an_unenrolled_administrator_cannot_reach_dashboard_without_mfa(app, db_session, make_org):
    org = make_org("mfa-gate-noaccess")
    admin = _make_admin(db_session, org, mfa_enabled=False)

    client = app.test_client()
    client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    resp = client.get("/dashboard/overview")
    # Not authenticated yet -- MFA was never completed.
    assert resp.status_code in (302, 401)


def test_enrolling_with_the_right_code_completes_login(app, db_session, make_org):
    org = make_org("mfa-gate-enrol-complete")
    admin = _make_admin(db_session, org, mfa_enabled=False)

    client = app.test_client()
    client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    # GET the challenge page first -- that's where a fresh secret is
    # generated and stashed in the session (mirroring a real authenticator
    # app scanning the page's QR code / setup key before producing a code).
    client.get("/account/mfa-challenge")
    with client.session_transaction() as sess:
        secret = sess["_mfa_enroll_secret"]
    code = pyotp.TOTP(secret).now()

    resp = client.post("/account/mfa-challenge", data={"code": code}, follow_redirects=False)
    # The right code turns two-factor on and shows the recovery codes once;
    # the sign-in finishes when the person confirms they saved them.
    assert resp.status_code == 200
    assert b"recovery-codes" in resp.data
    assert client.get("/dashboard/overview").status_code in (302, 401)
    resp = client.post(
        "/account/mfa-challenge",
        data={"step": "codes", "recovery_confirmed": "1"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    from app.models.user import User

    db_session.refresh(admin)
    reloaded = db_session.get(User, admin.id)
    assert reloaded.mfa_enabled is True
    assert reloaded.mfa_secret == secret

    dash = client.get("/dashboard/overview")
    assert dash.status_code == 200


def test_enrolling_with_the_wrong_code_does_not_complete_login(app, db_session, make_org):
    org = make_org("mfa-gate-enrol-wrong")
    admin = _make_admin(db_session, org, mfa_enabled=False)

    client = app.test_client()
    client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )

    resp = client.post("/account/mfa-challenge", data={"code": "000000"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"did not match" in resp.data

    dash = client.get("/dashboard/overview")
    assert dash.status_code in (302, 401)


def test_an_already_enrolled_administrator_must_enter_a_valid_code(app, db_session, make_org):
    secret = pyotp.random_base32()
    org = make_org("mfa-gate-enrolled")
    admin = _make_admin(db_session, org, mfa_enabled=True, mfa_secret=secret)

    client = app.test_client()
    client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )

    # Wrong code: still not logged in.
    client.post("/account/mfa-challenge", data={"code": "000000"})
    dash = client.get("/dashboard/overview")
    assert dash.status_code in (302, 401)

    # Right code: now logged in.
    code = pyotp.TOTP(secret).now()
    resp = client.post("/account/mfa-challenge", data={"code": code}, follow_redirects=False)
    assert resp.status_code in (302, 303)

    dash = client.get("/dashboard/overview")
    assert dash.status_code == 200


def test_mfa_challenge_with_no_pending_login_redirects_to_login(app):
    client = app.test_client()
    resp = client.get("/account/mfa-challenge", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("Location", "")


# ---------------------------------------------------------------------------
# An administrator invited into a FOREIGN organisation (not their home
# organisation) must be gated on MFA exactly like a home-organisation
# administrator -- required_for() used to check only User.is_org_admin,
# which only ever answers for the user's own home organisation, so this
# class of administrator signed in on a password alone with no MFA step at
# all (second refuter pass, R1).
#
# MFA authority fails closed on an organisation's active state (third
# refuter pass, R5/R6): deactivation is not enforced at login or at
# session-switch time -- neither user_can_access_org nor
# switch_active_organization checks Organization.is_active -- so an
# administrator of a deactivated organisation (home or invited) can still
# reach it and must still be challenged. A prior round filtered
# org_ids_for down to active organisations only, which let such an
# administrator through on a password alone; that filtering is gone.
# ---------------------------------------------------------------------------


def _make_user_with_org_role(db_session, home_org, *, role_org=None, role="org_admin"):
    """A user whose HOME organisation membership carries no admin authority
    at all (plain default Role), but who optionally holds a real OrgRole
    grant in a different organisation they were invited into -- not a
    database shortcut on the User row."""
    from app.models.user import User

    user = User(
        email=f"invited-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=home_org.id,
        confirmed=True,
        # Someone with no administrator grant anywhere is a member inside their first week.
        signup_at=_WEEK_OVER if role_org is not None else None,
    )
    user.password = _PASSWORD
    db_session.add(user)
    db_session.commit()
    if role_org is not None:
        from app.models.org_role import OrgRole

        OrgRole.set_role(role_org.id, user.id, role, granted_by_id=user.id)
        db_session.commit()
    return user


def test_an_org_admin_of_an_invited_org_not_home_org_is_sent_to_mfa_on_password_login(
    app, db_session, make_org
):
    home_org = make_org("mfa-gate-invited-home")
    other_org = make_org("mfa-gate-invited-other")
    user = _make_user_with_org_role(db_session, home_org, role_org=other_org)

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": user.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" in resp.headers.get("Location", "")


def test_sso_callback_sends_an_org_admin_of_an_invited_org_to_the_mfa_challenge(
    app, db_session, make_org, monkeypatch
):
    home_org = make_org("sso-mfa-gate-invited-home")
    other_org = make_org("sso-mfa-gate-invited-other")
    user = _make_user_with_org_role(db_session, home_org, role_org=other_org)
    tid, oid, external_id = _azure_identity()
    user.external_id = external_id
    user.sso_provider = "azure"
    db_session.commit()

    client = app.test_client()
    userinfo = {
        "sub": f"external-{uuid.uuid4().hex[:8]}",
        "oid": oid,
        "tid": tid,
        "email": user.email,
        "given_name": "Invited",
        "family_name": "Admin",
    }
    resp = _sso_callback(client, monkeypatch, db_session, userinfo)

    assert resp.status_code in (302, 303), resp.get_data(as_text=True)
    assert "/mfa-challenge" in resp.headers.get("Location", "")
    with client.session_transaction() as sess:
        assert sess.get("_mfa_pending_user_id") == user.id
        assert "_user_id" not in sess
    dash = client.get("/dashboard/overview")
    assert dash.status_code in (302, 401)


def test_a_user_with_no_org_role_admin_row_anywhere_is_not_sent_to_mfa_no_regression(
    app, db_session, make_org
):
    home_org = make_org("mfa-gate-no-admin-anywhere-home")
    user = _make_user_with_org_role(db_session, home_org, role_org=None)

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": user.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" not in resp.headers.get("Location", "")


def test_an_administrator_whose_home_organisation_is_deactivated_is_still_sent_to_mfa(
    app, db_session, make_org
):
    """R5 (regression, home-organisation side): deactivation is not
    enforced at login or at session-switch time, so an administrator whose
    home organisation is deactivated can still sign in and switch into it
    exactly as before -- a prior round's active-only filter on
    org_ids_for let this administrator reach /admin on a password alone,
    with no MFA step at all."""
    org = make_org("mfa-gate-home-deactivated")
    admin = _make_admin(db_session, org, mfa_enabled=False)
    org.is_active = False
    db_session.commit()

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" in resp.headers.get("Location", "")


def test_an_administrator_whose_home_organisation_is_active_null_is_still_sent_to_mfa(
    app, db_session, make_org
):
    """R6: NULL Organization.is_active means legacy-active in this
    codebase's own convention (see app/jobs/tenant_safe_job.py's
    isnot(False) filter, "NULL is legacy-active"), the opposite of the
    is_(True) filter a prior round wrote, which would have treated this
    administrator as if their organisation were inactive. Pinned even
    though org_ids_for no longer filters on active state at all, so a
    future reintroduction of filtering is caught regardless of which
    direction it gets the NULL case wrong."""
    org = make_org("mfa-gate-home-active-null")
    admin = _make_admin(db_session, org, mfa_enabled=False)
    org.is_active = None
    db_session.commit()

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" in resp.headers.get("Location", "")


def test_an_org_admin_of_only_a_deactivated_invited_org_is_still_sent_to_mfa(
    app, db_session, make_org
):
    """R5 (regression, invited-organisation side -- the exact scenario the
    third review reproduced): a user whose HOME organisation carries no
    admin authority at all, but who holds an org_admin OrgRole grant in a
    different, deactivated organisation they were invited into, is still
    challenged on MFA. A prior round's active-only filter on org_ids_for
    excluded the deactivated organisation from the id set entirely, so
    this administrator signed in on a password alone and reached /admin."""
    home_org = make_org("mfa-gate-deactivated-only-home")
    deactivated_org = make_org("mfa-gate-deactivated-only-other")
    deactivated_org.is_active = False
    db_session.commit()
    user = _make_user_with_org_role(db_session, home_org, role_org=deactivated_org)

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": user.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" in resp.headers.get("Location", "")


def test_an_org_admin_of_a_deactivated_org_is_still_gated_if_also_admin_elsewhere(
    app, db_session, make_org
):
    """A user who is an org_admin of both a deactivated organisation and a
    separate, active one is gated on MFA either way -- org_ids_for applies
    no active-state filtering at all now, so neither organisation is ever
    excluded from the lookup in the first place."""
    from app.models.org_role import OrgRole

    home_org = make_org("mfa-gate-deactivated-plus-home")
    deactivated_org = make_org("mfa-gate-deactivated-plus-deactivated")
    deactivated_org.is_active = False
    active_org = make_org("mfa-gate-deactivated-plus-active")
    db_session.commit()
    user = _make_user_with_org_role(db_session, home_org, role_org=deactivated_org)
    OrgRole.set_role(active_org.id, user.id, "org_admin", granted_by_id=user.id)
    db_session.commit()

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": user.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" in resp.headers.get("Location", "")


# ---------------------------------------------------------------------------
# /api/auth/login: the same MFA gate, applied to the JSON API endpoint
# (hot-fix for a complete MFA bypass -- this endpoint used to call
# session_registry.login_and_register unconditionally after a correct
# password, with no check at all for whether the user has MFA enabled).
# ---------------------------------------------------------------------------


def _api_login(client, email, password):
    return client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )


def test_api_login_refuses_an_mfa_enrolled_administrator(app, db_session, make_org):
    secret = pyotp.random_base32()
    org = make_org("api-mfa-gate-enrolled")
    admin = _make_admin(db_session, org, mfa_enabled=True, mfa_secret=secret)

    client = app.test_client()
    resp = _api_login(client, admin.email, _PASSWORD)

    assert resp.status_code == 401, resp.get_json()
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"] == "mfa_required"

    # No real session was established: the session has no logged-in user id,
    # and an authenticated-only route still refuses the follow-up request.
    with client.session_transaction() as sess:
        assert "_user_id" not in sess
    dash = client.get("/dashboard/overview")
    assert dash.status_code in (302, 401)


def test_api_login_refuses_an_administrator_who_has_not_enrolled_mfa_either(
    app, db_session, make_org
):
    """mfa_service.required_for() treats an unenrolled administrator the same
    as an enrolled one -- MFA is required either way, and this API endpoint
    cannot complete enrolment, so it must refuse rather than ever let an
    unenrolled administrator through on a password alone."""
    org = make_org("api-mfa-gate-unenrolled")
    admin = _make_admin(db_session, org, mfa_enabled=False)

    client = app.test_client()
    resp = _api_login(client, admin.email, _PASSWORD)

    assert resp.status_code == 401, resp.get_json()
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"] == "mfa_required"

    with client.session_transaction() as sess:
        assert "_user_id" not in sess
    dash = client.get("/dashboard/overview")
    assert dash.status_code in (302, 401)


def test_api_login_still_succeeds_for_a_plain_user_no_regression(app, db_session, make_org):
    org = make_org("api-mfa-gate-plain")
    user = _make_plain_user(db_session, org)

    client = app.test_client()
    resp = _api_login(client, user.email, _PASSWORD)

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["success"] is True
    assert body["user"]["email"] == user.email

    with client.session_transaction() as sess:
        assert sess.get("_user_id") == str(user.id)
