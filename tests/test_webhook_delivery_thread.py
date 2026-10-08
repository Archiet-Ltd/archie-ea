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
    rightly refuses, and so does the connected-peer check. Tests of the guards
    themselves (name contains "ssrf") keep both."""
    if "ssrf" in request.node.name:
        return
    monkeypatch.setattr(
        "app.services.webhook_service.validate_outbound_url", lambda url, **kw: url, raising=False
    )
    monkeypatch.setattr(
        "app.services.webhook_service._peer_is_public", lambda address: True, raising=False
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


# ---------------------------------------------------------------------------
# Round 3 (N-1 .. N-5, D-5): redirects, deadlines and fairness, atomic claims,
# rows before queueing, the test route's audit row.
# ---------------------------------------------------------------------------

import subprocess
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path


@pytest.fixture(autouse=True)
def _fresh_dispatcher():
    """Each test gets its own dispatcher, built from that test's config."""
    import app.services.webhook_service as ws

    reset = getattr(ws, "reset_delivery_executor", None)
    if reset:
        reset()
    yield
    if reset:
        reset()


class _Scripted:
    """Threaded HTTP receiver: a status per request (the last repeats), an
    optional delay before answering, a Location header on 3xx."""

    def __init__(self, statuses=(200,), delay=0.0, location=None, body=b"ok"):
        self.requests = []
        self.statuses = [statuses] if isinstance(statuses, int) else list(statuses)
        self.delay = delay
        self.location = location
        self.body = body
        outer = self
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                with lock:
                    index = len(outer.requests)
                    outer.requests.append({"headers": dict(self.headers), "body": raw})
                status = outer.statuses[min(index, len(outer.statuses) - 1)]
                time.sleep(outer.delay)
                self.send_response(status)
                if outer.location and 300 <= status < 400:
                    self.send_header("Location", outer.location)
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                try:
                    self.wfile.write(outer.body)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class _RawServer:
    """Accepts connections and misbehaves: mode "hang" reads and never answers,
    "body" sends headers then drips the body a byte at a time, "headers" drips
    a header line a byte at a time. Counts connections open right now."""

    def __init__(self, mode):
        self.mode = mode
        self.live = 0
        self.max_live = 0
        self._lock = threading.Lock()
        self._conns = []
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        self.url = f"http://127.0.0.1:{self._sock.getsockname()[1]}/hook"
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self._conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with self._lock:
            self.live += 1
            self.max_live = max(self.max_live, self.live)
        try:
            if self.mode == "hang":
                while conn.recv(4096):
                    pass
                return
            seen = b""
            while b"\r\n\r\n" not in seen:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                seen += chunk
            if self.mode == "body":
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\n\r\n")
                while True:
                    conn.sendall(b"x")
                    time.sleep(0.1)
            else:
                conn.sendall(b"HTTP/1.1 200 OK\r\n")
                while True:
                    conn.sendall(b"X")
                    time.sleep(0.1)
        except OSError:
            pass
        finally:
            with self._lock:
                self.live -= 1

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass
        for conn in self._conns:
            try:
                conn.close()
            except OSError:
                pass


def _service_in_request(app, org_id, attrs):
    from flask import g

    from app.services.webhook_service import WebhookService

    ctx = app.test_request_context("/")
    ctx.push()
    g.current_org_id = org_id
    service = WebhookService()
    service.max_retries = attrs.pop("max_retries", 1)
    service.retry_delay = attrs.pop("retry_delay", 0)
    service.timeout = attrs.pop("timeout", 5)
    for key, value in attrs.items():
        setattr(service, key, value)
    return ctx, service


def _publish_with(app, org_id, payload, **attrs):
    """publish_event as a request of org_id would, with service attributes set;
    returns (event_id, future)."""
    ctx, service = _service_in_request(app, org_id, attrs)
    try:
        event = service.publish_event("application.created", payload, "1")
        return event.id, getattr(service, "_delivery_future", None)
    finally:
        ctx.pop()


def _retry_with(app, org_id, event_id, **attrs):
    ctx, service = _service_in_request(app, org_id, attrs)
    try:
        assert service.retry_event(event_id) is True
        return service._delivery_future
    finally:
        ctx.pop()


def _row(app, org_id, event_id):
    from app import db
    from app.models.webhook import WebhookDelivery

    with app.app_context():
        rows = db.session.query(WebhookDelivery).filter(
            WebhookDelivery.organization_id == org_id, WebhookDelivery.event_id == event_id
        ).all()
        out = [
            {"status": r.status, "response_status": r.response_status,
             "response_body": r.response_body, "error": r.error_message,
             "attempts": r.attempt_count}
            for r in rows
        ]
        db.session.remove()
        return out


# ---- N-1: a redirect is never followed -------------------------------------


@pytest.mark.parametrize("code", [302, 307])
def test_n1_redirect_to_internal_address_is_not_followed(app, committed_orgs, code):
    org_a, _ = committed_orgs[0]
    internal = _Scripted(200, body=b"INTERNAL-SECRET-METADATA")
    public = _Scripted(code, location=internal.url + "/latest/meta-data/")
    try:
        _subscribe(app, org_a, public.url, secret="s")
        event_id, future = _publish_with(app, org_a, {"n": 1})
        future.result(15)
        assert len(public.requests) == 1
        assert internal.requests == [], "the redirect was followed to the internal service"
        (row,) = _row(app, org_a, event_id)
        assert row["status"] == "failed"
        assert row["response_status"] == code
        assert row["response_body"] is None
        assert "INTERNAL-SECRET" not in (row["error"] or "")
    finally:
        public.close()
        internal.close()


def test_n1_test_route_does_not_probe_internal_addresses_through_a_redirect(app, committed_orgs):
    from flask import g

    from app.services.webhook_service import WebhookService

    org_a, _ = committed_orgs[0]
    internal = _Scripted(200)
    public = _Scripted(307, location=internal.url)
    try:
        sub_id = _subscribe(app, org_a, public.url)
        with app.test_request_context("/"):
            g.current_org_id = org_a
            result = WebhookService().test_subscription(sub_id, "1")
        assert result["success"] is False
        assert internal.requests == []
    finally:
        public.close()
        internal.close()


