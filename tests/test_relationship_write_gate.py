"""Relationship create and edit are gated by the permission matrix and checked
against the caller's active organisation (T-perm-02a)."""

import uuid

import pytest


@pytest.fixture
def client(app):
    return app.test_client()


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"RW {label} {suffix}", slug=f"rw-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, org_role):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    user = User(
        email=f"rw-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Rel",
        last_name=org_role,
        organization_id=org.id,
        role=Role.query.filter_by(name="User").first(),
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, org_role)
    db_session.flush()
    return user


def _element(db_session, org, name):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(
        name=f"{name} {uuid.uuid4().hex[:6]}",
        type="ApplicationComponent",
        layer="application",
        organization_id=org.id,
    )
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org, a, b):
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=a.id, target_id=b.id, type="flow", organization_id=org.id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _count(db_session, org_id=None):
    from app.models.archimate_core import ArchiMateRelationship

    db_session.expire_all()
    return db_session.query(ArchiMateRelationship).count()


def _payload(a, b):
    return {
        "source_element_id": a.id,
        "target_element_id": b.id,
        "relationship_type": "flow",
    }


class TestCreate:
    def test_viewer_create_is_refused(self, app, db_session, login_as, client):
        org = _org(db_session, "vc")
        viewer = _user(db_session, org, "viewer")
        a, b = _element(db_session, org, "A"), _element(db_session, org, "B")
        db_session.commit()
        before = _count(db_session)
        login_as(client, viewer)
        resp = client.post("/archimate/api/relationships", json=_payload(a, b))
        assert resp.status_code == 403
        assert _count(db_session) == before

    def test_architect_create_succeeds(self, app, db_session, login_as, client):
        org = _org(db_session, "ac")
        architect = _user(db_session, org, "architect")
        a, b = _element(db_session, org, "A"), _element(db_session, org, "B")
        db_session.commit()
        before = _count(db_session)
        login_as(client, architect)
        resp = client.post("/archimate/api/relationships", json=_payload(a, b))
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert _count(db_session) == before + 1

    def test_create_across_organisations_is_not_found(
        self, app, db_session, login_as, client
    ):
        org_a, org_b = _org(db_session, "xa"), _org(db_session, "xb")
        architect = _user(db_session, org_a, "architect")
        mine = _element(db_session, org_a, "mine")
        theirs = _element(db_session, org_b, "theirs")
        db_session.commit()
        before = _count(db_session)
        login_as(client, architect)
        # B's element is already in the session's identity map.
        from app.models.archimate_core import ArchiMateElement

        assert db_session.get(ArchiMateElement, theirs.id) is not None
        for src, tgt in ((mine, theirs), (theirs, mine)):
            resp = client.post("/archimate/api/relationships", json=_payload(src, tgt))
            assert resp.status_code == 404, resp.get_data(as_text=True)
        assert _count(db_session) == before


class TestEdit:
    def test_viewer_edit_is_refused(self, app, db_session, login_as, client):
        org = _org(db_session, "ve")
        viewer = _user(db_session, org, "viewer")
        a, b = _element(db_session, org, "A"), _element(db_session, org, "B")
        rel = _relationship(db_session, org, a, b)
        db_session.commit()
        login_as(client, viewer)
        resp = client.put(f"/archimate/api/relationships/{rel.id}", json={"description": "x"})
        assert resp.status_code == 403
        db_session.expire_all()
        assert rel.description != "x"

    def test_architect_edit_succeeds(self, app, db_session, login_as, client):
        org = _org(db_session, "ae")
        architect = _user(db_session, org, "architect")
        a, b = _element(db_session, org, "A"), _element(db_session, org, "B")
        rel = _relationship(db_session, org, a, b)
        db_session.commit()
        login_as(client, architect)
        resp = client.put(f"/archimate/api/relationships/{rel.id}", json={"description": "ok"})
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_edit_across_organisations_is_not_found(
        self, app, db_session, login_as, client
    ):
        org_a, org_b = _org(db_session, "ea"), _org(db_session, "eb")
        architect = _user(db_session, org_a, "architect")
        x, y = _element(db_session, org_b, "X"), _element(db_session, org_b, "Y")
        # Foreign relationship, and a relationship in the caller's organisation
        # whose endpoints belong to the other organisation.
        foreign = _relationship(db_session, org_b, x, y)
        mixed = _relationship(db_session, org_a, x, y)
        db_session.commit()
        login_as(client, architect)
        from app.models.archimate_core import ArchiMateRelationship

        # Both rows are in the session's identity map before the request.
        assert db_session.get(ArchiMateRelationship, foreign.id) is not None
        for rel in (foreign, mixed):
            resp = client.put(
                f"/archimate/api/relationships/{rel.id}", json={"description": "taken"}
            )
            assert resp.status_code == 404, resp.get_data(as_text=True)
        db_session.expire_all()
        assert foreign.description != "taken"
        assert mixed.description != "taken"


class TestDelete:
    def _exists(self, db_session, rel_id):
        from app.models.archimate_core import ArchiMateRelationship

        db_session.expire_all()
        return db_session.get(ArchiMateRelationship, rel_id) is not None

    def test_viewer_delete_is_refused(self, app, db_session, login_as, client):
        org = _org(db_session, "vd")
        viewer = _user(db_session, org, "viewer")
        a, b = _element(db_session, org, "A"), _element(db_session, org, "B")
        rel = _relationship(db_session, org, a, b)
        db_session.commit()
        login_as(client, viewer)
        resp = client.delete(f"/archimate/api/relationships/{rel.id}")
        assert resp.status_code == 403
        assert self._exists(db_session, rel.id)

    def test_architect_delete_succeeds(self, app, db_session, login_as, client):
        org = _org(db_session, "ad")
        architect = _user(db_session, org, "architect")
        a, b = _element(db_session, org, "A"), _element(db_session, org, "B")
        rel = _relationship(db_session, org, a, b)
        rel_id = rel.id
        db_session.commit()
        login_as(client, architect)
        resp = client.delete(f"/archimate/api/relationships/{rel_id}")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert not self._exists(db_session, rel_id)

    def test_delete_across_organisations_is_not_found(
        self, app, db_session, login_as, client
    ):
        org_a, org_b = _org(db_session, "da"), _org(db_session, "db")
        architect = _user(db_session, org_a, "architect")
        x, y = _element(db_session, org_b, "X"), _element(db_session, org_b, "Y")
        foreign = _relationship(db_session, org_b, x, y)
        mixed = _relationship(db_session, org_a, x, y)
        ids = (foreign.id, mixed.id)
        db_session.commit()
        login_as(client, architect)
        from app.models.archimate_core import ArchiMateRelationship

        assert db_session.get(ArchiMateRelationship, foreign.id) is not None
        for rid in ids:
            resp = client.delete(f"/archimate/api/relationships/{rid}")
            assert resp.status_code == 404, resp.get_data(as_text=True)
        for rid in ids:
            assert self._exists(db_session, rid)
