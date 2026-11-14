"""Every application component in the model reaches the application list.

An imported component has to be an application record too, or the portfolio answer
says it has none and duplicate detection never compares it.
"""

import os

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.journey

db_required = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set - these tests need PostgreSQL",
)

LONG_NAME = (
    "MarketingIQ — backend (Go/Fiber, 441 routes) with the scheduler, the "
    "sender and the report builder behind one gateway for every tenant in the estate"
)
EXISTING_NAME = "Keycloak (identity, JWT/JWKS)"
PLAIN_NAME = "Temporal"


def _oef(elements, relationships=""):
    body = "".join(
        f'<element identifier="{i}" xsi:type="{t}"><name>{n}</name></element>' for i, n, t in elements
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-m">\n'
        "  <name>Link fixture</name>\n"
        f"  <elements>{body}</elements>\n"
        f"  <relationships>{relationships}</relationships>\n"
        "</model>\n"
    )


def _import(xml, org, db_session, tenant_ctx, **kw):
    from app.services.archimate_import_service import ArchiMateImportService

    with tenant_ctx(org.id):
        result = ArchiMateImportService().import_xml(
            xml, strategy="skip_duplicates", organization_id=org.id, **kw
        )
        db_session.commit()
    return result


def _element(org_id, name):
    from app.models.archimate_core import ArchiMateElement

    return ArchiMateElement.query.filter(  # model-safety-ok: explicit org predicate
        ArchiMateElement.organization_id == org_id, ArchiMateElement.name == name
    ).first()


def _rows(org_id):
    from app.models.application_portfolio import ApplicationComponent

    return ApplicationComponent.query.filter(  # model-safety-ok: explicit org predicate
        ApplicationComponent.organization_id == org_id
    ).order_by(ApplicationComponent.id).all()


def _answer(org_id, element_id, tenant_ctx):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with tenant_ctx(org_id):
        return IntelligenceQueryService.portfolio_component_for_element(element_id)


def _existing_application(org, name, db_session):
    """An application entered by hand: its record and its element."""
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(row)
    db_session.commit()
    return row.id, row.archimate_element_id


@db_required
def test_import_links_new_and_existing_components_including_a_long_name(
    app, db_session, make_org, tenant_ctx
):
    org = make_org("link-import")
    org_id = org.id
    with tenant_ctx(org_id):
        existing_id, existing_el = _existing_application(org, EXISTING_NAME, db_session)
    xml = _oef(
        [
            ("id-k", EXISTING_NAME, "ApplicationComponent"),
            ("id-l", LONG_NAME, "ApplicationComponent"),
        ]
    )
    assert len(LONG_NAME) > 60
    _import(xml, org, db_session, tenant_ctx)

    with tenant_ctx(org_id):
        rows = _rows(org_id)
        assert len(rows) == 2
        keep = [r for r in rows if r.id == existing_id]
        assert keep and keep[0].archimate_element_id == existing_el
        for name in (EXISTING_NAME, LONG_NAME):
            element = _element(org_id, name)
            assert element is not None
            answer = _answer(org_id, element.id, tenant_ctx)
            row = [r for r in rows if r.archimate_element_id == element.id]
            assert len(row) == 1, name
            assert answer["application_component_id"] == row[0].id, name
            assert "no_application_component" not in answer["reasons"], name


def _set_up_switch_off_org(org, db_session, tenant_ctx):
    """A model imported before the application type existed: every component, none linked."""
    with tenant_ctx(org.id):
        existing_id, _ = _existing_application(org, EXISTING_NAME, db_session)
    xml = _oef(
        [
            ("id-k", EXISTING_NAME, "ApplicationComponent"),
            ("id-l", LONG_NAME, "ApplicationComponent"),
            ("id-t", PLAIN_NAME, "ApplicationComponent"),
            ("id-p", "Platform", "ApplicationComponent"),
            ("id-q", "Platform worker", "ApplicationComponent"),
        ],
        '<relationship identifier="r1" xsi:type="Composition" source="id-p" target="id-q"/>',
    )
    _import(xml, org, db_session, tenant_ctx, promote_top_level=False)
    return existing_id