# ---- N-2: deadline, size cap, per-organisation fairness, rate limit --------


@pytest.mark.parametrize("mode", ["body", "headers"])
def test_n2_slow_drip_receiver_is_cut_off_at_the_deadline(app, committed_orgs, mode):
    org_a, _ = committed_orgs[0]
    drip = _RawServer(mode)
    try:
        _subscribe(app, org_a, drip.url)
        started = time.time()
        event_id, future = _publish_with(app, org_a, {"n": 1}, attempt_deadline=1.0, timeout=5)
        future.result(10)  # without a wall-clock cap this never finishes
        elapsed = time.time() - started
        assert elapsed < 2.5, f"attempt ran {elapsed:.1f}s against a 1s deadline"
        (row,) = _row(app, org_a, event_id)
        assert row["status"] == "failed" and "deadline" in row["error"]
    finally:
        drip.close()


def test_n2_response_is_never_read_past_64kb(app, committed_orgs, monkeypatch):
    import requests

    org_a, _ = committed_orgs[0]
    big = _Scripted(200, body=b"A" * (3 * 1024 * 1024))
    read = {"bytes": 0}
    real = requests.models.Response.iter_content

    def counting(self, *args, **kwargs):
        for chunk in real(self, *args, **kwargs):
            read["bytes"] += len(chunk)
            yield chunk

    monkeypatch.setattr(requests.models.Response, "iter_content", counting)
    try:
        _subscribe(app, org_a, big.url)
        event_id, future = _publish_with(app, org_a, {"n": 1})
        future.result(15)
        assert 0 < read["bytes"] <= 64 * 1024 + 8192
        (row,) = _row(app, org_a, event_id)
        assert row["status"] == "success" and len(row["response_body"]) <= 1000
    finally:
        big.close()


@pytest.mark.parametrize("mode", ["hang", "body"])
def test_n2_one_organisation_cannot_delay_another(app, committed_orgs, mode):
    org_a, org_b = committed_orgs[0]
    bad = _RawServer(mode)
    fast = _Scripted(200)
    futures = []
    try:
        _subscribe(app, org_a, bad.url)
        _subscribe(app, org_b, fast.url)
        for i in range(30):
            futures.append(_publish_with(app, org_a, {"n": i}, attempt_deadline=1.0)[1])
        started = time.time()
        _, future_b = _publish_with(app, org_b, {"b": 1}, attempt_deadline=1.0)
        future_b.result(1.0 + 2.5)
        assert time.time() - started < 1.0 + 2.5
        assert len(fast.requests) == 1
        assert bad.max_live <= 2, f"org A had {bad.max_live} deliveries in flight"
    finally:
        bad.close()
        for f in futures:
            if f is not None:
                f.cancel()
        fast.close()


def test_n2_workers_take_organisations_round_robin():
    from app.services.webhook_service import _FairDispatcher

    order = []
    gate = threading.Event()
    dispatcher = _FairDispatcher(workers=1, per_org_limit=1, queue_max=100)
    try:
        blocker = dispatcher.submit_for_org("x", gate.wait, 5)
        time.sleep(0.2)  # the one worker is now busy
        futures = [dispatcher.submit_for_org("A", order.append, f"A{i}") for i in range(4)]
        futures += [dispatcher.submit_for_org("B", order.append, f"B{i}") for i in range(2)]
        gate.set()
        blocker.result(5)
        for f in futures:
            f.result(5)
    finally:
        dispatcher.shutdown(wait=False)
    assert order[:4] == ["A0", "B0", "A1", "B1"], order


def test_n2_publish_endpoint_is_rate_limited_per_organisation(
    app, db_session, make_org, login_as, client, monkeypatch
):
    """The app-wide limiter already holds each user to 30 writes a minute; the
    organisation as a whole is held to its own cap, so several users cannot
    multiply it."""
    from app.models.user import Role, User

    PUBLISH_RATE_LIMIT = 6
    monkeypatch.setitem(app.config, "RATE_LIMITING_ENABLED", True)
    monkeypatch.setitem(app.config, "WEBHOOK_PUBLISH_RATE_LIMIT", PUBLISH_RATE_LIMIT)
    org = make_org("ratelimit")
    role = Role.query.filter_by(name="Administrator").first()
    users = []
    for i in range(3):  # the free plan admits three people
        user = User(
            email=f"hook-rl-{uuid.uuid4().hex[:8]}@example.com", first_name="T", last_name="U",
            organization_id=org.id, role=role, is_org_admin=True, confirmed=True,
        )
        db_session.add(user)
        users.append(user)
    db_session.flush()
    statuses = []
    for n in range(24):
        user = users[n % 3]  # 8 each: under every user's own limit
        login_as(client, user)
        resp = client.post(
            "/api/webhooks/public/events",
            json={"event_type": "application.created", "payload": {"n": n}},
            headers={"Accept": "application/json"},
        )
        statuses.append(resp.status_code)
    # a token bucket: the first LIMIT publishes pass, then it throttles
    assert 429 in statuses, "24 publishes by three users of one organisation were all accepted"
    first_refusal = statuses.index(429)
    assert PUBLISH_RATE_LIMIT <= first_refusal <= PUBLISH_RATE_LIMIT + 10, first_refusal
    assert set(statuses[:first_refusal]) == {201}


# ---- N-3: a delivery is claimed atomically before every send ---------------


def test_n3_double_click_retry_sends_exactly_one_extra_delivery(app, committed_orgs):
    org_a, _ = committed_orgs[0]
    receiver = _Scripted(statuses=(500, 200), delay=0.5)
    try:
        _subscribe(app, org_a, receiver.url)
        event_id, future = _publish_with(app, org_a, {"n": 1})
        future.result(15)
        assert len(receiver.requests) == 1
        first = _retry_with(app, org_a, event_id)
        second = _retry_with(app, org_a, event_id)
        first.result(15)
        second.result(15)
        time.sleep(0.3)
        assert len(receiver.requests) == 2, f"{len(receiver.requests)} sends for one failed delivery"
        assert [r["status"] for r in _row(app, org_a, event_id)] == ["success"]
    finally:
        receiver.close()


