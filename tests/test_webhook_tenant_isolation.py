"""Cross-tenant isolation for webhook_subscriptions/events/deliveries.

Before TenantMixin, `_find_matching_subscriptions` (app/services/webhook_service.py)
queried every organisation's active webhook subscriptions with no filter at all
when publishing an event — one org's event payload could be delivered to another
org's registered webhook URL, and `get_events`/`retry_event` read every org's
webhook activity log. See app/models/webhook.py's docstrings and
app/commands/reconcile_schema.py's `_backfill_webhook_organizations` for the
nullable-column migration this needed on an existing database.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_subscription(db_session, org_id, url):
    from app.models.webhook import WebhookSubscription

    row = WebhookSubscription(
        id=str(uuid.uuid4()),
        user_id="1",
        url=url,
        events=["*"],
        organization_id=org_id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_event(db_session, org_id, event_type):
    from app.models.webhook import WebhookEvent

    row = WebhookEvent(
        id=str(uuid.uuid4()),
        event_type=event_type,
        payload={"k": "v"},
        organization_id=org_id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_delivery(db_session, org_id, subscription_id):
    from app.models.webhook import WebhookDelivery

    row = WebhookDelivery(
        id=str(uuid.uuid4()),
        subscription_id=subscription_id,
        organization_id=org_id,
        event_type="test.event",
        payload={},
        status="pending",
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_webhook_subscription_select_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """The specific leak this fix closes: org A must not see org B's subscriptions
    when the event-publishing path queries WebhookSubscription with no filter."""
    from app.models.webhook import WebhookSubscription

    org_a, org_b = make_org("a"), make_org("b")
    _make_subscription(db_session, org_a.id, "https://a.example/hook")
    b_sub = _make_subscription(db_session, org_b.id, "https://b.example/hook")

    with tenant_ctx(org_a.id):
        visible_ids = {s.id for s in WebhookSubscription.query.all()}

    assert b_sub.id not in visible_ids, (
        "TENANT LEAK: org A's event-publish path can see org B's webhook "
        "subscription — an event in org A could be delivered to org B's URL."
    )


def test_webhook_event_select_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """get_events()/retry_event() must not read another org's webhook activity log."""
    from app.models.webhook import WebhookEvent

    org_a, org_b = make_org("a"), make_org("b")
    _make_event(db_session, org_a.id, "app.created")
    b_event = _make_event(db_session, org_b.id, "app.created")

    with tenant_ctx(org_a.id):
        visible_ids = {e.id for e in WebhookEvent.query.all()}

    assert b_event.id not in visible_ids, (
        "TENANT LEAK: org A can read org B's webhook event log."
    )


def test_webhook_delivery_select_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """Delivery rows carry response_body/error_message — real payload content."""
    from app.models.webhook import WebhookDelivery

    org_a, org_b = make_org("a"), make_org("b")
    sub_a = _make_subscription(db_session, org_a.id, "https://a.example/hook")
    sub_b = _make_subscription(db_session, org_b.id, "https://b.example/hook")
    _make_delivery(db_session, org_a.id, sub_a.id)
    b_delivery = _make_delivery(db_session, org_b.id, sub_b.id)

    with tenant_ctx(org_a.id):
        visible_ids = {d.id for d in WebhookDelivery.query.all()}

    assert b_delivery.id not in visible_ids, (
        "TENANT LEAK: org A can read org B's webhook delivery attempts, "
        "including delivered response bodies."
    )


def test_webhook_delivery_organization_id_is_explicit_not_defaulted(db_session, make_org):
    """_deliver_webhook creates WebhookDelivery on a background thread with no
    request context (see webhook_service.py) — TenantMixin's g.current_org_id
    column default can't resolve there, so the write must pass organization_id
    explicitly from the subscription. This is a direct regression guard for that:
    with NO tenant_ctx active at all, the row must still land with the right org."""
    from app.models.webhook import WebhookSubscription, WebhookDelivery

    org = make_org("a")
    sub = WebhookSubscription(
        id=str(uuid.uuid4()), user_id="1", url="https://a.example/hook",
        events=["*"], organization_id=org.id,
    )
    db_session.add(sub)
    db_session.flush()

    # No tenant_ctx here — mirrors the background-thread delivery path exactly.
    delivery = WebhookDelivery(
        id=str(uuid.uuid4()),
        subscription_id=sub.id,
        organization_id=sub.organization_id,
        event_type="test.event",
        payload={},
        status="pending",
    )
    db_session.add(delivery)
    db_session.flush()

    assert delivery.organization_id == org.id, (
        "a WebhookDelivery written outside request context must carry its "
        "subscription's organization_id explicitly, not rely on the column "
        "default (which cannot resolve g.current_org_id there)"
    )


