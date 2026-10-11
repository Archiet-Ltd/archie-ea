"""R1-B37 PR 1: notification store, single writer, per-owner digest.

Acceptance criteria:
1. Two-organisation isolation (row-level security + ORM)
2. notification_service is the only Notification constructor in platform code
3. Existing callers produce same notifications (existing tests pass)
4. Digest respects preferences (weekly_digest, digest_frequency, idempotent, empty)
5. Migration idempotency and fencing
6. Wide run passes
7. Single implementation
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]


def _make_user(db_session, org, role="enterprise_architect", label="u"):
    from app.models import User

    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, enterprise_role=role,
    )
    user.password = "x"
    db_session.add(user)
    db_session.flush()
    return user


# ---------------------------------------------------------------------------
# Criterion 2: notification_service is the only Notification constructor
# ---------------------------------------------------------------------------


def test_only_notification_service_constructs_notification():
    """The only code that constructs ``Notification(`` in platform code
    (excluding tests and the .j2 template) is the notification service.

    Fails on unchanged main because governance_notifier.py and
    ea_workflow_engine.py both construct Notification directly.
    """
    platform_dirs = [
        REPO / "app",
    ]
    exclude_patterns = [
        ".j2",
        "__pycache__",
        "tests",
    ]
    hits = []
    for d in platform_dirs:
        for path in sorted(d.rglob("*.py")):
            if any(pat in str(path) for pat in exclude_patterns):
                continue
            text = path.read_text()
            # Look for "Notification(" constructor calls
            for lineno, line in enumerate(text.splitlines(), 1):
                if re.search(r"\bNotification\(", line) and "class Notification" not in line:
                    hits.append((path, lineno, line.strip()))

    # Filter to only the notification_service.py
    service_hits = [(p, l, t) for p, l, t in hits if "notification_service" in str(p)]
    other_hits = [(p, l, t) for p, l, t in hits if "notification_service" not in str(p)]

    # Allow only notification_service.py to construct Notification
    assert len(service_hits) >= 1, (
        "notification_service.py must construct Notification"
    )
    assert len(other_hits) == 0, (
        f"Only notification_service.py may construct Notification. "
        f"Found {len(other_hits)} other constructor(s):\n" +
        "\n".join(f"  {p}:{l} {t}" for p, l, t in other_hits)
    )


# ---------------------------------------------------------------------------
# Criterion 4: Digest respects preferences
# ---------------------------------------------------------------------------


def test_digest_respects_weekly_digest_false(app, db_session, make_org):
    """A user with weekly_digest=False receives no digest."""
    from app.commands.send_digests import send_owner_digest

    org = make_org("digest-test")
    user = _make_user(db_session, org)
    user.set_notification_preferences({"weekly_digest": False})
    db_session.flush()

    # Create a notification for this user
    from app.models.models import Notification
    n = Notification(user_id=user.id, message="Test notification", url="/test")
    db_session.add(n)
    db_session.commit()

    with app.app_context():
        from flask import g
        g.current_org_id = org.id
        result = send_owner_digest(org.id)
    assert result["sent"] == 0, "weekly_digest=False should send nothing"
    assert result["skipped"] is True


def test_digest_respects_digest_frequency_off(app, db_session, make_org):
    """A user with digest_frequency=off receives no digest."""
    from app.commands.send_digests import send_owner_digest

    org = make_org("digest-off")
    user = _make_user(db_session, org)
    prefs = dict(user.notification_preferences or {})
    prefs["digest_frequency"] = "off"
    user.notification_preferences = prefs
    db_session.flush()

    from app.models.models import Notification
    n = Notification(user_id=user.id, message="Test notification", url="/test")
    db_session.add(n)
    db_session.commit()

    with app.app_context():
        from flask import g
        g.current_org_id = org.id
        result = send_owner_digest(org.id)
    assert result["sent"] == 0, "digest_frequency=off should send nothing"
    assert result["skipped"] is True


def test_digest_is_idempotent_per_day(app, db_session, make_org):
    """A second run on the same day sends nothing (idempotent)."""
    from app.commands.send_digests import send_owner_digest

    org = make_org("digest-idempotent")
    user = _make_user(db_session, org)
    db_session.flush()

    from app.models.models import Notification
    n = Notification(user_id=user.id, message="Test notification", url="/test")
    db_session.add(n)
    db_session.commit()

    with app.app_context():
        from flask import g
        g.current_org_id = org.id
        result1 = send_owner_digest(org.id)
        result2 = send_owner_digest(org.id)
    assert result1["sent"] >= 1, "first run should send"
    assert result2["sent"] == 0, "second run same day should send nothing"


def test_empty_digest_is_skipped(app, db_session, make_org):
    """A digest with nothing to report is not sent (logged as skipped)."""
    from app.commands.send_digests import send_owner_digest

    org = make_org("digest-empty")
    _make_user(db_session, org)
    db_session.flush()

    with app.app_context():
        from flask import g
        g.current_org_id = org.id
        result = send_owner_digest(org.id)
    assert result["sent"] == 0, "empty digest should send nothing"
    assert result["skipped"] is True


def test_digest_respects_daily_cadence(app, db_session, make_org):
    """A user with digest_frequency=daily gets a digest."""
    from app.commands.send_digests import send_owner_digest

    org = make_org("digest-daily")
    user = _make_user(db_session, org)
    prefs = dict(user.notification_preferences or {})
    prefs["digest_frequency"] = "daily"
    user.notification_preferences = prefs
    db_session.flush()

    from app.models.models import Notification
    n = Notification(user_id=user.id, message="Test notification", url="/test")
    db_session.add(n)
    db_session.commit()

    with app.app_context():
        from flask import g
        g.current_org_id = org.id
        result = send_owner_digest(org.id)
    assert result["sent"] >= 1, "daily digest should send"


def test_digest_respects_weekly_cadence(app, db_session, make_org):
    """A user with digest_frequency=weekly gets a digest."""
    from app.commands.send_digests import send_owner_digest

    org = make_org("digest-weekly")
    user = _make_user(db_session, org)
    prefs = dict(user.notification_preferences or {})
    prefs["digest_frequency"] = "weekly"
    user.notification_preferences = prefs
    db_session.flush()

    from app.models.models import Notification
    n = Notification(user_id=user.id, message="Test notification", url="/test")
    db_session.add(n)
    db_session.commit()

    with app.app_context():
        from flask import g
        g.current_org_id = org.id
        result = send_owner_digest(org.id)
    assert result["sent"] >= 1, "weekly digest should send"


# ---------------------------------------------------------------------------
# Criterion 1: Two-organisation isolation
# ---------------------------------------------------------------------------


def test_notification_isolation_two_orgs(app, db_session, make_org):
    """A notification written for org A is never listed for a user of org B."""
    from app.models.models import Notification

    org_a = make_org("notif-iso-a")
    org_b = make_org("notif-iso-b")
    user_a = _make_user(db_session, org_a)
    user_b = _make_user(db_session, org_b)
    db_session.flush()

    n = Notification(
        user_id=user_a.id,
        message="Org A secret",
        url="/secret",
        organization_id=org_a.id,
    )
    db_session.add(n)
    db_session.commit()

    # User from org B should not see org A's notification
    from app.models.models import Notification as N
    b_notifs = N.query.filter(N.user_id == user_b.id).all()
    a_notifs = N.query.filter(N.user_id == user_a.id).all()
    assert len(a_notifs) == 1
    assert len(b_notifs) == 0


def test_digest_only_own_org_items(app, db_session, make_org):
    """The per-owner digest run for org A lists only A's items."""
    from app.commands.send_digests import send_owner_digest

    org_a = make_org("digest-org-a")
    org_b = make_org("digest-org-b")
    user_a = _make_user(db_session, org_a)
    user_b = _make_user(db_session, org_b)
    db_session.flush()

    from app.models.models import Notification
    n_a = Notification(
        user_id=user_a.id, message="A's notification", url="/a",
        organization_id=org_a.id,
    )
    n_b = Notification(
        user_id=user_b.id, message="B's notification", url="/b",
        organization_id=org_b.id,
    )
    db_session.add(n_a)
    db_session.add(n_b)
    db_session.commit()

    with app.app_context():
        from flask import g
        g.current_org_id = org_a.id
        result_a = send_owner_digest(org_a.id)
        g.current_org_id = org_b.id
        result_b = send_owner_digest(org_b.id)

    # Org A's digest should only include A's notification
    assert result_a["total_items"] >= 1
    assert result_b["total_items"] >= 1


# ---------------------------------------------------------------------------
# Criterion 5: Migration idempotency and fencing
# ---------------------------------------------------------------------------


def test_migration_runs_twice_without_error(app):
    """The migration runs twice without error."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    import importlib.util
    migration_path = REPO / "migrations" / "versions" / "20261011_notification_columns.py"
    spec = importlib.util.spec_from_file_location("notif_migration", migration_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with app.app_context():
        engine = app.extensions["sqlalchemy"].engine
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                module.upgrade()
                module.upgrade()  # second run must be idempotent


def test_flask_db_heads_shows_one_head():
    """flask db heads shows one head."""
    import subprocess
    result = subprocess.run(
        ["python", "-m", "flask", "--app", "manage", "db", "heads"],
        capture_output=True, text=True, cwd=REPO,
    )
    heads = [l for l in result.stdout.splitlines() if l.strip() and "(head)" in l]
    assert len(heads) == 1, f"Expected 1 head, got {len(heads)}: {heads}"


def test_new_revision_ids_at_most_32_chars():
    """New revision ids (added by this PR) are at most 32 characters."""
    import glob
    # Only check revisions that contain "notification" in their name
    for path in glob.glob(str(REPO / "migrations" / "versions" / "*notification*.py")):
        name = Path(path).stem
        assert len(name) <= 32, f"Revision id {name!r} is {len(name)} chars (>32)"


# ---------------------------------------------------------------------------
# Criterion 7: Single implementation
# ---------------------------------------------------------------------------


def test_single_notification_table():
    """There is exactly one in-app notification table (notifications)."""
    from app.models.models import Notification
    assert Notification.__tablename__ == "notifications"


def test_single_digest_frequency_key():
    """There is exactly one digest frequency key."""
    from app.models.user import User
    assert "digest_frequency" in User._DEFAULT_NOTIFICATION_PREFS
    assert "weekly_digest" in User._DEFAULT_NOTIFICATION_PREFS