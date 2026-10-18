"""Reading or importing the platform's own LLM provider API keys is platform-wide
configuration: only a platform admin may do either.

``GET /admin/api-settings/env-keys`` and ``POST /admin/api-settings/load-env`` both read
straight from ``os.environ`` -- the real ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``,
``OPENROUTER_API_KEY`` and the rest of the provider map these routes share. ``env-keys``
returns the last four characters of each configured key's real value; ``load-env`` writes
the full real value into an ``APISettings`` row owned by the caller's own organisation
(``APISettings`` is tenant-scoped -- ``organization_id`` is auto-set on INSERT by the
tenant-isolation ORM listener). Both were guarded only by ``@admin_required``
(``Permission.ADMINISTER``), which every self-registered organisation's own administrator
holds for their own brand-new org -- no invitation into anyone else's org needed. Any
tenant admin could walk away with a working copy of the platform's real LLM provider
credential, stored and usable from inside their own organisation (lead's own review,
finding S1, CRITICAL, reproduced). They now also require ``@platform_admin_required``,
stacked alongside the existing ``@admin_required`` rather than replacing it.

Both live module trees carry these two routes at the same URLs
(``app/modules/admin/v2/routes/admin_routes.py``, guardrail-enabled, and
``app/modules/admin/routes/admin_routes.py``, the v1 modular tree) -- fixed in both
defensively, since which tree is registered depends on the ``USE_ADMIN_GUARDRAILS``/
``USE_NEW_ADMIN`` flags at deploy time and this test suite cannot assume either is the one
running in production (same reasoning as ``test_jira_routes_platform_admin.py`` and
``test_abacus_admin_routes_platform_admin.py``). Because both trees serve the identical URL
under the same ``/admin`` prefix, a single URL-routed test here exercises whichever tree
this process has mounted, and passes because both were fixed.
"""

from __future__ import annotations

import uuid

import pytest


def _user(db_session, org, *, platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"envkeys-{uuid.uuid4().hex[:6]}@example.test",
        first_name="EnvKeys",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _world(db_session, make_org):
    # Two organisations, DEFECT-7 pattern: a tenant admin from EITHER org must be
    # refused, proving this is a platform-admin check and not an org-specific one
    # that happens to match org A by coincidence.
    org_a = make_org("envkeys-a")
    org_b = make_org("envkeys-b")
    tenant_admin_a = _user(db_session, org_a)
    tenant_admin_b = _user(db_session, org_b)
    platform_admin = _user(db_session, org_a, platform=True)
    db_session.commit()
    return tenant_admin_a.id, tenant_admin_b.id, platform_admin.id


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


# Both of the lead's named S1 routes -- a GET preview and a POST import -- neither
# requires any pre-existing resource to answer 403; authorisation runs before any of
# the view's own body logic.
ENV_KEY_ROUTES = [
    ("get", "/admin/api-settings/env-keys"),
    ("post", "/admin/api-settings/load-env"),
]


@pytest.mark.parametrize("method,path", ENV_KEY_ROUTES)
def test_a_tenant_administrator_is_refused_on_both_env_key_routes(
    app, db_session, make_org, client, login_as, method, path
):
    tenant_admin_a_id, tenant_admin_b_id, _platform_id = _world(db_session, make_org)

    for admin_id in (tenant_admin_a_id, tenant_admin_b_id):
        _login(db_session, client, login_as, admin_id)
        if method == "post":
            response = client.post(path, json={"keys": ["OPENAI_API_KEY"]})
        else:
            response = getattr(client, method)(path)
        assert response.status_code == 403, (
            f"{method.upper()} {path} should refuse a plain organisation admin "
            f"(self-registered, no session switch into any other org); got "
            f"{response.status_code}"
        )


@pytest.mark.parametrize("method,path", ENV_KEY_ROUTES)
def test_a_platform_administrator_is_never_refused_on_either_env_key_route(
    app, db_session, make_org, client, login_as, method, path
):
    _tenant_a_id, _tenant_b_id, platform_admin_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_admin_id)
    if method == "post":
        response = client.post(path, json={"keys": ["OPENAI_API_KEY"]})
    else:
        response = getattr(client, method)(path)

    assert response.status_code not in (401, 403)


def test_tenant_administrator_cannot_copy_the_platforms_real_openai_key_into_their_own_org(
    monkeypatch, db_session, make_org, client, login_as
):
    """Direct proof of the S1 attack path: a tenant admin POSTing load-env must be
    refused outright -- not merely have the POST silently no-op -- and the platform's
    real key value must never land in the tenant's own ``APISettings`` row."""
    from app.models.models import APISettings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-REAL-PLATFORM-SECRET-DO-NOT-LEAK")

    org_a = make_org("envkeys-load-a")
    tenant_admin = _user(db_session, org_a)
    org_a_id = org_a.id
    db_session.commit()

    _login(db_session, client, login_as, tenant_admin.id)
    response = client.post(
        "/admin/api-settings/load-env",
        json={"keys": ["OPENAI_API_KEY"], "update_existing": False},
    )

    assert response.status_code == 403

    db_session.expunge_all()
    leaked = APISettings.query.filter_by(
        organization_id=org_a_id, provider="openai"
    ).first()
    assert leaked is None, (
        "the platform's real OPENAI_API_KEY must never be written into a tenant's "
        "own APISettings row"
    )
