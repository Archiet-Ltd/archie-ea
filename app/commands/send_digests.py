"""Per-owner notification digest sender.

Sends a digest of unread in-app notifications to each owner who has opted in.
Runs per organisation through the tenant-safe job harness.

Idempotent per day: a second run on the same day sends nothing.
A digest with nothing to report is not sent (logged as skipped).
Respects weekly_digest and digest_frequency preferences.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from html import escape

from app import db

logger = logging.getLogger(__name__)


def _get_owners_with_items(organization_id: int):
    """Return (user, [notifications]) for each owner in this org who has
    unread notifications that have not yet been included in a digest."""
    from app.models.models import Notification
    from app.models.user import User

    today = date.today()
    today_start = datetime(today.year, today.month, today.day)

    # Find users in this org who have unread notifications not yet digested
    rows = (
        db.session.query(User, Notification)
        .filter(
            User.organization_id == organization_id,
            Notification.user_id == User.id,
            Notification.read.is_(False),
            Notification.email_sent_at.is_(None),
        )
        .all()
    )
    return rows


def _should_send_digest(user):
    """Check whether this user should receive a digest based on preferences."""
    # weekly_digest=False means no digest at all
    if not user.get_notification_preference("weekly_digest"):
        return False

    # digest_frequency=off means no digest
    freq = user.get_notification_preference("digest_frequency")
    if freq == "off":
        return False

    return True


def _already_sent_today(user_id: int) -> bool:
    """Check if a digest was already sent to this user today."""
    from app.models.models import Notification

    today = date.today()
    today_start = datetime(today.year, today.month, today.day)
    tomorrow_start = today_start + timedelta(days=1)

    sent = (
        db.session.query(Notification.id)
        .filter(
            Notification.user_id == user_id,
            Notification.email_sent_at >= today_start,
            Notification.email_sent_at < tomorrow_start,
        )
        .first()
    )
    return sent is not None


def send_owner_digest(organization_id: int) -> dict:
    """Send one per-owner digest for *organization_id*.

    Returns a dict with keys:
      - sent: number of digests actually sent
      - skipped: True if no digest was needed
      - total_items: number of notification items found
      - errors: list of error messages
    """
    from app._bootstrap._digest_emails import _safe_send_email
    from app.models.user import User

    result = {"sent": 0, "skipped": False, "total_items": 0, "errors": []}

    # Get all users in this org who have undigested notifications
    owner_rows = _get_owners_with_items(organization_id)
    if not owner_rows:
        result["skipped"] = True
        logger.info("Owner digest: org %s has no undigested notifications", organization_id)
        return result

    # Group by user
    user_notifications = {}
    for user, notif in owner_rows:
        if user.id not in user_notifications:
            user_notifications[user.id] = {"user": user, "items": []}
        user_notifications[user.id]["items"].append(notif)

    result["total_items"] = len(owner_rows)

    for uid, data in user_notifications.items():
        user = data["user"]
        items = data["items"]

        try:
            # Check preferences
            if not _should_send_digest(user):
                logger.info(
                    "Owner digest: user %s in org %s opted out (preferences)",
                    user.id, organization_id,
                )
                continue

            # Check idempotency
            if _already_sent_today(user.id):
                logger.info(
                    "Owner digest: user %s in org %s already sent today",
                    user.id, organization_id,
                )
                continue

            # Build and send the digest
            items_html = "".join(
                f"<li><a href=\"{escape(item.url or '#')}\">{escape(item.message[:200])}</a></li>"
                for item in items[:20]
            )
            if not items_html:
                items_html = "<li>No new notifications.</li>"

            html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1f2937;max-width:640px;margin:0 auto;padding:16px">
<h2 style="color:#111827;border-bottom:2px solid #2563eb;padding-bottom:8px">
    Your Notification Digest
</h2>
<p style="color:#6b7280">You have {len(items)} unread notification(s):</p>
<ul>{items_html}</ul>
<p style="font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:8px">
    This is an automated digest from Entelim. To change your notification preferences,
    visit your account settings.
</p>
</body>
</html>"""

            from flask import current_app
            _safe_send_email(
                current_app._get_current_object(),
                "Your Notification Digest",
                [user.email],
                html,
            )

            # Mark notifications as emailed
            now = datetime.utcnow()
            for item in items:
                item.email_sent_at = now
            db.session.commit()

            result["sent"] += 1
            logger.info(
                "Owner digest: sent %d items to user %s in org %s",
                len(items), user.id, organization_id,
            )

        except Exception as exc:
            logger.exception(
                "Owner digest: failed for user %s in org %s: %s",
                user.id, organization_id, exc,
            )
            result["errors"].append(str(exc))

    if result["sent"] == 0 and not result["errors"]:
        result["skipped"] = True

    return result