"""The one reader and writer of Organization.settings["onboarding"].

Keeps profile facts recorded during onboarding in one place, so a future
module (the company-context reading engine) has a consistent shape to read
from rather than a second store. Never writes any other key under
`settings`.
"""
from __future__ import annotations

from app import db
from app.models.organization import Organization

_KEY = "onboarding"


def read(org: Organization) -> dict:
    settings = org.settings or {}
    return dict(settings.get(_KEY, {}))


def write(org: Organization, **fields) -> dict:
    """Merge *fields* into Organization.settings["onboarding"] and persist."""
    settings = dict(org.settings or {})
    current = dict(settings.get(_KEY, {}))
    current.update({k: v for k, v in fields.items() if v is not None})
    settings[_KEY] = current
    org.settings = settings
    db.session.add(org)
    db.session.commit()
    return current
