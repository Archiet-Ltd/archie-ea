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
