"""T-app-04: every live exporter writes an Application as ApplicationComponent plus the Entelim property.

ADR-0014 Decision 5. One organisation holds an Application and a plain component; each entry point is
called for it. The Application is written as ApplicationComponent with ``entelim:specialization`` =
``Application``; the plain component carries no such property; no file holds ``Application`` as a type.
"""

import os
import re

import pytest

pytestmark = pytest.mark.journey

db_required = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set - exchange tests need PostgreSQL",
)

PROP = "entelim:specialization"
# Any xsi:type / Archi type / plain type attribute whose value is exactly "Application" (optionally archimate:-prefixed).
RAW_APPLICATION_TYPE = re.compile(r'''(?:xsi:type|type)\s*=\s*["'](?:archimate:)?Application["']''')


def _assert_mapping(xml, app_name="Orbit CRM", comp_name="Plain Component"):
    assert not RAW_APPLICATION_TYPE.search(xml), "a file wrote Application as a type"
    assert "ApplicationComponent" in xml
    assert app_name in xml and comp_name in xml
    assert xml.count(PROP) >= 1
    # The property value names the specialisation, exactly once for the one Application.
    assert len(re.findall(r">\s*Application\s*<|value=\"Application\"", xml)) == 1


@pytest.fixture
def world(app, db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent as InventoryRow
    from app.models.archimate_core import ArchiMateElement, SavedDiagram, SavedDiagramElement
    from app.models.solution_models import Solution, SolutionArchiMateElement

    org = make_org("every-path")
    org_id = org.id
    with tenant_ctx(org_id):
        application = ArchiMateElement(name="Orbit CRM", type="Application", layer="Application", organization_id=org_id)
        component = ArchiMateElement(
            name="Plain Component", type="ApplicationComponent", layer="Application", organization_id=org_id
        )
        db_session.add_all([application, component])
        db_session.flush()

        diagram = SavedDiagram(name="Every path", viewpoint_type="application_cooperation", organization_id=org_id)
        db_session.add(diagram)
        db_session.flush()
        for i, el in enumerate((application, component)):
            db_session.add(SavedDiagramElement(diagram_id=diagram.id, element_id=el.id, position_x=10 * i, position_y=5))

        solution = Solution(name="Every path solution", organization_id=org_id, archimate_element_id=application.id)
        db_session.add(solution)
        db_session.flush()
        db_session.add(
            SolutionArchiMateElement(
                solution_id=solution.id,
                layer_type="application",
                element_id=application.id,
                element_table="archimate_elements",
                element_name=application.name,
            )
        )
        db_session.add(
            SolutionArchiMateElement(
                solution_id=solution.id,
                layer_type="application",
                element_id=component.id,
                element_table="archimate_elements",
                element_name=component.name,
            )
        )
        inventory = InventoryRow(name="Orbit CRM", organization_id=org_id)
        db_session.add(inventory)
        db_session.flush()
        solution.applications.append(inventory)
        db_session.commit()
        ids = {"diagram": diagram.id, "solution": solution.id}
    return {"org": org_id, "ctx": tenant_ctx, **ids}


@db_required
def test_saved_viewpoint_exchange_export(world):
    from app.services.archimate_export_service import export_saved_viewpoint

    with world["ctx"](world["org"]):
        _assert_mapping(export_saved_viewpoint(world["diagram"]))


@db_required
def test_saved_viewpoint_plain_exchange_export(world):
    from app.services.archimate_export_service import load_viewpoint_dict, to_open_exchange_xml

    with world["ctx"](world["org"]):
        _assert_mapping(to_open_exchange_xml(load_viewpoint_dict(world["diagram"])))


@db_required
def test_composer_archi_export_and_render(world):
    from app.services.archimate_export_service import load_viewpoint_dict
    from app.services.composer_export_formats import export_saved_viewpoint_archi, render_viewpoint

    with world["ctx"](world["org"]):
        archi = export_saved_viewpoint_archi(world["diagram"])
        rendered = render_viewpoint(load_viewpoint_dict(world["diagram"]), "archi")
    for out in (archi, rendered if isinstance(rendered, str) else rendered[0]):
        assert 'archimate:ApplicationComponent' in out
        assert not RAW_APPLICATION_TYPE.search(out)
        assert out.count(PROP) == 1
        assert "Orbit CRM" in out and "Plain Component" in out


@db_required
def test_architect_ui_oef_export(world):
    from app.services.archimate_oef_service import ArchiMateOEFService

    with world["ctx"](world["org"]):
        _assert_mapping(ArchiMateOEFService().export_model())


@db_required
def test_solution_design_oef_export(world):
    from app.services.archimate_oef_export_service import archimate_oef_export_service

    with world["ctx"](world["org"]):
        _assert_mapping(archimate_oef_export_service.export_solution(world["solution"]))


@db_required
def test_solution_exchange_export(world):
    from app.modules.architecture.services.archimate_exchange_service import ArchiMateExchangeService

    with world["ctx"](world["org"]):
        xml = ArchiMateExchangeService().export_solution_to_archimate_xml(world["solution"])
    assert not RAW_APPLICATION_TYPE.search(xml)
    assert "ApplicationComponent" in xml
    assert xml.count(PROP) >= 1
    assert "Orbit CRM" in xml


@db_required
def test_enterprise_export_service(world):
    from app.services.enterprise_export import EnterpriseExportService

    with world["ctx"](world["org"]):
        xml = EnterpriseExportService.export_archimate_xml()
    assert not RAW_APPLICATION_TYPE.search(xml)
    assert not re.search(r'xsi:type="[A-Za-z]*:Application"', xml)
    assert re.search(r'xsi:type="[^"]*:ApplicationComponent"', xml)
    assert xml.count(PROP) == 1 and "Orbit CRM" in xml and "Plain Component" in xml


@db_required
def test_solution_design_oef_download_route(world, client, db_session, login_as):
    from app.models import User

    user = User(email=f"every-path-{world['org']}@example.com", organization_id=world["org"], confirmed=True)
    user.password = "x"
    db_session.add(user)
    db_session.flush()
    from app.models.solution_models import Solution

    db_session.get(Solution, world["solution"]).created_by_id = user.id
    db_session.commit()
    login_as(client, user)
    resp = client.get(f"/solutions/{world['solution']}/export-oef")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _assert_mapping(resp.get_data(as_text=True))
