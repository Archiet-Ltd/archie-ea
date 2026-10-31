"""A new row of a fenced child takes its organisation from its parent when nothing is signed in.

Once a table carries organization_id NOT NULL, every writer that runs outside a request (a job, a
seeder, a command, a test fixture) would fail on it. A model names its parent in
``__tenant_parent__`` and the tenant listener fills the column from that parent; a signed-in
organisation still wins.
"""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture
def world(app, _schema):
    from app import db
    from app.models.organization import Organization
    from app.models.solution_models import Solution

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        org_a = Organization(name="Par A %s" % marker, slug="par-a-%s" % marker)
        org_b = Organization(name="Par B %s" % marker, slug="par-b-%s" % marker)
        db.session.add_all([org_a, org_b])
        db.session.flush()
        sol_a = Solution(name="Par sol A %s" % marker, organization_id=org_a.id)
        sol_b = Solution(name="Par sol B %s" % marker, organization_id=org_b.id)
        db.session.add_all([sol_a, sol_b])
        db.session.commit()
        yield {"org_a": org_a.id, "org_b": org_b.id, "sol_a": sol_a.id, "sol_b": sol_b.id}
        db.session.rollback()


def test_every_batch_model_names_its_parent(app):
    """Each model fenced by a batch with a parent column declares where its organisation comes from."""
    import importlib.util
    from pathlib import Path

    from app import db
    from app.models.mixins.core import TenantMixin

    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    sources = {}
    for path in sorted(versions.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"b_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for table, srcs in getattr(module, "BACKFILL", ()):
            sources[table] = {(col, parent) for parent, col in srcs}
    declared = {}
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        if issubclass(cls, TenantMixin) and isinstance(getattr(cls, "__tablename__", None), str):
            declared[cls.__tablename__] = set(getattr(cls, "__tenant_parent__", ()))
    missing = sorted(t for t, want in sources.items() if want and t in declared and declared.get(t) != want)
    assert not missing, f"models whose __tenant_parent__ does not match the migration backfill: {missing}"


def test_a_job_with_no_signed_in_organisation_gets_the_parents_organisation(app, world):
    from app import db
    from app.models.solution_sad_models import SolutionQualityAttribute

    with app.app_context():
        row = SolutionQualityAttribute(solution_id=world["sol_b"], attribute_name="latency")
        db.session.add(row)
        db.session.commit()
        assert row.organization_id == world["org_b"]


def test_a_signed_in_organisation_still_wins(app, world):
    from flask import g

    from app import db
    from app.models.solution_sad_models import SolutionQualityAttribute

    with app.test_request_context():
        g.current_org_id = world["org_a"]
        row = SolutionQualityAttribute(solution_id=world["sol_a"], attribute_name="latency")
        db.session.add(row)
        db.session.commit()
        assert row.organization_id == world["org_a"]


def test_a_row_whose_parent_cannot_be_found_is_left_unowned_and_refused(app, world):
    from sqlalchemy.exc import IntegrityError

    from app import db
    from app.models.solution_sad_models import SolutionQualityAttribute

    with app.app_context():
        db.session.add(SolutionQualityAttribute(solution_id=987654321, attribute_name="latency"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_a_signed_in_organisation_cannot_hang_a_row_from_another_organisations_parent(app, world):
    """Two organisations: A is signed in and names B's solution. The write is refused, never
    stamped with A's organisation (which would link A's row to B's record)."""
    from flask import g
    from sqlalchemy import text

    from app import db
    from app.middleware.tenant_isolation import TenantParentMismatch
    from app.models.solution_sad_models import SolutionQualityAttribute

    with app.test_request_context():
        g.current_org_id = world["org_a"]
        db.session.add(SolutionQualityAttribute(solution_id=world["sol_b"], attribute_name="planted"))
        with pytest.raises(TenantParentMismatch):
            db.session.flush()
        db.session.rollback()
        left = db.session.execute(
            text("SELECT count(*) FROM solution_quality_attributes WHERE solution_id = :s"),
            {"s": world["sol_b"]},
        ).scalar()
    assert left == 0


def test_a_row_naming_its_own_organisation_cannot_hang_from_another_organisations_parent(app, world):
    """Organisation A is signed in and sets organization_id to A itself, but names B's solution.
    Setting the column must not switch the parent check off: the write is refused."""
    from flask import g
    from sqlalchemy import text

    from app import db
    from app.middleware.tenant_isolation import TenantParentMismatch
    from app.models.solution_sad_models import SolutionQualityAttribute

    with app.test_request_context():
        g.current_org_id = world["org_a"]
        db.session.add(
            SolutionQualityAttribute(
                solution_id=world["sol_b"], attribute_name="planted", organization_id=world["org_a"]
            )
        )
        with pytest.raises(TenantParentMismatch):
            db.session.flush()
        db.session.rollback()
        left = db.session.execute(
            text("SELECT count(*) FROM solution_quality_attributes WHERE solution_id = :s"),
            {"s": world["sol_b"]},
        ).scalar()
    assert left == 0


def test_a_row_naming_its_parents_organisation_is_accepted(app, world):
    """The check refuses a mismatch only: a row that names the organisation its parent belongs to
    is written as before."""
    from flask import g

    from app import db
    from app.models.solution_sad_models import SolutionQualityAttribute

    with app.test_request_context():
        g.current_org_id = world["org_a"]
        row = SolutionQualityAttribute(
            solution_id=world["sol_a"], attribute_name="latency", organization_id=world["org_a"]
        )
        db.session.add(row)
        db.session.commit()
        assert row.organization_id == world["org_a"]


def test_a_creator_from_another_organisation_does_not_block_the_signed_in_organisation(app, world):
    """A user named as creator is an actor, not a parent: a platform admin working inside
    organisation A creates a row under A even though their own account sits in B."""
    from flask import g

    from app import db
    from app.models.solution_governance import SolutionTemplate
    from app.models.user import User

    with app.test_request_context():
        admin = User(email="par-%s@example.test" % uuid.uuid4().hex[:8], first_name="P", last_name="A",
                     organization_id=world["org_b"], confirmed=True)
        admin.password = uuid.uuid4().hex
        db.session.add(admin)
        db.session.flush()
        g.current_org_id = world["org_a"]
        row = SolutionTemplate(name="T", template_json="{}", created_by_id=admin.id)
        db.session.add(row)
        db.session.flush()
        assert row.organization_id == world["org_a"]
        db.session.rollback()
