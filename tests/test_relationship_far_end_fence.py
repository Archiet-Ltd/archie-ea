"""Tests that no screen lists, counts or returns another organisation's element through a relationship row.

D1: /architecture/api/elements/<id>/detail (api_element_detail)
D2: /architecture/relationships (list_relationships)
D3: /architecture/api/elements/<id>/relationships (api_element_relationships)
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship


def _el(db_session, org, name, type_, layer, **extra):
    row = ArchiMateElement(organization_id=org.id, name=name, type=type_, layer=layer, **extra)
    db_session.add(row)
    db_session.flush()
    return row


def _rel(db_session, org, source, target, kind):
    db_session.add(ArchiMateRelationship(organization_id=org.id, source_id=source.id, target_id=target.id, type=kind))
    db_session.flush()


def _user(db_session, org, role="enterprise_architect"):
    from app.models.user import User
    import uuid
    user = User(
        email=f"test-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _cross_rel(db_session, org_a, source_a, target_b, kind):
    """Insert a crossing relationship row with plain SQL (bypasses ORM tenant filter)."""
    db_session.execute(
        text(
            "INSERT INTO archimate_relationships (organization_id, source_id, target_id, type) "
            "VALUES (:org_id, :source_id, :target_id, :type)"
        ),
        {"org_id": org_a.id, "source_id": source_a.id, "target_id": target_b.id, "type": kind},
    )
    db_session.flush()


class TestApiElementDetailFarEndFence:
    """D1: GET /architecture/api/elements/<id>/detail must not return another org's element."""

    def test_outgoing_cross_org_excluded(self, app, db_session, make_org, client, login_as):
        mine, theirs = make_org("d1-mine"), make_org("d1-theirs")
        service_a = _el(db_session, mine, "Orders mine", "BusinessService", "Business")
        service_b = _el(db_session, theirs, "Orders theirs", "BusinessService", "Business")
        local_target = _el(db_session, mine, "Local target", "BusinessService", "Business")
        # Same-org relationship (should appear)
        _rel(db_session, mine, service_a, local_target, "Serving")
        # Cross-org relationship (should NOT appear)
        _cross_rel(db_session, mine, service_a, service_b, "Serving")

        user = _user(db_session, mine)
        login_as(client, user)
        resp = client.get(f"/architecture/api/elements/{service_a.id}/detail")
        assert resp.status_code == 200
        data = resp.get_json()
        # Response is the element data directly, not wrapped in success

        outgoing_ids = {r["target_id"] for r in data["outgoing"]}
        outgoing_names = {r["target_name"] for r in data["outgoing"]}
        # Same-org target should be present
        assert "Local target" in outgoing_names
        # Cross-org target must NOT be present
        assert service_b.id not in outgoing_ids
        assert "Orders theirs" not in outgoing_names
        # Count should be 1 (only same-org)
        assert len(data["outgoing"]) == 1

    def test_incoming_cross_org_excluded(self, app, db_session, make_org, client, login_as):
        mine, theirs = make_org("d1-mine2"), make_org("d1-theirs2")
        service_a = _el(db_session, mine, "Orders mine", "BusinessService", "Business")
        service_b = _el(db_session, theirs, "Orders theirs", "BusinessService", "Business")
        local_source = _el(db_session, mine, "Local source", "BusinessService", "Business")
        # Same-org relationship (should appear)
        _rel(db_session, mine, local_source, service_a, "Serving")
        # Cross-org relationship (should NOT appear)
        _cross_rel(db_session, mine, service_b, service_a, "Serving")

        user = _user(db_session, mine)
        login_as(client, user)
        resp = client.get(f"/architecture/api/elements/{service_a.id}/detail")
        assert resp.status_code == 200
        data = resp.get_json()
        # Response is the element data directly, not wrapped in success

        incoming_ids = {r["source_id"] for r in data["incoming"]}
        incoming_names = {r["source_name"] for r in data["incoming"]}
        # Same-org source should be present
        assert "Local source" in incoming_names
        # Cross-org source must NOT be present
        assert service_b.id not in incoming_ids
        assert "Orders theirs" not in incoming_names
        # Count should be 1 (only same-org)
        assert len(data["incoming"]) == 1


