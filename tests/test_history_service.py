"""Tests for the history service (as-of and difference queries).

Covers: two-organisation isolation on the as-of and difference answers, an
as-of answer matching a snapshot taken on that date, and a difference
listing every audit entry for the interval and nothing else.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_org(db_session, label="org"):
    import uuid
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Test {label} {suffix}", slug=f"test-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _make_user(db_session, org_id, label="user"):
    import uuid
    from app.models.user import User

    suffix = uuid.uuid4().hex[:10]
    user = User(
        email=f"{label}-{suffix}@example.com",
        first_name="Test",
        last_name=label,
        organization_id=org_id,
        password="test-password-ignored-by-login-as",
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_element(db_session, org_id, name="Test Element", element_type="ApplicationComponent", layer="application"):
    from app.models.models import ArchiMateElement

    el = ArchiMateElement(
        name=name,
        type=element_type,
        layer=layer,
        organization_id=org_id,
    )
    db_session.add(el)
    db_session.flush()
    return el


def _make_relationship(db_session, org_id, source_id, target_id, rel_type="Composition"):
    from app.models.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        type=rel_type,
        source_id=source_id,
        target_id=target_id,
        organization_id=org_id,
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _make_entity_history(db_session, org_id, table_name, record_id, snapshot, valid_from, valid_to=None, recorded_at=None):
    """Directly insert an entity_history row (bypasses the trigger for controlled test data)."""
    from app.models.entity_history import EntityHistory

    eh = EntityHistory(
        organization_id=org_id,
        table_name=table_name,
        record_id=record_id,
        snapshot=snapshot,
        valid_from=valid_from,
        valid_to=valid_to,
        recorded_at=recorded_at or valid_from,
        source="trigger",
    )
    db_session.add(eh)
    db_session.flush()
    return eh


def _make_audit_log(db_session, org_id, user_id, table_name, record_id, action, created_at, new_value=None):
    """Directly insert an audit log entry."""
    from app.models.audit_log import AuditLog

    entry = AuditLog(
        organization_id=org_id,
        user_id=user_id,
        table_name=table_name,
        record_id=record_id,
        action=action,
        created_at=created_at,
        new_value=new_value or {},
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def _record(db_session, org_id, table_name, record_id, snapshot, at, action="create",
            user_id=None, source="screen", before=None):
    """Write one audit row the way the audit middleware does (sealed in the chain)."""
    from app.models.audit_log import AuditLog

    entry = AuditLog(
        organization_id=org_id,
        user_id=user_id,
        table_name=table_name,
        record_id=record_id,
        action=action,
        created_at=at,
        old_value=before,
        new_value=snapshot if action != "delete" else None,
        extra_json={"source": source, "correlation_id": f"run-{record_id}-{at.timestamp()}",
                    "label": (snapshot or before or {}).get("name")},
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def _service(db_session, org_id):
    from app.modules.intelligence.services.history_service import HistoryService
    return HistoryService(db_session, org_id)


# ---------------------------------------------------------------------------
# Two-organisation isolation: as-of
# ---------------------------------------------------------------------------

def test_as_of_returns_only_caller_organisation_elements(db_session):
    """Elements from org B must not appear in org A's as-of result."""
    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")

    now = datetime.utcnow()
    past = now - timedelta(hours=1)

    # Org A: one element
    _record(
        db_session, org_a.id, "archimate_elements", 1,
        {"name": "Element A", "type": "ApplicationComponent", "layer": "application"},
        past,
    )

    # Org B: one element (should not appear)
    _record(
        db_session, org_b.id, "archimate_elements", 2,
        {"name": "Element B", "type": "BusinessActor", "layer": "business"},
        past,
    )

    result = _service(db_session, org_a.id).as_of(now)

    assert len(result.elements) == 1
    assert result.elements[0].name == "Element A"
    assert result.elements[0].organization_id == org_a.id


