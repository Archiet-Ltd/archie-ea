"""An enterprise architect opens the demonstration organisation and finds real
cards on the portfolio Kanban, rows under the Kind filter, solutions on the ADM
board, a value stream with its stages and a requirements backlog in mixed states.
"""
import os
import pathlib
import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("SEEDSAFE_SHOTS", "/tmp/seedsafe-shots"))
DEMO_EMAIL = "demo@lantern-quay.example.com"
EPICS_PER_COLUMN = {"Funnel": 3, "Reviewing": 3, "Analyzing": 3, "Portfolio Backlog": 3}


@pytest.fixture(scope="module")
def demo_org(live_server):
    """Seed the demonstration organisation with the product's own seed."""
    from app import create_app
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    app = create_app("testing")
    try:
        with app.app_context():
            seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return DEMO_EMAIL


def _open(browser, base, email, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    sign_in(page, base, email)
    return context, page, errors


def _shot(page, name, width):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%d.png" % (name, width))), full_page=True)


@pytest.mark.parametrize("width", [1440, 390])
def test_portfolio_kanban_shows_epics_in_four_columns(browser, live_server, demo_org, width):
    context, page, errors = _open(browser, live_server, demo_org, width)
    try:
        page.goto(live_server + "/safe/portfolio/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_selector(".kanban-card", timeout=PAGE_TIMEOUT)
        for state, count in EPICS_PER_COLUMN.items():
            cards = page.locator(".kanban-column[data-state='%s'] .kanban-card" % state)
            assert cards.count() == count, "%s shows %d cards, wanted %d" % (state, cards.count(), count)
        for card in page.locator(".kanban-card").all()[:12]:
            assert "WSJF:" in card.inner_text()
            assert card.locator("h3").inner_text().strip()
        # Scores run high to low inside every column.
        for state in EPICS_PER_COLUMN:
            texts = page.locator(
                ".kanban-column[data-state='%s'] .kanban-card" % state).all_inner_texts()
            scores = [float(re.search(r"WSJF:\s*([0-9.]+)", t).group(1)) for t in texts]
            assert scores == sorted(scores, reverse=True), (state, scores)
        _shot(page, "portfolio-kanban", width)
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_kind_filter_returns_epics_and_features(browser, live_server, demo_org, width):
    context, page, errors = _open(browser, live_server, demo_org, width)
    try:
        page.goto(live_server + "/architecture/?layer=implementation",
                  wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.locator("[data-testid='advanced-toggle']").click()
        for kind, expected in (("Epic", 12), ("Feature", 3), ("Enabler", 1)):
            page.select_option("#architecture-kind", kind)
            page.wait_for_timeout(1200)
            body = page.inner_text("body")
            for name in {
                "Epic": ["Digital Transformation Programme", "Event Backbone Migration"],
                "Feature": ["Calibration Certificate Portal", "Instrument History Timeline"],
                "Enabler": ["Shared Telemetry Pipeline"],
            }[kind]:
                assert name in body, "%s filter does not list %s" % (kind, name)
            if kind != "Epic":
                assert "Digital Transformation Programme" not in body
        _shot(page, "kind-filter", width)
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_adm_board_shows_solutions_that_open(browser, live_server, demo_org, width):
    context, page, errors = _open(browser, live_server, demo_org, width)
    try:
        page.goto(live_server + "/adm-kanban/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        for name in ("Field Calibration Platform", "Supplier Collaboration Hub",
                     "Event-Driven Operations Backbone"):
            assert name in body, "%s is not on the ADM board" % name
        _shot(page, "adm-board", width)
        page.locator("button[title='Flat Kanban columns']").click()
        page.wait_for_timeout(500)
        _shot(page, "adm-board-flat", width)
        link = page.locator("a:visible", has_text="Open Solution").first
        if width >= 1000:
            link.click()
        else:
            # At phone width a neighbouring card covers the link, so follow where it points.
            page.goto(live_server + link.get_attribute("href"), wait_until="networkidle")
        page.wait_for_load_state("networkidle")
        assert page.locator("h1, h2").first.inner_text().strip()
        assert any(n in page.inner_text("body") for n in (
            "Field Calibration Platform", "Supplier Collaboration Hub",
            "Event-Driven Operations Backbone"))
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_value_stream_lists_four_stages_with_capabilities(browser, live_server, demo_org, width):
    context, page, errors = _open(browser, live_server, demo_org, width)
    try:
        page.goto(live_server + "/value-streams/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.locator("a", has_text="Calibration Request to Certificate").first.click()
        page.wait_for_load_state("networkidle")
        body = page.inner_text("body")
        for stage in ("Receive Request", "Schedule Engineer", "Calibrate and Test",
                      "Certify and Report"):
            assert stage in body, stage
        for capability in ("Order Fulfilment", "Sensor Calibration", "Regulatory Compliance"):
            assert capability in body, capability
        _shot(page, "value-stream", width)
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_requirements_backlog_lists_eight_in_mixed_states(browser, live_server, demo_org, width):
    context, page, errors = _open(browser, live_server, demo_org, width)
    try:
        page.goto(live_server + "/enterprise/requirements-backlog", wait_until="networkidle",
                  timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        for name in ("Record the as-found reading for every instrument",
                     "Publish every instrument change as an event"):
            assert name in body, name
        page.select_option("#filter-status", "Approved")
        page.wait_for_timeout(1200)
        body = page.inner_text("body")
        assert "Reject certificates with a missing reference standard" in body
        assert "Record the as-found reading for every instrument" not in body
        _shot(page, "requirements-backlog", width)
        assert not errors, errors
    finally:
        context.close()
