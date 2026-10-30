"""A user account is not a model record: a new organisation's Overview counts none until a model record exists."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _sign_up(db_session, org):
    from app.models.user import User

    user = User(
        email=f"sam-{uuid.uuid4().hex[:10]}@example.com", first_name="Sam", last_name="Starter",
        organization_id=org.id, password="not-used", confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _added(app, org_id):
    from app.modules.dashboard.services.overview_figures import _activity, _history

    now = datetime.utcnow()
    with app.test_request_context("/"):
        activity = _activity(_history(org_id, now + timedelta(minutes=1)), now + timedelta(minutes=1), org_id)
    return activity["ranges"]["90d"]["total"], activity["ranges"]["90d"]["manual"], activity["this_year"]


def test_a_new_organisation_counts_no_model_records_and_one_element_counts_one(app, db_session, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.models.audit_log import AuditLog

    org = make_org("new")
    _sign_up(db_session, org)
    assert AuditLog.query.filter_by(organization_id=org.id, table_name="user").count() >= 1, "sign-up is audited"

    assert _added(app, org.id) == (0, 0, 0)

    db_session.add(ArchiMateElement(name="Billing", type="ApplicationComponent", layer="application", organization_id=org.id))
    db_session.flush()
    assert _added(app, org.id) == (1, 1, 1)


def test_the_changes_page_query_leaves_out_the_new_persons_own_account(app, db_session, make_org):
    from app.modules.intelligence.services.history_service import HistoryService

    org = make_org("changes")
    _sign_up(db_session, org)
    window = (datetime.utcnow() - timedelta(minutes=5), datetime.utcnow() + timedelta(minutes=5))
    service = HistoryService(db_session, org.id)
    assert [c.table_name for c in service.difference(*window).changes] == ["user"]
    assert service.difference(*window, model_records_only=True).changes == []