def test_as_of_returns_only_caller_organisation_relationships(db_session):
    """Relationships from org B must not appear in org A's as-of result."""
    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")

    now = datetime.utcnow()
    past = now - timedelta(hours=1)

    # Org A: one relationship
    _record(
        db_session, org_a.id, "archimate_relationships", 1,
        {"type": "Composition", "source_id": 10, "target_id": 11},
        past,
    )

    # Org B: one relationship (should not appear)
    _record(
        db_session, org_b.id, "archimate_relationships", 2,
        {"type": "Flow", "source_id": 20, "target_id": 21},
        past,
    )

    result = _service(db_session, org_a.id).as_of(now)

    assert len(result.relationships) == 1
    assert result.relationships[0].type == "Composition"
    assert result.relationships[0].organization_id == org_a.id


# ---------------------------------------------------------------------------
# Two-organisation isolation: difference
# ---------------------------------------------------------------------------

def test_difference_returns_only_caller_organisation_changes(db_session):
    """Changes from org B must not appear in org A's difference result."""
    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")

    t1 = datetime(2025, 1, 1, 12, 0, 0)
    t3 = datetime(2025, 1, 3, 12, 0, 0)

    # Org A: one change in range
    _record(
        db_session, org_a.id, "archimate_elements", 1,
        {"name": "Element A", "type": "ApplicationComponent", "layer": "application"},
        t1,
    )

    # Org B: one change in range (should not appear)
    _record(
        db_session, org_b.id, "archimate_elements", 2,
        {"name": "Element B", "type": "BusinessActor", "layer": "business"},
        t1,
    )

    result = _service(db_session, org_a.id).difference(t1, t3)

    assert len(result.changes) == 1
    assert result.changes[0].table_name == "archimate_elements"
    assert result.changes[0].record_id == 1


# ---------------------------------------------------------------------------
# As-of snapshot equality
# ---------------------------------------------------------------------------

def test_as_of_snapshot_matches_recorded_snapshot(db_session):
    """The as-of answer for a date must equal the snapshot recorded on that date."""
    org = _make_org(db_session)

    now = datetime.utcnow()
    past = now - timedelta(hours=1)

    snapshot = {
        "name": "Test Element",
        "type": "ApplicationComponent",
        "layer": "application",
        "description": "A test element for snapshot verification",
    }

    _record(db_session, org.id, "archimate_elements", 1, snapshot, past)

    result = _service(db_session, org.id).as_of(now)

    assert len(result.elements) == 1
    element = result.elements[0]
    assert element.name == snapshot["name"]
    assert element.type == snapshot["type"]
    assert element.layer == snapshot["layer"]
    assert element.snapshot == snapshot


def test_as_of_excludes_elements_outside_date_range(db_session):
    """Elements whose valid interval does not cover the requested date must not appear."""
    org = _make_org(db_session)

    t1 = datetime(2025, 1, 1, 12, 0, 0)
    t2 = datetime(2025, 1, 2, 12, 0, 0)
    t3 = datetime(2025, 1, 3, 12, 0, 0)

    # Element created at t1 and deleted at t2 (gone before t3)
    old = {"name": "Old Element", "type": "ApplicationComponent", "layer": "application"}
    _record(db_session, org.id, "archimate_elements", 1, old, t1)
    _record(db_session, org.id, "archimate_elements", 1, None, t2, action="delete", before=old)

    # Element created at t2 (still there at t3)
    _record(
        db_session, org.id, "archimate_elements", 2,
        {"name": "Current Element", "type": "BusinessActor", "layer": "business"},
        t2,
    )

    # Query as of t3: only the second element should appear
    result = _service(db_session, org.id).as_of(t3)

    assert len(result.elements) == 1
    assert result.elements[0].name == "Current Element"


# ---------------------------------------------------------------------------
# Difference completeness
# ---------------------------------------------------------------------------

