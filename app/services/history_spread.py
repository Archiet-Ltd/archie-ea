"""Give a demonstration or sample organisation's model records their dates as they are written.

A demonstration or sample organisation is built in one sitting, so every record in its change history
would carry the same day and the Overview's chart of records added would draw one bar. Inside
``dated_history`` each record's first version is written with a date across the last 90 days instead,
and with the source it names.

The change history is the hash-chained audit log. A row is sealed once, when it is written, in order;
nothing here edits or seals a row afterwards. The audit writer reads the date from the block it is
written in (``app.services.change_source.dated_when_written``).
"""
from __future__ import annotations

import datetime as _dt
from contextlib import contextmanager

from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value

from app.services.change_source import dated_when_written

# Days before today on which the model's records were added, repeated down the list of records. Every
# third record names a connected source, the rest were added by hand.
HISTORY_DAYS_AGO = (0, 1, 2, 4, 5, 8, 11, 12, 15, 19, 23, 27, 31, 36, 40, 47, 53, 58, 66, 74, 83)

MODEL_TABLES = ("archimate_elements", "archimate_relationships")
HAND_LABEL = "Entered by the team"
CONNECTED_SOURCE = ("connector", "GitHub")


class HistoryDating:
    """Dates and sources for the records written inside one ``dated_history`` block."""

    def __init__(self, days_ago=HISTORY_DAYS_AGO, now=None, rest=None, connected=CONNECTED_SOURCE):
        self.days_ago = tuple(days_ago)
        self.now = now or _dt.datetime.utcnow()
        # the (kind, label) every record that is not connected names; None leaves it unsourced
        self.rest = rest
        # the (kind, label) every third record names; None gives every record the ``rest`` source
        self.connected = connected
        self.written = 0

    def date_new_record(self, connection, table_label, record):
        """The date for ``record``'s first version, or None for a table this audit
        listener does not control (so this is never asked about it in the first place).

        A model record that names no source yet is given one, on the record itself and so in the
        first version written for it.
        """
        from app.middleware.audit_middleware import CONTROLLED_TABLE_LABELS

        if table_label not in CONTROLLED_TABLE_LABELS:
            return None
        index = self.written
        self.written += 1
        if table_label in MODEL_TABLES and getattr(record, "source_kind", None) is None:
            if self.connected and index % 3 == 0:
                source = self.connected
            else:
                source = self.rest
            if source:
                kind, label = source
                connection.execute(
                    update(type(record).__table__)
                    .where(
                        type(record).__table__.c.id == record.id,
                        type(record).__table__.c.organization_id == record.organization_id,
                    )
                    .values(source_kind=kind, source_label=label)
                )
                set_committed_value(record, "source_kind", kind)
                set_committed_value(record, "source_label", label)
        return self.now - _dt.timedelta(days=self.days_ago[index % len(self.days_ago)], hours=index % 5)


@contextmanager
def dated_history(days_ago=HISTORY_DAYS_AGO, now=None, rest=None, connected=CONNECTED_SOURCE):
    """Write the model records created inside the block with their dates spread over the last 90 days."""
    dating = HistoryDating(days_ago=days_ago, now=now, rest=rest, connected=connected)
    with dated_when_written(dating):
        yield dating