def test_n3_retry_during_the_workers_backoff_sends_no_duplicate(app, committed_orgs):
    org_a, _ = committed_orgs[0]
    receiver = _Scripted(statuses=(500, 200))
    try:
        _subscribe(app, org_a, receiver.url)
        event_id, future = _publish_with(app, org_a, {"n": 1}, max_retries=2, retry_delay=2.0)
        assert _wait_for(lambda: len(receiver.requests) == 1)
        # The worker is now in its backoff; its row must not look retryable.
        assert _wait_for(lambda: [r["status"] for r in _row(app, org_a, event_id)] == ["retrying"])
        retry = _retry_with(app, org_a, event_id)
        retry.result(10)
        assert len(receiver.requests) == 1, "retry sent while the worker was between attempts"
        assert _wait_for(lambda: [r["status"] for r in _row(app, org_a, event_id)] == ["success"], 15)
        assert len(receiver.requests) == 2
    finally:
        receiver.close()


def test_n3_row_is_failed_only_after_the_last_attempt(app, committed_orgs):
    org_a, _ = committed_orgs[0]
    receiver = _Scripted(statuses=(500,))
    seen = []
    try:
        _subscribe(app, org_a, receiver.url)
        event_id, future = _publish_with(app, org_a, {"n": 1}, max_retries=3, retry_delay=0.4)
        deadline = time.time() + 20
        while time.time() < deadline:
            rows = _row(app, org_a, event_id)
            seen.extend((r["status"], r["attempts"]) for r in rows)
            if rows and rows[0]["status"] == "failed":
                break
            time.sleep(0.05)
        # "failed" may only appear together with the last (third) attempt
        assert [s for s in seen if s[0] == "failed" and s[1] < 3] == [], seen
        assert ("retrying", 1) in seen or ("retrying", 2) in seen, seen
        assert [r["status"] for r in _row(app, org_a, event_id)] == ["failed"]
        assert _row(app, org_a, event_id)[0]["attempts"] == 3
    finally:
        receiver.close()


# ---- N-4: a row before the queue, a bounded queue, exit never waits --------


def test_n4_every_published_event_has_a_row_even_while_queued(app, committed_orgs):
    org_a, _ = committed_orgs[0]
    hang = _RawServer("hang")
    futures = []
    try:
        _subscribe(app, org_a, hang.url)
        event_ids = []
        for i in range(40):
            event_id, future = _publish_with(app, org_a, {"n": i}, attempt_deadline=2.0)
            event_ids.append(event_id)
            futures.append(future)
        missing = [e for e in event_ids if not _row(app, org_a, e)]
        assert missing == [], f"{len(missing)} published events have no delivery row"
    finally:
        hang.close()
        for f in futures:
            if f is not None:
                f.result(60)


def test_n4_queue_is_bounded_and_stale_pending_rows_are_recovered(app, committed_orgs, monkeypatch):
    from sqlalchemy import text

    from app import db

    org_a, _ = committed_orgs[0]
    monkeypatch.setitem(app.config, "WEBHOOK_DELIVERY_QUEUE_MAX", 3)
    hang = _RawServer("hang")
    good = _Scripted(200)
    futures = []
    try:
        sub_id = _subscribe(app, org_a, hang.url)
        for i in range(12):
            event_id, future = _publish_with(app, org_a, {"n": i}, attempt_deadline=2.0)
            futures.append((i, event_id, future))
        overflow = [(i, e) for i, e, f in futures if f is None]
        assert overflow, "a queue bounded at 3 accepted 12 events behind two busy workers"
        hang.close()
        for i, e, f in futures:
            if f is not None:
                f.result(30)
        i, event_id = overflow[0]
        assert [r["status"] for r in _row(app, org_a, event_id)] == ["pending"]
        with app.app_context():
            db.session.execute(
                text("UPDATE webhook_subscriptions SET url = :u WHERE id = :i"),
                {"u": good.url, "i": sub_id},
            )
            db.session.execute(
                text("UPDATE webhook_deliveries SET created_at = created_at - interval '30 minutes' "
                     "WHERE event_id = :e"), {"e": event_id},
            )
            db.session.commit()
        _retry_with(app, org_a, event_id).result(15)
        sent = [json.loads(r["body"])["payload"]["n"] for r in good.requests]
        assert i in sent
        assert [r["status"] for r in _row(app, org_a, event_id)] == ["success"]
    finally:
        hang.close()
        good.close()


def test_n4_process_exit_does_not_wait_on_queued_or_running_deliveries():
    repo = Path(__file__).resolve().parent.parent
    script = (
        "import time\n"
        "from app.services.webhook_service import _get_delivery_executor\n"
        "ex = _get_delivery_executor(2)\n"
        "for _ in range(6):\n"
        "    ex.submit(time.sleep, 120)\n"
        "print('queued', flush=True)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script], cwd=repo, stdout=subprocess.PIPE, text=True
    )
    try:
        lines = []
        for line in proc.stdout:  # the app prints a banner on import
            lines.append(line.strip())
            if line.strip() == "queued":
                break
        assert "queued" in lines, lines
        started = time.time()
        proc.wait(timeout=10)  # a pool that drains its queue at exit takes minutes
        assert time.time() - started < 10
    finally:
        if proc.poll() is None:
            proc.kill()


def test_n4_shutdown_cancels_queued_work_without_waiting():
    from app.services.webhook_service import _FairDispatcher

    gate = threading.Event()
    dispatcher = _FairDispatcher(workers=1, per_org_limit=1, queue_max=10)
    running = dispatcher.submit_for_org("A", gate.wait, 30)
    time.sleep(0.2)
    queued = dispatcher.submit_for_org("A", lambda: None)
    started = time.time()
    dispatcher.shutdown(wait=False, cancel_futures=True)
    try:
        assert time.time() - started < 1.0
        assert queued.cancelled()
        assert dispatcher.submit_for_org("A", lambda: None) is None
    finally:
        gate.set()
        running.result(5)


