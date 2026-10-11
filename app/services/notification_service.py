"""Single writer for the in-app notification store.

Every ``Notification`` row is created through this service. No other platform
code constructs ``Notification(`` directly — see the gate in
``tests/test_notification_store_pr1.py``.
"""

from __future__ import annotations

import logging
from typing import Optional

from app import db

logger = logging.getLogger(__name__)


class NotificationService:
    """Single writer for the ``notifications`` table.

    Callers (governance_notifier, ea_workflow_engine) import and call
    ``NotificationService.create(...)`` instead of constructing ``Notification``
    directly.
    """

    @staticmethod
    def create(
        user_id: int,
        message: str,
        url: Optional[str] = None,
        *,
        organization_id: Optional[int] = None,
        event_type: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[int] = None,
    ):
        """Create and persist one in-app notification.

        When ``organization_id`` is not supplied it is resolved from the
        recipient user's own organisation. The notifications table is under
        row-level security and the application runs as a NON-BYPASSRLS role, so
        a row must carry the session organisation or the INSERT is refused and
        the bell query hides it. Resolving from the recipient user keeps a
        caller that has not (or cannot) supply an organisation from silently
        producing a notification nobody can see.

        Returns the new ``Notification`` instance.
        """
        from app.models.models import Notification
        from app.models.user import User

        if organization_id is None:
            recipient = db.session.get(User, user_id)
            organization_id = recipient.organization_id if recipient else None

        notif = Notification(
            user_id=user_id,
            message=message,
            url=url,
            organization_id=organization_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        db.session.add(notif)
        return notif