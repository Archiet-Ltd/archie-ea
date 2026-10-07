"""Shared helpers for the SCIM provisioning and leaver tests (R1-B26 PR 1)."""

from __future__ import annotations

import json
import uuid

SCIM = "/scim/v2"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"


def make_user(db_session, org, label="user", *, administrator=False, enterprise_role="solution_architect", **extra):
    """A confirmed user of ``org``; ``administrator=True`` gives the Administrator role."""
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="Administrator" if administrator else "User").first()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
        **extra,
    )
    # Assigned after construction: the constructor picks the default Role when
    # the relationship is unset, which would override a role_id keyword.
    user.role = role
    db_session.add(user)
    db_session.flush()
    return user


def issue_token(db_session, org, created_by=None):
    """``(row, raw)`` for a fresh SCIM token of ``org``."""
    from app.services import provisioning_service

    row, raw = provisioning_service.issue_scim_token(org.id, created_by)
    return row, raw


def call(client, method, path, token=None, body=None, **kwargs):
    """One SCIM request. ``path`` is relative to ``/scim/v2``."""
    headers = dict(kwargs.pop("headers", {}))
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body)
        headers.setdefault("Content-Type", "application/scim+json")
    return client.open(SCIM + path, method=method, data=data, headers=headers, **kwargs)


def user_body(email=None, *, given="Pat", family="Person", external_id=None, active=None):
    body = {
        "schemas": [USER_SCHEMA],
        "userName": email or f"scim-{uuid.uuid4().hex[:8]}@example.com",
        "name": {"givenName": given, "familyName": family},
    }
    if external_id is not None:
        body["externalId"] = external_id
    if active is not None:
        body["active"] = active
    return body


def patch_body(*operations):
    return {"schemas": [PATCH_SCHEMA], "Operations": list(operations)}


def give_unlimited_plan(db_session, org):
    """Put ``org`` on a plan with no people limit, so a test can seed more
    than the Community plan's three users."""
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    db_session.add(Subscription(
        organization_id=org.id, plan=SubscriptionPlan.enterprise,
        status=SubscriptionStatus.active, seats_purchased=500,
    ))
    db_session.flush()
    return org
