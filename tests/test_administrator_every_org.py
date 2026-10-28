"""The repair for organisations left without an Administrator, the check that
finds them, and the rule that keeps an organisation from losing its last one."""

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def app():
    from app import create_app, db
    from app.models.user import Role

    app = create_app("testing")
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        Role.insert_roles()
    return app


def _org(db, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name="%s %s" % (label, suffix), slug="%s-%s" % (label.lower(), suffix))
    db.session.add(org)
    db.session.flush()
    return org


def _user(db, org, label, role_name):
    from app.models.user import Role, User

    user = User(
        email="%s-%s@example.com" % (label, uuid.uuid4().hex[:8]),
        first_name=label,
        last_name="Member",
        organization_id=org.id,
        confirmed=True,
    )
    user.role = Role.query.filter_by(name=role_name).first()
    db.session.add(user)
    db.session.flush()
    return user


def _check_module():
    spec = importlib.util.spec_from_file_location("check_org_administrators", REPO / "scripts" / "check_org_administrators.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(app):
    """Org 'headless' (two Architects, no Administrator), org 'healthy' (an Administrator), org 'empty'."""
    from app import db

    with app.app_context():
        headless = _org(db, "headless")
        first = _user(db, headless, "first", "Architect")
        second = _user(db, headless, "second", "Architect")
        healthy = _org(db, "healthy")
        keeper = _user(db, healthy, "keeper", "Administrator")
        _user(db, healthy, "other", "Architect")
        empty = _org(db, "empty")
        db.session.commit()
        return {
            "headless": headless.id, "first": first.id, "second": second.id,
            "healthy": healthy.id, "keeper": keeper.id, "empty": empty.id,
        }


def test_the_repair_makes_the_earliest_member_administrator_once(app):
    from app import db
    from app.models.audit_log import AuditLog
    from app.models.org_role import OrgRole
    from app.models.user import User
    from app.services.administrator_guard import repair_missing_administrators

    ids = _seed(app)
    with app.app_context():
        platform_admins_before = User.query.filter_by(is_platform_admin=True).count()
        with db.engine.begin() as connection:
            first_run = repair_missing_administrators(connection)
        mine = [r for r in first_run if r["organization_id"] == ids["headless"]]
        assert len(mine) == 1, first_run
        assert mine[0]["user_id"] == ids["first"], "the earliest member is the one chosen"
        assert mine[0]["was"] == "Architect"
        assert all(r["organization_id"] not in (ids["healthy"], ids["empty"]) for r in first_run)

        db.session.expire_all()
        chosen = db.session.get(User, ids["first"])
        assert chosen.is_admin() and chosen.is_org_admin
        assert not chosen.is_platform_admin, "the repair never grants platform administration"
        assert User.query.filter_by(is_platform_admin=True).count() == platform_admins_before
        assert OrgRole.get_role(ids["headless"], ids["first"]) == "org_admin"
        assert not db.session.get(User, ids["second"]).is_admin()
        assert db.session.get(User, ids["keeper"]).is_admin()

        rows = AuditLog.query.filter_by(organization_id=ids["headless"], table_name="users", record_id=ids["first"]).all()
        assert len(rows) == 1
        assert rows[0].row_hash, "the audit row is sealed into the organisation's chain"
        assert rows[0].new_value["role"] == "Administrator"
        assert rows[0].old_value["role"] == "Architect"

        with db.engine.begin() as connection:
            second_run = repair_missing_administrators(connection)
        assert second_run == [], "a second run changes nothing"
        assert AuditLog.query.filter_by(organization_id=ids["headless"], table_name="users").count() == 1


def test_the_repair_works_in_batches(app):
    from app import db
    from app.services.administrator_guard import organisations_without_administrator, repair_missing_administrators

    with app.app_context():
        orgs = []
        for n in range(5):
            org = _org(db, "batch%d" % n)
            _user(db, org, "batchuser%d" % n, "Viewer")
            orgs.append(org.id)
        db.session.commit()
        with db.engine.begin() as connection:
            assert len(organisations_without_administrator(connection, limit=2)) == 2
            repaired = repair_missing_administrators(connection, batch_size=2)
        assert {o for o in orgs} <= {r["organization_id"] for r in repaired}
        with db.engine.begin() as connection:
            assert organisations_without_administrator(connection) == []


def test_the_check_fails_while_an_organisation_has_none_and_passes_after_the_repair(app):
    from app import db
    from app.services.administrator_guard import repair_missing_administrators

    ids = _seed(app)
    check = _check_module()
    with app.app_context():
        with db.engine.begin() as connection:
            found = check.find_organisations(connection)
        assert ids["headless"] in {o["id"] for o in found}
        assert ids["healthy"] not in {o["id"] for o in found}
        assert ids["empty"] not in {o["id"] for o in found}, "an organisation with no members is not listed"
        with db.engine.begin() as connection:
            repair_missing_administrators(connection)
        with db.engine.begin() as connection:
            assert check.find_organisations(connection) == []


def test_verify_registers_the_check_as_a_gate():
    source = (REPO / "scripts" / "verify.py").read_text(encoding="utf-8")
    assert 'Gate("org-administrators"' in source
    assert "check_org_administrators.py" in source


def test_the_last_administrator_rule(app):
    from app import db
    from app.models.org_role import OrgRole
    from app.services.administrator_guard import (
        LAST_ADMINISTRATOR_MESSAGE,
        LastAdministratorError,
        administrator_ids,
        ensure_keeps_administrator,
    )

    with app.app_context():
        org = _org(db, "rule")
        sole = _user(db, org, "sole", "Administrator")
        other = _user(db, org, "bystander", "Architect")
        db.session.commit()
        assert administrator_ids(org.id) == {sole.id}
        with pytest.raises(LastAdministratorError) as raised:
            ensure_keeps_administrator(org.id, [sole.id])
        assert raised.value.message == LAST_ADMINISTRATOR_MESSAGE
        ensure_keeps_administrator(org.id, [other.id])  # not an administrator: fine

        OrgRole.set_role(org.id, other.id, "org_admin")
        db.session.commit()
        assert administrator_ids(org.id) == {sole.id, other.id}
        ensure_keeps_administrator(org.id, [sole.id])  # another remains: fine
        with pytest.raises(LastAdministratorError):
            ensure_keeps_administrator(org.id, [sole.id, other.id])

        # Removing people (rather than demoting them) from an organisation they are
        # the only members of leaves it empty: allowed. While another member remains, refused.
        alone = _org(db, "rulealone")
        only = _user(db, alone, "onlyone", "Administrator")
        db.session.commit()
        ensure_keeps_administrator(alone.id, [only.id], leaving=True)
        with pytest.raises(LastAdministratorError):
            ensure_keeps_administrator(alone.id, [only.id])  # demoting the only member's role

        with pytest.raises(LastAdministratorError):
            ensure_keeps_administrator(org.id, [sole.id, other.id], leaving=False)
        third = _user(db, org, "third", "Viewer")
        db.session.commit()
        with pytest.raises(LastAdministratorError):
            ensure_keeps_administrator(org.id, [sole.id, other.id], leaving=True)  # third remains
        ensure_keeps_administrator(org.id, [sole.id, other.id, third.id], leaving=True)  # nobody remains

        headless = _org(db, "rulenone")
        _user(db, headless, "nobody", "Viewer")
        db.session.commit()
        ensure_keeps_administrator(headless.id, [])  # nothing to lose, never blocks
