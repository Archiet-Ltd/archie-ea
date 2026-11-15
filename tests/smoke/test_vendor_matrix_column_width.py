"""
Vendor Capability Matrix Column Width Tests

Tests that the capability column:
1. On mobile (390px): takes no more than ~45% of visible table width
2. At 1440px: long capability names are readable (wrapping or on hover/focus)
3. The matrix scrolls horizontally within its container
"""
import json
import os
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in


def _seed_vendor_matrix_data(app, org_id, admin_user_id):
    """Seed vendor organization, products, and capability mappings for matrix testing."""
    from app import db
    from app.models.vendor.vendor_organization import (
        VendorOrganization,
        VendorProduct,
        VendorProductCapability,
    )
    from app.models.business_capabilities import BusinessCapability

    # Create or get vendor organization (shared reference data, no org_id)
    vendor = VendorOrganization.query.filter_by(name="Test Vendor Corp").first()
    if not vendor:
        vendor = VendorOrganization(
            name="Test Vendor Corp",
            display_name="Test Vendor Corporation",
            vendor_type="SaaS",
            website="https://testvendor.example.com",
            status="active",
            is_seed_data=False,
        )
        db.session.add(vendor)
        db.session.flush()
    else:
        # Update if needed
        vendor.display_name = "Test Vendor Corporation"
        vendor.vendor_type = "SaaS"
        vendor.website = "https://testvendor.example.com"
        vendor.status = "active"

    # Create or get vendor products (shared reference data, no org_id)
    product1 = VendorProduct.query.filter_by(vendor_organization_id=vendor.id, name="Enterprise Platform Suite").first()
    if not product1:
        product1 = VendorProduct(
            name="Enterprise Platform Suite",
            vendor_organization_id=vendor.id,
            product_type="ERP",
            description="Comprehensive ERP platform",
        )
        db.session.add(product1)
    
    product2 = VendorProduct.query.filter_by(vendor_organization_id=vendor.id, name="Analytics Cloud Pro").first()
    if not product2:
        product2 = VendorProduct(
            name="Analytics Cloud Pro",
            vendor_organization_id=vendor.id,
            product_type="ERP",
            description="Advanced analytics and BI",
        )
        db.session.add(product2)
    
    product3 = VendorProduct.query.filter_by(vendor_organization_id=vendor.id, name="HR Management System").first()
    if not product3:
        product3 = VendorProduct(
            name="HR Management System",
            vendor_organization_id=vendor.id,
            product_type="HCM",
            description="Human capital management",
        )
        db.session.add(product3)
    db.session.flush()

    # Create business capabilities with long names (tenant-scoped)
    capability_names = [
        "Financial Planning and Analysis",
        "Supply Chain Planning and Optimization",
        "Customer Relationship Management and Engagement",
        "Human Capital Management and Workforce Planning",
        "Enterprise Risk Management and Compliance Monitoring",
        "IT Service Management and Infrastructure Operations",
        "Strategic Planning and Performance Measurement",
        "Procurement and Vendor Relationship Management",
    ]
    capabilities = []
    for name in capability_names:
        cap = BusinessCapability.query.filter_by(name=name, organization_id=org_id).first()
        if not cap:
            cap = BusinessCapability(
                name=name,
                business_domain=name.split(" ")[0],  # Use first word as domain
                level=1,
                organization_id=org_id,
            )
            db.session.add(cap)
        capabilities.append(cap)
    db.session.flush()

    # Create vendor product capability mappings (tenant-scoped via TenantMixin)
    mappings = []
    for i, product in enumerate([product1, product2, product3]):
        for j, cap in enumerate(capabilities):
            # Vary coverage percentages
            coverage = 30 + (i * 20) + (j * 5)
            coverage = min(coverage, 95)
            # Check if mapping already exists
            existing = VendorProductCapability.query.filter_by(
                vendor_product_id=product.id,
                business_capability_id=cap.id,
                organization_id=org_id
            ).first()
            if not existing:
                mapping = VendorProductCapability(
                    vendor_product_id=product.id,
                    business_capability_id=cap.id,
                    coverage_percentage=coverage,
                    out_of_box_percentage=coverage - 10,
                    maturity_level=3 + (i % 3),
                    fit_score=70 + (i * 5),
                    implementation_complexity=4 + (j % 4),
                    gaps=json.dumps([f"Gap {j+1} for {product.name}"]),
                    strengths=json.dumps([f"Strength {j+1} for {product.name}"]),
                    organization_id=org_id,
                )
                mappings.append(mapping)
    if mappings:
        db.session.add_all(mappings)
    db.session.commit()

    return {
        "vendor_id": vendor.id,
        "product_ids": [p.id for p in [product1, product2, product3]],
        "capability_ids": [c.id for c in capabilities],
    }


