"""The OEF importer states its own organisation on every read and write.

From the command line there is no request and no ambient tenant, so the ORM
tenant fence is a no-op. Two organisations importing the same file must each
get the full counts and must not touch each other's rows.
"""
import os

import pytest

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "archiet-model-v1.oef.xml")


def _cli_import(app, org_id):
    """The call path a `flask` command uses: app context, no request, no g binding."""
    from flask import g

    from app.services.archimate_import_service import ArchiMateImportService

    with app.app_context():
        assert getattr(g, "current_org_id", None) is None
        with open(FIXTURE, "rb") as fh:
            return ArchiMateImportService().import_xml(
                fh.read(), strategy="skip_duplicates", organization_id=org_id
            )


def _rows(db_session, org_id):
    from sqlalchemy import text

    el = db_session.execute(
        text("select id from archimate_elements where organization_id = :o order by id"), {"o": org_id}
    ).scalars().all()
    rel = db_session.execute(
        text("select id from archimate_relationships where organization_id = :o order by id"), {"o": org_id}
    ).scalars().all()
    return el, rel


def test_two_organisations_import_the_same_file_from_the_cli(app, db_session, make_org):
    org_a, org_b = make_org("cli-a"), make_org("cli-b")
    db_session.commit()
    a_id, b_id = org_a.id, org_b.id

    first = _cli_import(app, a_id)
    a_rows = _rows(db_session, a_id)
    second = _cli_import(app, b_id)
    b_rows = _rows(db_session, b_id)

    for result in (first, second):
        assert result["created"] == 311
        assert result["relationships_created"] == 509
        assert result["skipped"] == 0
        assert result["relationships_skipped"] == 0
        assert result["failed"] == 0
        assert result["relationships_failed"] == []

    assert len(a_rows[0]) == 311 and len(a_rows[1]) == 509
    assert len(b_rows[0]) == 311 and len(b_rows[1]) == 509
    assert not set(a_rows[0]) & set(b_rows[0])
    assert not set(a_rows[1]) & set(b_rows[1])
    # the second import changed nothing of the first organisation's
    assert _rows(db_session, a_id) == a_rows
