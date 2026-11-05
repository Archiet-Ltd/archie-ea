"""What the person reads when Monelytics refuses or half-answers a cancel or resume.

Offline, like tests/test_billing_monelytics_cancel.py. The cancel answers 204 with
no body, as Monelytics' real contract does.
"""
import re

import pytest

from tests.test_billing_monelytics import (  # noqa: F401  (fixtures + helpers)
    BASE_URL, TOKEN_URL, FakeResponse, _admin_org, _org, _subscription_response,
    _token_response, _user, monelytics_env, no_monelytics,
)

SUB = "mon-sub-1"
PROVIDER_TEXT = "provider says: pq: internal Traceback boom"


class Empty204:
    status_code = 204
    content = b""
    text = ""

    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


def _fake(calls, state, *, cancel_status=None, resume_status=None, get_fails_after_change=False,
          plan_code="STARTUP", gone_after_404=False):
    def fake(method, url, **kw):
        if url == TOKEN_URL:
            return _token_response()
        path = url.replace(BASE_URL, "")
        calls.append((method, path, kw.get("headers", {}).get("X-Tenant-Id")))
        if method == "GET" and path == "/api/billing/subscriptions/ENTELIM":
            if gone_after_404 and state.get("gone"):
                return FakeResponse(404, {"error": "none"})
            if get_fails_after_change and state["changed"]:
                return FakeResponse(503, {"error": "upstream"})
            return _subscription_response(sub_id=SUB, plan_code=plan_code, period_end="2030-01-15T00:00:00Z",
                                          cancel_at_period_end=state["cancel"])
        if method == "DELETE":
            if cancel_status:
                state["gone"] = cancel_status == 404
                return FakeResponse(cancel_status, {"error": PROVIDER_TEXT})
            state["cancel"], state["changed"] = True, True
            return Empty204()
        if method == "POST" and path.endswith("/resume"):
            if resume_status:
                return FakeResponse(resume_status, {"error": PROVIDER_TEXT})
            state["cancel"], state["changed"] = False, True
            return _subscription_response(sub_id=SUB, cancel_at_period_end=False)
        raise AssertionError((method, path))
    return fake


def _patch(monkeypatch, fake):
    from app.services import monelytics_provider
    monkeypatch.setattr(monelytics_provider.requests, "request", fake)


def _state(cancel=False):
    return {"cancel": cancel, "changed": False}


RETRY = "Try again in a moment."


@pytest.mark.parametrize("which,status,must_say,must_not_say", [
    ("cancel", 400, "free plan has nothing to cancel", RETRY),
    ("cancel", 404, "No subscription was found for this organisation", RETRY),
    ("resume", 404, "No subscription was found for this organisation", RETRY),
    ("resume", 409, "plan has ended", RETRY),
    ("cancel", 502, RETRY, None),
    ("resume", 502, RETRY, None),
])
def test_each_refusal_says_what_happened_and_what_to_do(
    db_session, monelytics_env, monkeypatch, which, status, must_say, must_not_say
):
    from app.services.billing_service import BillingError, BillingService

    org = _org(db_session, f"s{which}{status}"); db_session.commit()
    kw = {"cancel_status": status} if which == "cancel" else {"resume_status": status}
    _patch(monkeypatch, _fake([], _state(cancel=(which == "resume")), **kw))
    with pytest.raises(BillingError) as exc:
        getattr(BillingService, f"{which}_subscription")(org)
    text = str(exc.value)
    assert must_say in text
    if must_not_say:
        assert must_not_say not in text
    assert "provider says" not in text and "Traceback" not in text
    if status in (400, 404, 409):
        assert "Choose a plan" in text or "free plan has nothing to cancel" in text
        assert getattr(exc.value, "choose_plan", False), "the page sends the person to the plans"


def test_the_free_plan_sentence_offers_the_plans(db_session, monelytics_env, monkeypatch):
    from app.services.billing_service import BillingError, BillingService

    org = _org(db_session, "free400"); db_session.commit()
    _patch(monkeypatch, _fake([], _state(), cancel_status=400))
    with pytest.raises(BillingError) as exc:
        BillingService.cancel_subscription(org)
    assert "Choose a plan" in str(exc.value)