def _save_shots(page, name):
    """Save light and dark screenshots when SMOKE_SHOT_DIR is set."""
    out = os.environ.get("SMOKE_SHOT_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        page.wait_for_timeout(200)
        page.locator('[data-testid="matrix-table-container"]').screenshot(
            path=os.path.join(out, f"{name}_{scheme}.png")
        )


def _goto(page, live_server, path):
    page.goto(f"{live_server}{path}", wait_until="networkidle", timeout=PAGE_TIMEOUT)


def _measure_capability_column(page):
    """Measure the capability column width and table container width."""
    return page.evaluate("""() => {
        const container = document.querySelector('[data-testid="matrix-table-container"]');
        const table = container?.querySelector('table');
        const header = table?.querySelector('thead th:first-child');
        const firstCell = table?.querySelector('tbody td:first-child');
        
        if (!container || !header || !firstCell) return null;
        
        const containerRect = container.getBoundingClientRect();
        const headerRect = header.getBoundingClientRect();
        const cellRect = firstCell.getBoundingClientRect();
        
        // Visible table width (container width minus scrollbar if present)
        const tableWidth = containerRect.width;
        const headerWidth = headerRect.width;
        const cellWidth = cellRect.width;
        
        return {
            containerWidth: Math.round(tableWidth),
            headerWidth: Math.round(headerWidth),
            cellWidth: Math.round(cellWidth),
            headerPercent: Math.round((headerWidth / tableWidth) * 100),
            cellPercent: Math.round((cellWidth / tableWidth) * 100),
            hasHorizontalScroll: container.scrollWidth > container.clientWidth,
        };
    }""")


def _check_long_name_readability(page):
    """Check if long capability names are readable at 1440px."""
    return page.evaluate("""() => {
        const cells = document.querySelectorAll('tbody td:first-child');
        const results = [];
        cells.forEach(cell => {
            const text = cell.textContent.trim();
            const rect = cell.getBoundingClientRect();
            const style = window.getComputedStyle(cell);
            const isTruncated = style.textOverflow === 'ellipsis' && style.overflow === 'hidden';
            const hasWrap = style.whiteSpace === 'normal' || style.whiteSpace === 'pre-wrap';
            results.push({
                text: text.slice(0, 60),
                fullText: text,
                width: Math.round(rect.width),
                isTruncated,
                hasWrap,
                whiteSpace: style.whiteSpace,
                overflow: style.overflow,
                textOverflow: style.textOverflow,
            });
        });
        return results;
    }""")


def _check_hover_focus_expansion(page):
    """Check if capability names expand on hover/focus."""
    return page.evaluate("""() => {
        const cells = document.querySelectorAll('tbody td:first-child');
        if (cells.length === 0) return null;
        const cell = cells[0];
        const originalWidth = cell.getBoundingClientRect().width;
        const originalOverflow = window.getComputedStyle(cell).overflow;
        const originalTextOverflow = window.getComputedStyle(cell).textOverflow;
        const originalWhiteSpace = window.getComputedStyle(cell).whiteSpace;
        
        // Simulate hover by checking focus styles
        cell.focus();
        const focusedWidth = cell.getBoundingClientRect().width;
        const focusedOverflow = window.getComputedStyle(cell).overflow;
        const focusedTextOverflow = window.getComputedStyle(cell).textOverflow;
        const focusedWhiteSpace = window.getComputedStyle(cell).whiteSpace;
        cell.blur();
        
        return {
            originalWidth: Math.round(originalWidth),
            focusedWidth: Math.round(focusedWidth),
            originalOverflow,
            focusedOverflow,
            originalTextOverflow,
            focusedTextOverflow,
            originalWhiteSpace,
            focusedWhiteSpace,
            expandsOnFocus: focusedWidth > originalWidth || focusedOverflow === 'visible',
        };
    }""")


@pytest.fixture(scope="module")
def matrix_org(live_server, app):
    """Create an organization with vendor matrix data."""
    org = create_fresh_org("enterprise_architect")
    with app.app_context():
        _seed_vendor_matrix_data(app, org["org_id"], org["user_ids"]["enterprise_architect"])
    yield org


def test_matrix_capability_column_width_mobile(browser, live_server, matrix_org):
    """On mobile (390px), capability column should be ~45% of table width."""
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        color_scheme="dark",
    )
    page = context.new_page()
    try:
        sign_in(page, live_server, matrix_org["emails"]["enterprise_architect"])
        _goto(page, live_server, "/dashboards/vendor-capability-matrix")
        
        # Wait for page to load and select ERP category
        expect(page.locator("#f-category")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.select_option("#f-category", "ERP")
        
        # Wait for matrix to load
        expect(page.locator("table.dense-table")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)  # Let Alpine.js render
        
        # Measure column widths
        measurements = _measure_capability_column(page)
        _save_shots(page, "matrix_390")
        assert measurements is not None, "Could not measure column widths"
        
        print("\nMobile (390px) measurements:")
        print(f"  Container width: {measurements['containerWidth']}px")
        print(f"  Header width: {measurements['headerWidth']}px ({measurements['headerPercent']}%)")
        print(f"  Cell width: {measurements['cellWidth']}px ({measurements['cellPercent']}%)")
        print(f"  Has horizontal scroll: {measurements['hasHorizontalScroll']}")
        
        # Capability column should be no more than ~45% of table width
        assert measurements['cellPercent'] <= 50, \
            f"Capability column is {measurements['cellPercent']}% of table width, should be <= 45%"
        
        # Table should have horizontal scroll
        assert measurements['hasHorizontalScroll'], "Table should scroll horizontally"
        
        # Header should not be wider than its content (no empty 215px header cell)
        assert measurements['headerWidth'] <= measurements['cellWidth'] + 20, \
            f"Header ({measurements['headerWidth']}px) much wider than cell ({measurements['cellWidth']}px)"
        
    finally:
        context.close()


def test_matrix_capability_column_width_desktop(browser, live_server, matrix_org):
    """At 1440px, long capability names should be readable (wrapping or on hover/focus)."""
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        color_scheme="light",
    )
    page = context.new_page()
    try:
        sign_in(page, live_server, matrix_org["emails"]["enterprise_architect"])
        _goto(page, live_server, "/dashboards/vendor-capability-matrix")
        
        # Wait for page to load and select ERP category
        expect(page.locator("#f-category")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.select_option("#f-category", "ERP")
        
        # Wait for matrix to load
        expect(page.locator("table.dense-table")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        
        # Check long name readability
        readability = _check_long_name_readability(page)
        _save_shots(page, "matrix_1440")
        print("\nDesktop (1440px) readability:")
        for r in readability:
            print(f"  '{r['text']}...' - width: {r['width']}px, truncated: {r['isTruncated']}, wraps: {r['hasWrap']}")
        
        # At least some long names should not be truncated (wrapping) or should expand on focus
        long_names = [r for r in readability if len(r['fullText']) > 30]
        assert long_names, "Should have long capability names for testing"
        
        # Check hover/focus expansion
        focus_check = _check_hover_focus_expansion(page)
        print(f"\nFocus expansion check: {focus_check}")
        
        # Either names wrap (whitespace-normal) or expand on focus
        all_wrap = all(r['hasWrap'] for r in long_names)
        expands_on_focus = focus_check and focus_check.get('expandsOnFocus', False)
        
        assert all_wrap or expands_on_focus, \
            "Long capability names should either wrap or expand on hover/focus"
        
    finally:
        context.close()


def test_matrix_horizontal_scroll_container(browser, live_server, matrix_org):
    """Matrix should scroll horizontally within its own container."""
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        color_scheme="dark",
    )
    page = context.new_page()
    try:
        sign_in(page, live_server, matrix_org["emails"]["enterprise_architect"])
        _goto(page, live_server, "/dashboards/vendor-capability-matrix")
        
        expect(page.locator("#f-category")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.select_option("#f-category", "ERP")
        
        expect(page.locator("table.dense-table")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        
        # Check that the container has overflow-auto and table is wider than container
        scroll_info = page.evaluate("""() => {
            const container = document.querySelector('[data-testid="matrix-table-container"]');
            const table = container?.querySelector('table');
            if (!container || !table) return null;
            return {
                containerWidth: container.clientWidth,
                containerScrollWidth: container.scrollWidth,
                tableWidth: table.scrollWidth,
                hasOverflowAuto: window.getComputedStyle(container).overflowX === 'auto',
                canScroll: container.scrollWidth > container.clientWidth,
            };
        }""")
        
        print(f"\nScroll container info: {scroll_info}")
        assert scroll_info is not None, "Could not find scroll container"
        assert scroll_info['hasOverflowAuto'], "Container should have overflow-x: auto"
        assert scroll_info['canScroll'], "Container should be scrollable (table wider than container)"
        assert scroll_info['tableWidth'] > scroll_info['containerWidth'], "Table should be wider than container"
        
    finally:
        context.close()