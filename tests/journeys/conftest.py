"""Shared harness for persona journey tests.

pytest.ini has declared this gate since before the open-source release:

    journey: End-to-end journey tests - one per user persona, CI-enforced gate

No such test existed, in the working tree or anywhere in git history. These are
the first. They exist because a static reading of this codebase cannot tell you
whether an archetype can do its job: on 2026-07-30 static analysis found 43
endpoints that were present in source and raised TypeError on every call.

A journey asserts an archetype can complete the work it exists to do, end to
end, over real HTTP - not that a route is registered.
"""

import uuid

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app, db
    from app.models.user import Role

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        db.create_all()
        Role.insert_roles()

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def make_org(db, label):
    """A real Organization row.

    slug is NOT NULL and has no default, so it must be supplied explicitly -
    omitting it raises NotNullViolation on commit rather than at construction.
    """
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(
        name="%s Org %s" % (label, suffix),
        slug="%s-org-%s" % (label.lower().replace(" ", "-"), suffix),
    )
    db.session.add(org)
    db.session.flush()
    db.session.commit()
    return org.id


def make_user(db, org_id, label, enterprise_role, role_name="Administrator", is_platform_admin=False):
    """A user pinned to *org_id* and carrying *enterprise_role*.

    enterprise_role is the field ENTERPRISE_ROLE_SECTION_MAP keys on, so it is
    what actually determines the archetype's navigation. It is a separate
    vocabulary from the boolean is_platform_admin flag that
    app.middleware.tenant_decorators.platform_admin_required actually checks
    (see that module's own docstring on the two authz vocabularies) -- a user
    with enterprise_role="platform_admin" is not, on its own, a platform admin
    in the sense any platform_admin_required route cares about. Pass
    is_platform_admin=True explicitly for a journey that needs to pass one of
    those routes.

    organization_id is set explicitly: User has a before_insert listener that
    reassigns an unset organization_id to the shared default org, which would
    silently break any tenancy assertion in a journey.

    role_name attaches a Role, because the test app only runs db.create_all()
    and never Role.insert_roles() - so users otherwise have no role at all and
    every @require_roles route returns 403 before the journey reaches anything
    worth asserting.
    """
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email="%s-%s@example.com" % (label.lower(), suffix),
        first_name=label,
        last_name="Journey",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=enterprise_role,
        is_platform_admin=is_platform_admin,
    )
    db.session.add(user)
    db.session.flush()

    if role_name:
        role = Role.query.filter_by(name=role_name).first()
        if role is None:
            Role.insert_roles()
            role = Role.query.filter_by(name=role_name).first()
        if role is None:
            raise RuntimeError("Canonical role %r was not seeded" % role_name)
        user.role = role

    db.session.commit()
    return user.id


def login(client, user_id):
    """Sign *client* in as *user_id*, defeating flask_login's ``g`` cache.

    When a test keeps one app or request context open for its whole length,
    ``g`` survives between test-client requests. flask_login caches the
    resolved user on ``g._login_user`` and the tenant middleware caches
    ``g.current_org_id``, so writing a new ``_user_id`` into the session cookie
    does NOT change who the next request runs as: a second client keeps
    executing as the first client's user.

    That reads as a cross-tenant leak, and it is not one -- it is the test
    harness. This exact artifact previously produced four false leak reports
    (see the note in tests/test_ba_tenant_and_authz.py::_login and the tenancy
    section of CLAUDE.md). Clearing the caches here means a tenancy assertion in
    a journey test measures the application, not the fixture.

    Journey tests log in after their ``with app.app_context():`` setup blocks
    have closed, and nothing here supplies an ambient context (pytest-flask is
    not installed; ``base_url`` comes from pytest-base-url). The session row is
    therefore minted through ``client.application``, and the login fails
    loudly if no sid comes back: a session without one is rejected as revoked.
    """
    from flask import g, has_app_context

    from tests._session_test_helpers import mint_test_sid

    _sid = mint_test_sid(user_id, app=client.application)
    assert _sid, "test login for user %r did not register a session" % (user_id,)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        sess["_sid"] = _sid

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)


def cleanup(db, model, ids):
    for i in ids:
        if i is None:
            continue
        try:
            obj = db.session.get(model, i)
            if obj is not None:
                db.session.delete(obj)
                db.session.commit()
        except Exception:
            db.session.rollback()


def reachable(client, path):
    """True when a path resolves to something the app serves.

    404 means the route is not registered at all. A redirect is reachable -
    Flask emits 308 for a missing trailing slash and 302 for auth flows, and
    neither means the capability is absent.
    """
    resp = client.get(path, follow_redirects=False)
    return resp.status_code != 404, resp.status_code
