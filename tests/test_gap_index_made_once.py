"""One gap record per Gap element: the index exists once, and a commit never writes a second record."""
import importlib.util
import pathlib

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

MIGRATION = (pathlib.Path(__file__).resolve().parent.parent
             / "migrations" / "versions" / "20261112_gap_one_record_per_element.py")


def _element(db_session, org_id, name):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(name=name, type="Gap", layer="implementation_migration", organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _count(db_session, org_id, element_id):
    return db_session.execute(sa.text(
        "SELECT count(*) FROM gaps WHERE organization_id = :o AND archimate_element_id = :e"
    ), {"o": org_id, "e": element_id}).scalar()


def test_a_commit_for_one_organisation_does_not_write_a_second_record_for_anothers_gap(
        db_session, make_org, tenant_ctx):
    from app.models.implementation_migration import Gap

    org_a, org_b = make_org("gapa"), make_org("gapb")
    element = _element(db_session, org_b.id, "No fraud screening")
    db_session.add(Gap(name="No fraud screening", organization_id=org_b.id, archimate_element_id=element.id))
    db_session.flush()
    element_id = element.id

    with tenant_ctx(org_a.id):
        db_session.commit()

    assert _count(db_session, org_b.id, element_id) == 1


def test_the_migration_after_create_all_finds_the_index_and_does_not_make_it_again(db_session):
    spec = importlib.util.spec_from_file_location("gap_one_record_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    bind = db_session.connection()
    present = bind.execute(sa.text(
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_gaps_org_element'")).scalar()
    assert present == 1  # create_all made it from the model

    context = MigrationContext.configure(bind)
    with Operations.context(context):
        module.upgrade()

    assert bind.execute(sa.text(
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_gaps_org_element'")).scalar() == 1


def test_the_index_refuses_a_second_record_for_one_organisation_and_element(db_session, make_org):
    from app.models.implementation_migration import Gap

    org = make_org("gapi")
    element = _element(db_session, org.id, "Slow onboarding")
    db_session.add(Gap(name="Slow onboarding", organization_id=org.id, archimate_element_id=element.id))
    db_session.flush()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(Gap(name="Slow onboarding again", organization_id=org.id,
                               archimate_element_id=element.id))
            db_session.flush()
