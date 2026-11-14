"""Tests that no screen lists, counts or returns another organisation's element through a relationship row.

D1: /architecture/api/elements/<id>/detail (api_element_detail)
D2: /architecture/relationships (list_relationships)
D3: /architecture/api/elements/<id>/relationships (api_element_relationships)
ISO-1: /enterprise/architecture/relationships (architecture_relationships)
ISO-2: D1 drawer solutions list (api_element_detail solutions field)
"""

from __future__ import annotations

import pytest
import re
from sqlalchemy import text

from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.solution_models import Solution
from app.models.solution_archimate_element import SolutionArchiMateElement


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


def _cross_sae(db_session, org_a, element_a, solution_b, role="primary"):
    """Insert a crossing solution-archimate-element row with plain SQL."""
    db_session.execute(
        text(
            "INSERT INTO solution_archimate_elements (solution_id, element_id, element_role) "
            "VALUES (:solution_id, :element_id, :element_role)"
        ),
        {"solution_id": solution_b.id, "element_id": element_a.id, "element_role": role},
    )
    db_session.flush()


# Regex patterns for checking rendered rows (from probe_test.py)
CELL = re.compile(r"<td class=\"px-6 py-3 text-foreground\">\s*([^<]*?)\s*</td>")
ROW = re.compile(r"id=\"relationship-(\d+)\"")


def _get_response(db_session, client, login_as, user, path, fresh):
    """Get response, optionally with a fresh session (expunge all)."""
    if fresh:
        db_session.expunge_all()
    login_as(client, user)
    return client.get(path)


class TestApiElementDetailFarEndFence:
    """D1: GET /architecture/api/elements/<id>/detail must not return another org's element."""

    @pytest.mark.parametrize("fresh", [False, True], ids=["same", "fresh"])
    def test_outgoing_cross_org_excluded(self, app, db_session, make_org, client, login_as, fresh):
        mine, theirs = make_org("d1-mine"), make_org("d1-theirs")
        service_a = _el(db_session, mine, "Orders mine", "BusinessService", "Business")
        service_b = _el(db_session, theirs, "Orders theirs", "BusinessService", "Business")
        local_target = _el(db_session, mine, "Local target", "BusinessService", "Business")
        # Same-org relationship (should appear)
        _rel(db_session, mine, service_a, local_target, "Serving")
        # Cross-org relationship (should NOT appear)
        _cross_rel(db_session, mine, service_a, service_b, "Serving")

        user = _user(db_session, mine)
        resp = _get_response(db_session, client, login_as, user, f"/architecture/api/elements/{service_a.id}/detail", fresh)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        outgoing_ids = {r["target_id"] for r in data["outgoing"]}
        outgoing_names = {r["target_name"] for r in data["outgoing"]}
        # Same-org target should be present
        assert "Local target" in outgoing_names
        # Cross-org target must NOT be present
        assert service_b.id not in outgoing_ids
        assert "Orders theirs" not in outgoing_names
        # Count should be 1 (only same-org)
        assert len(data["outgoing"]) == 1

    @pytest.mark.parametrize("fresh", [False, True], ids=["same", "fresh"])
    def test_incoming_cross_org_excluded(self, app, db_session, make_org, client, login_as, fresh):
        mine, theirs = make_org("d1-mine2"), make_org("d1-theirs2")
        service_a = _el(db_session, mine, "Orders mine", "BusinessService", "Business")
        service_b = _el(db_session, theirs, "Orders theirs", "BusinessService", "Business")
        local_source = _el(db_session, mine, "Local source", "BusinessService", "Business")
        # Same-org relationship (should appear)
        _rel(db_session, mine, local_source, service_a, "Serving")
        # Cross-org relationship (should NOT appear)
        _cross_rel(db_session, mine, service_b, service_a, "Serving")

        user = _user(db_session, mine)
        resp = _get_response(db_session, client, login_as, user, f"/architecture/api/elements/{service_a.id}/detail", fresh)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

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

    @pytest.mark.parametrize("fresh", [False, True], ids=["same", "fresh"])
    def test_cross_org_relationship_not_listed(self, app, db_session, make_org, client, login_as, fresh):
        mine, theirs = make_org("d2-mine"), make_org("d2-theirs")
        service_a = _el(db_session, mine, "Orders mine", "BusinessService", "Business")
        service_b = _el(db_session, theirs, "Orders theirs", "BusinessService", "Business")
        local_target = _el(db_session, mine, "Local target", "BusinessService", "Business")
        # Same-org relationship (should appear)
        _rel(db_session, mine, service_a, local_target, "Serving")
        # Cross-org relationship (should NOT appear in list or count)
        _cross_rel(db_session, mine, service_a, service_b, "Serving")

        user = _user(db_session, mine)
        resp = _get_response(db_session, client, login_as, user, "/architecture/relationships", fresh)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        # Check rendered cells and row ids, not raw digits in page
        cells = CELL.findall(html)
        rows = ROW.findall(html)

        # Same-org relationship should be listed
        assert "Local target" in cells
        # Cross-org relationship must NOT be listed
        assert "Orders theirs" not in cells
        assert f"#{service_b.id}" not in cells
        # Row for crossing relationship must not be present
        assert str(service_b.id) not in rows
        # Only one relationship row should be present
        assert len(rows) == 1


