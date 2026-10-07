"""The unauthenticated client-error beacon cannot rewrite an event's recorded stack.

``/api/client-error`` is deliberately open (it must catch errors on the login page and from
expired sessions) and deduplicates by a fingerprint of location and message, so a repeat report
bumps the existing event's count. Before the fix a repeat report also replaced the stored stack,
so any caller who knew a location and message could overwrite the stack an operator would read.
"""

from __future__ import annotations

import uuid


def _report(client, location, message, stack):
    return client.post(
        "/api/client-error",
        json={"message": message, "location": location, "stack": stack, "url": "https://example.test/"},
    )


def test_a_repeat_report_counts_but_keeps_the_first_recorded_stack(app, client, db_session):
    from app.models.error_event import ErrorEvent

    location = f"app.js:{uuid.uuid4().hex[:6]}"
    message = "Cannot read properties of undefined"

    assert _report(client, location, message, "first stack: real frames").status_code == 204
    assert _report(client, location, message, "second stack: attacker text").status_code == 204

    events = ErrorEvent.query.filter_by(location=location).all()
    assert len(events) == 1
    assert events[0].occurrence_count == 2
    assert events[0].stack == "first stack: real frames"


def test_a_first_report_without_a_stack_can_still_gain_one(app, client, db_session):
    from app.models.error_event import ErrorEvent

    location = f"app.js:{uuid.uuid4().hex[:6]}"
    message = "Script error"

    _report(client, location, message, None)
    _report(client, location, message, "stack that arrived later")

    event = ErrorEvent.query.filter_by(location=location).one()
    assert event.occurrence_count == 2
    assert event.stack == "stack that arrived later"
