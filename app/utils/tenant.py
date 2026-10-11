"""Shared tenant-context helpers.

``g.current_org_id`` is what the tenant-isolation listeners key off
(CLAUDE.md "Multi-tenancy is implicit"), and is what
``run_for_each_tenant``'s per-tenant loop restores when it finishes, so
reading it here (rather than ``current_user.organization`` -- an ORM
relationship) is both the correct source and avoids holding an object
reference across the recompute call.
"""
from __future__ import annotations

from flask import g
from flask_login import current_user


def current_organization_id() -> int | None:
    """The plain int this request belongs to -- never an ORM object."""
    org_id = getattr(g, "current_org_id", None)
    if org_id is not None:
        return int(org_id)
    org_id = getattr(current_user, "organization_id", None)
    return int(org_id) if org_id is not None else None