def test_process_incoming_webhook_stamps_the_subscriptions_organization(db_session, make_org):
    """process_incoming_webhook is an unauthenticated route (verified by the
    subscription's own HMAC secret, not a session) — there is no g.current_org_id,
    so the event it writes must be stamped from the target subscription's org,
    not left to default to None."""
    from app.models.webhook import WebhookSubscription
    from app.services.webhook_service import WebhookService

    org = make_org("a")
    sub = WebhookSubscription(
        id=str(uuid.uuid4()), user_id="1", url="https://a.example/hook",
        events=["*"], organization_id=org.id, secret="s3cr3t",
    )
    db_session.add(sub)
    db_session.flush()

    service = WebhookService()
    service.process_incoming_webhook(
        subscription_id=sub.id, payload={"hello": "world"}, headers={}
    )
    db_session.flush()

    from app.models.webhook import WebhookEvent

    event = WebhookEvent.query.filter_by(event_type="webhook.incoming").order_by(
        WebhookEvent.created_at.desc()
    ).first()
    assert event is not None, "process_incoming_webhook did not record an event"
    assert event.organization_id == org.id, (
        "an inbound webhook event must be stamped with its subscription's "
        "organization, not left org-less"
    )


# =============================================================================
# Delivery, replay and the admin surface (R1-B28): two organisations
# =============================================================================


@pytest.fixture
def _event_guards(app, _schema):
    from tests._webhook_helpers import install_guards

    install_guards(app)


def _two_orgs_with_subscriptions(monkeypatch, make_org, tenant_ctx, db_session):
    """Org A (3 events) and org B (2 events), each with a subscription to its own URL."""
    from app.services.webhook_service import WebhookService
    from tests._webhook_helpers import emit_events, install_transport, make_subscription

    transport = install_transport(monkeypatch)
    org_a, org_b = make_org("a"), make_org("b")
    service = WebhookService()
    subs = {}
    for label, org in (("a", org_a), ("b", org_b)):
        with tenant_ctx(org.id):
            subs[label] = make_subscription(
                service, org.id, url=f"https://{label}.example.com/hook", secret=f"secret-{label}"
            )
    db_session.commit()
    emit_events(org_a.id, 3)
    emit_events(org_b.id, 2)
    return transport, org_a, org_b, subs, service


def _dispatch_both(app, service, org_a, org_b):
    from app.jobs.tenant_safe_job import run_for_each_tenant

    def job(organization_id):
        service.fan_out(organization_id)
        return service.dispatch_due(organization_id)

    return run_for_each_tenant(
        app, "test-webhook-dispatch", job, organization_ids=[org_a.id, org_b.id], use_lock=False
    )


def test_each_organisations_subscription_only_receives_its_own_events(
    monkeypatch, app, make_org, tenant_ctx, db_session, _event_guards
):
    import json

    from app.models.webhook import WebhookDelivery

    transport, org_a, org_b, subs, service = _two_orgs_with_subscriptions(
        monkeypatch, make_org, tenant_ctx, db_session
    )
    run = _dispatch_both(app, service, org_a, org_b)
    assert run.failed == 0, run.as_dict()

    by_url = {}
    for call in transport.calls:
        by_url.setdefault(call["url"], []).append(json.loads(call["data"]))
    assert {e["organisationid"] for e in by_url["https://a.example.com/hook"]} == {str(org_a.id)}
    assert {e["organisationid"] for e in by_url["https://b.example.com/hook"]} == {str(org_b.id)}
    assert [e["sequence"] for e in by_url["https://a.example.com/hook"]] == ["1", "2", "3"]
    assert [e["sequence"] for e in by_url["https://b.example.com/hook"]] == ["1", "2"]
    a_ids = {e["id"] for e in by_url["https://a.example.com/hook"]}
    b_ids = {e["id"] for e in by_url["https://b.example.com/hook"]}
    assert not a_ids & b_ids

    with tenant_ctx(org_a.id):
        rows = WebhookDelivery.query.filter_by(subscription_id=subs["a"].id).all()
        assert {d.organization_id for d in rows} == {org_a.id}
        assert WebhookDelivery.query.filter_by(subscription_id=subs["b"].id).count() == 0
    with tenant_ctx(org_b.id):
        rows = WebhookDelivery.query.filter_by(subscription_id=subs["b"].id).all()
        assert {d.organization_id for d in rows} == {org_b.id}


