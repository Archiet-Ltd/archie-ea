"""Execute complete provisioning modules with an explicit in-memory persistence seam.

No source extraction and no real database. PostgreSQL transaction coverage is
separate; these tests check arguments, commit order, failure and existing-user paths.
"""
import copy
import runpy
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def provisioning(monkeypatch):
    rows, committed, logged_in = [], [], []

    class Query:
        def __init__(self, kind, filters=None):
            self.kind, self.filters = kind, filters or {}

        def filter_by(self, **kwargs):
            return Query(self.kind, kwargs)

        def first(self):
            return next((row for row in rows if row.kind == self.kind and
                         all(getattr(row, key, None) == value for key, value in self.filters.items())), None)

    class User(SimpleNamespace):
        kind = 'user'
        query = Query('user')
        role = None

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            # is_org_admin derives from is_admin().  The real User.__init__
            # assigns the Administrator role when the email matches ADMIN_EMAIL.
            # In this mock, set role to Administrator so is_admin() returns True.
            if getattr(self, 'email', None) == 'new-admin@example.com':
                self.role = SimpleNamespace(name='Administrator')

        def is_admin(self):
            return getattr(self.role, 'name', None) == 'Administrator'

        @property
        def is_org_admin(self):
            return self.is_admin()

        @is_org_admin.setter
        def is_org_admin(self, value):
            if value:
                self.grant_org_admin()

        def grant_org_admin(self):
            # Mirrors the real User.grant_org_admin (app/models/user.py):
            # register_user() calls this to grant org-admin in the owner's
            # own (just-created) organisation.
            self.role = SimpleNamespace(name='Administrator')

        def revoke_org_admin(self):
            if not self.is_admin() or getattr(self, 'is_platform_admin', False):
                return
            self.role = SimpleNamespace(name='Architect')

    class Organization(SimpleNamespace):
        kind = 'organization'
        query = Query('organization')

    class Session:
        failed_role = False
        failed_commit = False
        commits = 0
        rollbacks = 0

        def add(self, row):
            rows.append(row)

        def flush(self):
            for index, row in enumerate(rows, 1):
                if not getattr(row, 'id', None):
                    row.id = index

        def commit(self):
            if self.failed_commit:
                raise RuntimeError('Fixture final commit failed')
            self.commits += 1
            self.flush()
            committed[:] = copy.deepcopy(rows)

        def rollback(self):
            self.rollbacks += 1
            rows[:] = copy.deepcopy(committed)

    session = Session()

    class OrgRole:
        @staticmethod
        def set_role(org_id, user_id, role, granted_by_id=None):
            if session.failed_role:
                raise RuntimeError('Fixture role persistence failed')
            assert org_id and user_id, 'Persisted identities required before role assignment'
            rows.append(SimpleNamespace(kind='org_role', organization_id=org_id,
                                        user_id=user_id, role=role, granted_by=granted_by_id))

    class SessionRegistry:
        """Stands in for the login and server-side session step of registration.

        The real one needs a request, a cookie session and the sessions table;
        this seam only records who was logged in and requires that the owner
        was already committed at that point.
        """

        @staticmethod
        def login_and_register(user, remember=False):
            assert session.commits == 1, 'Login must follow the committed owner'
            logged_in.append(user)

    db = SimpleNamespace(session=session)

    class Role(SimpleNamespace):
        kind = 'role'
        query = Query('role')

        @staticmethod
        def insert_roles():
            return None

    # Seed the Administrator role so Role.query.filter_by(name="Administrator")
    # resolves in the registration path (account_service assigns it as the
    # org-admin authority).
    rows.append(Role(name='Administrator'))

    modules = {
        'manage': dict(app=SimpleNamespace(app_context=nullcontext), db=db),
        'config': dict(Config=SimpleNamespace(ADMIN_EMAIL='new-admin@example.com', ADMIN_PASSWORD='fixture-only')),
        'app.models': dict(User=User, Organization=Organization, Role=Role),
        'app.models.organization': dict(Organization=Organization),
        'app.models.org_role': dict(OrgRole=OrgRole),
        'app.extensions': dict(db=db),
        'app.services': dict(session_registry=SessionRegistry),
        'app.flask_email': dict(send_email=lambda *args, **kwargs: pytest.fail('No email expected')),
        # The address is free: no invitation-only account holds it.
        'app.modules.account.services.invitation_service': dict(
            release_for_registration=lambda email: False,
        ),
        'flask_login': dict(login_user=lambda user: logged_in.append(user), logout_user=lambda: None),
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)

    def run(kind):
        if kind == 'initial_admin':
            runpy.run_path(str(ROOT / 'create_admin.py'))
            return next((row for row in rows if row.kind == 'user'), None)
        namespace = runpy.run_path(str(ROOT / 'app/modules/account/services/account_service.py'))
        return namespace['AccountService'].register_user('New', 'Owner', 'new-owner@example.com', 'fixture-only')

    return SimpleNamespace(run=run, rows=rows, committed=committed, session=session,
                           logged_in=logged_in, User=User, Organization=Organization)


@pytest.mark.parametrize('kind', ['initial_admin', 'registration'])
def test_new_owner_commits_same_org_role_with_user(provisioning, kind):
    p = provisioning
    user = p.run(kind)
    saved_user = next(row for row in p.committed if row.kind == 'user')
    roles = [row for row in p.committed if row.kind == 'org_role']
    assert len(roles) == 1
    assert (roles[0].organization_id, roles[0].user_id, roles[0].role) == (
        saved_user.organization_id, saved_user.id, 'org_admin')
    assert user.is_org_admin is True
    assert p.session.commits == 1
    assert p.logged_in == ([user] if kind == 'registration' else [])


@pytest.mark.parametrize('kind', ['initial_admin', 'registration'])
@pytest.mark.parametrize('failure', ['role', 'commit'])
def test_persistence_failure_cannot_leave_partial_owner_or_login(provisioning, kind, failure, capsys):
    p = provisioning
    setattr(p.session, 'failed_' + failure, True)
    with pytest.raises(RuntimeError, match='role persistence failed' if failure == 'role' else 'final commit failed'):
        p.run(kind)
    assert p.committed == [] and p.rows == []
    assert p.session.commits == 0
    assert p.session.rollbacks == 1
    assert p.logged_in == []
    assert 'Admin created:' not in capsys.readouterr().out


@pytest.mark.parametrize('existing_role', [None, 'viewer', 'architect', 'org_admin'])
def test_existing_admin_is_not_granted_or_promoted(provisioning, existing_role):
    p = provisioning
    existing = p.User(id=77, email='new-admin@example.com', organization_id=42, is_org_admin=False)
    p.rows.append(existing)
    if existing_role:
        p.rows.append(SimpleNamespace(kind='org_role', organization_id=42, user_id=77, role=existing_role))
    before = copy.deepcopy(p.rows)
    p.run('initial_admin')
    assert p.rows == before
    assert p.session.commits == 0 and p.session.rollbacks == 0


def test_initial_admin_uses_existing_default_org_without_touching_foreign_roles(provisioning):
    p = provisioning
    org = p.Organization(id=42, slug='default', name='Existing organization')
    foreign = SimpleNamespace(kind='org_role', organization_id=99, user_id=88, role='viewer')
    p.rows.extend([org, foreign])
    user = p.run('initial_admin')
    roles = [row for row in p.committed if row.kind == 'org_role']
    assert len(roles) == 2
    assert roles[0] == foreign
    assert (roles[1].organization_id, roles[1].user_id, roles[1].role) == (42, user.id, 'org_admin')
