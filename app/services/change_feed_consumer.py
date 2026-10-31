"""Turn the organisation's change events into bell notifications for owners.

One function, ``consume_changes``, reads the organisation's event log from its
stored offset (``event_log_service.read_from_offset``), asks the rules in
``app/services/change_rules.py`` who should be told, writes ``Notification``
rows, and moves the offset in the same transaction. The offset only advances
when the rows commit, and a notification already written for an event is never
written again, so replaying a range is harmless.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.change_feed_offset import ChangeFeedOffset
from app.models.models import Notification
from app.models.user import User
from app.services import change_rules
from app.services.event_log_service import read_from_offset
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

CONSUMER = "owner_notifications"
BATCH_SIZE = 200


def _locked_offset(organization_id: int) -> ChangeFeedOffset:
    """The organisation's offset row, locked for this transaction."""
    def find():
        return db.session.execute(
            db.select(ChangeFeedOffset)
            .where(ChangeFeedOffset.organization_id == organization_id)
            .where(ChangeFeedOffset.consumer == CONSUMER)
            .with_for_update()
        ).scalar_one_or_none()

    row = find()
    if row is not None:
        return row
    savepoint = db.session.begin_nested()
    try:
        db.session.add(
            ChangeFeedOffset(organization_id=organization_id, consumer=CONSUMER, last_ordinal=0)
        )
        db.session.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()  # another worker created it first
    return find()


def _users_in(organization_id: int, candidates: List[Dict]) -> set:
    wanted = {c["user_id"] for c in candidates}
    if not wanted:
        return set()
    return set(
        db.session.execute(
            db.select(User.id)
            .where(User.organization_id == organization_id)
            .where(User.id.in_(wanted))
        ).scalars()
    )


def write_notifications(organization_id: int, planned: List[Dict]) -> int:
    """Write the planned notifications that are new; returns how many.

    Each link ends in a fragment that names what it is about: the alert key when
    the rule gives one (the same date or condition, found by an edit or by the
    daily check, is one alert), otherwise the event id. A notification already
    written with the same link is never written again, so a replay or a second
    daily run is a no-op. Adds without committing; the caller commits.
    """
    people = _users_in(organization_id, planned)
    written = 0
    for candidate in planned:
        if candidate["user_id"] not in people:
            continue  # never tell someone outside the organisation
        if candidate.get("alert_key"):
            url = f"{candidate['url']}#alert-{candidate['alert_key']}"
        else:
            url = f"{candidate['url']}#change-{candidate['event_id']}"
        already = db.session.execute(
            db.select(Notification.id)
            .where(Notification.user_id == candidate["user_id"])
            .where(Notification.organization_id == organization_id)
            .where(Notification.url == url)
            .limit(1)
        ).first()
        if already is not None:
            continue
        # The one writer of notifications; it adds without committing, so the
        # notifications and the offset advance in the same transaction.
        NotificationService.create(
            candidate["user_id"],
            candidate["message"],
            url,
            organization_id=organization_id,
            event_type=candidate["event_type"],
            entity_type=candidate["entity_type"],
            entity_id=candidate["entity_id"],
        )
        written += 1
    return written


def _consume_batch(organization_id: int, batch_size: int) -> int:
    """One batch: write its notifications and move the offset, in one commit.

    Returns the number of events read (0 means the log is caught up).
    """
    offset = _locked_offset(organization_id)
    events = read_from_offset(organization_id, offset.last_ordinal, limit=batch_size)
    if not events:
        db.session.commit()
        return 0

    planned = []
    for event in events:
        for candidate in change_rules.candidates_for(event):
            planned.append({**candidate, "event_id": event["event_id"]})
    write_notifications(organization_id, planned)

    offset.last_ordinal = max(offset.last_ordinal, events[-1]["ordinal"])
    db.session.commit()
    return len(events)


def consume_changes(organization_id: int, *, batch_size: int = BATCH_SIZE) -> int:
    """Notify owners of every change not yet seen; returns events consumed.

    Safe to run again at any time, from any number of workers: the offset row
    is locked per organisation, and a failed batch rolls back whole.
    """
    consumed = 0
    while True:
        try:
            count = _consume_batch(organization_id, batch_size)
        except Exception:
            db.session.rollback()
            logger.exception("change feed: batch failed for organisation %s", organization_id)
            raise
        consumed += count
        if count < batch_size:
            return consumed


__all__ = ["BATCH_SIZE", "CONSUMER", "consume_changes", "write_notifications"]