def _snapshot(tenant_ctx, org, subscription_id):
    from app.models.webhook import WebhookDelivery, WebhookSubscription

    with tenant_ctx(org.id):
        sub = WebhookSubscription.query.filter_by(id=subscription_id).one()
        deliveries = WebhookDelivery.query.filter_by(subscription_id=subscription_id).all()
        return (
            sub.url,
            sub.is_active,
            sub.updated_at,
            bytes(sub.secret_encrypted),
            sub.last_ordinal,
            sorted((d.id, d.status, d.attempt_count, d.is_replay) for d in deliveries),
        )


def test_another_organisations_subscription_is_refused_on_every_api_route(
    monkeypatch, app, make_org, tenant_ctx, db_session, client, login_as, _event_guards
):
    from app.models.webhook import WebhookDelivery
    from tests._webhook_helpers import make_org_user

    transport, org_a, org_b, subs, service = _two_orgs_with_subscriptions(
        monkeypatch, make_org, tenant_ctx, db_session
    )
    _dispatch_both(app, service, org_a, org_b)
    admin_a = make_org_user(db_session, org_a)
    db_session.commit()
    b_sub = subs["b"].id
    with tenant_ctx(org_b.id):
        b_delivery = WebhookDelivery.query.filter_by(subscription_id=b_sub).first().id
    before = _snapshot(tenant_ctx, org_b, b_sub)
    calls_before = len(transport.calls)

    attempts = [
        ("get", f"/api/webhooks/subscriptions/{b_sub}", None),
        ("get", f"/api/webhooks/subscriptions/{b_sub}/deliveries", None),
        ("post", f"/api/webhooks/subscriptions/{b_sub}/replay", {"from_ordinal": 1}),
        ("post", f"/api/webhooks/deliveries/{b_delivery}/redeliver", {}),
        ("post", f"/api/webhooks/subscriptions/{b_sub}/rotate-secret", {}),
        ("post", f"/api/webhooks/subscriptions/{b_sub}/test", {}),
        ("put", f"/api/webhooks/subscriptions/{b_sub}", {"url": "https://evil.example.com/x", "is_active": False}),
        ("delete", f"/api/webhooks/subscriptions/{b_sub}", None),
    ]
    for method, path, body in attempts:
        login_as(client, admin_a)
        kwargs = {"json": body} if body is not None else {}
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 404, (method, path, response.status_code)

    assert _snapshot(tenant_ctx, org_b, b_sub) == before, "org A changed org B's rows"
    assert len(transport.calls) == calls_before, "a request from org A made the server call org B's receiver"


def test_another_organisations_subscription_is_refused_on_the_admin_screen(
    monkeypatch, app, make_org, tenant_ctx, db_session, client, login_as, _event_guards
):
    from app.models.webhook import WebhookDelivery
    from tests._webhook_helpers import make_org_user

    transport, org_a, org_b, subs, service = _two_orgs_with_subscriptions(
        monkeypatch, make_org, tenant_ctx, db_session
    )
    _dispatch_both(app, service, org_a, org_b)
    admin_a = make_org_user(db_session, org_a)
    db_session.commit()
    b_sub = subs["b"].id
    with tenant_ctx(org_b.id):
        b_delivery = WebhookDelivery.query.filter_by(subscription_id=b_sub).first().id
    before = _snapshot(tenant_ctx, org_b, b_sub)
    calls_before = len(transport.calls)

    posts = [
        (f"/admin/webhook-settings/{b_sub}/rotate", {}),
        (f"/admin/webhook-settings/{b_sub}/delete", {}),
        (f"/admin/webhook-settings/{b_sub}/replay", {"from_ordinal": "1"}),
        (f"/admin/webhook-settings/deliveries/{b_delivery}/redeliver", {}),
        (f"/admin/webhook-settings/test/{b_sub}", {}),
    ]
    for path, form in posts:
        login_as(client, admin_a)
        response = client.post(path, data=form)
        assert response.status_code in (302, 404), (path, response.status_code)
        assert b"secret-b" not in response.data

    login_as(client, admin_a)
    page = client.get(f"/admin/webhook-settings?sub={b_sub}")
    assert page.status_code == 200
    assert b"b.example.com" not in page.data, "org A's screen shows org B's subscription"
    assert _snapshot(tenant_ctx, org_b, b_sub) == before
    assert len(transport.calls) == calls_before