@pytest.mark.parametrize("which", ["cancel", "resume"])
def test_when_the_refetch_fails_after_the_change_was_accepted_it_is_reported_as_done(
    db_session, monelytics_env, monkeypatch, which
):
    from app.services import billing_plans
    from app.services.billing_service import BillingService

    org = _org(db_session, f"rf{which}"); db_session.commit()
    state = _state(cancel=(which == "resume"))
    _patch(monkeypatch, _fake([], state, get_fails_after_change=True))
    BillingService.cancel_subscription(org) if which == "cancel" else BillingService.resume_subscription(org)
    assert state["changed"], "the provider accepted the change"
    sub = billing_plans.current_subscription(org)
    assert sub.cancel_at_period_end is (which == "cancel")


@pytest.mark.parametrize("path,word", [
    ("/admin/billing/cancel", "is cancelled"),
    ("/admin/billing/resume", "withdrawn"),
])
def test_the_page_never_says_nothing_changed_after_an_accepted_change(
    app, enrolled_admin, db_session, client, login_as, monelytics_env, monkeypatch, path, word
):
    org, admin = _admin_org(db_session, "flash" + word[:3])
    state = _state(cancel=path.endswith("resume"))
    _patch(monkeypatch, _fake([], state, get_fails_after_change=True))
    with app.app_context():
        login_as(client, admin)
        resp = client.post(path, data={}, follow_redirects=False)
        with client.session_transaction() as sess:
            flashes = [m for _c, m in sess.get("_flashes", [])]
    assert state["changed"]
    assert resp.status_code == 302
    text = " ".join(flashes)
    assert word in text
    assert "Nothing was changed" not in text and "could not" not in text
    if path.endswith("cancel"):
        assert "end date will show once the payment provider answers" in text or "until" in text


def test_community_subscription_at_monelytics_shows_no_cancel(
    app, enrolled_admin, db_session, client, login_as, monelytics_env, monkeypatch
):
    org, admin = _admin_org(db_session, "comm")
    _patch(monkeypatch, _fake([], _state(), plan_code="COMMUNITY"))
    with app.app_context():
        login_as(client, admin)
        html = client.get("/admin/billing/").get_data(as_text=True)
    assert "Cancel subscription" not in html, "a free plan is offered a cancel Monelytics refuses with 400"
    assert re.search(r'href="[^"]*#plans"', html), "the choose-a-plan action is shown instead"


def test_a_cancelled_subscription_reads_cancelled_with_its_end_date(
    app, enrolled_admin, db_session, client, login_as, monelytics_env, monkeypatch
):
    org, admin = _admin_org(db_session, "cbadge")
    _patch(monkeypatch, _fake([], _state(cancel=True)))
    with app.app_context():
        login_as(client, admin)
        html = client.get("/admin/billing/").get_data(as_text=True)
    assert 'data-testid="billing-status-badge"' in html
    assert "Cancelled, ends on 15 Jan 2030" in html


@pytest.mark.parametrize("cancelled", [False, True])
def test_with_monelytics_the_page_never_says_online_payment_is_not_open(
    app, enrolled_admin, db_session, client, login_as, monelytics_env, monkeypatch, cancelled
):
    org, admin = _admin_org(db_session, f"open{cancelled}")
    _patch(monkeypatch, _fake([], _state(cancel=cancelled)))
    with app.app_context():
        login_as(client, admin)
        html = client.get("/admin/billing/").get_data(as_text=True)
    assert "not open yet" not in html
    assert 'data-testid="billing-details-form"' in html


def test_billing_details_save_and_survive_a_reload_with_monelytics(
    app, enrolled_admin, db_session, client, login_as, monelytics_env, monkeypatch
):
    org, admin = _admin_org(db_session, "det")
    calls = []
    _patch(monkeypatch, _fake(calls, _state()))
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/billing/details", data={"billing_email": "ap@example.com", "po_number": "PO-77"})
        assert resp.status_code == 302
        html = client.get("/admin/billing/").get_data(as_text=True)
    assert 'value="ap@example.com"' in html and 'value="PO-77"' in html
    assert not [c for c in calls if c[0] in ("PUT", "PATCH") or "billing-details" in c[1]]


