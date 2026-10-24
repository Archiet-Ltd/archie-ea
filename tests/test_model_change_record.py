"""Every change to the model is recorded once, with before, after and source.

Writers covered: an element created, renamed and deleted; a relationship; an
application's owner changed and its annual cost edited; a capability; an
import run, a connector sync and an accepted AI proposal. Each leaves exactly
one audit row per change carrying organisation, user, record, before, after,
source and a correlation id, the rows form an intact chain, and the history
service answers from them for one organisation only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from flask import g

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label="architect"):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:10]
    user = User(
        email=f"{label}-{suffix}@example.com", first_name="Test", last_name=label,
        organization_id=org.id, password="not-used", confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _rows(db_session, org_id, table, record_id=None):
    from app.models.audit_log import AuditLog

    query = AuditLog.query.filter(
        AuditLog.organization_id == org_id, AuditLog.table_name == table
    )
    if record_id is not None:
        query = query.filter(AuditLog.record_id == record_id)
    return query.order_by(AuditLog.id).all()


def _in_request(app, org, user):
    """A signed-in request context for ``user`` in ``org``."""
    from flask_login import login_user

    ctx = app.test_request_context("/")
    ctx.push()
    g.current_org_id = org.id
    login_user(user)
    return ctx


def _element(db_session, org_id, name="Billing"):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
    db_session.add(element)
    db_session.flush()
    return element


def test_element_create_update_delete_each_recorded_once_with_before_and_after(app, db_session, make_org):
    org = make_org("a")
    user = _user(db_session, org)
    ctx = _in_request(app, org, user)
    try:
        element = _element(db_session, org.id, "Billing")
        element.name = "Billing platform"
        db_session.flush()
        element_id = element.id
        db_session.delete(element)
        db_session.flush()
    finally:
        ctx.pop()

    created, updated, deleted = _rows(db_session, org.id, "archimate_elements", element_id)
    assert (created.action, updated.action, deleted.action) == ("create", "update", "delete")
    assert created.new_value["name"] == "Billing"
    assert updated.old_value == {"name": "Billing"} and updated.new_value == {"name": "Billing platform"}
    assert deleted.old_value["name"] == "Billing platform"
    for row in (created, updated, deleted):
        assert row.user_id == user.id
        assert row.organization_id == org.id
        assert row.extra_json["source"] == "screen"
        assert row.extra_json["correlation_id"]
    assert len({created.extra_json["correlation_id"], updated.extra_json["correlation_id"]}) == 1


def test_relationship_create_is_recorded(app, db_session, make_org):
    from app.models.archimate_core import ArchiMateRelationship

    org = make_org("a")
    a, b = _element(db_session, org.id, "A"), _element(db_session, org.id, "B")
    rel = ArchiMateRelationship(type="Serving", source_id=a.id, target_id=b.id, organization_id=org.id)
    db_session.add(rel)
    db_session.flush()

    (row,) = _rows(db_session, org.id, "archimate_relationships", rel.id)
    assert row.action == "create"
    assert row.new_value["source_id"] == a.id and row.new_value["target_id"] == b.id


def test_owner_change_is_one_update_showing_the_old_and_new_owner(app, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.element_ownership_service import set_element_owner

    org = make_org("a")
    old_owner, new_owner = _user(db_session, org, "old"), _user(db_session, org, "new")
    application = ApplicationComponent(name="Ledger", organization_id=org.id)
    db_session.add(application)
    db_session.flush()

    set_element_owner(
        element_type="application", element_id=application.id, user_id=old_owner.id,
        organization_id=org.id, ownership_type="primary", commit=False,
    )
    db_session.flush()
    set_element_owner(
        element_type="application", element_id=application.id, user_id=new_owner.id,
        organization_id=org.id, ownership_type="primary", replace_primary=True, commit=False,
    )
    db_session.flush()

    rows = _rows(db_session, org.id, "application_owners")
    assert [r.action for r in rows] == ["create", "update"]
    assert rows[1].old_value["user_id"] == old_owner.id
    assert rows[1].new_value["user_id"] == new_owner.id


def test_cost_edit_is_one_update_with_the_old_and_new_figure(app, db_session, make_org):
    from decimal import Decimal

    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_cost_accessor import set_annual_cost

    org = make_org("a")
    application = ApplicationComponent(name="Ledger", organization_id=org.id)
    db_session.add(application)
    db_session.flush()
    set_annual_cost(application, Decimal("1000"))
    db_session.flush()
    before_count = len(_rows(db_session, org.id, "application_component", application.id))
    set_annual_cost(application, Decimal("2500"))
    db_session.flush()

    rows = _rows(db_session, org.id, "application_component", application.id)
    assert len(rows) == before_count + 1
    edit = rows[-1]
    assert edit.action == "update"
    assert float(edit.old_value["total_cost_of_ownership"]) == 1000.0
    assert float(edit.new_value["total_cost_of_ownership"]) == 2500.0


def test_capability_create_is_recorded(app, db_session, make_org):
    from app.models.business_capabilities import BusinessCapability

    org = make_org("a")
    capability = BusinessCapability(name="Order to cash", organization_id=org.id)
    db_session.add(capability)
    db_session.flush()

    (row,) = _rows(db_session, org.id, "business_capability", capability.id)
    assert row.action == "create" and row.new_value["name"] == "Order to cash"


def test_import_run_is_one_source_and_one_correlation_id(app, db_session, make_org):
    from app.services.change_source import records_changes_as

    org = make_org("a")

    @records_changes_as("import")
    def run_import():
        return [_element(db_session, org.id, f"Imported {i}") for i in range(3)]

    elements = run_import()
    rows = [r for e in elements for r in _rows(db_session, org.id, "archimate_elements", e.id)]
    assert len(rows) == 3
    assert {r.extra_json["source"] for r in rows} == {"import"}
    assert len({r.extra_json["correlation_id"] for r in rows}) == 1

    later = _element(db_session, org.id, "By hand")
    (row,) = _rows(db_session, org.id, "archimate_elements", later.id)
    assert row.extra_json["correlation_id"] != rows[0].extra_json["correlation_id"]
    assert row.extra_json["source"] == "system"


@pytest.mark.parametrize("source", ["connector", "ai_proposal"])
def test_connector_and_accepted_proposal_sources_are_recorded(app, db_session, make_org, source):
    from app.services.change_source import change_source

    org = make_org("a")
    user = _user(db_session, org)
    with change_source(source, user_id=user.id):
        element = _element(db_session, org.id, "Synced")
    (row,) = _rows(db_session, org.id, "archimate_elements", element.id)
    assert row.extra_json["source"] == source
    assert row.user_id == user.id


def test_the_real_entry_points_declare_their_source():
    from app.modules.architecture_assistant.document_ingestion import DocumentIngestionService
    from app.services import connector_framework, lucid_import_service
    from app.services.archimate_import_service import ArchiMateImportService

    for func, source in (
        (connector_framework.run_connector_sync, "connector"),
        (ArchiMateImportService.execute_import, "import"),
        (lucid_import_service.import_payload, "import"),
        (DocumentIngestionService.accept_proposal, "ai_proposal"),
        (DocumentIngestionService.batch_accept, "ai_proposal"),
    ):
        assert getattr(func, "records_changes_as", None) == source, func


def test_chain_verifies_after_the_changes_and_breaks_when_a_row_is_altered(app, db_session, make_org):
    from app.models.audit_log import AuditLog

    org = make_org("a")
    element = _element(db_session, org.id, "One")
    element.name = "Two"
    db_session.flush()

    assert AuditLog.verify_chain(org.id)["status"] == "intact"

    first = _rows(db_session, org.id, "archimate_elements", element.id)[0]
    db_session.execute(
        AuditLog.__table__.update().where(AuditLog.__table__.c.id == first.id)
        .values(new_value={"name": "Tampered"})
    )
    assert AuditLog.verify_chain(org.id)["status"] == "broken"


def test_history_lists_the_changes_for_the_organisation_and_not_for_another(app, db_session, make_org):
    from app.modules.intelligence.services.history_service import HistoryService

    org_a, org_b = make_org("a"), make_org("b")
    mine = _element(db_session, org_a.id, "Mine")
    theirs = _element(db_session, org_b.id, "Theirs")
    mine.name = "Mine renamed"
    db_session.flush()

    window = (datetime.utcnow() - timedelta(minutes=5), datetime.utcnow() + timedelta(minutes=5))
    changes_a = HistoryService(db_session, org_a.id).difference(*window).changes
    changes_b = HistoryService(db_session, org_b.id).difference(*window).changes

    assert {c.record_id for c in changes_a} == {mine.id}
    assert {c.record_id for c in changes_b} == {theirs.id}
    assert [c.action for c in changes_a] == ["update", "insert"]
    update = changes_a[0]
    assert update.previous_snapshot == {"name": "Mine"} and update.snapshot == {"name": "Mine renamed"}
    assert update.source == "system"

    as_of_a = HistoryService(db_session, org_a.id).as_of(datetime.utcnow() + timedelta(minutes=1))
    assert [e.name for e in as_of_a.elements] == ["Mine renamed"]
    as_of_before = HistoryService(db_session, org_a.id).as_of(window[0])
    assert as_of_before.elements == []


def test_the_changes_page_shows_rows_for_its_own_organisation_only(app, db_session, make_org, client, login_as):
    org_a, org_b = make_org("a"), make_org("b")
    user_a = _user(db_session, org_a)
    _element(db_session, org_a.id, "Visible to A")
    _element(db_session, org_b.id, "Secret of B")
    db_session.commit()

    login_as(client, user_a)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    response = client.get(f"/intelligence/history/changes?from={today}&to={today}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Visible to A" in body
    assert "Secret of B" not in body
    assert 'data-testid="change-row"' in body