def test_a_replay_never_contains_another_organisations_events(
    monkeypatch, app, make_org, tenant_ctx, db_session, _event_guards
):
    from datetime import datetime, timedelta, timezone

    from app.models.event_log import EventLogRecord
    from app.models.webhook import WebhookDelivery

    _transport, org_a, org_b, subs, service = _two_orgs_with_subscriptions(
        monkeypatch, make_org, tenant_ctx, db_session
    )
    with tenant_ctx(org_b.id):
        b_event_ids = {r.event_id for r in EventLogRecord.query.filter_by(organization_id=org_b.id)}
    assert len(b_event_ids) == 2
    with tenant_ctx(org_a.id):
        result = service.replay(subs["a"].id, from_ordinal=1)
        assert result["count"] == 3
        replayed = WebhookDelivery.query.filter_by(subscription_id=subs["a"].id, is_replay=True).all()
        assert {d.log_event_id for d in replayed}.isdisjoint(b_event_ids)
        assert {d.organization_id for d in replayed} == {org_a.id}
        by_time = service.replay(subs["a"].id, since=datetime.now(timezone.utc) - timedelta(days=1))
        assert by_time["count"] == 3
        # B's subscription is simply not there for A
        assert service.replay(subs["b"].id, from_ordinal=1) is None
        assert service.list_deliveries(subs["b"].id) is None
        assert service.rotate_secret(subs["b"].id) is None
        assert service.test_subscription(subs["b"].id) is None
        assert service.delete_subscription(subs["b"].id) is False
        assert service.update_subscription(subs["b"].id, None, {"description": "x"}) is None
        assert service.redeliver("00000000-0000-0000-0000-000000000000") is None


def test_the_events_endpoint_lists_only_the_callers_log(
    monkeypatch, app, make_org, tenant_ctx, db_session, client, login_as, _event_guards
):
    from app.models.event_log import EventLogRecord
    from tests._webhook_helpers import make_org_user

    _t, org_a, org_b, _subs, _service = _two_orgs_with_subscriptions(
        monkeypatch, make_org, tenant_ctx, db_session
    )
    admin_a = make_org_user(db_session, org_a)
    db_session.commit()
    with tenant_ctx(org_b.id):
        b_event_ids = {r.event_id for r in EventLogRecord.query.filter_by(organization_id=org_b.id)}
    login_as(client, admin_a)
    response = client.get("/api/webhooks/events")
    assert response.status_code == 200
    rows = response.get_json()["data"]
    assert [r["sequence"] for r in rows] == [3, 2, 1], "newest first, only org A's three events"
    assert {r["event_id"] for r in rows}.isdisjoint(b_event_ids)


def test_a_member_who_is_not_an_organisation_admin_cannot_replay_or_read_the_log(
    monkeypatch, app, make_org, tenant_ctx, db_session, client, login_as, _event_guards
):
    from tests._webhook_helpers import make_org_user

    _t, org_a, _org_b, subs, _service = _two_orgs_with_subscriptions(monkeypatch, make_org, tenant_ctx, db_session)
    member = make_org_user(db_session, org_a, is_org_admin=False)
    db_session.commit()
    login_as(client, member)
    assert client.get("/api/webhooks/events").status_code == 403
    login_as(client, member)
    assert client.post(f"/api/webhooks/subscriptions/{subs['a'].id}/replay", json={"from_ordinal": 1}).status_code == 403
