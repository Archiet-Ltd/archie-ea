"""WebhookService.publish_event delivers from a real background thread.

The delivery thread used to have no application context, so the first
db.session / current_app call raised and the thread died silently while the
publish route still returned 201. These tests drive the real thread against a
local HTTP receiver. They commit real rows (a thread uses its own connection and
cannot see a rolled-back test transaction) and delete them in teardown.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class _Receiver:
    def __init__(self, status=200):
        self.requests = []
        self.status = status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append({"headers": dict(self.headers), "body": body})
                self.send_response(outer.status)
                self.end_headers()
                self.wfile.write(b"ok" if outer.status < 400 else b"boom")

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(autouse=True)
def _allow_loopback_receivers(request, monkeypatch):
    """The local receivers sit on 127.0.0.1, which the outbound URL guard
    rightly refuses. Tests of the guard itself (name contains "ssrf") keep it."""
    if "ssrf" in request.node.name:
        return
    monkeypatch.setattr(
        "app.services.webhook_service.validate_outbound_url", lambda url, **kw: url, raising=False
    )


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@pytest.fixture
def committed_orgs(app, _schema):
    """Two committed organisations, removed again afterwards."""
    from sqlalchemy import bindparam, text

    from app import db
    from app.models.organization import Organization

    ids = []
    with app.app_context():
        for label in ("a", "b"):
            suffix = uuid.uuid4().hex[:10]
            org = Organization(name=f"Hook {label} {suffix}", slug=f"hook-{label}-{suffix}")
            db.session.add(org)
            db.session.commit()
            ids.append(org.id)
    created_subs = []
    yield ids, created_subs
    with app.app_context():
        for table in ("webhook_deliveries", "webhook_events", "webhook_subscriptions"):
            db.session.execute(
                text(f"DELETE FROM {table} WHERE organization_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": ids},
            )
        db.session.execute(
            text("DELETE FROM organizations WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": ids},
        )
        db.session.commit()


def _subscribe(app, org_id, url, secret=None, headers=None, is_active=True):
    from app import db
    from app.models.webhook import WebhookSubscription

    with app.app_context():
        sub = WebhookSubscription(
            id=str(uuid.uuid4()),
            user_id="1",
            url=url,
            events=["*"],
            secret=secret,
            headers=headers or {},
            is_active=is_active,
            organization_id=org_id,
        )
        db.session.add(sub)
        db.session.commit()
        return sub.id


def _publish(app, org_id, event_type, payload, retries=1):
    """publish_event as a logged-in request of org_id would, then join the thread."""
    from flask import g

    from app.services.webhook_service import WebhookService

    with app.test_request_context("/"):
        g.current_org_id = org_id
        service = WebhookService()
        service.max_retries = retries
        service.retry_delay = 0
        service.timeout = 5
        event = service.publish_event(event_type, payload, "1")
        future = getattr(service, "_delivery_future", None)
        return event.id, future


def test_real_thread_delivers_one_signed_request(app, committed_orgs):
    org_a, _ = committed_orgs[0]
    receiver = _Receiver(200)
    try:
        _subscribe(app, org_a, receiver.url, secret="s3cr3t")
        _, thread = _publish(app, org_a, "application.created", {"name": "CRM"})
        assert thread is not None, "publish_event submitted no delivery"
        assert _wait_for(lambda: len(receiver.requests) >= 1), "no delivery within 5 seconds"
        thread.result(10)
        assert len(receiver.requests) == 1

        got = receiver.requests[0]
        body = json.loads(got["body"])
        assert body["event_type"] == "application.created"
        assert body["payload"] == {"name": "CRM"}
        expected = hmac.new(
            b"s3cr3t", json.dumps(body, sort_keys=True).encode(), hashlib.sha256
        ).hexdigest()
        assert got["headers"]["X-Webhook-Signature"] == expected

        from app import db
        from app.models.webhook import WebhookDelivery

        with app.app_context():
            rows = db.session.query(WebhookDelivery).filter(
                WebhookDelivery.organization_id == org_a
            ).all()
            assert [r.status for r in rows] == ["success"]
            assert rows[0].organization_id == org_a
    finally:
        receiver.close()


def test_event_for_org_a_is_never_delivered_to_org_b(app, committed_orgs):
    org_a, org_b = committed_orgs[0]
    recv_a, recv_b = _Receiver(200), _Receiver(200)
    try:
        _subscribe(app, org_a, recv_a.url)
        _subscribe(app, org_b, recv_b.url)
        _, thread = _publish(app, org_a, "application.created", {"name": "CRM"})
        assert _wait_for(lambda: len(recv_a.requests) >= 1)
        thread.result(10)
        time.sleep(0.3)
        assert len(recv_a.requests) == 1
        assert recv_b.requests == [], "org B's subscriber received org A's event"
    finally:
        recv_a.close()
        recv_b.close()


def test_receiver_500_records_failed_delivery_and_logs(app, committed_orgs, caplog):
    org_a, _ = committed_orgs[0]
    receiver = _Receiver(500)
    try:
        _subscribe(app, org_a, receiver.url)
        with caplog.at_level("ERROR"):
            _, thread = _publish(app, org_a, "application.created", {"name": "CRM"})
            thread.result(15)
        assert thread.done()

        from app import db
        from app.models.webhook import WebhookDelivery

        with app.app_context():
            rows = db.session.query(WebhookDelivery).filter(
                WebhookDelivery.organization_id == org_a
            ).all()
            assert len(rows) == 1
            assert rows[0].status == "failed"
            assert rows[0].response_status == 500
        assert any("Failed to deliver webhook" in r.getMessage() for r in caplog.records)
    finally:
        receiver.close()


# ---------------------------------------------------------------------------
# Round 2: bounded pool, no connection held across the network call, retry,
# tenant fail-closed, URL logging, outbound guard, is_active.
# ---------------------------------------------------------------------------


def _delivery_rows(app, org_id):
    from app import db
    from app.models.webhook import WebhookDelivery

    with app.app_context():
        rows = db.session.query(WebhookDelivery).filter(
            WebhookDelivery.organization_id == org_id
        ).all()
        return [
            {"id": r.id, "status": r.status, "event_id": r.event_id, "error": r.error_message,
             "attempts": r.attempt_count, "sub": r.subscription_id}
            for r in rows
        ]


def _retry(app, org_id, event_id):
    from flask import g

    from app.services.webhook_service import WebhookService

    with app.test_request_context("/"):
        g.current_org_id = org_id
        service = WebhookService()
        service.max_retries = 1
        service.retry_delay = 0
        service.timeout = 5
        assert service.retry_event(event_id) is True
        return service._delivery_future


def _new_event(app, org_id, payload=None):
    from app import db
    from app.models.webhook import WebhookEvent

    with app.app_context():
        event = WebhookEvent(
            id=str(uuid.uuid4()), event_type="application.created", payload=payload or {},
            user_id="1", event_metadata={}, organization_id=org_id,
        )
        db.session.add(event)
        db.session.commit()
        return event.id


def test_d1_hanging_receiver_does_not_exhaust_the_connection_pool(app, committed_orgs, monkeypatch):
    """50 events to a receiver that never answers must leave the pool usable.

    Round 1 held one pooled connection per in-flight delivery thread, so with a
    production-sized pool the later events and unrelated requests starved.
    """
    from flask import g
    from sqlalchemy import text

    import sqlalchemy as sa

    from app import db
    from app.services.webhook_service import WebhookService

    # Swap the test app's NullPool engine for a production-shaped QueuePool.
    pooled_app = app
    engine = sa.create_engine(
        app.config["SQLALCHEMY_DATABASE_URI"], pool_size=5, max_overflow=10, pool_timeout=30
    )
    monkeypatch.setitem(db._app_engines[app], None, engine)
    with app.app_context():
        assert db.engine.pool.size() == 5  # a real QueuePool, not NullPool

    org_a, _ = committed_orgs[0]
    hang = socket.socket()
    hang.bind(("127.0.0.1", 0))
    hang.listen(128)  # accepts into the backlog, never answers
    url = f"http://127.0.0.1:{hang.getsockname()[1]}/hook"
    futures = []
    try:
        _subscribe(app, org_a, url, secret="s")
        for i in range(50):
            with pooled_app.test_request_context("/"):
                g.current_org_id = org_a
                service = WebhookService()
                service.max_retries = 1
                service.retry_delay = 0
                service.timeout = 20
                service.publish_event("application.created", {"n": i}, "1")
                futures.append(getattr(service, "_delivery_future", None))
        time.sleep(1.5)  # let workers reach the hanging post

        with pooled_app.app_context():
            started = time.time()
            db.session.execute(text("select 1")).scalar()
            elapsed = time.time() - started
            db.session.remove()
        assert elapsed < 1.0, f"select 1 took {elapsed:.1f}s: delivery is holding pooled connections"
    finally:
        hang.close()  # resets the hanging connections so workers finish
        for f in futures:
            if f is not None:
                f.result(60)
        engine.dispose()


def test_d2_logs_carry_subscription_id_and_host_never_the_url(app, committed_orgs, caplog):
    org_a, _ = committed_orgs[0]
    receiver = _Receiver(200)
    bad = _Receiver(500)
    try:
        ok_id = _subscribe(app, org_a, receiver.url + "/T0KENsecretOK")
        bad_id = _subscribe(app, org_a, bad.url + "/T0KENsecretBAD")
        with caplog.at_level("INFO"):  # urllib3 DEBUG logs paths; that is not ours
            _, future = _publish(app, org_a, "application.created", {"name": "CRM"})
            future.result(15)
        text_logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "T0KENsecret" not in text_logged
        assert ok_id in text_logged and bad_id in text_logged
        assert "127.0.0.1" in text_logged
    finally:
        receiver.close()
        bad.close()


def test_d3_retry_event_is_signed_carries_headers_and_selects_by_event_id(app, committed_orgs):
    org_a, _ = committed_orgs[0]
    receiver = _Receiver(500)
    try:
        _subscribe(app, org_a, receiver.url, secret="s3cr3t", headers={"X-Custom": "yes"})
        event_1, f1 = _publish(app, org_a, "application.created", {"name": "one"})
        f1.result(15)
        event_2, f2 = _publish(app, org_a, "application.created", {"name": "two"})
        f2.result(15)
        assert len(receiver.requests) == 2

        receiver.status = 200
        _retry(app, org_a, event_1).result(15)

        assert len(receiver.requests) == 3, "retry must resend only event 1's delivery"
        got = receiver.requests[2]
        body = json.loads(got["body"])
        assert body["payload"] == {"name": "one"}
        assert got["headers"]["X-Custom"] == "yes"
        expected = hmac.new(
            b"s3cr3t", json.dumps(body, sort_keys=True).encode(), hashlib.sha256
        ).hexdigest()
        assert got["headers"]["X-Webhook-Signature"] == expected

        by_event = {r["event_id"]: r["status"] for r in _delivery_rows(app, org_a)}
        assert by_event == {event_1: "success", event_2: "failed"}
    finally:
        receiver.close()


def test_d4_no_organisation_delivers_nothing_and_logs(app, committed_orgs, caplog):
    org_a, _ = committed_orgs[0]
    receiver = _Receiver(200)
    try:
        sub_id = _subscribe(app, org_a, receiver.url)
        from app.services.webhook_service import WebhookService

        with app.app_context():
            service = WebhookService()
            with caplog.at_level("ERROR"):
                service._run_delivery_in_app_context(app, "evt-1", None, [sub_id])
        time.sleep(0.3)
        assert receiver.requests == []
        assert any("no organization" in r.getMessage() for r in caplog.records)
        assert _delivery_rows(app, org_a) == []
    finally:
        receiver.close()


def test_d5_worker_body_ignores_another_organisations_subscription(app, committed_orgs):
    org_a, org_b = committed_orgs[0]
    recv_b = _Receiver(200)
    try:
        sub_b = _subscribe(app, org_b, recv_b.url)
        event_id = _new_event(app, org_a, {"a": 1})
        from app.services.webhook_service import WebhookService

        with app.app_context():
            service = WebhookService()
            service.max_retries, service.retry_delay = 1, 0
            service._run_delivery_in_app_context(app, event_id, org_a, [sub_b])
        time.sleep(0.3)
        assert recv_b.requests == []
        assert _delivery_rows(app, org_b) == [] and _delivery_rows(app, org_a) == []
    finally:
        recv_b.close()


def test_d6_retry_picks_up_stale_pending_but_not_fresh_pending(app, committed_orgs):
    org_a, _ = committed_orgs[0]
    receiver = _Receiver(200)
    try:
        sub_id = _subscribe(app, org_a, receiver.url, secret="k")
        from datetime import datetime, timedelta

        from app import db
        from app.models.webhook import WebhookDelivery

        event_id = _new_event(app, org_a, {"a": 1})
        with app.app_context():
            for age, tag in ((timedelta(minutes=30), "stale"), (timedelta(minutes=1), "fresh")):
                db.session.add(WebhookDelivery(
                    id=str(uuid.uuid4()), event_id=event_id, subscription_id=sub_id,
                    organization_id=org_a, event_type="application.created",
                    payload={"tag": tag}, status="pending", attempt_count=0,
                    created_at=datetime.utcnow() - age,
                ))
            db.session.commit()
        _retry(app, org_a, event_id).result(15)
        tags = [json.loads(r["body"])["tag"] for r in receiver.requests]
        assert tags == ["stale"]
    finally:
        receiver.close()


def test_inactive_subscription_is_skipped_at_delivery_time(app, committed_orgs):
    org_a, _ = committed_orgs[0]
    receiver = _Receiver(200)
    try:
        sub_id = _subscribe(app, org_a, receiver.url, is_active=False)
        event_id = _new_event(app, org_a)
        from app.services.webhook_service import WebhookService

        with app.app_context():
            WebhookService()._run_delivery_in_app_context(app, event_id, org_a, [sub_id])
        time.sleep(0.3)
        assert receiver.requests == []
        assert _delivery_rows(app, org_a) == []
    finally:
        receiver.close()


def test_ssrf_guard_blocks_loopback_url_without_sending(app, committed_orgs):
    org_a, _ = committed_orgs[0]
    receiver = _Receiver(200)  # real guard in force: 127.0.0.1 is not public
    try:
        _subscribe(app, org_a, receiver.url)
        _, future = _publish(app, org_a, "application.created", {"name": "CRM"})
        future.result(15)
        assert receiver.requests == []
        rows = _delivery_rows(app, org_a)
        assert len(rows) == 1 and rows[0]["status"] == "failed"
        assert "Blocked outbound URL" in rows[0]["error"]
    finally:
        receiver.close()
