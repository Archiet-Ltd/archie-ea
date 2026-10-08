"""RAID log (Issue/Dependency) — the 2 Sep 2026 Capgemini delivery-team
dry-run found the register was Risk-only. These tests pin the FULL round
trip — create via the real API, then read it back both via the API and via
the register page's own render — not just that a row lands in the database.
That distinction is the exact lesson from this session's plateau-tagging bug
(write worked, nothing could ever read it back): a feature is not done until
a human can see what they just recorded, and that is what these tests
actually check.

RaidKind originally also had ASSUMPTION, added without checking whether an
Assumption store already existed — it did (app/models/demand.py, richer,
already FK'd to a programme). Removed the same session it was caught; the
tests below reflect Issue/Dependency only.
"""

import pytest

from tests.test_ba_tenant_and_authz import _cleanup_ids, _login, _make_org_id, _make_user_id


@pytest.fixture(scope="module")
def app():
    from app import create_app, db

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def ea_user(app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "Raid")
        uid = _make_user_id(db, org, "RaidEA", enterprise_role="enterprise_architect")
        yield uid, org
        from app.models.raid_item import RaidItem
        _cleanup_ids(db, RaidItem, [i.id for i in RaidItem.query.filter_by(organization_id=org).all()])
        _cleanup_ids(db, User, [uid])
        _cleanup_ids(db, Organization, [org])


def test_create_then_read_back_via_api(client, app, ea_user):
    """The exact round trip a real user needs: create a Dependency, then GET
    it back — not just assert a DB row exists."""
    uid, org = ea_user
    with app.app_context():
        _login(client, uid)
        r = client.post("/api/raid", json={
            "kind": "dependency",
            "title": "S/4HANA landscape build blocks Order-to-Cash dual-run",
            "owner": "Marcus Reed",
            "target_date": "2026-05-30",
            "programme_name": "Constellation",
        })
        assert r.status_code == 201, r.get_data(as_text=True)
        created = r.get_json()
        assert created["kind"] == "dependency"

        # Read back — this is the check the plateau bug skipped.
        r2 = client.get("/api/raid")
        assert r2.status_code == 200
        titles = [i["title"] for i in r2.get_json()]
        assert "S/4HANA landscape build blocks Order-to-Cash dual-run" in titles

        r3 = client.get("/api/raid?kind=dependency")
        kinds = {i["kind"] for i in r3.get_json()}
        assert kinds == {"dependency"}


def test_create_then_read_back_via_register_page_render(client, app, ea_user):
    """Not just the API — the actual HTML the register page renders, which is
    what a human looking at the screen actually sees."""
    uid, org = ea_user
    with app.app_context():
        _login(client, uid)
        r = client.post("/api/raid", json={
            "kind": "issue",
            "title": "Year-end close overlaps cutover window",
        })
        assert r.status_code == 201

        page = client.get("/risks/")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "Year-end close overlaps cutover window" in html
        assert "issue" in html.lower()


def test_status_transition_round_trips(client, app, ea_user):
    uid, org = ea_user
    with app.app_context():
        _login(client, uid)
        r = client.post("/api/raid", json={"kind": "dependency", "title": "Northwind stays on current AWS region"})
        item_id = r.get_json()["id"]

        r2 = client.patch(f"/api/raid/{item_id}", json={"status": "resolved"})
        assert r2.status_code == 200
        assert r2.get_json()["status"] == "resolved"

        # Read back independently — the update actually persisted, not just echoed.
        r3 = client.get("/api/raid")
        match = next(i for i in r3.get_json() if i["id"] == item_id)
        assert match["status"] == "resolved"


def test_invalid_kind_rejected_not_silently_dropped(client, app, ea_user):
    uid, org = ea_user
    with app.app_context():
        _login(client, uid)
        r = client.post("/api/raid", json={"kind": "bogus", "title": "X"})
        assert r.status_code == 400
        assert "error" in r.get_json()


def test_assumption_kind_no_longer_accepted(client, app, ea_user):
    """ASSUMPTION was removed once its duplicate (demand.Assumption) was
    found — confirm the API rejects it rather than silently accepting a kind
    that no longer round-trips through anything."""
    uid, org = ea_user
    with app.app_context():
        _login(client, uid)
        r = client.post("/api/raid", json={"kind": "assumption", "title": "X"})
        assert r.status_code == 400
        assert "error" in r.get_json()