@db_required
def test_backfill_links_unlinked_components_once_and_changes_nothing_the_second_time(
    app, db_session, make_org, tenant_ctx, capsys
):
    from click.testing import CliRunner

    from app.commands.application_element_migration import (
        application_element_cli,
        link_application_elements,
    )

    org = make_org("link-backfill")
    org_id = org.id
    existing_id = _set_up_switch_off_org(org, db_session, tenant_ctx)
    with tenant_ctx(org_id):
        before = len(_rows(org_id))
    assert before == 1

    first = link_application_elements(organization_ids=[org_id])["organisations"][0]
    # Temporal, the long name and Platform are top-level; Platform worker is a part.
    assert first["new_application_created"] == 3
    assert first["left_without_application"] == 1
    with tenant_ctx(org_id):
        rows = _rows(org_id)
        names = sorted(r.name for r in rows)
        assert len(rows) == 4
        assert any(r.id == existing_id for r in rows)
        for name in (LONG_NAME, PLAIN_NAME, "Platform"):
            element = _element(org_id, name)
            answer = _answer(org_id, element.id, tenant_ctx)
            assert answer["application_component_id"] is not None, name
        part = _element(org_id, "Platform worker")
        assert _answer(org_id, part.id, tenant_ctx)["application_component_id"] is None

    second = link_application_elements(organization_ids=[org_id])["organisations"][0]
    assert second["new_application_created"] == 0
    assert second["linked_to_existing_application"] == 0
    with tenant_ctx(org_id):
        assert sorted(r.name for r in _rows(org_id)) == names

    runner = CliRunner()
    out = runner.invoke(application_element_cli, ["link", "--org-ids", str(org_id)], obj=None)
    assert "new_application_created: 0" in out.output


@db_required
def test_backfill_links_a_component_to_the_application_of_the_same_name(
    app, db_session, make_org, tenant_ctx
):
    from app.commands.application_element_migration import link_application_elements
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("link-existing")
    org_id = org.id
    with tenant_ctx(org_id):
        row_id, _ = _existing_application(org, PLAIN_NAME, db_session)
        # The application's own element is gone, as after an earlier clean-up.
        db_session.execute(
            sa.text("UPDATE application_components SET archimate_element_id = NULL WHERE id = :i"),
            {"i": row_id},
        )
        db_session.commit()
    _import(_oef([("id-t", PLAIN_NAME, "ApplicationComponent")]), org, db_session, tenant_ctx, promote_top_level=False)

    result = link_application_elements(organization_ids=[org_id])["organisations"][0]
    assert result["linked_to_existing_application"] == 1
    assert result["new_application_created"] == 0
    with tenant_ctx(org_id):
        rows = _rows(org_id)
        assert [r.id for r in rows] == [row_id]
        assert rows[0].archimate_element_id == _element(org_id, PLAIN_NAME).id
        assert ApplicationComponent.query.filter_by(organization_id=org_id).count() == 1  # model-safety-ok: explicit org predicate


@db_required
def test_backfill_for_one_organisation_leaves_another_untouched(app, db_session, make_org, tenant_ctx):
    from app.commands.application_element_migration import link_application_elements

    a, b = make_org("link-a"), make_org("link-b")
    a_id, b_id = a.id, b.id
    _set_up_switch_off_org(a, db_session, tenant_ctx)
    _set_up_switch_off_org(b, db_session, tenant_ctx)
    with tenant_ctx(b_id):
        b_before = [(r.id, r.name, r.archimate_element_id) for r in _rows(b_id)]

    link_application_elements(organization_ids=[a_id])

    with tenant_ctx(b_id):
        assert [(r.id, r.name, r.archimate_element_id) for r in _rows(b_id)] == b_before
        assert _element(b_id, PLAIN_NAME).type == "ApplicationComponent"
    with tenant_ctx(a_id):
        assert len(_rows(a_id)) == 4


@db_required
def test_detection_after_the_backfill_compares_the_formerly_unlinked_component(
    app, db_session, make_org, tenant_ctx
):
    from app.commands.application_element_migration import link_application_elements
    from app.modules.duplicate_detection.services.unified_duplicate_detection_service import UnifiedDuplicateDetectionService

    org = make_org("link-detect")
    org_id = org.id
    with tenant_ctx(org_id):
        _existing_application(org, "Alerts gateway and consumers", db_session)
    _import(
        _oef([("id-a", "Alerts gateway and consumers service", "ApplicationComponent"), ("id-t", PLAIN_NAME, "ApplicationComponent")]),
        org, db_session, tenant_ctx, promote_top_level=False,
    )
    with tenant_ctx(org_id):
        before = UnifiedDuplicateDetectionService().run_detection(0.6)
    link_application_elements(organization_ids=[org_id])
    with tenant_ctx(org_id):
        after = UnifiedDuplicateDetectionService().run_detection(0.6)
    assert after["applications_analyzed"] == before["applications_analyzed"] + 2
    assert before["groups_found"] == 0
    assert after["groups_found"] == 1
