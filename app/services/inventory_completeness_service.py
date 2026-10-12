"""Which of an owner's applications are missing required facts or have gone stale.

The required facts are the ones the owner can enter on the application edit
form, so every finding can be fixed from the page that reports it. A fact is
missing when it is empty; nothing is filled in on the owner's behalf. A record
is stale when it was last changed more than ``STALE_AFTER_DAYS`` days ago; a
record with no change date is reported as such, not as stale.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

REQUIRED_FACTS = (
    ("description", "Description"),
    ("lifecycle_status", "Lifecycle status"),
    ("deployment_status", "Deployment status"),
    ("business_owner", "Business owner"),
    ("technical_owner", "Technical owner"),
)
STALE_AFTER_DAYS = 90


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def check_application(application, today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """One application's findings: missing facts, and how old the record is."""
    today = today or datetime.date.today()
    missing = [label for attr, label in REQUIRED_FACTS if _empty(getattr(application, attr, None))]
    changed = getattr(application, "updated_at", None)
    age_days = (today - changed.date()).days if changed is not None else None
    return {
        "id": application.id,
        "name": application.name,
        "missing": missing,
        "age_days": age_days,
        "stale": age_days is not None and age_days > STALE_AFTER_DAYS,
        "age_unknown": age_days is None,
    }


def check_applications(applications, today: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
    """Findings for each application that has any, most missing facts first."""
    findings = [check_application(a, today) for a in applications]
    flagged = [f for f in findings if f["missing"] or f["stale"] or f["age_unknown"]]
    flagged.sort(key=lambda f: (-len(f["missing"]), f["name"] or ""))
    return flagged
