"""Test that reproduces the converted work package detail page issue."""
import pytest
from tests.test_gap_work_package_link import _gap, _wp, world


@pytest.mark.usefixtures("db_session")
def test_converted_work_package_detail_page(db_session, world, client, login_as):
    """Test that a work package converted from a gap can be viewed on its detail page."""
    from app.services import work_package_service as svc
    
    a = world["a"]
    
    # Create a gap
    gap = _gap(db_session, a, "Test gap for conversion")
    db_session.flush()
    
    # Convert the gap to a work package (the real conversion route)
    login_as(client, world["editor_a"])
    resp = client.post(
        f"/capability-map/api/roadmap/gaps/{gap.id}/work-packages",
        json={"template": "auto"}
    )
    print(f"Convert response: status={resp.status_code}, body={resp.get_json()}")
    assert resp.status_code in (200, 201), f"Conversion failed: {resp.get_json()}"
    
    # Get the created work package
    wps = svc.work_packages_for_gaps([gap.id], a.id).get(gap.id, [])
    print(f"Work packages for gap: {wps}")
    assert len(wps) > 0, "No work package created from gap"
    wp = wps[0]
    print(f"Work package: id={wp.id}, name={wp.name}, source_table={wp.source_table}, source_id={wp.source_id}")
    db_session.flush()
    
    # Now try to access the detail page
    login_as(client, world["editor_a"])
    resp = client.get(f"/implementation/work-packages/{wp.id}")
    print(f"Detail page response: status={resp.status_code}")
    if resp.status_code == 302:
        print(f"Redirect location: {resp.location}")
        flash_messages = resp.headers.get('X-Flas-Messages', '')
        print(f"Flash messages: {flash_messages}")
    html = resp.get_data(as_text=True) if resp.status_code == 200 else ""
    print(f"Package name in body: {'Test gap' in html or wp.name in html}")
    
    # Before fix: this should fail (302 redirect)
    # After fix: this should pass (200 with package name)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert wp.name in html, f"Expected package name '{wp.name}' in response body"


@pytest.mark.usefixtures("db_session")
def test_converted_work_package_detail_page_org_b_404(db_session, world, client, login_as):
    """Test that a user from organisation B cannot access a work package from organisation A."""
    from app.services import work_package_service as svc
    
    a = world["a"]
    
    # Create a gap in organisation A
    gap = _gap(db_session, a, "Test gap for conversion org A")
    db_session.flush()
    
    # Convert the gap to a work package as an Architect from organisation A
    login_as(client, world["editor_a"])
    resp = client.post(
        f"/capability-map/api/roadmap/gaps/{gap.id}/work-packages",
        json={"template": "auto"}
    )
    assert resp.status_code in (200, 201), f"Conversion failed: {resp.get_json()}"
    
    # Get the created work package
    wps = svc.work_packages_for_gaps([gap.id], a.id).get(gap.id, [])
    assert len(wps) > 0, "No work package created from gap"
    wp = wps[0]
    db_session.flush()
    
    # Try to access the detail page as a user from organisation B
    login_as(client, world["editor_b"])
    resp = client.get(f"/implementation/work-packages/{wp.id}")
    print(f"Org B detail page response: status={resp.status_code}")
    
    # Should return 404 (not found) because the work package belongs to organisation A
    assert resp.status_code == 404, f"Expected 404 for org B user, got {resp.status_code}"