def test_the_billing_details_are_this_organisations_own(
    app, enrolled_admin, db_session, client, login_as, monelytics_env, monkeypatch
):
    org_a, admin_a = _admin_org(db_session, "deta")
    org_b, admin_b = _admin_org(db_session, "detb")
    _patch(monkeypatch, _fake([], _state()))
    with app.app_context():
        login_as(client, admin_a)
        client.post("/admin/billing/details", data={"billing_email": "a@example.com", "po_number": ""})
        html_a = client.get("/admin/billing/").get_data(as_text=True)
    assert 'value="a@example.com"' in html_a, "A's own save shows on A's page"
    with app.app_context():
        db_session.expire_all()
        login_as(client, admin_b)
        html = client.get("/admin/billing/").get_data(as_text=True)
    assert "a@example.com" not in html


def test_the_invoice_card_says_where_receipts_come_from(
    app, enrolled_admin, db_session, client, login_as, monelytics_env, monkeypatch
):
    org, admin = _admin_org(db_session, "inv")
    calls = []
    _patch(monkeypatch, _fake(calls, _state()))
    with app.app_context():
        login_as(client, admin)
        html = client.get("/admin/billing/").get_data(as_text=True)
    assert 'data-testid="invoices-note"' in html
    assert not [c for c in calls if "invoice" in c[1]]


def _details_page(app, db_session, client, login_as, monkeypatch, tag):
    org, admin = _admin_org(db_session, tag)
    calls = []
    _patch(monkeypatch, _fake(calls, _state()))
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/billing/details", data={"billing_email": "ap@example.com", "po_number": "PO-77"})
        assert resp.status_code == 302
        html = client.get("/admin/billing/").get_data(as_text=True)
    return html, calls


def test_the_billing_details_sentences_say_only_what_happens(
    app, enrolled_admin, db_session, client, login_as, monelytics_env, monkeypatch
):
    html, calls = _details_page(app, db_session, client, login_as, monkeypatch, "dsent")
    assert "Future invoices carry them" not in html
    assert "Printed on every invoice" not in html
    assert "Manage billing" not in html and "/admin/billing/portal" not in html
    assert "kept with your organisation" in html and "copy of an invoice" in html
    assert not [c for c in calls if c[0] in ("PUT", "PATCH", "POST")], "no invented provider route"


def test_the_invoice_note_claims_nothing_the_code_cannot_support(
    app, enrolled_admin, db_session, client, login_as, monelytics_env, monkeypatch
):
    html, _ = _details_page(app, db_session, client, login_as, monkeypatch, "inote")
    assert "issued by the payment provider" not in html
    assert "/contact" in html and "invoice" in html.lower()


def test_after_a_404_the_page_offers_the_plans_and_no_cancel(
    app, enrolled_admin, db_session, client, login_as, monelytics_env, monkeypatch
):
    org, admin = _admin_org(db_session, "gone")
    _patch(monkeypatch, _fake([], _state(), cancel_status=404, gone_after_404=True))
    with app.app_context():
        login_as(client, admin)
        assert "Cancel subscription" in client.get("/admin/billing/").get_data(as_text=True)
        client.post("/admin/billing/cancel", follow_redirects=False)
        html = client.get("/admin/billing/").get_data(as_text=True)
    assert "Cancel subscription" not in html
    assert 'data-testid="billing-current-plan"' in html
    current = re.search(r'data-testid="billing-current-plan">\s*([^<]+?)\s*<', html).group(1)
    assert current == "Community", current
    assert "Choose Startup" in html


def test_saving_billing_details_takes_the_settings_row_lock(
    app, enrolled_admin, db_session, monelytics_env, monkeypatch
):
    from app.modules.organization.website_crawl import confirm
    from app.services.billing_service import BillingService

    org, _admin = _admin_org(db_session, "lock")
    db_session.commit()
    taken = []
    real = confirm.lock_settings
    monkeypatch.setattr(confirm, "lock_settings", lambda o: (taken.append(o.id), real(o)))
    BillingService.update_billing_details(org, "a@example.com", "PO-1")
    assert taken == [org.id]


def test_the_cancelled_badge_is_the_library_badge(
    app, enrolled_admin, db_session, client, login_as, monelytics_env, monkeypatch
):
    org, admin = _admin_org(db_session, "cb2")
    _patch(monkeypatch, _fake([], _state(cancel=True)))
    with app.app_context():
        login_as(client, admin)
        html = client.get("/admin/billing/").get_data(as_text=True)
    badge = re.search(r'data-testid="billing-status-badge">(.*?)</div>', html, re.S).group(1)
    assert 'data-slot="badge"' in badge and "warning" not in badge, badge
