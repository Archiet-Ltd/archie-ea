"""
Webhook service for managing event-driven notifications
"""

import atexit
import hashlib
import hmac
import http.client
import json
import socket
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import Future
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from flask import current_app
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from app.extensions import db
from app.models.webhook import WebhookDelivery, WebhookEvent, WebhookSubscription
from app.utils.ssrf_guard import BlockedOutboundURL, _is_public_ip, validate_outbound_url

_DEFAULT_DELIVERY_WORKERS = 8
# At most this many deliveries of one organisation are in flight at once; the
# rest wait in that organisation's own queue and occupy no worker.
_DEFAULT_PER_ORG_IN_FLIGHT = 2
# Bound on queued (not yet running) deliveries across the process. Past it the
# delivery row simply stays "pending" for the stale-pending retry to pick up.
_DEFAULT_QUEUE_MAX = 1000
# Wall-clock cap on one send attempt (connect + headers + body), seconds.
_DEFAULT_ATTEMPT_DEADLINE = 10.0
# Never read more than this much of a receiver's response.
MAX_RESPONSE_BYTES = 64 * 1024
# A row is only treated as lost with its process after the longest a live worker
# could legitimately take (see WebhookService._stale_after) plus this margin.
STALE_MARGIN_SECONDS = 60
# A "pending" row has no worker mid-flight on it (claiming it is atomic), so it
# is recovered after this fixed, short time whatever the retry settings.
PENDING_STALE_AFTER = timedelta(minutes=10)
# Most rows one periodic sweep hands to the dispatcher for one organisation.
SWEEP_BATCH_PER_ORG = 100


class _FairDispatcher:
    """Process-wide bounded delivery workers with per-organisation fairness.

    * Workers are daemon threads, so process exit never waits on deliveries.
    * Each organisation has its own queue; workers take work round-robin across
      organisations, and an organisation never has more than ``per_org_limit``
      deliveries running, so one organisation's slow or hanging receivers
      cannot occupy every worker.
    * The total queue is bounded: ``submit_for_org`` returns ``None`` when full
      and the caller leaves the delivery row ``pending``.
    """

    def __init__(self, workers, per_org_limit, queue_max):
        self.per_org_limit = max(1, int(per_org_limit))
        self.queue_max = max(1, int(queue_max))
        self._cond = threading.Condition()
        self._queues = {}
        self._rotation = deque()
        self._inflight = defaultdict(int)
        self._queued = 0
        self._stopping = False
        self._threads = []
        for i in range(max(1, int(workers))):
            t = threading.Thread(
                target=self._work, name=f"webhook-delivery-{i}", daemon=True
            )
            t.start()
            self._threads.append(t)

    def submit_for_org(self, org_id, fn, *args) -> Optional[Future]:
        future = Future()
        with self._cond:
            if self._stopping or self._queued >= self.queue_max:
                return None
            if org_id not in self._queues:
                self._queues[org_id] = deque()
                self._rotation.append(org_id)
            self._queues[org_id].append((future, fn, args))
            self._queued += 1
            self._cond.notify_all()
        return future

    def submit(self, fn, *args) -> Optional[Future]:
        """ThreadPoolExecutor-shaped entry for work with no organisation."""
        return self.submit_for_org(None, fn, *args)

    def _next(self):
        for _ in range(len(self._rotation)):
            org_id = self._rotation[0]
            queue = self._queues[org_id]
            if self._inflight[org_id] < self.per_org_limit:
                item = queue.popleft()
                self._queued -= 1
                self._inflight[org_id] += 1
                if queue:
                    self._rotation.rotate(-1)
                else:
                    self._rotation.popleft()
                    del self._queues[org_id]
                return org_id, item
            self._rotation.rotate(-1)
        return None

    def _work(self):
        while True:
            with self._cond:
                picked = None
                while not self._stopping:
                    picked = self._next()
                    if picked is not None:
                        break
                    self._cond.wait()
                if self._stopping:
                    return
            org_id, (future, fn, args) = picked
            try:
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(fn(*args))
                    except BaseException as exc:  # reported through the future
                        future.set_exception(exc)
            finally:
                with self._cond:
                    self._inflight[org_id] -= 1
                    if self._inflight[org_id] <= 0:
                        del self._inflight[org_id]
                    self._cond.notify_all()

    def shutdown(self, wait=False, cancel_futures=True):
        """Stop taking work; drop queued work. Rows queued here stay "pending"
        and the stale-pending retry recovers them. Never joins unless asked."""
        with self._cond:
            self._stopping = True
            dropped = []
            if cancel_futures:
                for queue in self._queues.values():
                    dropped.extend(item[0] for item in queue)
            self._queues.clear()
            self._rotation.clear()
            self._queued = 0
            self._cond.notify_all()
        for future in dropped:
            future.cancel()
        if wait:
            for t in self._threads:
                t.join()


_executor: Optional[_FairDispatcher] = None
_executor_lock = threading.Lock()