def test_difference_lists_every_change_in_interval(db_session):
    """Every recorded change inside the range must appear, with its before and after."""
    org = _make_org(db_session)

    t1 = datetime(2025, 1, 1, 12, 0, 0)
    t2 = datetime(2025, 1, 2, 12, 0, 0)
    t3 = datetime(2025, 1, 3, 12, 0, 0)
    t4 = datetime(2025, 1, 4, 12, 0, 0)

    _record(db_session, org.id, "archimate_elements", 1,
            {"name": "v1", "type": "ApplicationComponent"}, t1)
    _record(db_session, org.id, "archimate_elements", 1, {"name": "v2"}, t2,
            action="update", before={"name": "v1"})
    _record(db_session, org.id, "archimate_elements", 1, {"name": "v3"}, t3,
            action="update", before={"name": "v2"})

    result = _service(db_session, org.id).difference(t1, t4)

    assert [c.action for c in result.changes] == ["update", "update", "insert"]
    assert [c.snapshot["name"] for c in result.changes] == ["v3", "v2", "v1"]
    assert [c.previous_snapshot for c in result.changes[:2]] == [{"name": "v2"}, {"name": "v1"}]


def test_difference_excludes_versions_outside_interval(db_session):
    """Versions whose interval does not overlap the range must not appear."""
    org = _make_org(db_session)

    t1 = datetime(2025, 1, 1, 12, 0, 0)
    t2 = datetime(2025, 1, 2, 12, 0, 0)
    t3 = datetime(2025, 1, 3, 12, 0, 0)
    t4 = datetime(2025, 1, 4, 12, 0, 0)

    # A change recorded before the range, and one after it.
    _record(db_session, org.id, "archimate_elements", 1,
            {"name": "before", "type": "ApplicationComponent"}, t1 + timedelta(hours=1))
    _record(db_session, org.id, "archimate_elements", 2,
            {"name": "after", "type": "BusinessActor"}, t4)

    # Query [t2, t3]: neither version overlaps
    result = _service(db_session, org.id).difference(t2, t3)

    assert len(result.changes) == 0


def test_difference_includes_audit_log_who_and_why(db_session):
    """Each change entry must carry who changed it and why from the audit log."""
    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "architect")

    t1 = datetime(2025, 1, 1, 12, 0, 0)
    t2 = datetime(2025, 1, 2, 12, 0, 0)

    _record(
        db_session, org.id, "archimate_elements", 1,
        {"name": "Element", "type": "ApplicationComponent", "change_reason": "Initial creation"},
        t1, user_id=user.id, source="import",
    )

    result = _service(db_session, org.id).difference(t1, t2)

    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.changed_by_id == user.id
    assert change.changed_by_name == user.email
    assert change.change_reason == "Initial creation"
    assert change.source == "import"


def test_difference_empty_range_returns_no_changes(db_session):
    """A range with no history rows returns an empty result, not an error."""
    org = _make_org(db_session)

    t1 = datetime(2025, 1, 1, 12, 0, 0)
    t2 = datetime(2025, 1, 2, 12, 0, 0)

    result = _service(db_session, org.id).difference(t1, t2)

    assert result.changes == []
    assert result.from_date == t1
    assert result.to_date == t2


# ---------------------------------------------------------------------------
# As-of: empty result
# ---------------------------------------------------------------------------

def test_as_of_empty_database_returns_no_elements(db_session):
    """An org with no history rows returns empty lists, not an error."""
    org = _make_org(db_session)

    result = _service(db_session, org.id).as_of(datetime.utcnow())

    assert result.elements == []
    assert result.relationships == []


# ---------------------------------------------------------------------------
# API endpoint tests (two-org isolation via HTTP)
# ---------------------------------------------------------------------------

