#!/usr/bin/env python3
"""Browser walk for fix05 - capture screenshots at 1440px and 390px."""

import os

import pytest

from .fresh_org import create_fresh_org, sign_in


@pytest.mark.smoke
def test_fix05_browser_walk(browser, live_server):
    """Capture screenshots of all fixed pages at 1440px and 390px."""
    org = create_fresh_org("enterprise_architect")
    email = org["emails"]["enterprise_architect"]

    pages = [
        ("/strategic/capability-health", "capability_health"),
        ("/strategic/investment-matrix", "investment_matrix_strategic"),
        ("/hybrid-mapping-dashboard", "hybrid_mapping"),
        ("/strategic/risk-assessment", "risk_assessment"),
        ("/enterprise/strategic/investment-matrix", "investment_matrix_enterprise"),
        ("/ea-workflows/deliverables/vision", "vision_deliverable"),
    ]

    walk_dir = "/home/ubuntu/verify/seats/fix05/walk"
    os.makedirs(walk_dir, exist_ok=True)

    # Desktop 1440px
    context = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 1440, "height": 1000}
    )
    page = context.new_page()
    sign_in(page, live_server, email)

    for path, name in pages:
        page.goto(live_server + path, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_load_state("networkidle", timeout=90000)
        screenshot_path = os.path.join(walk_dir, f"{name}_1440px.png")
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"Saved: {screenshot_path}")

    context.close()

    # Mobile 390px
    context = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 390, "height": 844}
    )
    page = context.new_page()
    sign_in(page, live_server, email)

    for path, name in pages:
        page.goto(live_server + path, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_load_state("networkidle", timeout=90000)
        screenshot_path = os.path.join(walk_dir, f"{name}_390px.png")
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"Saved: {screenshot_path}")

    context.close()