# ---- N-5: the test route keeps its session and writes its audit row --------


def test_n5_test_route_writes_its_audit_row(app, db_session, make_org, login_as, client, monkeypatch):
    from app.models.audit_log import AuditLog
    from app.models.user import Role, User
    from app.models.webhook import WebhookSubscription

    monkeypatch.setattr(
        "app.services.webhook_service.validate_outbound_url", lambda url, **kw: url, raising=False
    )
    receiver = _Scripted(200)
    try:
        org = make_org("auditrow")
        role = Role.query.filter_by(name="Administrator").first()
        user = User(
            email=f"hook-audit-{uuid.uuid4().hex[:8]}@example.com", first_name="T", last_name="U",
            organization_id=org.id, role=role, is_org_admin=True, confirmed=True,
        )
        db_session.add(user)
        db_session.flush()
        sub = WebhookSubscription(
            id=str(uuid.uuid4()), user_id=str(user.id), url=receiver.url, events=["*"],
            organization_id=org.id, is_active=True,
        )
        db_session.add(sub)
        db_session.flush()
        login_as(client, user)
        before = AuditLog.query.filter_by(user_id=user.id).count()
        resp = client.post(f"/api/webhooks/subscriptions/{sub.id}/test")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["data"]["success"] is True
        assert len(receiver.requests) == 1
        assert AuditLog.query.filter_by(user_id=user.id).count() == before + 1
    finally:
        receiver.close()


# ---- D-5: the explicit organisation check, with no tenant scope ------------


def test_d5_deliverable_checks_the_organisation_without_tenant_scope(app, committed_orgs):
    from app import db
    from app.models.webhook import WebhookSubscription
    from app.services.webhook_service import WebhookService

    org_a, org_b = committed_orgs[0]
    sub_b = _subscribe(app, org_b, "http://example.invalid/hook")
    sub_b_inactive = _subscribe(app, org_b, "http://example.invalid/hook", is_active=False)
    with app.app_context():  # no tenant_scope, no g.current_org_id
        row = db.session.get(WebhookSubscription, sub_b)
        inactive = db.session.get(WebhookSubscription, sub_b_inactive)
        assert row is not None and row.organization_id == org_b
        assert WebhookService._deliverable(row, org_b) is True
        assert WebhookService._deliverable(row, org_a) is False
        assert WebhookService._deliverable(inactive, org_b) is False
        assert WebhookService._deliverable(None, org_b) is False
        db.session.remove()


# ===========================================================================
# Round 4: HTTPS deadline, claim tokens, periodic sweep, admin routes, rebinding
# ===========================================================================


@pytest.fixture(scope="module")
def tls_material(tmp_path_factory):
    """A throwaway certificate for 127.0.0.1 / localhost (the receiver's)."""
    import datetime as dt
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    folder = tmp_path_factory.mktemp("tls")
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


@pytest.fixture
def trust_tls(tls_material, monkeypatch):
    # Deliveries ignore the environment (trust_env off), so trust the test
    # certificate through requests' default bundle instead.
    monkeypatch.setattr("requests.adapters.DEFAULT_CA_BUNDLE_PATH", tls_material[0])
    return tls_material


class _TlsRawServer(_RawServer):
    """_RawServer behind TLS. Mode "handshake" accepts the TCP connection and
    never answers the ClientHello (the connect phase stalls)."""

    def __init__(self, mode, material):
        import ssl

        self._tls_mode = mode
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(*material)
        super().__init__("hang" if mode == "handshake" else mode)
        self.url = self.url.replace("http://", "https://")

    def _serve(self, conn):
        if self._tls_mode != "handshake":
            try:
                conn = self._ctx.wrap_socket(conn, server_side=True)
            except OSError:
                return
        super()._serve(conn)


def _wrap_scripted_in_tls(sock, material):
    import ssl

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(*material)
    return ctx.wrap_socket(sock, server_side=True)


@pytest.mark.parametrize("mode", ["body", "headers", "hang", "handshake"])
def test_r31_https_receiver_is_cut_off_at_the_deadline(app, committed_orgs, trust_tls, mode):
    org_a, _ = committed_orgs[0]
    drip = _TlsRawServer(mode, trust_tls)
    try:
        _subscribe(app, org_a, drip.url)
        started = time.time()
        event_id, future = _publish_with(app, org_a, {"n": 1}, attempt_deadline=1.0, timeout=30)
        future.result(15)  # a TLS drip was still alive after 60s before the fix
        elapsed = time.time() - started
        assert elapsed < 3.0, f"{mode}: attempt ran {elapsed:.1f}s against a 1s deadline"
        (row,) = _row(app, org_a, event_id)
        assert row["status"] == "failed" and "deadline" in row["error"], row
    finally:
        drip.close()


def test_r31_https_receiver_that_answers_still_delivers(app, committed_orgs, trust_tls):
    """The tracked TLS connection is a normal one when the receiver behaves."""
    org_a, _ = committed_orgs[0]
    secure = _Scripted(200)
    secure.server.socket = _wrap_scripted_in_tls(secure.server.socket, trust_tls)
    url = secure.url.replace("http://", "https://")
    try:
        _subscribe(app, org_a, url)
        event_id, future = _publish_with(app, org_a, {"n": 1})
        future.result(15)
        assert len(secure.requests) == 1
        (row,) = _row(app, org_a, event_id)
        assert row["status"] == "success", row
    finally:
        secure.close()