def test_api_as_of_respects_tenant_isolation(client, app, db_session, login_as):
    """The JSON API must only return the authenticated user's organisation's data."""
    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    user_a = _make_user(db_session, org_a.id, "architect-a")

    now = datetime.utcnow()
    past = now - timedelta(hours=1)

    # Org A: one element
    _record(
        db_session, org_a.id, "archimate_elements", 1,
        {"name": "Element A", "type": "ApplicationComponent", "layer": "application"},
        past,
    )
    # Org B: one element (should not appear)
    _record(
        db_session, org_b.id, "archimate_elements", 2,
        {"name": "Element B", "type": "BusinessActor", "layer": "business"},
        past,
    )

    login_as(client, user_a)

    resp = client.get(f"/intelligence/api/history/as-of?as_of={now.isoformat()}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["data"]["elements"]) == 1
    assert data["data"]["elements"][0]["name"] == "Element A"


def test_api_changes_respects_tenant_isolation(client, app, db_session, login_as):
    """The JSON changes API must only return the authenticated user's organisation's data."""
    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    user_a = _make_user(db_session, org_a.id, "architect-a")

    t1 = datetime(2025, 1, 1, 12, 0, 0)
    t2 = datetime(2025, 1, 2, 12, 0, 0)

    # Org A: one change
    _record(
        db_session, org_a.id, "archimate_elements", 1,
        {"name": "Element A", "type": "ApplicationComponent", "layer": "application"},
        t1,
    )
    # Org B: one change (should not appear)
    _record(
        db_session, org_b.id, "archimate_elements", 2,
        {"name": "Element B", "type": "BusinessActor", "layer": "business"},
        t1,
    )

    login_as(client, user_a)

    resp = client.get(f"/intelligence/api/history/changes?from={t1.isoformat()}&to={t2.isoformat()}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["data"]["changes"]) == 1
    assert data["data"]["changes"][0]["record_id"] == 1


def test_api_element_history_respects_tenant_isolation(client, app, db_session, login_as):
    """The element history API must only return versions for the caller's org."""
    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    user_a = _make_user(db_session, org_a.id, "architect-a")

    now = datetime.utcnow()
    past = now - timedelta(hours=1)

    # Org A: element 1 with history
    _make_entity_history(
        db_session, org_a.id, "archimate_elements", 1,
        {"name": "Element A v1", "type": "ApplicationComponent", "layer": "application"},
        valid_from=past,
    )
    # Org B: element 1 with history (same record_id, different org — must not leak)
    _make_entity_history(
        db_session, org_b.id, "archimate_elements", 1,
        {"name": "Element B v1", "type": "BusinessActor", "layer": "business"},
        valid_from=past,
    )

    db_session.commit()
    login_as(client, user_a)

    resp = client.get("/intelligence/api/history/element/1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["data"]["versions"]) == 1
    assert data["data"]["versions"][0]["snapshot"]["name"] == "Element A v1"


# ---------------------------------------------------------------------------
# API error cases
# ---------------------------------------------------------------------------

def test_api_as_of_missing_parameter_returns_400(client, app, db_session, login_as):
    """Missing as_of parameter must return a 400 error."""
    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "architect")

    db_session.commit()
    login_as(client, user)

    resp = client.get("/intelligence/api/history/as-of")
    assert resp.status_code == 400


def test_api_changes_missing_parameters_returns_400(client, app, db_session, login_as):
    """Missing from/to parameters must return a 400 error."""
    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "architect")

    db_session.commit()
    login_as(client, user)

    resp = client.get("/intelligence/api/history/changes")
    assert resp.status_code == 400


def test_api_changes_invalid_range_returns_400(client, app, db_session, login_as):
    """from >= to must return a 400 error."""
    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "architect")

    db_session.commit()
    login_as(client, user)

    t = datetime(2025, 1, 1, 12, 0, 0).isoformat()
    resp = client.get(f"/intelligence/api/history/changes?from={t}&to={t}")
    assert resp.status_code == 400


def test_api_element_history_not_found_returns_404(client, app, db_session, login_as):
    """An element with no history in the caller's org must return 404."""
    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "architect")

    db_session.commit()
    login_as(client, user)

    resp = client.get("/intelligence/api/history/element/99999")
    assert resp.status_code == 404