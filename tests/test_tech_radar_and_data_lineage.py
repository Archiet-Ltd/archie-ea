"""ARCH-124 (Tech Radar) and ARCH-123 (Data Lineage) — QA register S5 closure.

Both surfaces are classifications/traces over data that already exists
(the Technology-layer ArchiMateElement catalogue, and the DataObject
ArchiMateElement catalogue respectively). These tests assert:
  * an empty tenant renders an honest empty state, never a fabricated demo,
  * real records show up once they exist,
  * a write only succeeds when it references a real, existing element.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def client(app):
    return app.test_client()


def _make_org(db_session, label="org"):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Test {label} {suffix}", slug=f"test-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _make_user(db_session, org_id, label="user", role_name="Administrator"):
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()

    role = Role.query.filter(Role.name.in_((role_name, "Administrator", "Admin"))).first()
    if role is None:
        role = Role(name=role_name)
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.commit()
    return user


def _login(client, app, user):
    from flask import g

    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _make_technology_element(db_session, org_id, name, element_type="Node"):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(
        name=name, type=element_type, layer="Technology", organization_id=org_id
    )
    db_session.add(el)
    db_session.flush()
    return el


def _make_data_object(db_session, org_id, name):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(
        name=name, type="DataObject", layer="Application", organization_id=org_id
    )
    db_session.add(el)
    db_session.flush()
    return el


# --------------------------------------------------------------------- #
# ARCH-124: Tech Radar                                                   #
# --------------------------------------------------------------------- #


class TestTechRadar:
    def test_empty_tenant_renders_honest_empty_state(self, app, db_session, client):
        org = _make_org(db_session, "radar-empty")
        user = _make_user(db_session, org.id, "RadarViewer")
        _login(client, app, user)

        resp = client.get("/technology/radar/")

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # The never-invent-data rule: an empty tenant must not show a demo
        # radar with any ring populated.
        assert 'data-testid="radar-empty"' in body
        assert "No technology in your model yet" in body
        assert 'data-testid="radar-count-adopt"' not in body

    def test_real_technology_element_appears_unclassified(self, app, db_session, client):
        org = _make_org(db_session, "radar-real")
        user = _make_user(db_session, org.id, "RadarViewer2")
        node = _make_technology_element(db_session, org.id, "PROD-DB-01", "Node")
        _login(client, app, user)

        resp = client.get("/technology/radar/")

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "PROD-DB-01" in body
        # The technology is listed as waiting for a ring, and its row menu
        # leads to the page where a person places it.
        assert f'data-testid="radar-ring-{node.id}"' in body
        assert "Not placed" in body
        place_url = f"/technology/radar/{node.id}/place"
        assert f'data-testid="radar-place-{node.id}"' in body
        assert place_url in body

        # Following that link lands on a page with the form to classify it.
        place = client.get(place_url)
        assert place.status_code == 200
        place_body = place.get_data(as_text=True)
        assert "PROD-DB-01" in place_body
        assert f'data-testid="radar-classify-{node.id}"' in place_body

    def test_classify_creates_a_real_entry(self, app, db_session, client):
        from app.models.tech_radar import TechRadarEntry

        org = _make_org(db_session, "radar-classify")
        user = _make_user(db_session, org.id, "RadarAdmin")
        node = _make_technology_element(db_session, org.id, "Kafka Cluster", "SystemSoftware")
        _login(client, app, user)

        # The radar page submits a plain HTML form, so this path redirects back
        # to the radar. It used to reply with jsonify() unconditionally, which
        # navigated the architect to /technology/radar/classify and left them
        # looking at a raw {"success": true, ...} body with no way back.
        resp = client.post(
            "/technology/radar/classify",
            data={"archimate_element_id": node.id, "ring": "trial", "rationale": "piloting"},
        )
        assert resp.status_code == 302, resp.get_data(as_text=True)
        assert "/technology/radar" in resp.headers["Location"]

        entry = TechRadarEntry.query.filter_by(archimate_element_id=node.id).first()
        assert entry is not None
        assert entry.ring == "trial"
        assert entry.set_by_user_id == user.id

    def test_classify_rejects_non_technology_element(self, app, db_session, client):
        """An application/motivation element must never be classifiable —
        the radar only classifies real Technology-layer elements."""
        org = _make_org(db_session, "radar-reject")
        user = _make_user(db_session, org.id, "RadarAdmin2")
        data_object = _make_data_object(db_session, org.id, "Customer Record")
        _login(client, app, user)

        resp = client.post(
            "/technology/radar/classify",
            data={"archimate_element_id": data_object.id, "ring": "adopt"},
        )
        assert resp.status_code == 400

        # And the API surface keeps its JSON contract, with the reason in it --
        # a form caller gets the same refusal as a flash on the radar.
        resp = client.post(
            "/technology/radar/classify",
            data={"archimate_element_id": data_object.id, "ring": "adopt"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_classify_rejects_unknown_ring(self, app, db_session, client):
        org = _make_org(db_session, "radar-ring")
        user = _make_user(db_session, org.id, "RadarAdmin3")
        node = _make_technology_element(db_session, org.id, "Edge Router", "Device")
        _login(client, app, user)

        resp = client.post(
            "/technology/radar/classify",
            data={"archimate_element_id": node.id, "ring": "not-a-real-ring"},
        )
        assert resp.status_code == 400


# --------------------------------------------------------------------- #
# ARCH-123: Data Lineage                                                 #
# --------------------------------------------------------------------- #


class TestDataLineage:
    def test_empty_tenant_renders_honest_empty_state(self, app, db_session, client):
        org = _make_org(db_session, "lineage-empty")
        user = _make_user(db_session, org.id, "LineageViewer")
        _login(client, app, user)

        resp = client.get("/architecture/data-lineage")

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "No data objects modelled yet" in body
        assert 'data-testid="lineage-edge-count"' not in body

    def test_untraced_data_objects_are_listed(self, app, db_session, client):
        org = _make_org(db_session, "lineage-untraced")
        user = _make_user(db_session, org.id, "LineageViewer2")
        _make_data_object(db_session, org.id, "Order Record")
        _login(client, app, user)

        resp = client.get("/architecture/data-lineage")

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Order Record" in body
        assert 'data-testid="lineage-untraced"' in body

    def test_create_lineage_requires_two_real_data_objects(self, app, db_session, client):
        from app.models.all_missing_models import DataLineage

        org = _make_org(db_session, "lineage-create")
        user = _make_user(db_session, org.id, "LineageAdmin")
        source = _make_data_object(db_session, org.id, "Customer Master")
        target = _make_data_object(db_session, org.id, "Customer Warehouse Copy")
        _login(client, app, user)

        resp = client.post(
            "/architecture/data-lineage/create",
            data={"source_id": source.id, "target_id": target.id, "lineage_type": "ETL"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        row = DataLineage.query.filter_by(
            archimate_element_id=source.id, target_archimate_element_id=target.id
        ).first()
        assert row is not None
        assert row.lineage_type == "ETL"

    def test_create_lineage_rejects_nonexistent_target(self, app, db_session, client):
        from app.models.all_missing_models import DataLineage

        org = _make_org(db_session, "lineage-reject")
        user = _make_user(db_session, org.id, "LineageAdmin2")
        source = _make_data_object(db_session, org.id, "Invoice Record")
        _login(client, app, user)

        before = DataLineage.query.filter_by(archimate_element_id=source.id).count()
        resp = client.post(
            "/architecture/data-lineage/create",
            data={"source_id": source.id, "target_id": 999999999, "lineage_type": "ETL"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        after = DataLineage.query.filter_by(archimate_element_id=source.id).count()
        assert after == before == 0  # nothing fabricated against a fake id

    def test_recorded_lineage_row_appears_on_the_view(self, app, db_session, client):
        from app.models.all_missing_models import DataLineage

        org = _make_org(db_session, "lineage-view")
        user = _make_user(db_session, org.id, "LineageAdmin3")
        source = _make_data_object(db_session, org.id, "Policy Record")
        target = _make_data_object(db_session, org.id, "Claims Record")
        _login(client, app, user)

        row = DataLineage(
            name=f"{source.name} -> {target.name}",
            archimate_element_id=source.id,
            target_archimate_element_id=target.id,
            lineage_type="Batch",
            created_by_id=user.id,
            organization_id=org.id,
        )
        db_session.add(row)
        db_session.commit()

        resp = client.get("/architecture/data-lineage")
        body = resp.get_data(as_text=True)
        assert "Policy Record" in body
        assert "Claims Record" in body
        assert 'data-testid="lineage-recorded"' in body