@pytest.mark.parametrize("mode", ["hang", "body"])
def test_r31_https_hanging_receiver_does_not_delay_another_organisation(
    app, committed_orgs, trust_tls, mode
):
    org_a, org_b = committed_orgs[0]
    bad = _TlsRawServer(mode, trust_tls)
    fast = _Scripted(200)
    futures = []
    try:
        _subscribe(app, org_a, bad.url)
        _subscribe(app, org_b, fast.url)
        for i in range(8):
            futures.append(_publish_with(app, org_a, {"n": i}, attempt_deadline=1.0, timeout=30)[1])
        started = time.time()
        _, future_b = _publish_with(app, org_b, {"b": 1}, attempt_deadline=1.0)
        future_b.result(1.0 + 2.5)
        assert time.time() - started < 1.0 + 2.5
        assert len(fast.requests) == 1
        assert bad.max_live <= 2, f"org A had {bad.max_live} deliveries in flight"
    finally:
        bad.close()
        for f in futures:
            if f is not None:
                f.result(30)
        fast.close()


def test_r31_connect_phase_runs_under_the_attempt_deadline(monkeypatch):
    """The connect timeout is cut to what is left of the deadline, and no
    connect is attempted once the deadline has passed."""
    import socket as socket_module

    from app.services import webhook_service as ws

    seen = []
    real_connect = socket_module.socket.connect

    def spy(self, address):
        if address[0] == "192.0.2.7":
            seen.append(self.gettimeout())
            raise OSError("unreachable")
        return real_connect(self, address)

    monkeypatch.setattr(socket_module.socket, "connect", spy)
    monkeypatch.setattr(ws, "_peer_is_public", lambda address: True)
    real = socket_module.getaddrinfo
    monkeypatch.setattr(
        socket_module, "getaddrinfo",
        lambda host, port, *a, **k: [(socket_module.AF_INET, socket_module.SOCK_STREAM, 6, "", ("192.0.2.7", port))]
        if host == "pin.example" else real(host, port, *a, **k),
    )
    with ws._AttemptGuard(1.0):
        with pytest.raises(OSError):
            ws._TrackedHTTPConnection("pin.example", 80, timeout=30)._new_conn()
    assert seen and seen[0] <= 1.0
    seen.clear()
    expired = ws._AttemptGuard(0.01)
    time.sleep(0.05)
    with expired:
        with pytest.raises(OSError):
            ws._TrackedHTTPConnection("pin.example", 80, timeout=30)._new_conn()
    assert seen == [], "a connect was attempted after the deadline had passed"


# ---- R3-2: claim tokens and the computed stale threshold -------------------


def test_r32_stale_threshold_is_computed_from_the_retry_config(app):
    from app.services.webhook_service import WebhookService

    with app.test_request_context("/"):
        service = WebhookService()
        service.max_retries = 3
        service.retry_delay = 300
        service.attempt_deadline = 10.0
        # 3 attempts x 10s + backoff 300s + 600s + 60s margin
        assert service._stale_after().total_seconds() >= 3 * 10 + 300 + 600 + 60
        service.retry_delay = 0
        assert service._stale_after().total_seconds() < 600


def test_r32_retry_while_the_first_worker_is_alive_sends_exactly_once(app, committed_orgs):
    from sqlalchemy import text

    from app import db

    org_a, _ = committed_orgs[0]
    receiver = _Scripted(statuses=(500, 200))
    try:
        _subscribe(app, org_a, receiver.url)
        # A long backoff: the live worker's row sits "retrying" for 700s, far
        # longer than the old fixed ten minutes.
        event_id, future = _publish_with(app, org_a, {"n": 1}, max_retries=2, retry_delay=700)
        assert _wait_for(lambda: len(receiver.requests) == 1)
        assert _wait_for(lambda: [r["status"] for r in _row(app, org_a, event_id)] == ["retrying"])
        with app.app_context():
            db.session.execute(
                text("UPDATE webhook_deliveries SET last_attempt_at = last_attempt_at - interval '11 minutes' "
                     "WHERE event_id = :e"), {"e": event_id},
            )
            db.session.commit()
        _retry_with(app, org_a, event_id, max_retries=2, retry_delay=700).result(10)
        time.sleep(0.3)
        assert len(receiver.requests) == 1, "a second POST went out while the first worker was alive"
    finally:
        receiver.close()


def test_r32_a_slower_worker_cannot_overwrite_a_newer_claim(app, committed_orgs):
    from sqlalchemy import text

    from app import db

    org_a, _ = committed_orgs[0]
    receiver = _Scripted(statuses=(200,), delay=1.5)
    try:
        _subscribe(app, org_a, receiver.url)
        event_id, future = _publish_with(app, org_a, {"n": 1})
        assert _wait_for(lambda: len(receiver.requests) == 1)
        # Another claimant takes the row (new token) and records its own result.
        with app.app_context():
            db.session.execute(
                text("UPDATE webhook_deliveries SET status = 'failed', error_message = 'newer claim', "
                     "attempt_count = 7, last_attempt_at = (now() at time zone 'utc') + interval '1 second' "
                     "WHERE event_id = :e"), {"e": event_id},
            )
            db.session.commit()
        future.result(15)  # the first worker now finishes its 200
        (row,) = _row(app, org_a, event_id)
        assert row["status"] == "failed" and row["error"] == "newer claim" and row["attempts"] == 7, row
    finally:
        receiver.close()


# ---- R3-3: periodic sweep and admin authorisation --------------------------


def _scheduler_job(app, monkeypatch, job_id):
    """Register the app's scheduler jobs without starting a scheduler."""
    from apscheduler.schedulers.background import BackgroundScheduler

    import app._bootstrap.extensions as ext

    monkeypatch.setattr(BackgroundScheduler, "start", lambda self, *a, **k: None)
    monkeypatch.setattr(ext, "_scheduler_belongs_in_this_process", lambda: True)
    monkeypatch.setitem(app.config, "TESTING", False)
    app.extensions.pop("ea_workflow_scheduler", None)
    try:
        ext.init_scheduler(app)
        scheduler = app.extensions["ea_workflow_scheduler"]
        return scheduler.get_job(job_id)
    finally:
        app.extensions.pop("ea_workflow_scheduler", None)
        app.config["TESTING"] = True


