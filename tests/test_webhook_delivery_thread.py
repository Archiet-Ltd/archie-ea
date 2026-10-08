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
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class _Receiver:
    def __init__(self, status=200):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append({"headers": dict(self.headers), "body": body})
                self.send_response(status)
                self.end_headers()
                self.wfile.write(b"ok" if status < 400 else b"boom")

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


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


def _subscribe(app, org_id, url, secret=None):
    from app import db
    from app.models.webhook import WebhookSubscription

    with app.app_context():
        sub = WebhookSubscription(
            id=str(uuid.uuid4()),
            user_id="1",
            url=url,
            events=["*"],
            secret=secret,
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
        thread = getattr(service, "_delivery_thread", None)
        return event.id, thread


def test_real_thread_delivers_one_signed_request(app, committed_orgs):
    org_a, _ = committed_orgs[0]
    receiver = _Receiver(200)
    try:
        _subscribe(app, org_a, receiver.url, secret="s3cr3t")
        _, thread = _publish(app, org_a, "application.created", {"name": "CRM"})
        assert thread is not None, "publish_event started no delivery thread"
        assert _wait_for(lambda: len(receiver.requests) >= 1), "no delivery within 5 seconds"
        thread.join(5)
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
        thread.join(5)
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
            thread.join(10)
        assert not thread.is_alive()

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
