"""An SSO email domain belongs to one organisation only (review D-06).

Real PostgreSQL, real routes, real models: saves go through the admin form and
login redirects go through ``get_config_for_email``.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError


def _admin(db_session, org, label):
    from app.models import Permission, Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    user = User(email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name=label,
                last_name="Admin", organization_id=org.id, role=role, confirmed=True)
    db_session.add(user)
    db_session.flush()
    return user


def _member(db_session, org, email):
    from app.models import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    user = User(email=email, first_name="M", last_name="M", organization_id=org.id,
                role=role, confirmed=True)
    db_session.add(user)
    db_session.flush()
    return user


def _save(client, login_as, admin, domain, enabled=True):
    login_as(client, admin)
    data = {"protocol": "oidc", "email_domain": domain, "client_id": "cid",
            "idp_metadata_url": "https://idp.example/.well-known/openid-configuration",
            "client_secret": "s"}
    if enabled:
        data["enabled"] = "on"
    return client.post("/admin/sso", data=data, follow_redirects=True)


def _config(org_id):
    from app.models.sso_config import SSOConfig

    return SSOConfig.query.filter_by(organization_id=org_id).first()


@pytest.fixture
def two_orgs(db_session, make_org):
    a, b = make_org("a"), make_org("b")
    return a, b, _admin(db_session, a, "a"), _admin(db_session, b, "b")


def test_second_org_cannot_claim_a_domain_in_any_case(client, login_as, db_session, two_orgs):
    org_a, org_b, admin_a, admin_b = two_orgs
    domain = f"x{uuid.uuid4().hex[:6]}.test"
    resp = _save(client, login_as, admin_a, domain)
    assert b"SSO configuration saved" in resp.data
    resp = _save(client, login_as, admin_b, domain.upper())
    body = resp.data.decode()
    assert "already in use" in body
    assert "SSO configuration saved" not in body
    # The message must not reveal which organisation holds the domain.
    assert org_a.name not in body and org_a.slug not in body
    assert _config(org_b.id) is None


def test_overlap_inside_a_list_is_refused(client, login_as, db_session, two_orgs):
    org_a, org_b, admin_a, admin_b = two_orgs
    domain = f"y{uuid.uuid4().hex[:6]}.test"
    _save(client, login_as, admin_a, domain)
    resp = _save(client, login_as, admin_b, f"free.test, {domain}")
    assert b"already in use" in resp.data
    assert _config(org_b.id) is None


def test_org_can_resave_its_own_domain(client, login_as, db_session, two_orgs):
    org_a, _b, admin_a, _ = two_orgs
    domain = f"z{uuid.uuid4().hex[:6]}.test"
    _save(client, login_as, admin_a, domain)
    resp = _save(client, login_as, admin_a, domain.upper())
    assert b"SSO configuration saved" in resp.data
    assert _config(org_a.id).email_domain == domain


def test_known_user_never_redirected_to_another_orgs_idp(db_session, two_orgs):
    """Even if a duplicate claim exists (legacy row), org B's user gets no config of org A."""
    from app.models.sso_config import SSOConfig
    from app.services.sso_service import SSOService

    org_a, org_b, _aa, _ab = two_orgs
    domain = f"w{uuid.uuid4().hex[:6]}.test"
    db_session.add(SSOConfig(organization_id=org_a.id, protocol="oidc", enabled=True,
                             email_domain=domain, client_id="a"))
    db_session.flush()
    victim = _member(db_session, org_b, f"victim@{domain}")
    assert SSOService().get_config_for_email(victim.email) is None


def test_own_org_users_still_use_own_sso(db_session, two_orgs):
    from app.models.sso_config import SSOConfig
    from app.services.sso_service import SSOService

    org_a, _b, _aa, _ab = two_orgs
    domain = f"v{uuid.uuid4().hex[:6]}.test"
    cfg = SSOConfig(organization_id=org_a.id, protocol="oidc", enabled=True,
                    email_domain=domain, client_id="a")
    db_session.add(cfg)
    db_session.flush()
    member = _member(db_session, org_a, f"Alice@{domain.upper()}")
    svc = SSOService()
    assert svc.get_config_for_email(member.email).id == cfg.id
    # Not yet a user: falls back to the domain match (first sign-in / JIT).
    assert svc.get_config_for_email(f"new@{domain}").id == cfg.id


def test_database_refuses_two_enabled_configs_with_the_same_domain(db_session, two_orgs):
    from app.models.sso_config import SSOConfig

    org_a, org_b, _aa, _ab = two_orgs
    domain = f"u{uuid.uuid4().hex[:6]}.test"
    db_session.add(SSOConfig(organization_id=org_a.id, protocol="oidc", enabled=True,
                             email_domain=domain, client_id="a"))
    db_session.flush()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(SSOConfig(organization_id=org_b.id, protocol="oidc", enabled=True,
                                     email_domain=f" {domain.upper()} ", client_id="b"))
            db_session.flush()
    # A disabled draft with the same text is allowed by the index.
    db_session.add(SSOConfig(organization_id=org_b.id, protocol="oidc", enabled=False,
                             email_domain=domain, client_id="b"))
    db_session.flush()


def test_migration_is_idempotent_and_disables_later_duplicates(db_session, two_orgs):
    import importlib.util
    from pathlib import Path

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import text

    org_a, org_b, _aa, _ab = two_orgs
    domain = f"t{uuid.uuid4().hex[:6]}.test"
    bind = db_session.connection()
    bind.execute(text("DROP INDEX IF EXISTS uq_sso_configs_enabled_email_domain"))
    ids = []
    for org in (org_a, org_b):
        ids.append(bind.execute(text(
            "INSERT INTO sso_configs (organization_id, protocol, enabled, email_domain) "
            "VALUES (:o, 'oidc', true, :d) RETURNING id"), {"o": org.id, "d": domain}).scalar())
    path = Path(__file__).resolve().parents[1] / "migrations/versions/20261008_sso_domain_unique.py"
    spec = importlib.util.spec_from_file_location("sso_domain_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with Operations.context(MigrationContext.configure(bind)):
        mod.upgrade()
        mod.upgrade()  # second run: no error
    states = dict(bind.execute(text(
        "SELECT id, enabled FROM sso_configs WHERE id = ANY(:i)"), {"i": ids}).fetchall())
    assert states[ids[0]] is True and states[ids[1]] is False
    assert mod.down_revision == "20261007_public_visitor_events"


def test_single_migration_head():
    import re
    from pathlib import Path

    revs, downs = set(), set()
    for f in (Path(__file__).resolve().parents[1] / "migrations/versions").glob("*.py"):
        s = f.read_text(encoding="utf8")
        r = re.search(r"^revision\s*=\s*['\"]([\w]+)", s, re.M)
        d = re.search(r"^down_revision\s*=\s*['\"]([\w]+)", s, re.M)
        if r:
            revs.add(r.group(1))
        if d:
            downs.add(d.group(1))
    heads = revs - downs
    assert heads == {"20261008_sso_domain_unique"}
