"""`flask application-element migrate` (ADR-0014 Decision 7).

Every inventory row's mirror element becomes an Application; other
ApplicationComponent elements stay components. Reversible, idempotent, scoped
per organisation, and relationships are listed but never changed.
"""

import os

import pytest

pytestmark = pytest.mark.journey

FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "archiet-model-v1.oef.xml"
)
MARKER = "entelim:migrated_from"


def _app_row(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    assert row.archimate_element_id, "backbone mirror expected"
    return row


def _drop_mirror(db_session, row):
    from app.models.archimate_core import ArchiMateElement

    el = db_session.get(ArchiMateElement, row.archimate_element_id)
    row.archimate_element_id = None
    db_session.flush()
    db_session.delete(el)
    db_session.flush()


def _plain_component(db_session, org, name):
    from app.services.archimate_backbone import create_backbone_element

    return create_backbone_element(
        element_type="ApplicationComponent",
        layer="Application",
        name=name,
        organization_id=org.id,
        session=db_session,
    )


def _rel(db_session, org, rtype, source_id, target_id):
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(
        organization_id=org.id, type=rtype, source_id=source_id, target_id=target_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _elements(db_session, org):
    from app.models.archimate_core import ArchiMateElement

    return {
        e.name: e
        for e in ArchiMateElement.query.filter(  # model-safety-ok: explicit org predicate
            ArchiMateElement.organization_id == org.id
        ).all()
    }


@pytest.fixture
def world(app, db_session, make_org):
    org_a, org_b = make_org("appel-a"), make_org("appel-b")
    a1 = _app_row(db_session, org_a, "A Entelim")
    a2 = _app_row(db_session, org_a, "A Iyene")
    a3 = _app_row(db_session, org_a, "A Orphan Row")
    _drop_mirror(db_session, a3)
    comp_a = _plain_component(db_session, org_a, "A Gateway")
    b1 = _app_row(db_session, org_b, "B Entelim")
    comp_b = _plain_component(db_session, org_b, "B Gateway")
    ok_rel = _rel(db_session, org_a, "composition", a1.archimate_element_id, comp_a.id)
    app_app = _rel(db_session, org_a, "composition", a1.archimate_element_id, a2.archimate_element_id)
    comp_app = _rel(db_session, org_a, "aggregation", comp_a.id, a2.archimate_element_id)
    db_session.commit()
    return dict(org_a=org_a, org_b=org_b, a1=a1, a2=a2, a3=a3, b1=b1,
                comp_a=comp_a, comp_b=comp_b, ok_rel=ok_rel,
                app_app=app_app, comp_app=comp_app)


def _run(**kw):
    from app.commands.application_element_migration import migrate_application_elements

    return migrate_application_elements(**kw)


def _by_org(result, org):
    return next(o for o in result["organisations"] if o["organization_id"] == org.id)


def test_dry_run_counts_and_writes_nothing(world, db_session):
    result = _run(dry_run=True, organization_ids=[world["org_a"].id])
    a = _by_org(result, world["org_a"])
    assert a["inventory_rows"] == 3
    assert a["elements_changed"] == 2
    assert a["elements_created"] == 1
    assert a["left_as_components"] == 1
    flagged = {r["relationship_id"] for r in a["relationships_needing_attention"]}
    assert flagged == {world["app_app"].id, world["comp_app"].id}
    assert world["ok_rel"].id not in flagged
    assert all(e.type != "Application" for e in _elements(db_session, world["org_a"]).values())
    assert [o["organization_id"] for o in result["organisations"]] == [world["org_a"].id]


def test_apply_changes_only_inventory_mirrors(world, db_session):
    from app.models.archimate_core import ArchiMateElement

    result = _run(dry_run=False, organization_ids=[world["org_a"].id])
    a = _by_org(result, world["org_a"])
    assert (a["elements_changed"], a["elements_created"], a["left_as_components"]) == (2, 1, 1)
    elements = _elements(db_session, world["org_a"])
    assert elements["A Entelim"].type == "Application"
    assert elements["A Entelim"].custom_properties[MARKER] == "ApplicationComponent"
    assert elements["A Iyene"].type == "Application"
    assert elements["A Gateway"].type == "ApplicationComponent"
    assert MARKER not in (elements["A Gateway"].custom_properties or {})
    db_session.refresh(world["a3"])
    orphan = db_session.get(ArchiMateElement, world["a3"].archimate_element_id)
    assert orphan.type == "Application" and orphan.layer == "Application"
    assert orphan.organization_id == world["org_a"].id


def test_organisation_b_is_untouched_by_a_run_scoped_to_a(world, db_session):
    _run(dry_run=False, organization_ids=[world["org_a"].id])
    b = _elements(db_session, world["org_b"])
    assert b["B Entelim"].type == "ApplicationComponent"
    assert MARKER not in (b["B Entelim"].custom_properties or {})
    assert b["B Gateway"].type == "ApplicationComponent"


def test_second_apply_is_idempotent(world, db_session):
    from app.models.archimate_core import ArchiMateElement

    _run(dry_run=False, organization_ids=[world["org_a"].id])
    again = _run(dry_run=False, organization_ids=[world["org_a"].id])
    a = _by_org(again, world["org_a"])
    assert (a["elements_changed"], a["elements_created"]) == (0, 0)
    assert a["inventory_rows"] == 3
    assert a["left_as_components"] == 1
    assert ArchiMateElement.query.filter(  # model-safety-ok: explicit org predicate
        ArchiMateElement.organization_id == world["org_a"].id,
        ArchiMateElement.type == "Application",
    ).count() == 3


def test_revert_restores_exactly(world, db_session):
    before = {n: (e.type, dict(e.custom_properties or {}))
              for n, e in _elements(db_session, world["org_a"]).items()}
    _run(dry_run=False, organization_ids=[world["org_a"].id])
    _run(dry_run=False, revert=True, organization_ids=[world["org_a"].id])
    after = _elements(db_session, world["org_a"])
    for name, (typ, props) in before.items():
        assert after[name].type == typ, name
        assert dict(after[name].custom_properties or {}) == props, name
    # the element made for the row that had none stays, as a component
    orphan = after["A Orphan Row"]
    assert orphan.type == "ApplicationComponent"
    assert MARKER not in (orphan.custom_properties or {})
    assert not any(e.type == "Application" for e in after.values())
    again = _run(dry_run=False, revert=True, organization_ids=[world["org_a"].id])
    assert _by_org(again, world["org_a"])["elements_changed"] == 0


def test_revert_leaves_hand_made_applications_alone(world, db_session):
    el = _plain_component(db_session, world["org_a"], "A Hand Made")
    el.type = "Application"
    db_session.commit()
    _run(dry_run=False, revert=True, organization_ids=[world["org_a"].id])
    assert _elements(db_session, world["org_a"])["A Hand Made"].type == "Application"


def test_flagged_relationships_are_listed_not_changed(world, db_session):
    from app.models.archimate_core import ArchiMateRelationship

    result = _run(dry_run=False, organization_ids=[world["org_a"].id])
    a = _by_org(result, world["org_a"])
    reasons = {r["relationship_id"]: r["reason"] for r in a["relationships_needing_attention"]}
    assert "compose" in reasons[world["app_app"].id].lower()
    assert reasons[world["comp_app"].id]
    for key, rtype in (("app_app", "composition"), ("comp_app", "aggregation")):
        rel = db_session.get(ArchiMateRelationship, world[key].id)
        assert rel.type == rtype
    assert db_session.get(ArchiMateRelationship, world["ok_rel"].id).type == "composition"
    assert world["a2"].archimate_element_id in a["demotion_candidates"]


def test_cli_command_is_registered_with_dry_run_and_revert(app, world):
    runner = app.test_cli_runner()
    out = runner.invoke(
        args=["application-element", "migrate", "--dry-run",
              "--org-ids", str(world["org_a"].id)]
    )
    assert out.exit_code == 0, out.output
    assert "inventory_rows" in out.output and "dry-run" in out.output.lower()
    out = runner.invoke(args=["application-element", "migrate", "--help"])
    assert "--revert" in out.output and "--dry-run" in out.output


def test_archiet_file_gives_55_applications_and_no_components(
    app, db_session, make_org, tenant_ctx
):
    from app.models.archimate_core import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_import_service import ArchiMateImportService

    org = make_org("appel-archiet")
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        xml = fh.read()
    with tenant_ctx(org.id):
        service = ArchiMateImportService()
        service.execute_import(service.parse_oef_xml(xml), strategy="skip_duplicates")
        db_session.commit()

    def count(type_name):
        return ArchiMateElement.query.filter(  # model-safety-ok: explicit org predicate
            ArchiMateElement.organization_id == org.id,
            ArchiMateElement.type == type_name,
        ).count()

    assert ApplicationComponent.query.filter(  # model-safety-ok: explicit org predicate
        ApplicationComponent.organization_id == org.id
    ).count() == 55
    assert count("ApplicationComponent") == 55
    _run(dry_run=False, organization_ids=[org.id])
    assert count("Application") == 55
    assert count("ApplicationComponent") == 0