class TestListRelationshipsFarEndFence:
    """D2: GET /architecture/relationships must not list or count another org's element."""

    def test_cross_org_relationship_not_listed(self, app, db_session, make_org, client, login_as):
        mine, theirs = make_org("d2-mine"), make_org("d2-theirs")
        service_a = _el(db_session, mine, "Orders mine", "BusinessService", "Business")
        service_b = _el(db_session, theirs, "Orders theirs", "BusinessService", "Business")
        local_target = _el(db_session, mine, "Local target", "BusinessService", "Business")
        # Same-org relationship (should appear)
        _rel(db_session, mine, service_a, local_target, "Serving")
        # Cross-org relationship (should NOT appear in list or count)
        _cross_rel(db_session, mine, service_a, service_b, "Serving")

        user = _user(db_session, mine)
        login_as(client, user)
        resp = client.get("/architecture/relationships")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        # Same-org relationship should be listed
        assert "Local target" in html
        # Cross-org relationship must NOT be listed
        assert "Orders theirs" not in html
        assert str(service_b.id) not in html
        # Total count in page should be 1 (only same-org)
        # The template shows "Showing X of Y relationships" or similar
        # We verify by checking only one relationship row appears
        assert html.count("Serving") == 1  # only the same-org one


class TestApiElementRelationshipsFarEndFence:
    """D3: GET /architecture/api/elements/<id>/relationships must not return another org's element."""

    def test_outgoing_cross_org_excluded(self, app, db_session, make_org, client, login_as):
        mine, theirs = make_org("d3-mine"), make_org("d3-theirs")
        service_a = _el(db_session, mine, "Orders mine", "BusinessService", "Business")
        service_b = _el(db_session, theirs, "Orders theirs", "BusinessService", "Business")
        local_target = _el(db_session, mine, "Local target", "BusinessService", "Business")
        # Same-org relationship (should appear)
        _rel(db_session, mine, service_a, local_target, "Serving")
        # Cross-org relationship (should NOT appear)
        _cross_rel(db_session, mine, service_a, service_b, "Serving")

        user = _user(db_session, mine)
        login_as(client, user)
        resp = client.get(f"/architecture/api/elements/{service_a.id}/relationships")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"

        outgoing_ids = {r["target_id"] for r in data["outgoing"]}
        # Same-org target should be present
        assert local_target.id in outgoing_ids
        # Cross-org target must NOT be present
        assert service_b.id not in outgoing_ids
        # Count should be 1 (only same-org)
        assert len(data["outgoing"]) == 1

    def test_incoming_cross_org_excluded(self, app, db_session, make_org, client, login_as):
        mine, theirs = make_org("d3-mine2"), make_org("d3-theirs2")
        service_a = _el(db_session, mine, "Orders mine", "BusinessService", "Business")
        service_b = _el(db_session, theirs, "Orders theirs", "BusinessService", "Business")
        local_source = _el(db_session, mine, "Local source", "BusinessService", "Business")
        # Same-org relationship (should appear)
        _rel(db_session, mine, local_source, service_a, "Serving")
        # Cross-org relationship (should NOT appear)
        _cross_rel(db_session, mine, service_b, service_a, "Serving")

        user = _user(db_session, mine)
        login_as(client, user)
        resp = client.get(f"/architecture/api/elements/{service_a.id}/relationships")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"

        incoming_ids = {r["source_id"] for r in data["incoming"]}
        # Same-org source should be present
        assert local_source.id in incoming_ids
        # Cross-org source must NOT be present
        assert service_b.id not in incoming_ids
        # Count should be 1 (only same-org)
        assert len(data["incoming"]) == 1