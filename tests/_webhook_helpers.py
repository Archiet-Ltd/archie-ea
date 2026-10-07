"""Shared helpers for the webhook delivery tests.

* ``FakeTransport`` replaces ``requests.post`` so no test makes a network call; it
  records exactly what would have been sent (URL, body bytes, headers, timeout,
  redirect flag).
* ``install_dns`` replaces the resolver the private-address guard uses, because the
  guard cannot be switched off and so must be fed resolver answers instead.
* ``emit_events`` puts events in an organisation's event log the way production
  does: ``emit_event`` writes the outbox, the relay copies it to ``event_log``.
"""

from __future__ import annotations

import json
import socket
import types
import uuid

PUBLIC_IP = "93.184.216.34"


class FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None):
        self.status_code = status_code
        self.text = f"status {status_code}"
        self.headers = headers or {}


class FakeTransport:
    """A stand-in for ``requests.post``; *handler(call)* returns a status or an exception."""

    def __init__(self, handler=None):
        self.calls: list[dict] = []
        self.handler = handler or (lambda call: 200)

    def post(self, url, data=None, headers=None, timeout=None, allow_redirects=None, **kwargs):
        call = {
            "url": url,
            "data": data,
            "headers": dict(headers or {}),
            "timeout": timeout,
            "allow_redirects": allow_redirects,
            "kwargs": kwargs,
        }
        self.calls.append(call)
        outcome = self.handler(call)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)

    def sequences(self) -> list[int]:
        return [int(json.loads(call["data"])["sequence"]) for call in self.calls]


def install_dns(monkeypatch, mapping: dict | None = None) -> None:
    """Resolve every host to a public address unless *mapping* says otherwise."""
    answers = mapping or {}

    def getaddrinfo(host, port, *args, **kwargs):
        address = answers.get(host, PUBLIC_IP)
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, port or 443))]

    shim = types.SimpleNamespace(getaddrinfo=getaddrinfo, gaierror=socket.gaierror)
    monkeypatch.setattr("app.utils.ssrf_guard.socket", shim)


def install_transport(monkeypatch, handler=None, dns: dict | None = None) -> FakeTransport:
    transport = FakeTransport(handler)
    monkeypatch.setattr("app.services.webhook_service.requests.post", transport.post)
    install_dns(monkeypatch, dns)
    return transport


def install_guards(app) -> None:
    """Re-install the transformation DB guards, as tests/test_event_log.py does."""
    from app import db
    from app.models.transformation_db_guards import ensure_transformation_db_guards

    with app.app_context(), db.engine.begin() as connection:
        ensure_transformation_db_guards(connection)


def emit_events(org_id: int, count: int, *, start: int = 1, event_type: str = "archimate_element.created") -> None:
    """Emit *count* catalogued events for *org_id* and relay them into the event log."""
    from app import db
    from app.services.event_log_service import relay_outbox_batch
    from app.services.outbox import emit_event

    for number in range(start, start + count):
        emit_event(
            organization_id=org_id,
            event_type=event_type,
            payload={"action": "created", "id": number},
            entity_type="archimate_element",
            entity_id=number,
        )
    db.session.commit()
    relay_outbox_batch()
    db.session.commit()


def make_org_user(db_session, org, *, is_org_admin=True, is_platform_admin=False):
    """A confirmed administrator of *org*."""
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"wh-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Web",
        last_name="Hook",
        organization_id=org.id,
        role=role,
        is_org_admin=is_org_admin,
        is_platform_admin=is_platform_admin,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def make_subscription(service, org_id, url="https://hooks.example.com/in", **kwargs):
    """Create a generic subscription through the one writer, in *org_id*'s context."""
    kwargs.setdefault("events", ["*"])
    return service.create_subscription(
        user_id="1", url=url, organization_id=org_id, **kwargs
    )
