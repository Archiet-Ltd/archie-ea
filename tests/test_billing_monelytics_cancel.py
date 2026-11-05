"""Cancel and resume through Monelytics: method, path, tenant fence, retry, errors.

Offline like tests/test_billing_monelytics.py: the provider's ``requests`` is
replaced, nothing reaches a host. Response shapes follow Monelytics' tenant
billing routes (DELETE /api/billing/subscriptions/:id cancels at period end,
POST .../:id/resume withdraws it).
"""
import pytest

from tests.test_billing_monelytics import (  # noqa: F401  (fixtures + helpers)
    BASE_URL, TOKEN_URL, FakeResponse, _admin_org, _org, _subscription_response,
    _token_response, _user, monelytics_env, no_monelytics,
)

SUB = "mon-sub-1"


def _router(calls, *, state, token_seq=None, fail=None):
    """A fake ``requests.request``. *state* holds cancelAtPeriodEnd."""
    tokens = iter(token_seq or ["token-1"] * 10)

    def fake(method, url, **kwargs):
        if url == TOKEN_URL:
            return _token_response(token=next(tokens))
        headers = kwargs.get("headers", {})
        calls.append((method, url.replace(BASE_URL, ""), headers.get("X-Tenant-Id"), headers.get("Authorization")))
        path = url.replace(BASE_URL, "")
        if fail and fail(method, path, headers):
            return fail(method, path, headers)
        if method == "GET" and path == "/api/billing/subscriptions/ENTELIM":
            return _subscription_response(sub_id=SUB, cancel_at_period_end=state["cancel"])
        if method == "DELETE" and path == f"/api/billing/subscriptions/{SUB}":
            state["cancel"] = True
            return _subscription_response(sub_id=SUB, cancel_at_period_end=True)
        if method == "POST" and path == f"/api/billing/subscriptions/{SUB}/resume":
            state["cancel"] = False
            return _subscription_response(sub_id=SUB, cancel_at_period_end=False)
        raise AssertionError(f"unexpected call {method} {path}")

    return fake


def _patch(monkeypatch, fake):
    from app.services import monelytics_provider
    monkeypatch.setattr(monelytics_provider.requests, "request", fake)


def test_cancel_sends_delete_with_the_tenant_header(db_session, monelytics_env, monkeypatch):
    from app.services import monelytics_provider as mp
    org = _org(db_session, "cancel"); db_session.commit()
    calls = []
    _patch(monkeypatch, _router(calls, state={"cancel": False}))
    mp.cancel_subscription(org)
    tenant = f"entelim-org-{org.id}"
    assert ("DELETE", f"/api/billing/subscriptions/{SUB}", tenant, "Bearer token-1") in calls
    assert all(c[2] == tenant for c in calls)


def test_resume_sends_post_resume_with_the_tenant_header(db_session, monelytics_env, monkeypatch):
    from app.services import monelytics_provider as mp
    org = _org(db_session, "resume"); db_session.commit()
    calls = []
    _patch(monkeypatch, _router(calls, state={"cancel": True}))
    mp.resume_subscription(org)
    tenant = f"entelim-org-{org.id}"
    assert ("POST", f"/api/billing/subscriptions/{SUB}/resume", tenant, "Bearer token-1") in calls
    assert all(c[2] == tenant for c in calls)


def test_a_401_refreshes_the_token_and_retries_once(db_session, monelytics_env, monkeypatch):
    from app.services import monelytics_provider as mp
    org = _org(db_session, "retry"); db_session.commit()
    calls = []
    def fail(method, path, headers):
        if method == "DELETE" and headers.get("Authorization") == "Bearer stale":
            return FakeResponse(401, {"error": "expired"})
        return None

    _patch(monkeypatch, _router(calls, state={"cancel": False}, token_seq=["stale", "fresh"], fail=fail))
    mp.cancel_subscription(org)
    deletes = [c for c in calls if c[0] == "DELETE"]
    assert [c[3] for c in deletes] == ["Bearer stale", "Bearer fresh"]


