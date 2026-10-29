
import re
from playwright.sync_api import expect, Page
from tests.smoke.conftest import PASSWORD
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
import pytest
from app import db

def test_element_diagram_export_journey(
    page: Page, live_server, seeded, app
):
    # 1. SETUP: Create elements and relationships
    with app.app_context():
        org_id = seeded["ids"]["org"]
        role = ArchiMateElement(
            name="Test Business Role",
            type="BusinessRole",
            layer="Business",
            organization_id=org_id,
        )
        component = ArchiMateElement(
            name="Test Application Component",
            type="ApplicationComponent",
            layer="Application",
            organization_id=org_id,
        )
        db.session.add_all([role, component])
        db.session.commit()

        rel = ArchiMateRelationship(
            source_id=role.id,
            target_id=component.id,
            type="Assignment",
            organization_id=org_id,
        )
        db.session.add(rel)
        db.session.commit()
        
        no_rel_element = ArchiMateElement(
            name="No Relationship Element",
            type="BusinessProcess",
            layer="Business",
            organization_id=org_id,
        )
        db.session.add(no_rel_element)
        db.session.commit()

    # 2. TEST WITH RELATIONSHIPS
    page.goto(live_server + "/auth/login")
    page.fill('input[name="email"]', seeded["emails"]["solution_architect"])
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    expect(page).to_have_url(re.compile(".*/dashboard"))

    # Go to the element page
    page.goto(live_server + f"/archimate/element/{role.id}")

    with page.expect_download() as download_info:
        page.click('button:has-text("Export Diagram")')
    
    download = download_info.value
    svg_content = download.path().read_text()

    # Assert that both elements and the relationship are in the SVG
    assert "Test Business Role" in svg_content
    assert "Test Application Component" in svg_content
    assert 'marker-end="url(#arrow)"' in svg_content

    # 3. TEST WITH NO RELATIONSHIPS
    page.goto(live_server + f"/archimate/element/{no_rel_element.id}")
    expect(page.locator("text=This element has no relationships yet.")).to_be_visible()
    
    with page.expect_download() as download_info_no_rel:
        page.click('button:has-text("Export Diagram")')

    download_no_rel = download_info_no_rel.value
    svg_content_no_rel = download_no_rel.path().read_text()
    
    assert "No Relationship Element" in svg_content_no_rel
    assert 'marker-end="url(#arrow)"' not in svg_content_no_rel