def test_r33_sweep_job_is_registered_on_the_existing_scheduler_every_5_minutes(app, monkeypatch):
    from datetime import timedelta

    from app.jobs.tenant_safe_job import PLATFORM_JOBS, TENANT_JOBS

    job = _scheduler_job(app, monkeypatch, "webhook_delivery_sweep")
    assert job is not None, "no webhook_delivery_sweep job on the APScheduler"
    assert job.trigger.interval == timedelta(minutes=5)
    assert "webhook_delivery_sweep" in (PLATFORM_JOBS | TENANT_JOBS)


def test_r33_the_sweep_sends_stale_pending_and_retrying_rows_but_not_fresh_or_failed(
    app, committed_orgs, monkeypatch
):
    from datetime import datetime, timedelta

    from app import db
    from app.models.webhook import WebhookDelivery

    org_a, org_b = committed_orgs[0]
    receiver = _Scripted(200)
    other = _Scripted(200)
    try:
        sub_a = _subscribe(app, org_a, receiver.url)
        sub_b = _subscribe(app, org_b, other.url)
        event_a = _new_event(app, org_a, {"a": 1})
        event_b = _new_event(app, org_b, {"b": 1})
        old = datetime.utcnow() - timedelta(minutes=30)
        rows = [
            ("stale-pending", "pending", old, None, sub_a, event_a, org_a),
            ("stale-retrying", "retrying", datetime.utcnow(), old, sub_a, event_a, org_a),
            ("fresh-pending", "pending", datetime.utcnow(), None, sub_a, event_a, org_a),
            ("failed", "failed", old, old, sub_a, event_a, org_a),
            ("other-org", "pending", old, None, sub_b, event_b, org_b),
        ]
        with app.app_context():
            for tag, status, created, last, sub, event, org in rows:
                db.session.add(WebhookDelivery(
                    id=str(uuid.uuid4()), event_id=event, subscription_id=sub,
                    organization_id=org, event_type="application.created",
                    payload={"tag": tag}, status=status, attempt_count=0,
                    created_at=created, last_attempt_at=last,
                ))
            db.session.commit()
        job = _scheduler_job(app, monkeypatch, "webhook_delivery_sweep")
        job.func()  # what the scheduler does every 5 minutes
        assert _wait_for(lambda: len(receiver.requests) >= 2 and len(other.requests) >= 1, 15)
        time.sleep(0.5)
        assert sorted(json.loads(r["body"])["tag"] for r in receiver.requests) == [
            "stale-pending", "stale-retrying"]
        assert [json.loads(r["body"])["tag"] for r in other.requests] == ["other-org"]
    finally:
        receiver.close()
        other.close()


