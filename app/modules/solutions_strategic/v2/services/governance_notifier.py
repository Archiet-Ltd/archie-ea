"""Proactive governance push (PROG-019).

When an AI governance pass (EA Briefing, import review, stewardship) produces
HIGH/critical findings, the people who can act on them shouldn't have to go
looking. GovernanceNotifier turns flagged findings into in-app Notifications for
the right audience and, when SMTP is configured, an email digest.

Deterministic and idempotent: one summary notification per user per source
(deduped against the user's recent unread notifications), so re-running a briefing
doesn't spam. Fully guarded — a push can never break the pass that triggered it.
"""

import logging
from datetime import datetime, timedelta
from html import escape
from typing import Any, Dict, List, Optional, Sequence

from app import db

logger = logging.getLogger(__name__)

_FLAGGED = ("critical", "high")
_DEFAULT_ROLES = ("platform_admin", "enterprise_architect")
# don't re-notify a user with the same message inside this window
_DEDUPE_HOURS = 12


class GovernanceNotifier:
    """Push flagged governance findings to the people who can act on them."""

    @classmethod
    def push_findings(
        cls,
        findings: Sequence[Dict[str, Any]],
        source_label: str,
        source_url: str,
        audience_roles: Sequence[str] = _DEFAULT_ROLES,
        extra_user_ids: Optional[Sequence[int]] = None,
        send_email: bool = True,
    ) -> Dict[str, Any]:
        """Notify the audience about HIGH/critical ``findings``.

        Returns a small report dict. Never raises.
        """
        try:
            flagged = [f for f in (findings or []) if f.get("severity") in _FLAGGED]
            if not flagged:
                return {"pushed": 0, "notified_users": 0, "emailed": False,
                        "reason": "no flagged findings"}

            user_ids = cls._audience_user_ids(audience_roles, extra_user_ids)
            if not user_ids:
                return {"pushed": 0, "notified_users": 0, "emailed": False,
                        "reason": "no audience"}

            top = flagged[0].get("title", "a governance finding")
            n = len(flagged)
            message = (
                f"{source_label}: {n} high-priority finding"
                f"{'s' if n != 1 else ''} need attention — e.g. \"{top}\"."
            )

            notified = cls._create_notifications(user_ids, message, source_url)
            emailed = cls._email_digest(source_label, flagged, source_url) if send_email else False

            db.session.commit()
            logger.info(
                "GovernanceNotifier: pushed %d flagged finding(s) from %s to %d user(s) (emailed=%s)",
                n, source_label, notified, emailed,
            )
            return {"pushed": n, "notified_users": notified, "emailed": emailed}
        except Exception as exc:  # noqa: BLE001 — a push must never break the caller
            logger.error("GovernanceNotifier push failed (caller unaffected): %s", exc)
            try:
                db.session.rollback()
            except Exception as rb_exc:
                logger.debug("rollback after push failure also failed: %s", rb_exc)
            return {"pushed": 0, "notified_users": 0, "emailed": False, "error": str(exc)}

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _audience_user_ids(roles, extra_user_ids) -> List[int]:
        from flask import g
        from app.models.user import User

        ids = set()
        try:
            # tenant-scoping-ok when g.current_org_id is set: restrict the
            # audience to the org whose finding triggered this push, or every
            # org's platform_admin/enterprise_architect gets emailed about
            # another tenant's data. Falls back to unscoped only outside a
            # request context (no cross-tenant notion applies there).
            org_id = getattr(g, "current_org_id", None)
            q = User.query.filter(User.enterprise_role.in_(list(roles)))
            if org_id is not None:
                q = q.filter(User.organization_id == org_id)
            for u in q.all():
                ids.add(u.id)
        except Exception as exc:
            logger.debug("audience role query failed: %s", exc)
        for uid in (extra_user_ids or []):
            if uid:
                ids.add(int(uid))
        return sorted(ids)

    @classmethod
    def _create_notifications(cls, user_ids, message, url) -> int:
        from app.models.models import Notification

        cutoff = datetime.utcnow() - timedelta(hours=_DEDUPE_HOURS)
        created = 0
        for uid in user_ids:
            # dedupe: skip if an identical unread notification was made recently
            recent = (
                Notification.query
                .filter(Notification.user_id == uid,
                        Notification.message == message,
                        Notification.created_at >= cutoff)
                .first()
            )
            if recent:
                continue
            db.session.add(Notification(user_id=uid, message=message, url=url))
            created += 1
        return created

    @staticmethod
    def _email_digest(source_label, flagged, url) -> bool:
        try:
            from flask import current_app, g
            from app._bootstrap._digest_emails import _safe_send_email
            from app.models.user import User

            # tenant-scoping-ok when g.current_org_id is set — see
            # _audience_user_ids above for why this must not be global.
            org_id = getattr(g, "current_org_id", None)
            _q = User.query.filter(User.enterprise_role.in_(list(_DEFAULT_ROLES)))
            if org_id is not None:
                _q = _q.filter(User.organization_id == org_id)
            recipients = [u.email for u in _q.all() if getattr(u, "email", None)]
            if not recipients:
                return False
            # finding titles come from AI governance analysis of real data
            # (application/capability names etc.) -- freeform, no character
            # restriction. source_label/url are caller-supplied strings too;
            # escape all of it before it reaches this HTML email.
            items = "".join(
                f"<li><b>{escape(f.get('severity', '').upper())}</b> — {escape(str(f.get('title', '')))}</li>"
                for f in flagged[:10]
            )
            html = (
                f"<h2>{escape(source_label)}: {len(flagged)} finding(s) need attention</h2>"
                f"<ul>{items}</ul>"  # raw-html-ok: items is built entirely from escape()'d parts above
                f"<p><a href=\"{escape(url)}\">Open in Entelim</a></p>"
            )
            # degrades to logging if SMTP is unconfigured — never raises
            return bool(_safe_send_email(
                current_app._get_current_object(),
                f"[Entelim] {source_label}: {len(flagged)} finding(s) need attention",
                recipients, html,
            ))
        except Exception as exc:
            logger.debug("governance email digest skipped: %s", exc)
            return False
