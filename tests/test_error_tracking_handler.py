"""Direct coverage for the server-side error-aggregation handler.

Before this file, nothing in the suite exercised ``_ErrorAggregationHandler``
or ``init_error_tracking`` directly: ``tests/smoke/test_error_aggregation.py``
drives the client-error route instead, and ``tests/test_error_digest.py``
inserts ``ErrorEvent`` rows straight into the database. The handler's own
``emit`` -> ``_persist`` -> ``db.engine.connect()`` path had zero direct
proof, so turning it off under the testing configuration (below) could have
silently turned it into dead code with nothing here to notice.

Uses the shared ``app`` and ``db_session`` fixtures from ``tests/conftest.py``
only. Test 3 opens its own ``db.engine.connect()`` deliberately, the same way
the handler itself does -- that is what is being proven, and it is outside
``db_session``'s rollback contract, so it cleans up by hand.
"""

import logging
import uuid

from app._bootstrap.error_tracking import _ErrorAggregationHandler, _fingerprint


def test_testing_config_does_not_attach_the_aggregation_handler(app):
    assert app.config["ERROR_TRACKING_ENABLED"] is False
    assert "error_aggregation_handler" not in app.extensions
    assert not any(
        isinstance(handler, _ErrorAggregationHandler)
        for handler in logging.getLogger().handlers
    )


def test_default_config_enables_error_tracking():
    from config import Config, ProductionConfig

    assert Config.ERROR_TRACKING_ENABLED is True
    assert ProductionConfig.ERROR_TRACKING_ENABLED is True


def test_handler_persists_a_warning_when_invoked(app, db_session):
    from app import db
    from app.models.error_event import ErrorEvent

    marker = uuid.uuid4().hex
    message = "handler direct test warning %s" % marker
    record = logging.LogRecord(
        name="tests.test_error_tracking_handler",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )
    fingerprint = _fingerprint(record.name, message)
    handler = _ErrorAggregationHandler(app)

    try:
        handler.emit(record)
        with app.app_context():
            conn = db.engine.connect()
            try:
                table = ErrorEvent.__table__
                row = conn.execute(
                    db.select(table.c.message, table.c.occurrence_count).where(
                        table.c.message == message
                    )
                ).one()
            finally:
                conn.close()
        assert row.occurrence_count == 1

        handler.emit(record)
        with app.app_context():
            conn = db.engine.connect()
            try:
                row = conn.execute(
                    db.select(table.c.message, table.c.occurrence_count).where(
                        table.c.message == message
                    )
                ).one()
            finally:
                conn.close()
        assert row.occurrence_count == 2
    finally:
        with app.app_context():
            conn = db.engine.connect()
            try:
                trans = conn.begin()
                conn.execute(
                    ErrorEvent.__table__.delete().where(
                        ErrorEvent.__table__.c.fingerprint == fingerprint
                    )
                )
                trans.commit()
            finally:
                conn.close()