def _hook_user(db_session, org, role_name="Administrator"):
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    user = User(
        email=f"hook-admin-{uuid.uuid4().hex[:8]}@example.com", first_name="T", last_name="U",
        organization_id=org.id, role=role, confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_r33_routes_use_the_active_org_admin_check(app, db_session, make_org, login_as, client, monkeypatch):
    from app.models.webhook import WebhookEvent
    from app.services.webhook_service import WebhookService

    retried = []
    # The delivery worker has its own connection, which this fixture's joined
    # transaction cannot share; only the authorisation is under test here.
    monkeypatch.setattr(WebhookService, "retry_event", lambda self, event_id: retried.append(event_id) or True)

    org = make_org("hookroutes")
    admin = _hook_user(db_session, org)
    admin.grant_org_admin()
    viewer = _hook_user(db_session, org, role_name="User")
    db_session.add(WebhookEvent(
        id=str(uuid.uuid4()), event_type="x.y", payload={}, user_id="1", event_metadata={},
        organization_id=org.id,
    ))
    db_session.flush()
    login_as(client, viewer)
    assert client.get("/api/webhooks/events").status_code == 403
    assert client.post("/api/webhooks/events/nope/retry").status_code == 403
    login_as(client, admin)
    resp = client.get("/api/webhooks/events")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["success"] is True
    event_id = resp.get_json()["data"][0]["id"]
    retry = client.post(f"/api/webhooks/events/{event_id}/retry")
    assert retry.status_code == 200, retry.get_data(as_text=True)
    assert retried == [event_id]


def test_r33_admin_of_the_home_org_is_not_admin_of_the_org_they_switched_into(
    app, db_session, make_org
):
    from flask import g
    from flask_login import login_user
    from werkzeug.exceptions import Forbidden

    from app.middleware.tenant_decorators import require_org_or_platform_admin

    home, other = make_org("home"), make_org("other")
    admin = _hook_user(db_session, home)
    admin.grant_org_admin()
    with app.test_request_context("/"):
        login_user(admin)
        g.current_org_id = home.id
        assert require_org_or_platform_admin(g.current_org_id) is None
        g.current_org_id = other.id
        with pytest.raises(Forbidden):
            require_org_or_platform_admin(g.current_org_id)


def test_r45_one_admin_predicate_shared_by_webhook_and_team_routes():
    import app.routes.webhook as webhook_routes
    from app.middleware.tenant_decorators import require_org_or_platform_admin
    from app.modules.admin import team_routes

    assert not hasattr(webhook_routes, "_is_webhook_admin")
    assert webhook_routes.require_org_or_platform_admin is require_org_or_platform_admin
    assert team_routes._require_org_or_platform_admin is require_org_or_platform_admin


# ---- R3-4: the connected peer is judged by the ssrf_guard rule --------------


def test_r34_ssrf_dns_rebinding_to_loopback_is_refused_at_connect(app, committed_orgs, monkeypatch):
    import socket as socket_module

    org_a, _ = committed_orgs[0]
    internal = _Scripted(200, body=b"internal secret")
    port = internal.server.server_address[1]
    real = socket_module.getaddrinfo
    calls = {"n": 0}

    def rebinding(host, *args, **kwargs):
        if host != "rebind.example":
            return real(host, *args, **kwargs)
        calls["n"] += 1
        address = "93.184.216.34" if calls["n"] == 1 else "127.0.0.1"  # TTL 0: public, then loopback
        return [(socket_module.AF_INET, socket_module.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(socket_module, "getaddrinfo", rebinding)
    try:
        _subscribe(app, org_a, f"http://rebind.example:{port}/hook")
        event_id, future = _publish_with(app, org_a, {"n": 1}, max_retries=3)
        future.result(15)
        assert calls["n"] >= 2, "the host was not resolved a second time"
        assert internal.requests == [], "the signed POST reached the loopback service"
        (row,) = _row(app, org_a, event_id)
        assert row["status"] == "failed" and row["attempts"] == 1, row
        assert "not a public address" in row["error"]
        assert "internal secret" not in (row["response_body"] or "")
    finally:
        internal.close()


# ---- L-1: a test delivery is one attempt -----------------------------------


def test_l1_test_delivery_makes_one_attempt(app, committed_orgs):
    from flask import g

    from app.services.webhook_service import WebhookService

    org_a, _ = committed_orgs[0]
    receiver = _Scripted(statuses=(500,))
    try:
        sub_id = _subscribe(app, org_a, receiver.url)
        with app.test_request_context("/"):
            g.current_org_id = org_a
            service = WebhookService()
            service.max_retries = 3
            service.retry_delay = 0
            result = service.test_subscription(sub_id, "1")
        assert result["success"] is False and result["attempts"] == 1
        assert len(receiver.requests) == 1
    finally:
        receiver.close()


# ===========================================================================
# Round 5: no sleeping workers, sweep joins, pinned resolution, no proxy
# ===========================================================================


def _make_committed_org(app):
    from app import db
    from app.models.organization import Organization

    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        org = Organization(name=f"Hook x {suffix}", slug=f"hook-x-{suffix}")
        db.session.add(org)
        db.session.commit()
        return org.id


def _drop_orgs(app, ids):
    from sqlalchemy import bindparam, text

    from app import db

    with app.app_context():
        for table in ("webhook_deliveries", "webhook_events", "webhook_subscriptions"):
            db.session.execute(
                text(f"DELETE FROM {table} WHERE organization_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)), {"ids": ids})
        db.session.execute(
            text("DELETE FROM organizations WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)), {"ids": ids})
        db.session.commit()


def test_r41_backoff_does_not_occupy_a_worker(app, committed_orgs):
    """Four organisations whose receivers are down, each backing off 3s between
    attempts, must not delay organisation B beyond one deadline plus a margin."""
    from app.services import webhook_service as ws

    org_b = committed_orgs[0][1]
    extra = [_make_committed_org(app) for _ in range(3)]
    down = _Scripted(statuses=(500,))
    fast = _Scripted(200)
    try:
        for org in [committed_orgs[0][0]] + extra:
            _subscribe(app, org, down.url)
        _subscribe(app, org_b, fast.url)
        for org in [committed_orgs[0][0]] + extra:
            for i in range(2):
                _publish_with(app, org, {"n": i}, max_retries=3, retry_delay=3.0,
                              max_workers=8, per_org_limit=2)
        assert _wait_for(lambda: len(down.requests) >= 8, 10)
        started = time.time()
        _, future_b = _publish_with(app, org_b, {"b": 1}, max_retries=3, retry_delay=3.0)
        future_b.result(5)
        assert time.time() - started < 1.5, "organisation B waited behind sleeping workers"
        assert len(fast.requests) == 1
        # the failed deliveries are still retried by timers, not abandoned
        assert _wait_for(lambda: len(down.requests) >= 16, 12)
    finally:
        down.close()
        fast.close()
        ws.reset_delivery_executor()
        _drop_orgs(app, extra)


def test_r41_a_backing_off_delivery_holds_no_worker_slot(app, committed_orgs):
    from app.services import webhook_service as ws

    org_a = committed_orgs[0][0]
    down = _Scripted(statuses=(500,))
    try:
        _subscribe(app, org_a, down.url)
        _publish_with(app, org_a, {"n": 1}, max_retries=2, retry_delay=5.0)
        assert _wait_for(lambda: len(down.requests) == 1)
        time.sleep(0.5)
        dispatcher = ws._executor
        assert dispatcher is not None
        with dispatcher._cond:
            assert dict(dispatcher._inflight) == {}, "a delivery in backoff still counts as in flight"
            assert dispatcher._queued == 0
    finally:
        down.close()


def _failed_count(app, org_id):
    from sqlalchemy import text

    from app import db

    with app.app_context():
        return db.session.execute(text(
            "SELECT count(*) FROM webhook_deliveries WHERE organization_id = :o AND status = 'failed'"),
            {"o": org_id}).scalar()


def test_r42_dead_rows_do_not_crowd_the_sweep_limit(app, committed_orgs, monkeypatch):
    from datetime import datetime, timedelta

    from app import db
    from app.models.webhook import WebhookDelivery

    org_a, _ = committed_orgs[0]
    live = _Scripted(200)
    try:
        dead_sub = _subscribe(app, org_a, "http://dead.example/hook", is_active=False)
        live_sub = _subscribe(app, org_a, live.url)
        event_id = _new_event(app, org_a, {"a": 1})
        old = datetime.utcnow() - timedelta(hours=2)
        with app.app_context():
            for i in range(100):
                db.session.add(WebhookDelivery(
                    id=str(uuid.uuid4()), event_id=event_id, subscription_id=dead_sub,
                    organization_id=org_a, event_type="application.created",
                    payload={"dead": i}, status="pending", attempt_count=0,
                    created_at=old - timedelta(minutes=i)))
            db.session.add(WebhookDelivery(
                id=str(uuid.uuid4()), event_id=event_id, subscription_id=live_sub,
                organization_id=org_a, event_type="application.created",
                payload={"tag": "live"}, status="pending", attempt_count=0,
                created_at=datetime.utcnow() - timedelta(minutes=30)))
            db.session.commit()
        _scheduler_job(app, monkeypatch, "webhook_delivery_sweep").func()
        assert _wait_for(lambda: len(live.requests) == 1, 15), "the live row was crowded out by dead rows"
        from sqlalchemy import text
        with app.app_context():
            counts = dict(db.session.execute(text(
                "SELECT status, count(*) FROM webhook_deliveries WHERE organization_id = :o GROUP BY status"),
                {"o": org_a}).all())
        assert counts.get("failed", 0) <= 100
        assert _wait_for(lambda: _failed_count(app, org_a) == 100, 15), _failed_count(app, org_a)
    finally:
        live.close()


def test_r43_five_unreachable_addresses_are_bounded_by_one_deadline(app, committed_orgs, monkeypatch):
    import socket as socket_module

    org_a, _ = committed_orgs[0]
    real_connect = socket_module.socket.connect
    attempted = []

    def blackhole(self, address):
        if str(address[0]).startswith("192.0.2."):
            attempted.append(address[0])
            time.sleep(self.gettimeout() or 5)
            raise socket_module.timeout("timed out")
        return real_connect(self, address)

    real = socket_module.getaddrinfo

    def five(host, port, *args, **kwargs):
        if host == "five.example":
            return [(socket_module.AF_INET, socket_module.SOCK_STREAM, 6, "", (f"192.0.2.{i}", port))
                    for i in range(1, 6)]
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket_module.socket, "connect", blackhole)
    monkeypatch.setattr(socket_module, "getaddrinfo", five)
    _subscribe(app, org_a, "http://five.example:81/hook")
    started = time.time()
    event_id, future = _publish_with(app, org_a, {"n": 1}, attempt_deadline=1.0, timeout=5)
    future.result(15)
    elapsed = time.time() - started
    assert elapsed < 2.5, f"five unreachable addresses took {elapsed:.1f}s against a 1s deadline"
    assert len(set(attempted)) == 1, f"connected to more than the one pinned address: {attempted}"
    (row,) = _row(app, org_a, event_id)
    assert row["status"] == "failed", row


def test_r43_ssrf_slow_resolver_is_bounded_by_the_deadline(app, committed_orgs, monkeypatch):
    import socket as socket_module

    org_a, _ = committed_orgs[0]
    real = socket_module.getaddrinfo

    def slow(host, *args, **kwargs):
        if host == "slow.example":
            time.sleep(6)
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket_module, "getaddrinfo", slow)
    _subscribe(app, org_a, "http://slow.example:81/hook")
    started = time.time()
    event_id, future = _publish_with(app, org_a, {"n": 1}, attempt_deadline=1.0, timeout=5)
    future.result(15)
    elapsed = time.time() - started
    assert elapsed < 2.5, f"a slow resolver held the attempt {elapsed:.1f}s against a 1s deadline"
    (row,) = _row(app, org_a, event_id)
    assert row["status"] == "failed", row


def test_r43_pinned_connection_keeps_the_hostname_for_host_header_and_sni(app, committed_orgs, trust_tls, monkeypatch):
    import socket as socket_module

    org_a, _ = committed_orgs[0]
    secure = _Scripted(200)
    secure.server.socket = _wrap_scripted_in_tls(secure.server.socket, trust_tls)
    port = secure.server.server_address[1]
    real = socket_module.getaddrinfo
    monkeypatch.setattr(
        socket_module, "getaddrinfo",
        lambda host, p, *a, **k: [(socket_module.AF_INET, socket_module.SOCK_STREAM, 6, "", ("127.0.0.1", p))]
        if host == "localhost" else real(host, p, *a, **k))
    try:
        # "localhost" is on the certificate; the connection dials the pinned address
        _subscribe(app, org_a, f"https://localhost:{port}/hook")
        event_id, future = _publish_with(app, org_a, {"n": 1})
        future.result(15)
        (row,) = _row(app, org_a, event_id)
        assert row["status"] == "success", row
        assert secure.requests[0]["headers"]["Host"] == f"localhost:{port}"
    finally:
        secure.close()


def test_r44_proxy_environment_is_ignored(app, committed_orgs, monkeypatch):
    import socket as socket_module

    org_a, _ = committed_orgs[0]
    proxy = _Scripted(200)
    target = _Scripted(200)
    real = socket_module.getaddrinfo
    monkeypatch.setattr(
        socket_module, "getaddrinfo",
        lambda host, p, *a, **k: [(socket_module.AF_INET, socket_module.SOCK_STREAM, 6, "", ("127.0.0.1", p))]
        if host == "target.example" else real(host, p, *a, **k))
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, proxy.url.rsplit("/hook", 1)[0])
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    try:
        port = target.server.server_address[1]
        _subscribe(app, org_a, f"http://target.example:{port}/hook")
        event_id, future = _publish_with(app, org_a, {"n": 1})
        future.result(15)
        assert proxy.requests == [], "the delivery went through the environment's proxy"
        assert len(target.requests) == 1
    finally:
        proxy.close()
        target.close()


def test_r46_pending_rows_use_a_ten_minute_threshold_whatever_the_retry_delay(app, committed_orgs):
    from datetime import datetime, timedelta

    from app import db
    from app.models.webhook import WebhookDelivery

    org_a, _ = committed_orgs[0]
    receiver = _Scripted(200)
    try:
        sub = _subscribe(app, org_a, receiver.url)
        event_id = _new_event(app, org_a, {"a": 1})
        with app.app_context():
            for tag, age in (("old", 11), ("fresh", 5)):
                db.session.add(WebhookDelivery(
                    id=str(uuid.uuid4()), event_id=event_id, subscription_id=sub,
                    organization_id=org_a, event_type="application.created",
                    payload={"tag": tag}, status="pending", attempt_count=0,
                    created_at=datetime.utcnow() - timedelta(minutes=age)))
            db.session.commit()
        _retry_with(app, org_a, event_id, max_retries=3, retry_delay=3600).result(15)
        assert [json.loads(r["body"])["tag"] for r in receiver.requests] == ["old"]
    finally:
        receiver.close()


@pytest.mark.parametrize("address,public", [
    ("100.64.0.1", False), ("100.127.255.254", False), ("100.63.255.255", True),
    ("100.128.0.1", True), ("93.184.216.34", True), ("10.0.0.1", False),
])
def test_r46_cgnat_range_is_not_public(address, public):
    from app.utils.ssrf_guard import _is_public_ip

    assert _is_public_ip(address) is public