def _get_delivery_executor(
    max_workers: int,
    per_org_limit: int = _DEFAULT_PER_ORG_IN_FLIGHT,
    queue_max: int = _DEFAULT_QUEUE_MAX,
) -> _FairDispatcher:
    """The process's dispatcher, created lazily (after gunicorn forks)."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = _FairDispatcher(max_workers, per_org_limit, queue_max)
            atexit.register(_shutdown_delivery_executor)
        return _executor


def _shutdown_delivery_executor() -> None:
    """At process exit: do not wait on queued or running deliveries."""
    with _executor_lock:
        executor = _executor
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def reset_delivery_executor() -> None:
    """Drop the dispatcher so the next use builds a fresh one (tests, reconfig)."""
    global _executor
    with _executor_lock:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# One send attempt with a wall-clock deadline. A socket timeout applies to each
# read, so a receiver that drips a byte at a time never trips it; a watchdog
# closes the attempt's sockets when the deadline passes instead.
# ---------------------------------------------------------------------------

_attempt_local = threading.local()


class DeliveryDeadlineExceeded(Exception):
    pass


class BlockedPeerAddress(BlockedOutboundURL):
    """The socket connected to an address the outbound URL guard forbids."""


class _AttemptGuard:
    def __init__(self, deadline: float):
        self.deadline = deadline
        self.expired = False
        self.blocked_peer = None
        self._started = time.monotonic()
        self._socks = []
        self._lock = threading.Lock()
        self._timer = None

    def remaining(self) -> float:
        return self.deadline - (time.monotonic() - self._started)

    @staticmethod
    def _kill(sock) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def track(self, sock) -> None:
        """Watch the connection behind ``sock``. A duplicate of the descriptor
        is kept: TLS wrapping detaches the original socket object, but a
        shutdown through any descriptor of the connection ends it, so the
        deadline still reaches a connection that is mid-handshake or wrapped."""
        try:
            watched = sock.dup()
        except OSError:
            return
        with self._lock:
            self._socks.append(watched)
            if self.expired:
                self._kill(watched)

    def _expire(self) -> None:
        with self._lock:
            self.expired = True
            for sock in self._socks:
                self._kill(sock)

    def __enter__(self):
        _attempt_local.guard = self
        self._timer = threading.Timer(self.deadline, self._expire)
        self._timer.daemon = True
        self._timer.start()
        return self

    def __exit__(self, *exc):
        self._timer.cancel()
        _attempt_local.guard = None
        with self._lock:
            socks, self._socks = self._socks, []
        for sock in socks:
            try:
                sock.close()
            except OSError:
                pass
        return False


def _peer_is_public(address: str) -> bool:
    """The ssrf_guard public-address rule, applied to the connected peer."""
    return _is_public_ip(address)


def _bounded(fn, seconds):
    """Run ``fn`` in a helper thread and wait at most ``seconds`` for it. Used
    for name resolution, which cannot be interrupted: past the limit the helper
    is abandoned (it is a daemon) and socket.timeout is raised."""
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # handed back to the caller
            box["error"] = exc

    helper = threading.Thread(target=run, name="webhook-resolve", daemon=True)
    helper.start()
    helper.join(max(0.05, seconds))
    if helper.is_alive():
        raise socket.timeout("name resolution exceeded the delivery deadline")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _resolve_pinned(host, port, seconds):
    """Resolve ``host`` once, under the remaining deadline, and return the first
    address that passes the public-address rule as (family, sockaddr). Pinning
    that address is what closes DNS rebinding at source."""
    infos = _bounded(lambda: socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM), seconds)
    first_refused = None
    for family, _type, _proto, _canon, sockaddr in infos:
        if _peer_is_public(sockaddr[0]):
            return family, sockaddr
        first_refused = first_refused or sockaddr[0]
    raise BlockedPeerAddress(
        f"resolved address {first_refused} is not a public address"
        if first_refused
        else f"host {host!r} did not resolve"
    )


class _TrackSocketMixin:
    def _new_conn(self):
        guard = getattr(_attempt_local, "guard", None)
        if guard is None:
            return super()._new_conn()
        remaining = guard.remaining()
        if remaining <= 0:
            guard.expired = True
            raise socket.timeout("delivery deadline reached before connect")
        # One resolution, under the deadline; connect only to the pinned public
        # address. The connection object still carries the hostname, so the TLS
        # server name and the Host header are the hostname's.
        try:
            family, sockaddr = _resolve_pinned(
                getattr(self, "_dns_host", self.host), self.port, remaining
            )
        except BlockedPeerAddress:
            guard.blocked_peer = "resolved"
            raise
        remaining = guard.remaining()
        if remaining <= 0:
            guard.expired = True
            raise socket.timeout("delivery deadline reached before connect")
        current = self.timeout if isinstance(self.timeout, (int, float)) else remaining
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            for option in getattr(self, "socket_options", None) or []:
                sock.setsockopt(*option)
            sock.settimeout(max(0.05, min(float(current), remaining)))
            if self.source_address:
                sock.bind(self.source_address)
            sock.connect(sockaddr)
            # Belt and braces: judge the address actually connected to.
            peer = sock.getpeername()[0]
            if not _peer_is_public(peer):
                guard.blocked_peer = peer
                raise BlockedPeerAddress(f"connected peer {peer} is not a public address")
        except BaseException:
            sock.close()
            raise
        guard.track(sock)
        return sock


class _TrackedHTTPConnection(_TrackSocketMixin, HTTPConnection):
    pass


class _TrackedHTTPSConnection(_TrackSocketMixin, HTTPSConnection):
    pass


class _TrackedHTTPPool(HTTPConnectionPool):
    ConnectionCls = _TrackedHTTPConnection


class _TrackedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _TrackedHTTPSConnection


class _DeadlineAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": _TrackedHTTPPool,
            "https": _TrackedHTTPSPool,
        }


def _is_blocked_peer_error(exc) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, BlockedPeerAddress):
            return True
        exc = getattr(exc, "reason", None) or exc.__cause__ or exc.__context__             or (exc.args[0] if exc.args and isinstance(exc.args[0], BaseException) else None)
    return False


def _post_with_deadline(url, payload, headers, timeout, deadline):
    """POST once. Returns (status_code, body_text); body_text is None for a
    redirect (never followed, never read). Raises DeliveryDeadlineExceeded past
    the wall-clock deadline, or requests/OS errors."""
    deadline = max(0.05, float(deadline))
    sock_timeout = max(0.05, min(float(timeout), deadline))
    started = time.monotonic()
    guard = _AttemptGuard(deadline)
    session = requests.Session()
    session.trust_env = False  # no proxy, netrc or CA settings from the environment
    session.mount("http://", _DeadlineAdapter())
    session.mount("https://", _DeadlineAdapter())
    try:
        with guard:
            response = session.post(
                url,
                json=payload,
                headers=headers,
                timeout=(sock_timeout, sock_timeout),
                allow_redirects=False,
                stream=True,
            )
            try:
                status = response.status_code
                if guard.expired:  # cut off mid-headers: a truncated reply is no reply
                    raise DeliveryDeadlineExceeded()
                if 300 <= status < 400:
                    return status, None
                body = bytearray()
                for chunk in response.iter_content(8192):
                    body.extend(chunk)
                    if len(body) >= MAX_RESPONSE_BYTES:
                        break
                    if time.monotonic() - started > deadline:
                        raise DeliveryDeadlineExceeded()
                if guard.expired:
                    raise DeliveryDeadlineExceeded()
                encoding = response.encoding or "utf-8"
            finally:
                response.close()
        try:
            text = bytes(body[:MAX_RESPONSE_BYTES]).decode(encoding, "replace")
        except LookupError:
            text = bytes(body[:MAX_RESPONSE_BYTES]).decode("utf-8", "replace")
        return status, text
    except DeliveryDeadlineExceeded:
        raise DeliveryDeadlineExceeded(f"delivery exceeded the {deadline:g}s deadline")
    except BlockedPeerAddress:
        raise
    except (requests.RequestException, OSError, http.client.HTTPException) as exc:
        if guard.blocked_peer is not None or _is_blocked_peer_error(exc):
            raise BlockedPeerAddress("peer address is not a public address") from exc
        if guard.expired or time.monotonic() - started >= deadline:
            raise DeliveryDeadlineExceeded(
                f"delivery exceeded the {deadline:g}s deadline"
            ) from exc
        raise
    finally:
        session.close()


class WebhookService:
    """Service for managing webhook subscriptions and event delivery"""

    def __init__(self):
        self.max_retries = current_app.config.get("WEBHOOK_MAX_RETRIES", 3)
        self.retry_delay = current_app.config.get("WEBHOOK_RETRY_DELAY", 60)  # seconds
        self.timeout = current_app.config.get("WEBHOOK_TIMEOUT", 30)  # seconds
        self.max_workers = current_app.config.get(
            "WEBHOOK_DELIVERY_MAX_WORKERS", _DEFAULT_DELIVERY_WORKERS
        )
        self.per_org_limit = current_app.config.get(
            "WEBHOOK_DELIVERY_PER_ORG_IN_FLIGHT", _DEFAULT_PER_ORG_IN_FLIGHT
        )
        self.queue_max = current_app.config.get("WEBHOOK_DELIVERY_QUEUE_MAX", _DEFAULT_QUEUE_MAX)
        self.attempt_deadline = current_app.config.get(
            "WEBHOOK_DELIVERY_DEADLINE_SECONDS", _DEFAULT_ATTEMPT_DEADLINE
        )

    def create_subscription(
        self,
        user_id: str,
        url: str,
        events: List[str],
        secret: Optional[str] = None,
        description: Optional[str] = None,
        filters: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        webhook_type: str = "generic",
    ) -> WebhookSubscription:
        """Create a new webhook subscription"""
        subscription = WebhookSubscription(
            id=str(uuid.uuid4()),
            user_id=user_id,
            url=url,
            events=events,
            secret=secret,
            description=description or "",
            webhook_type=webhook_type if webhook_type in ("generic", "teams", "slack") else "generic",
            filters=filters or {},
            headers=headers or {},
            is_active=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        db.session.add(subscription)
        db.session.commit()

        current_app.logger.info(
            f"Created webhook subscription {subscription.id} for user {user_id}"
        )
        return subscription

    def get_user_subscriptions(self, user_id: str) -> List[WebhookSubscription]:
        """Get all subscriptions for a user"""
        # user_id column is varchar; current_user.id is int -> cast to avoid
        # "operator does not exist: character varying = integer".
        return WebhookSubscription.query.filter_by(
            user_id=str(user_id), is_active=True
        ).all()

    def get_subscription(self, subscription_id: str, user_id: str) -> Optional[WebhookSubscription]:
        """Get a specific subscription for a user"""
        return WebhookSubscription.query.filter_by(
            id=subscription_id, user_id=user_id, is_active=True
        ).first()

    def get_subscription_by_id(self, subscription_id: str) -> Optional[WebhookSubscription]:
        """Get a subscription by ID (internal use)"""
        return WebhookSubscription.query.filter_by(id=subscription_id, is_active=True).first()

    def update_subscription(
        self, subscription_id: str, user_id: str, updates: Dict
    ) -> Optional[WebhookSubscription]:
        """Update a webhook subscription"""
        subscription = self.get_subscription(subscription_id, user_id)
        if not subscription:
            return None

        allowed_fields = [
            "url",
            "events",
            "secret",
            "description",
            "webhook_type",
            "filters",
            "headers",
            "is_active",
        ]
        for field, value in updates.items():
            if field in allowed_fields:
                setattr(subscription, field, value)

        subscription.updated_at = datetime.utcnow()
        db.session.commit()

        current_app.logger.info(f"Updated webhook subscription {subscription_id}")
        return subscription

    def delete_subscription(self, subscription_id: str, user_id: str) -> bool:
        """Delete a webhook subscription"""
        subscription = self.get_subscription(subscription_id, user_id)
        if not subscription:
            return False

        subscription.is_active = False
        subscription.updated_at = datetime.utcnow()
        db.session.commit()

        current_app.logger.info(f"Deleted webhook subscription {subscription_id}")
        return True

    def test_subscription(self, subscription_id: str, user_id: str) -> Optional[Dict]:
        """Test a webhook subscription by sending a test event"""
        subscription = self.get_subscription(subscription_id, user_id)
        if not subscription:
            return None

        test_event = {
            "event_type": "webhook.test",
            "payload": {
                "message": "This is a test webhook",
                "timestamp": datetime.utcnow().isoformat(),
                "subscription_id": subscription_id,
            },
            "metadata": {"test": True},
        }

        return self._deliver_webhook(subscription, test_event)

    def publish_event(
        self, event_type: str, payload: Dict, user_id: str, metadata: Optional[Dict] = None
    ) -> WebhookEvent:
        """Publish an event to all subscribed webhooks.

        The event and one "pending" delivery row per matching subscription are
        written in a single short commit before anything is queued, so no
        published event can exist without its rows: whatever is lost in memory
        (a full queue, a restart) is recoverable from the table.
        """
        event = WebhookEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            payload=payload,
            user_id=user_id,
            event_metadata=metadata or {},
            created_at=datetime.utcnow(),
        )

        db.session.add(event)
        db.session.flush()  # resolves the organisation column default
        org_id = event.organization_id

        subscriptions = (
            self._find_matching_subscriptions(event_type, payload) if org_id is not None else []
        )
        event_data = {
            "event_type": event_type,
            "payload": payload,
            "metadata": event.event_metadata,
            "event_id": event.id,
            "timestamp": event.created_at.isoformat(),
        }
        subscription_ids = []
        for sub in subscriptions:
            if sub.organization_id != org_id:
                continue
            db.session.add(
                WebhookDelivery(
                    id=str(uuid.uuid4()),
                    event_id=event.id,
                    subscription_id=sub.id,
                    organization_id=org_id,  # explicit: workers have no request context
                    event_type=event_type,
                    payload=self._build_payload_for_subscription(sub, event_data),
                    status="pending",
                    attempt_count=0,
                    created_at=datetime.utcnow(),
                )
            )
            subscription_ids.append(sub.id)
        db.session.commit()

        if org_id is None:
            current_app.logger.error(
                f"Webhook event {event.id} ({event_type}) has no organization; nothing delivered"
            )
        elif subscription_ids:
            # The worker has no request, so it gets the real app and plain ids,
            # not ORM instances bound to this request's session.
            self._delivery_future = _get_delivery_executor(
                self.max_workers, self.per_org_limit, self.queue_max
            ).submit_for_org(
                org_id,
                self._run_delivery_in_app_context,
                current_app._get_current_object(),
                event.id,
                org_id,
                subscription_ids,
            )
            if self._delivery_future is None:
                current_app.logger.warning(
                    f"Webhook delivery queue full; event {event.id} left pending "
                    f"for {len(subscription_ids)} subscription(s)"
                )

        current_app.logger.info(
            f"Published event {event_type} with {len(subscriptions)} subscriptions"
        )
        return event

    def _find_matching_subscriptions(
        self, event_type: str, payload: Dict
    ) -> List[WebhookSubscription]:
        """Find subscriptions that match the event"""
        subscriptions = WebhookSubscription.query.filter_by(is_active=True).all()
        matching = []

        for subscription in subscriptions:
            # Check if event type matches
            if event_type not in subscription.events and "*" not in subscription.events:
                continue

            # Check filters
            if subscription.filters:
                if not self._matches_filters(payload, subscription.filters):
                    continue

            matching.append(subscription)

        return matching

    def _matches_filters(self, payload: Dict, filters: Dict) -> bool:
        """Check if payload matches the subscription filters"""
        for key, expected_value in filters.items():
            if key not in payload:
                return False

            actual_value = payload[key]
            if isinstance(expected_value, dict):
                # Nested filter
                if not isinstance(actual_value, dict):
                    return False
                if not self._matches_filters(actual_value, expected_value):
                    return False
            elif actual_value != expected_value:
                return False

        return True

    @staticmethod
    def _snapshot_subscription(subscription: WebhookSubscription) -> SimpleNamespace:
        """Plain copy of what delivery needs, so no ORM object (and no open
        transaction) is touched while the network call is in flight."""
        return SimpleNamespace(
            id=subscription.id,
            organization_id=subscription.organization_id,
            url=subscription.url,
            headers=dict(subscription.headers or {}),
            secret=subscription.secret,
            webhook_type=getattr(subscription, "webhook_type", "generic") or "generic",  # model-safety-ok
        )

    @staticmethod
    def _deliverable(subscription, org_id) -> bool:
        """Active at delivery time and owned by the event's organisation."""
        return (
            subscription is not None
            and bool(subscription.is_active)
            and subscription.organization_id == org_id
        )

    def _run_delivery_in_app_context(self, app, event_id, org_id, subscription_ids):
        """Worker body: deliver one event inside its own application context.

        Loads the event's "pending" delivery rows (written at publish time) and
        their subscriptions by id under the event's tenant scope, so nothing
        from the publishing request's session is shared with this worker. A row
        whose subscription is no longer deliverable is marked failed. Never
        raises: failures are logged through the captured app.
        """
        from app.jobs.tenant_safe_job import tenant_scope

        if org_id is None:
            app.logger.error(f"Webhook event {event_id} has no organization; nothing delivered")
            return
        with app.app_context():
            try:
                with tenant_scope(org_id):
                    event = db.session.get(WebhookEvent, event_id)
                    if event is None or event.organization_id != org_id:
                        return
                    rows = (
                        db.session.query(WebhookDelivery)
                        .filter(
                            WebhookDelivery.event_id == event_id,
                            WebhookDelivery.organization_id == org_id,
                            WebhookDelivery.subscription_id.in_(list(subscription_ids)),
                            WebhookDelivery.status == "pending",
                        )
                        .all()
                    )
                    work, undeliverable = [], []
                    for delivery in rows:
                        sub = db.session.get(WebhookSubscription, delivery.subscription_id)
                        if self._deliverable(sub, org_id):
                            work.append((delivery.id, self._snapshot_subscription(sub), delivery.payload))
                        else:
                            undeliverable.append(delivery.id)
                    db.session.rollback()
                    db.session.remove()  # worker thread: nothing held from here on
                    for delivery_id in undeliverable:
                        self._fail_undeliverable(delivery_id, org_id)
                    for delivery_id, snap, formatted in work:
                        self._claim_and_attempt(delivery_id, org_id, snap, formatted, retry=False)
            except Exception as e:  # never let the worker die silently
                try:
                    db.session.rollback()
                except Exception:
                    pass
                app.logger.error(f"Webhook delivery failed for event {event_id}: {e}")
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass

    def _stale_after(self) -> timedelta:
        """How long a "pending"/"retrying" row may sit before its worker is
        presumed lost: the longest a live worker can take over all its attempts
        (deadline each) and backoffs, plus a margin. Computed from config, so a
        long retry delay can never make a live worker's row look abandoned."""
        total = max(1, int(self.max_retries))
        backoff = sum(float(self.retry_delay) * (i + 1) for i in range(total - 1))
        return timedelta(
            seconds=total * float(self.attempt_deadline) + backoff + STALE_MARGIN_SECONDS
        )

    def _retryable_clause(self, now, *, include_failed: bool):
        """Rows a retry or sweep may claim: failed (optionally), or left
        pending/retrying past the computed stale threshold."""
        from sqlalchemy import and_, or_

        stale = now - self._stale_after()
        clauses = [
            and_(
                WebhookDelivery.status == "pending",
                WebhookDelivery.created_at < now - PENDING_STALE_AFTER,
            ),
            and_(
                WebhookDelivery.status == "retrying",
                or_(
                    WebhookDelivery.last_attempt_at.is_(None),
                    WebhookDelivery.last_attempt_at < stale,
                ),
            ),
        ]
        if include_failed:
            clauses.append(WebhookDelivery.status == "failed")
        return or_(*clauses)

    def _claim_and_attempt(self, delivery_id, org_id, snap, formatted, *, retry: bool,
                           include_failed: bool = True) -> bool:
        """Claim the row atomically, then send. Only the winner sends."""
        try:
            token = self._claim_delivery(
                delivery_id, org_id, retry=retry, include_failed=include_failed
            )
            if token is None:
                return False
            self._attempt_delivery(
                delivery_id, snap, self._outbound_headers(snap, formatted), formatted,
                org_id=org_id, token=token,
            )
            return True
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            current_app.logger.error(
                f"Failed to deliver delivery {delivery_id} to subscription {snap.id}: {e}"
            )
            return False

    def _claim_delivery(self, delivery_id, org_id, *, retry: bool,
                        include_failed: bool = True):
        """One UPDATE moving the row to "retrying", guarded on its current
        status, so of any number of concurrent claimants exactly one wins.
        Returns the claim token, or None when another claimant has the row.

        The token is the ``last_attempt_at`` value this claim writes (a fresh
        microsecond timestamp, in the existing column: no migration). Every
        later write of the claimant's attempts must present it; a newer claim
        replaces it, so a slower worker can no longer overwrite the newer result.

        First delivery claims "pending". A retry claims "failed", or a row left
        "pending"/"retrying" past the computed stale threshold; never a fresh
        "pending" (its worker has it) or a fresh "retrying".
        """
        from sqlalchemy import update

        now = datetime.utcnow()
        if retry:
            allowed = self._retryable_clause(now, include_failed=include_failed)
        else:
            allowed = WebhookDelivery.status == "pending"
        try:
            result = db.session.execute(
                update(WebhookDelivery)
                .where(
                    WebhookDelivery.id == delivery_id,
                    WebhookDelivery.organization_id == org_id,
                    allowed,
                )
                .values(status="retrying", last_attempt_at=now)
                .execution_options(synchronize_session=False)
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return now if result.rowcount == 1 else None

    def _fail_undeliverable(self, delivery_id, org_id, statuses=("pending",)) -> None:
        from sqlalchemy import update

        try:
            db.session.execute(
                update(WebhookDelivery)
                .where(
                    WebhookDelivery.id == delivery_id,
                    WebhookDelivery.organization_id == org_id,
                    WebhookDelivery.status.in_(list(statuses)),
                )
                .values(
                    status="failed",
                    last_attempt_at=datetime.utcnow(),
                    error_message="Subscription is no longer active for this organisation",
                )
                .execution_options(synchronize_session=False)
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

    # ------------------------------------------------------------------
    # Payload formatters for Teams and Slack
    # ------------------------------------------------------------------

    def format_teams_payload(self, event_data: Dict) -> Dict:
        """Format event data as a Microsoft Teams Adaptive Card payload.

        Produces a valid Teams Incoming Webhook message with an AdaptiveCard
        v1.4 attachment so rich formatting is rendered in the Teams client.
        """
        event_type = event_data.get("event_type", "event")
        payload = event_data.get("payload", {})
        timestamp = event_data.get("timestamp", datetime.utcnow().isoformat())

        # Build a human-readable summary from the inner payload dict
        summary_lines = []
        for key, value in (payload.items() if isinstance(payload, dict) else []):
            if isinstance(value, (str, int, float, bool)) and value not in ("", None):
                label = key.replace("_", " ").title()
                summary_lines.append(f"**{label}:** {value}")
        summary_text = "\n\n".join(summary_lines) if summary_lines else "No additional details."

        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": event_type.replace(".", " ").title(),
                                "weight": "Bolder",
                                "size": "Medium",
                                "wrap": True,
                            },
                            {
                                "type": "TextBlock",
                                "text": summary_text,
                                "wrap": True,
                                "spacing": "Medium",
                            },
                            {
                                "type": "TextBlock",
                                "text": f"Sent at {timestamp}",
                                "isSubtle": True,
                                "size": "Small",
                                "wrap": True,
                            },
                        ],
                    },
                }
            ],
        }

    def format_slack_payload(self, event_data: Dict) -> Dict:
        """Format event data as a Slack Block Kit message payload.

        Produces a Slack message with a header block and a mrkdwn section so
        the notification renders well in Slack channels.
        """
        event_type = event_data.get("event_type", "event")
        payload = event_data.get("payload", {})
        timestamp = event_data.get("timestamp", datetime.utcnow().isoformat())

        # Build field lines from the inner payload dict
        field_lines = []
        for key, value in (payload.items() if isinstance(payload, dict) else []):
            if isinstance(value, (str, int, float, bool)) and value not in ("", None):
                label = key.replace("_", " ").title()
                field_lines.append(f"*{label}:* {value}")
        body_text = "\n".join(field_lines) if field_lines else "_No additional details._"

        return {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": event_type.replace(".", " ").title(),
                        "emoji": False,
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": body_text,
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Sent at {timestamp}",
                        }
                    ],
                },
            ]
        }

    def _build_payload_for_subscription(
        self, subscription: WebhookSubscription, event_data: Dict
    ) -> Dict:
        """Return the payload formatted for the subscription's webhook_type."""
        webhook_type = getattr(subscription, "webhook_type", "generic") or "generic"  # model-safety-ok
        if webhook_type == "teams":
            return self.format_teams_payload(event_data)
        if webhook_type == "slack":
            return self.format_slack_payload(event_data)
        return event_data

    def _outbound_headers(self, snap, formatted_payload: Dict) -> Dict:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Enterprise-Architecture-Webhook/1.0",
            **(snap.headers or {}),
        }
        # Signature is set last and covers the formatted payload.
        if snap.secret:
            payload_str = json.dumps(formatted_payload, sort_keys=True)
            headers["X-Webhook-Signature"] = hmac.new(
                snap.secret.encode(), payload_str.encode(), hashlib.sha256
            ).hexdigest()
        return headers

    def _deliver_webhook(self, subscription, event_data: Dict) -> Dict:
        """Deliver to a single subscription in the caller's thread (the test
        route): one attempt, no retry schedule, so a failing receiver cannot
        hold the request thread. Writes its own row, claims it and sends like a
        worker; the request's session is committed, never removed."""
        snap = (
            subscription
            if isinstance(subscription, SimpleNamespace)
            else self._snapshot_subscription(subscription)
        )
        formatted_payload = self._build_payload_for_subscription(snap, event_data)

        delivery = WebhookDelivery(
            id=str(uuid.uuid4()),
            event_id=event_data.get("event_id"),
            subscription_id=snap.id,
            organization_id=snap.organization_id,
            event_type=event_data.get("event_type"),
            payload=formatted_payload,
            status="pending",
            attempt_count=0,
            created_at=datetime.utcnow(),
        )
        delivery_id = delivery.id
        db.session.add(delivery)
        db.session.commit()

        token = self._claim_delivery(delivery_id, snap.organization_id, retry=False)
        if token is None:
            return {"delivery_id": delivery_id, "success": False, "attempts": 0}
        headers = self._outbound_headers(snap, formatted_payload)
        success, attempts = self._attempt_delivery(
            delivery_id, snap, headers, formatted_payload,
            org_id=snap.organization_id, token=token, max_attempts=1,
        )
        return {"delivery_id": delivery_id, "success": success, "attempts": attempts}

    def _record_attempt(self, delivery_id: str, org_id, token, **fields):
        """Write one attempt's outcome in its own short transaction, only while
        this claimant still holds the row (its token is the row's
        ``last_attempt_at``). Returns the claimant's next token, or None when
        the claim was lost, in which case nothing is written. The commit
        releases the connection; the session itself is left alone because this
        also runs inside a live request (the test route)."""
        from sqlalchemy import update

        new_token = datetime.utcnow()
        if new_token <= token:
            new_token = token + timedelta(microseconds=1)
        try:
            result = db.session.execute(
                update(WebhookDelivery)
                .where(
                    WebhookDelivery.id == delivery_id,
                    WebhookDelivery.organization_id == org_id,
                    WebhookDelivery.status == "retrying",
                    WebhookDelivery.last_attempt_at == token,
                )
                .values(last_attempt_at=new_token, **fields)
                .execution_options(synchronize_session=False)
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return new_token if result.rowcount == 1 else None

    def _attempt_delivery(self, delivery_id: str, snap, headers: Dict, payload: Dict, *,
                          org_id, token, max_attempts: Optional[int] = None,
                          attempt_number: int = 1):
        """Make ONE send attempt of a claimed row and record it, presenting the
        claim token. A failed attempt that will be retried is recorded (the row
        stays "retrying", the new token is kept), then the next attempt is
        handed back to the dispatcher after the backoff by a timer: no worker
        sleeps and a delivery waiting out its backoff occupies no in-flight
        slot. The row becomes "failed" only after the last attempt. If the claim
        is lost (a newer claimant took the row) this worker stops and writes
        nothing.

        A redirect is never followed: any 3xx is a failure recorded with its
        status and no body. Each attempt has a wall-clock deadline (name
        resolution included) and reads at most 64 KB of the response.

        Returns (success, attempt_number). Logs the subscription id and URL host
        only: the full URL is a bearer credential for Slack/Teams/Azure endpoints.
        """
        url = snap.url
        host = urlparse(url).hostname or "unknown-host"
        label = f"subscription {snap.id} (host {host})"
        state = {"token": token}

        def record(**fields):
            new_token = self._record_attempt(delivery_id, org_id, state["token"], **fields)
            state["token"] = new_token
            if new_token is None:
                current_app.logger.warning(
                    f"Webhook delivery {delivery_id} to {label} was claimed by another "
                    f"worker; this attempt result is discarded"
                )
            return new_token

        attempts = attempt_number

        def failed(message, outcome=None):
            return self._after_failed_attempt(
                record, delivery_id, snap, headers, payload, org_id, attempts,
                max_attempts, label, message, outcome,
            )

        try:
            _bounded(
                lambda: validate_outbound_url(url, require_https=False),
                float(self.attempt_deadline),
            )
        except BlockedOutboundURL as e:
            record(
                status="failed",
                attempt_count=attempts,
                error_message=f"Blocked outbound URL: {e}"[:500],
            )
            current_app.logger.error(f"Webhook to {label} blocked by outbound URL guard")
            return False, attempts
        except OSError as e:  # name resolution timed out or failed
            return failed(str(e)[:500] or "name resolution failed")

        try:
            status_code, body = _post_with_deadline(
                url, payload, headers, self.timeout, self.attempt_deadline
            )
        except BlockedPeerAddress as e:
            # Deterministic and hostile: no retry.
            record(
                status="failed",
                attempt_count=attempts,
                error_message=f"Blocked outbound URL: {e}"[:500],
            )
            current_app.logger.error(f"Webhook to {label} blocked: peer address not public")
            return False, attempts
        except (requests.RequestException, DeliveryDeadlineExceeded, OSError,
                http.client.HTTPException) as e:
            return failed(str(e)[:500])

        outcome = dict(
            attempt_count=attempts,
            response_status=status_code,
            response_body=body[:1000] if body is not None else None,
        )
        if 200 <= status_code < 300:
            if record(
                status="success", delivered_at=datetime.utcnow(),
                error_message=None, **outcome,
            ):
                current_app.logger.info(f"Delivered webhook to {label}")
            return True, attempts
        if 300 <= status_code < 400:
            # Deterministic: retrying the same URL redirects again.
            record(
                status="failed",
                error_message=f"HTTP {status_code}: redirect not followed",
                **outcome,
            )
            current_app.logger.error(
                f"Webhook to {label} answered a redirect ({status_code}); not followed"
            )
            return False, attempts
        return failed(f"HTTP {status_code}: {(body or '')[:200]}", outcome)

    def _total_attempts(self, max_attempts) -> int:
        total = max(1, int(self.max_retries))
        if max_attempts is not None:
            total = max(1, min(total, int(max_attempts)))
        return total

    def _after_failed_attempt(self, record, delivery_id, snap, headers, payload, org_id,
                              attempts, max_attempts, label, message, outcome=None):
        """Record a failed attempt; if another is due, schedule it on a timer."""
        total = self._total_attempts(max_attempts)
        last = attempts >= total
        fields = dict(outcome or {"attempt_count": attempts})
        new_token = record(
            status="failed" if last else "retrying", error_message=message, **fields
        )
        if new_token is None:
            return False, attempts
        if last:
            current_app.logger.error(
                f"Failed to deliver webhook to {label} after {total} attempts"
            )
            return False, attempts
        self._schedule_next_attempt(
            delivery_id, snap, headers, payload, org_id, new_token, attempts + 1,
            max_attempts, delay=self.retry_delay * attempts,  # backoff grows each attempt
        )
        return False, attempts

    def _schedule_next_attempt(self, delivery_id, snap, headers, payload, org_id, token,
                               next_number, max_attempts, delay):
        """After ``delay``, resubmit the delivery to the dispatcher. The timer
        holds no worker. The retry carries this claim token, so if the row has
        been claimed by someone else meanwhile its result is discarded; if the
        queue is full the row stays "retrying" and the periodic sweep recovers
        it."""
        app = current_app._get_current_object()

        def fire():
            executor = _get_delivery_executor(self.max_workers, self.per_org_limit, self.queue_max)
            future = executor.submit_for_org(
                org_id, self._continue_delivery, app, delivery_id, snap, headers, payload,
                org_id, token, next_number, max_attempts,
            )
            if future is None:
                app.logger.warning(
                    f"Webhook delivery queue full; retry {next_number} of {delivery_id} left to the sweep"
                )

        timer = threading.Timer(max(0.0, float(delay)), fire)
        timer.daemon = True
        timer.start()

    def _continue_delivery(self, app, delivery_id, snap, headers, payload, org_id,
                           token, attempt_number, max_attempts):
        """Worker body for a retry attempt after its backoff."""
        from app.jobs.tenant_safe_job import tenant_scope

        with app.app_context():
            try:
                with tenant_scope(org_id):
                    self._attempt_delivery(
                        delivery_id, snap, headers, payload, org_id=org_id, token=token,
                        max_attempts=max_attempts, attempt_number=attempt_number,
                    )
            except Exception as e:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                app.logger.error(f"Webhook retry attempt for {delivery_id} failed: {e}")
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass

    def get_events(self, limit: int = 50, offset: int = 0) -> List[WebhookEvent]:
        """Get webhook events (admin function)"""
        return (
            WebhookEvent.query.order_by(WebhookEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    def retry_event(self, event_id: str) -> bool:
        """Retry this event's failed deliveries, and deliveries left pending or
        retrying past the computed stale threshold (a worker lost with its
        process). Runs on the same bounded dispatcher and send path as first
        delivery: signed, with the subscription's custom headers, each row
        claimed atomically."""
        event = WebhookEvent.query.get(event_id)
        if not event:
            return False
        org_id = event.organization_id
        if org_id is None:
            current_app.logger.error(f"Webhook event {event_id} has no organization; no retry")
            return True
        self._delivery_future = _get_delivery_executor(
            self.max_workers, self.per_org_limit, self.queue_max
        ).submit_for_org(
            org_id,
            self._run_retry_in_app_context,
            current_app._get_current_object(),
            event_id,
            org_id,
        )
        if self._delivery_future is None:
            current_app.logger.warning(f"Webhook delivery queue full; retry of {event_id} not queued")
        return True

    def sweep_stale_deliveries(self, org_id):
        """Periodic recovery for one organisation (run under its tenant scope by
        the scheduler job): hand stale "pending"/"retrying" rows - queue
        overflow, work cancelled at exit, a dead worker's rows - to the same
        dispatcher and send path. Returns the queued Future, or None when there
        was nothing to do or the queue was full (the next sweep tries again)."""
        stale_row = (
            db.session.query(WebhookDelivery.id)
            .filter(
                WebhookDelivery.organization_id == org_id,
                self._retryable_clause(datetime.utcnow(), include_failed=False),
            )
            .first()
        )
        db.session.rollback()
        if stale_row is None:
            return None
        future = _get_delivery_executor(
            self.max_workers, self.per_org_limit, self.queue_max
        ).submit_for_org(
            org_id,
            self._run_retry_in_app_context,
            current_app._get_current_object(),
            None,
            org_id,
            False,
            SWEEP_BATCH_PER_ORG,
        )
        if future is None:
            current_app.logger.warning(
                f"Webhook delivery queue full; sweep for organisation {org_id} not queued"
            )
        return future

    def _run_retry_in_app_context(self, app, event_id, org_id, include_failed=True, limit=None):
        """Retry/sweep body. Rows are selected by joining to deliverable
        subscriptions (active, same organisation) BEFORE the limit, so rows on
        dead subscriptions can never crowd out live ones; those dead rows are
        marked failed instead of lingering."""
        from sqlalchemy import or_

        from app.jobs.tenant_safe_job import tenant_scope

        if org_id is None:
            app.logger.error(f"Webhook event {event_id} has no organization; no retry")
            return
        with app.app_context():
            try:
                with tenant_scope(org_id):
                    now = datetime.utcnow()
                    sub = WebhookSubscription
                    live_query = (
                        WebhookDelivery.query.join(sub, sub.id == WebhookDelivery.subscription_id)
                        .filter(
                            WebhookDelivery.organization_id == org_id,
                            self._retryable_clause(now, include_failed=include_failed),
                            sub.is_active.is_(True),
                            sub.organization_id == org_id,
                        )
                    )
                    dead_query = (
                        WebhookDelivery.query.outerjoin(sub, sub.id == WebhookDelivery.subscription_id)
                        .filter(
                            WebhookDelivery.organization_id == org_id,
                            self._retryable_clause(now, include_failed=False),
                            or_(sub.id.is_(None), sub.is_active.isnot(True),
                                sub.organization_id != org_id),
                        )
                    )
                    if event_id is not None:
                        live_query = live_query.filter(WebhookDelivery.event_id == event_id)
                        dead_query = dead_query.filter(WebhookDelivery.event_id == event_id)
                    live_query = live_query.order_by(WebhookDelivery.created_at)
                    dead_query = dead_query.order_by(WebhookDelivery.created_at)
                    if limit:
                        live_query = live_query.limit(int(limit))
                        dead_query = dead_query.limit(int(limit))
                    rows = live_query.all()
                    dead_ids = [d.id for d in dead_query.all()]
                    work = []
                    for delivery in rows:
                        sub_row = db.session.get(WebhookSubscription, delivery.subscription_id)
                        if self._deliverable(sub_row, org_id):
                            work.append((delivery.id, self._snapshot_subscription(sub_row),
                                         delivery.payload))
                        else:
                            dead_ids.append(delivery.id)
                    db.session.rollback()
                    db.session.remove()
                    for delivery_id in dead_ids:
                        self._fail_undeliverable(delivery_id, org_id, ("pending", "retrying"))
                    for delivery_id, snap, payload in work:
                        self._claim_and_attempt(
                            delivery_id, org_id, snap, payload,
                            retry=True, include_failed=include_failed,
                        )
            except Exception as e:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                app.logger.error(f"Webhook retry failed for event {event_id}: {e}")
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass

    def process_incoming_webhook(self, subscription_id: str, payload: Dict, headers: Dict) -> Dict:
        """Process an incoming webhook from external services"""
        # This route is unauthenticated (verified by the subscription's own HMAC
        # secret, not a session) -- there is no g.current_org_id to fall back on,
        # so the receiving subscription's own org is the only trustworthy source.
        subscription = self.get_subscription_by_id(subscription_id)
        # Store the incoming webhook event
        event = WebhookEvent(
            id=str(uuid.uuid4()),
            organization_id=subscription.organization_id if subscription else None,
            event_type="webhook.incoming",
            payload={"subscription_id": subscription_id, "payload": payload, "headers": headers},
            user_id=None,  # External webhook
            event_metadata={"incoming": True},
            created_at=datetime.utcnow(),
        )

        db.session.add(event)
        db.session.commit()

        # Here you could trigger internal workflows based on the webhook
        # For now, just log and return success
        current_app.logger.info(f"Processed incoming webhook for subscription {subscription_id}")

        return {"event_id": event.id, "processed_at": event.created_at.isoformat()}