def test_programme_link_round_trips_via_api_and_page(client, app, ea_user):
    """The concrete gap this closes: a RAID item can be tied to a real
    StrategicInitiative row (queryable), not just a free-text programme name
    a human typed. Create the programme, link a Dependency to it, then read
    the link back both via the API and via the rendered register page."""
    uid, org = ea_user
    with app.app_context():
        from app import db
        from app.models.strategic import StrategicInitiative

        programme = StrategicInitiative(
            name="Constellation", organization_id=org, record_kind="transformation_programme"
        )
        db.session.add(programme)
        db.session.commit()
        programme_id = programme.id

        _login(client, uid)
        r = client.post("/api/raid", json={
            "kind": "dependency",
            "title": "MuleSoft integration layer must land before SAP cutover",
            "strategic_initiative_id": programme_id,
        })
        assert r.status_code == 201, r.get_data(as_text=True)
        created = r.get_json()
        assert created["strategic_initiative_id"] == programme_id
        assert created["programme_name"] == "Constellation"

        # Read back via the filtered list API — queryable by programme, which
        # a free-text field never was.
        r2 = client.get(f"/api/raid?strategic_initiative_id={programme_id}")
        assert r2.status_code == 200
        titles = [i["title"] for i in r2.get_json()]
        assert "MuleSoft integration layer must land before SAP cutover" in titles

        # And via the actual rendered page.
        page = client.get("/risks/")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "MuleSoft integration layer must land before SAP cutover" in html
        assert "Constellation" in html

        db.session.delete(programme)
        db.session.commit()


def test_non_canonical_initiative_rejected(client, app, ea_user):
    """A StrategicInitiative without record_kind='transformation_programme'
    is an ordinary internal initiative, not a canonical programme — the same
    gate the Portfolio module's Benefit linkage already applies. Confirm the
    API enforces it too, not just the UI picker."""
    uid, org = ea_user
    with app.app_context():
        from app import db
        from app.models.strategic import StrategicInitiative

        plain = StrategicInitiative(name="Just an upgrade project", organization_id=org)
        db.session.add(plain)
        db.session.commit()
        plain_id = plain.id

        _login(client, uid)
        r = client.post("/api/raid", json={
            "kind": "issue", "title": "X", "strategic_initiative_id": plain_id,
        })
        assert r.status_code == 400
        assert "error" in r.get_json()

        db.session.delete(plain)
        db.session.commit()


def test_unknown_programme_id_rejected(client, app, ea_user):
    uid, org = ea_user
    with app.app_context():
        _login(client, uid)
        r = client.post("/api/raid", json={
            "kind": "issue", "title": "X", "strategic_initiative_id": 999999,
        })
        assert r.status_code == 400
        assert "error" in r.get_json()


def test_strategic_initiative_goal_link_round_trips(app, ea_user):
    """The other half of the same gap: a programme's Goal alignment used to
    be a JSON list of goal NAME strings on the initiative row — unqueryable
    and silently stale the moment a goal was renamed. Confirm the new
    strategic_initiative_goals junction actually links and queries back."""
    uid, org = ea_user
    with app.app_context():
        from app import db
        from app.models.motivation import Goal
        from app.models.strategic import StrategicInitiative

        programme = StrategicInitiative(
            name="Constellation", organization_id=org, record_kind="transformation_programme"
        )
        goal = Goal(name="Retire SCADE by Q4 2026", organization_id=org)
        db.session.add_all([programme, goal])
        db.session.commit()
        programme.goals.append(goal)
        db.session.commit()
        programme_id, goal_id = programme.id, goal.id

        # Independent read: reload the programme fresh and confirm the goal
        # is reachable from it, not just held in the in-memory session.
        db.session.expunge_all()
        reloaded = db.session.get(StrategicInitiative, programme_id)
        assert [g.name for g in reloaded.goals] == ["Retire SCADE by Q4 2026"]

        db.session.delete(db.session.get(Goal, goal_id))
        db.session.delete(db.session.get(StrategicInitiative, programme_id))
        db.session.commit()