class TestApiElementRelationshipsFarEndFence:
    """D3: GET /architecture/api/elements/<id>/relationships must not return another org's element."""

    @pytest.mark.parametrize("fresh", [False, True], ids=["same", "fresh"])
    def test_outgoing_cross_org_excluded(self, app, db_session, make_org, client, login_as, fresh):
        mine, theirs = make_org("d3-mine"), make_org("d3-theirs")
        service_a = _el(db_session, mine, "Orders mine", "BusinessService", "Business")
        service_b = _el(db_session, theirs, "Orders theirs", "BusinessService", "Business")
        local_target = _el(db_session, mine, "Local target", "BusinessService", "Business")
        # Same-org relationship (should appear)
        _rel(db_session, mine, service_a, local_target, "Serving")
        # Cross-org relationship (should NOT appear)
        _cross_rel(db_session, mine, service_a, service_b, "Serving")

        user = _user(db_session, mine)
        resp = _get_response(db_session, client, login_as, user, f"/architecture/api/elements/{service_a.id}/relationships", fresh)
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

    @pytest.mark.parametrize("fresh", [False, True], ids=["same", "fresh"])
    def test_incoming_cross_org_excluded(self, app, db_session, make_org, client, login_as, fresh):
        mine, theirs = make_org("d3-mine2"), make_org("d3-theirs2")
        service_a = _el(db_session, mine, "Orders mine", "BusinessService", "Business")
        service_b = _el(db_session, theirs, "Orders theirs", "BusinessService", "Business")
        local_source = _el(db_session, mine, "Local source", "BusinessService", "Business")
        # Same-org relationship (should appear)
        _rel(db_session, mine, local_source, service_a, "Serving")
        # Cross-org relationship (should NOT appear)
        _cross_rel(db_session, mine, service_b, service_a, "Serving")

        user = _user(db_session, mine)
        resp = _get_response(db_session, client, login_as, user, f"/architecture/api/elements/{service_a.id}/relationships", fresh)
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


class TestEnterpriseRelationshipsFarEndFence:
    """ISO-1: GET /enterprise/architecture/relationships must not list another org's element."""

    @pytest.mark.parametrize("fresh", [False, True], ids=["same", "fresh"])
    def test_cross_org_relationship_not_listed(self, app, db_session, make_org, client, login_as, fresh):
        mine, theirs = make_org("iso1-mine"), make_org("iso1-theirs")
        service_a = _el(db_session, mine, "Orders mine", "BusinessService", "Business")
        service_b = _el(db_session, theirs, "Orders theirs", "BusinessService", "Business")
        local_target = _el(db_session, mine, "Local target", "BusinessService", "Business")
        # Same-org relationship (should appear)
        _rel(db_session, mine, service_a, local_target, "Serving")
        # Cross-org relationship (should NOT appear)
        _cross_rel(db_session, mine, service_a, service_b, "Serving")

        user = _user(db_session, mine)
        resp = _get_response(db_session, client, login_as, user, "/enterprise/architecture/relationships", fresh)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        # Check rendered cells and row ids
        cells = CELL.findall(html)
        rows = ROW.findall(html)

        # Same-org relationship should be listed
        assert "Local target" in cells
        # Cross-org relationship must NOT be listed
        assert "Orders theirs" not in cells
        assert f"#{service_b.id}" not in cells
        assert str(service_b.id) not in rows
        # Only one relationship row should be present
        assert len(rows) == 1


class TestElementDetailSolutionsFarEndFence:
    """ISO-2: D1 drawer solutions list must not show another org's solution."""

    @pytest.mark.parametrize("fresh", [False, True], ids=["same", "fresh"])
    def test_cross_org_solution_not_listed(self, app, db_session, make_org, client, login_as, fresh):
        mine, theirs = make_org("iso2-mine"), make_org("iso2-theirs")
        service_a = _el(db_session, mine, "Orders mine", "BusinessService", "Business")
        # Cross-org solution linked to mine's element
        solution_b = Solution(organization_id=theirs.id, name="Foreign solution")
        db_session.add(solution_b)
        db_session.flush()
        _cross_sae(db_session, mine, service_a, solution_b, "primary")
        # Same-org solution (should appear)
        solution_a = Solution(organization_id=mine.id, name="Local solution")
        db_session.add(solution_a)
        db_session.flush()
        _cross_sae(db_session, mine, service_a, solution_a, "primary")

        user = _user(db_session, mine)
        resp = _get_response(db_session, client, login_as, user, f"/architecture/api/elements/{service_a.id}/detail", fresh)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        solution_ids = {s["solution_id"] for s in data["solutions"]}
        solution_names = {s["solution_name"] for s in data["solutions"]}
        # Same-org solution should be present
        assert solution_a.id in solution_ids
        assert "Local solution" in solution_names
        # Cross-org solution must NOT be present
        assert solution_b.id not in solution_ids
        assert "Foreign solution" not in solution_names
        # Count should be 1 (only same-org)
        assert len(data["solutions"]) == 1