"""Run-detection takes the Architect role check; the radar's role message is
kept to the case where the role check refused."""

import uuid

import pytest
from werkzeug.exceptions import Forbidden, abort

RUN_URL = "/applications/rationalization/api/run-detection"


@pytest.fixture
def client(app):
    return app.test_client()


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"RD {label} {suffix}", slug=f"rd-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, role_name):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"rd-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name=role_name,
        organization_id=org.id,
        role=Role.query.filter_by(name=role_name).first(),
        is_org_admin=False,
        is_platform_admin=False,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def world(db_session):
    from app.models.application_portfolio import ApplicationComponent

    org = _org(db_session, "own")
    other = _org(db_session, "other")
    for label in ("Ledger Core", "Ledger Core Copy"):
        db_session.add(ApplicationComponent(name=label, organization_id=org.id, lifecycle_status="operational"))
    w = {
        "viewer": _user(db_session, org, "Viewer"),
        "architect": _user(db_session, org, "Architect"),
        "member": _user(db_session, org, "User"),
        "other_architect": _user(db_session, other, "Architect"),
        "org": org,
        "other": other,
    }
    db_session.commit()
    return w


def test_viewer_is_refused_run_detection(app, login_as, client, world):
    with app.app_context():
        login_as(client, world["viewer"])
        assert client.post(RUN_URL, json={"strategy": "bogus"}).status_code == 403


def test_member_without_the_architect_role_is_refused_run_detection(app, login_as, client, world):
    # Passes the blueprint's general write floor (Permission.GENERAL) but is
    # neither admin nor architect, so only the route's own role check refuses.
    with app.app_context():
        login_as(client, world["member"])
        assert client.post(RUN_URL, json={"strategy": "bogus"}).status_code == 403


def test_architect_of_the_organisation_gets_the_run(app, login_as, client, world):
    with app.app_context():
        login_as(client, world["architect"])
        # A bad strategy is answered by the route body (400), which proves the
        # role check let the Architect through without running a full scan.
        assert client.post(RUN_URL, json={"strategy": "bogus"}).status_code == 400


def test_other_organisations_architect_cannot_group_this_organisations_applications(
    app, login_as, client, world
):
    from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

    with app.app_context():
        login_as(client, world["other_architect"])
        client.post(RUN_URL, json={"strategy": "fast", "similarity_threshold": 0.5})
        names = {g.name for g in UnifiedDuplicateGroup.query.all()}
        assert not any("Ledger Core" in (n or "") for n in names)


def test_radar_wrapper_keeps_a_bare_403_from_the_view_unchanged(app, monkeypatch):
    from app.modules.tech_radar import routes

    monkeypatch.setattr(routes, "require_roles", lambda *roles: (lambda f: f))

    @routes._radar_roles_required
    def view():
        abort(403)

    with app.test_request_context("/tech-radar/1/place"):
        with pytest.raises(Forbidden) as exc:
            view()
    assert exc.value.description != routes._PERMISSION_DENIED_MESSAGE


def test_radar_wrapper_names_the_role_when_the_role_check_refuses(app, monkeypatch):
    from app.modules.tech_radar import routes

    def refuse(*roles):
        def deco(f):
            def inner(*a, **k):
                abort(403)

            return inner

        return deco

    monkeypatch.setattr(routes, "require_roles", refuse)

    @routes._radar_roles_required
    def view():
        return "ran"

    with app.test_request_context("/tech-radar/1/place"):
        with pytest.raises(Forbidden) as exc:
            view()
    assert exc.value.description == routes._PERMISSION_DENIED_MESSAGE


def test_organisation_administrator_who_is_not_an_architect_is_refused(app, db_session, login_as, client, world):
    # Hotfix 440: duplicate detection stays off organisation administrators;
    # only the Architect role (or platform authority) starts a run.
    with app.app_context():
        admin = _user(db_session, _org(db_session, "admin"), "Administrator")
        admin.is_org_admin = True
        db_session.commit()
        login_as(client, admin)
        assert client.post(RUN_URL, json={"strategy": "bogus"}).status_code == 403
