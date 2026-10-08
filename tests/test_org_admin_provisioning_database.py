"""Real PostgreSQL provisioning, using the shared outer rollback transaction."""
import runpy
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from flask_login import current_user
from sqlalchemy import event

ROOT = Path(__file__).resolve().parents[1]


def provision(app, monkeypatch, kind, email):
    """Run the real entry point; replace only script configuration/bootstrap."""
    from app import db
    if kind == 'registration':
        from app.modules.account.services.account_service import AccountService
        return AccountService.register_user('Provision', uuid.uuid4().hex[:10], email, 'Disposable-test-password!')
    manage = ModuleType('manage')
    manage.app, manage.db = app, db
    configuration = ModuleType('config')
    configuration.Config = SimpleNamespace(ADMIN_EMAIL=email, ADMIN_PASSWORD='Disposable-test-password!')
    monkeypatch.setitem(sys.modules, 'manage', manage)
    monkeypatch.setitem(sys.modules, 'config', configuration)
    monkeypatch.setitem(app.config, 'ADMIN_EMAIL', email)
    # create_admin opens a nested context. Reuse the fixture-owned context so
    # its scoped session/savepoint remains the one under the rollback contract.
    from contextlib import nullcontext
    manage.app = SimpleNamespace(app_context=nullcontext)
    runpy.run_path(str(ROOT / 'create_admin.py'))
    from app.models.user import User
    return User.query.filter_by(email=email).one()


@pytest.mark.parametrize('kind', ['initial_admin', 'registration'])
def test_new_owner_persists_org_role_and_cannot_administer_foreign_org(
    app, db_session, make_org, monkeypatch, kind
):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User
    from app.services.rbac_service import rbac_service
    Role.insert_roles()
    foreign = make_org('provision-foreign')
    colleague = User(email=f'colleague-{uuid.uuid4().hex}@example.com', organization_id=foreign.id,
                     confirmed=True)
    db_session.add(colleague)
    db_session.flush()
    foreign_id, colleague_id = foreign.id, colleague.id
    OrgRole.set_role(foreign_id, colleague_id, 'viewer')
    db_session.commit()
    email = f'provision-{uuid.uuid4().hex}@example.com'
    with app.test_request_context('/account/register'):
        user = provision(app, monkeypatch, kind, email)
        user_id, org_id = user.id, user.organization_id
        assert user.is_org_admin is True
        if kind == 'registration':
            assert current_user.id == user_id
        db_session.expunge_all()
        saved = User.query.filter_by(id=user_id, organization_id=org_id).one()
        assert saved.email == email
        assignment = OrgRole.query.filter_by(organization_id=org_id, user_id=user_id).one()
        assert assignment.role == 'org_admin'
        assert assignment.granted_by == user_id
        assert rbac_service.is_org_admin(saved, org_id)
        assert not rbac_service.is_org_admin(saved, foreign_id)
        assert OrgRole.get_role(foreign_id, colleague_id) == 'viewer'


@pytest.mark.parametrize('kind', ['initial_admin', 'registration'])
@pytest.mark.parametrize('failure', ['role', 'commit'])
def test_persistence_failure_rolls_back_new_owner_and_organization(
    app, db_session, monkeypatch, kind, failure
):
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import Role, User
    Role.insert_roles()
    db_session.commit()
    before_org_ids = {row.id for row in Organization.query.all()}
    email = f'rejected-{uuid.uuid4().hex}@example.com'

    def reject_role(mapper, connection, target):
        raise RuntimeError('Fixture rejected real OrgRole insertion')

    def reject_commit(session):
        user = session.query(User).filter_by(email=email).first()
        if user is not None:
            assert session.query(OrgRole).filter_by(
                organization_id=user.organization_id, user_id=user.id, role='org_admin').count() == 1
            raise RuntimeError('Fixture rejected final provisioning commit')

    target, event_name, listener = ((OrgRole, 'before_insert', reject_role) if failure == 'role'
                                    else (db_session(), 'before_commit', reject_commit))
    event.listen(target, event_name, listener)
    try:
        with app.test_request_context('/account/register'):
            with pytest.raises(RuntimeError, match='Fixture rejected'):
                provision(app, monkeypatch, kind, email)
            assert not current_user.is_authenticated
    finally:
        event.remove(target, event_name, listener)
    db_session.expunge_all()
    assert User.query.filter_by(email=email).count() == 0
    assert {row.id for row in Organization.query.all()} == before_org_ids


@pytest.mark.parametrize('existing_role', [None, 'viewer', 'architect', 'org_admin'])
def test_rerunning_initial_admin_preserves_existing_assignment(
    app, db_session, make_org, monkeypatch, existing_role
):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User
    Role.insert_roles()
    org = make_org('existing-owner')
    email = f'existing-{uuid.uuid4().hex}@example.com'
    user = User(email=email, organization_id=org.id, is_org_admin=False, confirmed=True)
    db_session.add(user)
    db_session.flush()
    org_id, user_id = org.id, user.id
    if existing_role:
        OrgRole.set_role(org_id, user_id, existing_role)
    db_session.commit()
    provision(app, monkeypatch, 'initial_admin', email)
    db_session.expunge_all()
    assert OrgRole.get_role(org_id, user_id) == existing_role
    # is_org_admin follows the preserved per-organisation OrgRole assignment
    # too (rbac_service.is_org_admin checks OrgRole for the user's own
    # organisation, not only Permission.ADMINISTER), so it is True exactly
    # when the preserved assignment is 'org_admin'.
    expect_org_admin = existing_role == 'org_admin'
    reloaded = User.query.filter_by(id=user_id, organization_id=org_id).one()
    assert reloaded.is_org_admin is expect_org_admin
    assert reloaded.is_admin() is False, (
        "re-running initial-admin provisioning for an existing email must "
        "never grant Permission.ADMINISTER; a pre-existing org_admin OrgRole "
        "row answers is_org_admin True without touching the global Role"
    )
