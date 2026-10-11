"""Single writer for the in-app notification store.

Every ``Notification`` row is created through this service. No other platform
code constructs ``Notification(`` directly — see the gate in
``tests/test_notification_store_pr1.py``.
"""

from __future__ import annotations

import logging
from datetime import datetime
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

        Returns the new ``Notification`` instance.
        """
        from app.models.models import Notification

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