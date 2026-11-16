"""An organisation's administrator renames their workspace; every path uses the one rename function."""
import re
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from app.modules.organization.public_signals import PLACEHOLDER_ENDING

READ_ONLY_SENTENCE = "Your role is read-only, so it cannot make this change."
STAND_IN = "Ada Lovelace" + PLACEHOLDER_ENDING
NEW_NAME = "Analytical Engines Ltd"


def _org(db_session, label, name=None):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=name or f"Rename {label} {suffix}", slug=f"rename-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, role="admin"):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    if Role.query.filter_by(name="Administrator").first() is None:
        Role.insert_roles()
    held = Role.query.filter_by(name="Administrator" if role == "admin" else "User").first()
    user = User(
        email=f"rename-{uuid.uuid4().hex[:8]}@example.com", first_name="Test", last_name="User",
        organization_id=org.id, role=held,
        is_org_admin=(role == "admin"), confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    db_session.add(OrgRole(organization_id=org.id, user_id=user.id, role="org_admin" if role == "admin" else role))
    db_session.commit()
    return user


def _rename(app, client, login_as, user, org_id, name):
    with app.app_context():
        login_as(client, user)
        return client.post(f"/admin/organizations/{org_id}/rename", data={"name": name})


def test_administrator_renames_and_slug_and_audit_hold(app, db_session, login_as, client):
    from app.models.audit_log import AuditLog
    from app.models.organization import Organization

    org = _org(db_session, "ok", name=STAND_IN)
    admin = _user(db_session, org)
    slug, org_id = org.slug, org.id
    resp = _rename(app, client, login_as, admin, org_id, "  " + NEW_NAME + "  ")
    assert resp.status_code == 302
    db_session.expire_all()
    kept = db_session.get(Organization, org_id)
    assert (kept.name, kept.slug) == (NEW_NAME, slug)
    row = AuditLog.query.filter_by(action="organisation_renamed", record_id=org_id).one()
    assert row.old_value == {"name": STAND_IN} and row.new_value == {"name": NEW_NAME}
    assert row.user_id == admin.id and row.created_at is not None


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_member_and_viewer_are_refused_with_the_read_only_sentence(app, db_session, login_as, client, role):
    org = _org(db_session, role, name=STAND_IN)
    person = _user(db_session, org, role=role)
    org_id = org.id
    resp = _rename(app, client, login_as, person, org_id, NEW_NAME)
    assert resp.status_code == 403
    assert READ_ONLY_SENTENCE in resp.get_data(as_text=True)
    db_session.expire_all()
    from app.models.organization import Organization

    assert db_session.get(Organization, org_id).name == STAND_IN


def test_another_organisations_administrator_gets_404(app, db_session, login_as, client):
    from app.models.organization import Organization

    org_a, org_b = _org(db_session, "a"), _org(db_session, "b", name=STAND_IN)
    admin_a = _user(db_session, org_a)
    org_b_id = org_b.id
    resp = _rename(app, client, login_as, admin_a, org_b_id, NEW_NAME)
    assert resp.status_code == 404
    db_session.expire_all()
    assert db_session.get(Organization, org_b_id).name == STAND_IN


@pytest.mark.parametrize("bad, said", [("   ", "Enter a name"), ("x" * 201, "200 characters or fewer")])
def test_blank_or_too_long_name_is_refused_next_to_the_field(app, db_session, login_as, client, bad, said):
    from app.models.organization import Organization

    org = _org(db_session, "bad", name=STAND_IN)
    admin = _user(db_session, org)
    org_id = org.id
    resp = _rename(app, client, login_as, admin, org_id, bad)
    assert resp.status_code == 302
    page = client.get("/admin/")
    html = page.get_data(as_text=True)
    assert 'data-testid="workspace-name-error"' in html and said in html
    db_session.expire_all()
    assert db_session.get(Organization, org_id).name == STAND_IN


def test_nothing_stored_under_the_stand_in_name_survives_a_rename(app, db_session):
    from app.models.organization_signal import OrganizationSignal
    from app.services.organisation_rename import rename_organisation

    org = _org(db_session, "signals", name=STAND_IN)
    db_session.add(OrganizationSignal(
        organization_id=org.id, checksum=uuid.uuid4().hex, fetched_at=datetime.utcnow(),
        source="news", kind="news", subject=STAND_IN, title="Ada Lovelace in the news"))
    db_session.commit()
    rename_organisation(org, NEW_NAME)
    db_session.commit()
    left = OrganizationSignal.query.filter_by(organization_id=org.id).all()
    assert [r for r in left if PLACEHOLDER_ENDING in (r.subject or "")] == []
    assert PLACEHOLDER_ENDING not in org.name


def test_every_path_that_sets_the_name_calls_the_one_function():
    root = Path(__file__).resolve().parent.parent
    for rel in ("app/modules/admin/v2/routes/admin_routes.py", "app/modules/organization/website_crawl/confirm.py"):
        assert "rename_organisation(" in (root / rel).read_text(), rel
    assert not re.search(r"\borg\.name\s*=[^=]", (root / "app/modules/organization/website_crawl/confirm.py").read_text())
    assert not re.search(r"\borg\.name\s*=[^=]", (root / "app/modules/admin/v2/routes/admin_routes.py").read_text())