@pytest.mark.parametrize("status", [500, 503])
@pytest.mark.parametrize("action", ["cancel_subscription", "resume_subscription"])
def test_a_refusal_gives_a_plain_sentence(db_session, monelytics_env, monkeypatch, action, status):
    from app.services import monelytics_provider as mp
    org = _org(db_session, "fail"); db_session.commit()
    method = "DELETE" if action == "cancel_subscription" else "POST"
    fail = lambda m, p, h: FakeResponse(status, {"error": "Traceback (most recent call last): boom"}) if m == method else None
    _patch(monkeypatch, _router([], state={"cancel": True}, fail=fail))
    with pytest.raises(mp.MonelyticsError) as exc:
        getattr(mp, action)(org)
    text = str(exc.value)
    assert "Traceback" not in text and "boom" not in text
    assert text.endswith("Try again in a moment.")


def test_no_subscription_at_monelytics_is_a_plain_sentence(db_session, monelytics_env, monkeypatch):
    from app.services import monelytics_provider as mp
    org = _org(db_session, "none"); db_session.commit()
    _patch(monkeypatch, lambda m, u, **k: _token_response() if u == TOKEN_URL else FakeResponse(404, {"error": "none"}))
    with pytest.raises(mp.MonelyticsError) as exc:
        mp.cancel_subscription(org)
    assert str(exc.value) == mp.NO_SUBSCRIPTION


@pytest.mark.parametrize("path", ["/admin/billing/cancel", "/admin/billing/resume"])
def test_a_non_administrator_gets_403_and_nothing_is_sent(
    app, db_session, client, login_as, monelytics_env, monkeypatch, path
):
    org = _org(db_session, "member")
    member = _user(db_session, org, admin=False)
    db_session.commit()
    calls = []
    _patch(monkeypatch, _router(calls, state={"cancel": False}))
    with app.app_context():
        login_as(client, member)
        resp = client.post(path, data={})
    assert resp.status_code == 403
    assert calls == []


@pytest.mark.parametrize("path", ["/admin/billing/cancel", "/admin/billing/resume"])
def test_another_organisations_subscription_id_is_never_used(
    app, enrolled_admin, db_session, client, login_as, monelytics_env, monkeypatch, path
):
    """Organisation B's enrolled administrator posts organisation A's ids. The
    calls that follow are B's own: B's tenant on every one, A's id on none."""
    org_a, _admin_a = _admin_org(db_session, "orga")
    org_b, admin_b = _admin_org(db_session, "orgb")
    assert admin_b.mfa_enabled, "the administrator must be enrolled, or the route redirects before any call"
    calls = []
    _patch(monkeypatch, _router(calls, state={"cancel": path.endswith("resume")}))
    with app.app_context():
        login_as(client, admin_b)
        resp = client.post(path, data={"subscription_id": "mon-sub-of-A", "id": "mon-sub-of-A",
                                       "tenant_id": f"entelim-org-{org_a.id}"})
    assert "mfa-enrol" not in (resp.headers.get("Location") or ""), "redirected to enrolment: no call was tested"
    tenant_a, tenant_b = f"entelim-org-{org_a.id}", f"entelim-org-{org_b.id}"
    assert calls, "no call was made, so the fence was not tested"
    assert any(c[0] in ("DELETE", "POST") and c[1].startswith(f"/api/billing/subscriptions/{SUB}") for c in calls)
    assert all(c[2] == tenant_b for c in calls)
    assert all(c[2] != tenant_a for c in calls)
    assert all("mon-sub-of-A" not in c[1] for c in calls)


@pytest.mark.parametrize("path", ["/admin/billing/cancel", "/admin/billing/resume"])
def test_an_administrator_without_two_factor_is_sent_to_enrol_and_nothing_is_sent(
    app, db_session, client, login_as, monelytics_env, monkeypatch, path
):
    """Cancel and resume treat an unenrolled administrator the same way."""
    _org_a, admin = _admin_org(db_session, "noenrol")
    assert not admin.mfa_enabled
    calls = []
    _patch(monkeypatch, _router(calls, state={"cancel": path.endswith("resume")}))
    with app.app_context():
        login_as(client, admin)
        resp = client.post(path, data={})
    assert resp.status_code == 302 and "mfa-enrol" in resp.headers["Location"]
    assert calls == []
