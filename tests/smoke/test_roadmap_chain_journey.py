"""The gap to work package to plateau chain reads the same on every page.

A business architect in the demonstration organisation walks the chain: the
gaps register, the capability roadmap, capability health, the one-page roadmap,
the work package list and its pages, the gap's own page and backlog
prioritisation. Each question has one answer wherever it is asked: how many
gaps are recorded, which plateau a package is on, how far a package has got,
what state a gap is in and which requirements the backlog holds.
"""
import os
import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
# One date format: 02 Nov 2026.
DATE_PATTERNS = {
    "iso": re.compile(r"\b20\d\d-\d\d-\d\d\b"),
    "slash": re.compile(r"\b\d{1,2}/\d{1,2}/20\d\d\b"),
    "month first": re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}, 20\d\d\b"),
}


@pytest.fixture(scope="module")
def demo(live_server, app):
    """Seed the demonstration organisation the way the product's own seed does."""
    from app import db

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            from app.commands.seed_demo_company import seed_demo_company
            from app.models.implementation_migration import Gap
            from app.models.organization import Organization
            from app.models.solution_architect_models import SolutionRequirement
            from app.models.user import User

            seed_demo_company()
            org = Organization.query.filter_by(slug="lantern-quay").one()
            architect = User.query.filter_by(email=DEMO_EMAIL, organization_id=org.id).one()
            architect.password = PASSWORD
            db.session.commit()
            out = {
                "org_id": org.id,
                "gaps": Gap.query.filter(
                    Gap.organization_id == org.id, Gap.gap_kind != "plateau_transition"
                ).count(),
                "gap_names": [
                    g.name for g in Gap.query.filter(
                        Gap.organization_id == org.id, Gap.gap_kind != "plateau_transition"
                    ).order_by(Gap.id)
                ],
                "requirements": sorted(
                    r.name for r in SolutionRequirement.query.filter(
                        SolutionRequirement.organization_id == org.id,
                        SolutionRequirement.deleted_at.is_(None),
                    )
                ),
            }
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return out


def _number(text):
    match = re.search(r"\d+", text or "")
    assert match, "no figure in %r" % text
    return int(match.group(0))


def _open(page, base, path, ready):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector(ready, timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")


def test_every_page_gives_one_gap_count(page, live_server, demo):
    sign_in(page, live_server, DEMO_EMAIL)
    expected = demo["gaps"]
    assert expected >= 10, demo

    _open(page, live_server, "/enterprise/implementation/gap-analysis", "[data-testid='gap-stat-recorded']")
    assert _number(page.locator("[data-testid='gap-stat-recorded']").inner_text()) == expected

    _open(page, live_server, "/capability-roadmap", "[data-testid='rdm-recorded-gaps']")
    page.wait_for_function(
        "document.querySelector(\"[data-testid='rdm-recorded-gaps']\").innerText.match(/\\d/)",
        timeout=PAGE_TIMEOUT,
    )
    assert _number(page.locator("[data-testid='rdm-recorded-gaps']").inner_text()) == expected

    _open(page, live_server, "/strategic/capability-health", "[data-testid='health-recorded-gaps']")
    assert _number(page.locator("[data-testid='health-recorded-gaps']").inner_text()) == expected
    # The derived figure is still shown, and it says it is a signal.
    assert "signal" in page.locator("main").inner_text().lower()


def test_both_roadmaps_show_the_same_plateaus_with_their_packages(page, live_server, demo):
    sign_in(page, live_server, DEMO_EMAIL)

    _open(page, live_server, "/capability-roadmap", "[data-testid='rdm-plateau']")
    page.wait_for_function(
        "document.querySelectorAll(\"[data-testid='rdm-plateau']\").length > 0", timeout=PAGE_TIMEOUT)
    cap_cards = {}
    for card in page.locator("[data-testid='rdm-plateau']").all():
        name = card.locator("[data-testid='rdm-plateau-name']").inner_text().strip()
        cap_cards[name] = (
            _number(card.locator("[data-testid='rdm-plateau-gaps']").inner_text()),
            _number(card.locator("[data-testid='rdm-plateau-wps']").inner_text()),
        )
    assert len(cap_cards) == 5, cap_cards
    assert sum(gaps for gaps, _ in cap_cards.values()) > 0, cap_cards
    assert sum(wps for _, wps in cap_cards.values()) == 12, cap_cards

    _open(page, live_server, "/genome/roadmap/", "[data-testid='roadmap-column']")
    body = page.locator("main").inner_text()
    assert "Not on a plateau yet" not in body
    assert "not scheduled" not in body.lower()
    board = {}
    for card in page.locator("[data-testid='roadmap-plateau']").all():
        name = card.locator("[data-testid='roadmap-plateau-name']").inner_text().strip()
        board[name] = (
            _number(card.locator("[data-testid='roadmap-plateau-gaps']").inner_text()),
            len(card.locator("[data-testid='roadmap-work-package']").all())
            + _extra(card),
        )
    assert board == cap_cards, (board, cap_cards)

    # Every package on a card opens its own page, with content.
    link = page.locator("[data-testid='roadmap-work-package']").first
    title = link.inner_text().strip()
    link.click()
    page.wait_for_load_state("domcontentloaded")
    assert title in page.locator("main").inner_text()


def _extra(card):
    more = card.locator("[data-testid='roadmap-more-packages']")
    return _number(more.inner_text()) if more.count() else 0


def test_a_completed_package_reads_one_hundred_percent(page, live_server, demo):
    sign_in(page, live_server, DEMO_EMAIL)
    _open(page, live_server, "/enterprise/implementation/work-packages", "[data-testid='work-package-link']")
    rows = page.locator("tr", has=page.locator("[data-testid='work-package-link']"))
    seen = {}
    for row in rows.all():
        name = row.locator("[data-testid='work-package-link']").inner_text().strip()
        progress = row.locator("[data-testid='work-package-progress']")
        seen[name] = progress.inner_text().strip() if progress.count() else None
    assert seen["Procure handheld field testers"] == "100%", seen
    # A package that has not started reads 0%; one under way reads between.
    assert seen["Build digital twin of calibration lab"] == "0%", seen
    assert seen["Deploy automated calibration rigs"] not in (None, "0%", "100%"), seen

    # The card of the plateau holding the finished package counts it as done.
    _open(page, live_server, "/genome/roadmap/", "[data-testid='roadmap-column']")
    assert re.search(r"[1-9]\d* of \d+ done", page.locator("main").inner_text())


def test_a_package_opens_with_its_capability_and_elements(page, live_server, demo):
    sign_in(page, live_server, DEMO_EMAIL)
    _open(page, live_server, "/enterprise/implementation/work-packages", "[data-testid='work-package-link']")
    page.locator("[data-testid='work-package-link']", has_text="Deploy automated calibration rigs").click()
    page.wait_for_selector("[data-testid='wp-capability']", timeout=PAGE_TIMEOUT)
    assert page.locator("[data-testid='wp-capability']").inner_text().strip() == "Sensor Calibration"
    main = page.locator("main").inner_text()
    assert "No capability linked" not in main
    assert "No elements linked" not in main
    # The capability link lands on a page that names it.
    page.locator("[data-testid='wp-capability'] a").click()
    page.wait_for_load_state("domcontentloaded")
    assert "Sensor Calibration" in page.locator("main").inner_text()


def test_a_gap_shows_one_status_and_the_register_reads_the_records(page, live_server, demo):
    sign_in(page, live_server, DEMO_EMAIL)
    _open(page, live_server, "/enterprise/implementation/gap-analysis", "[data-testid='gap-stat-recorded']")
    in_progress = _number(page.locator("[data-testid='gap-stat-in-progress']").inner_text())
    assert in_progress > 0, "no gap is in progress although packages are"
    severities = {
        row.get_attribute("data-severity")
        for row in page.locator("[data-testid^='gap-link-']").all()
    }
    assert len(severities) > 1, severities

    # The gap is found by its recorded name, whatever id the database gave it.
    link = page.locator("[data-testid^='gap-link-']", has_text=demo["gap_names"][0]).first
    assert link.count() == 1, demo["gap_names"][0]
    link.click()
    page.wait_for_selector("[data-testid='kpi-status']", timeout=PAGE_TIMEOUT)
    status = page.locator("[data-testid='kpi-status']").inner_text()
    assert "Not set" not in status, status
    main = page.locator("main").inner_text()
    assert "Resolution status" not in main
    assert re.search(r"Identified|In progress|Resolved", status), status


def test_prioritisation_lists_the_backlogs_requirements(page, live_server, demo):
    sign_in(page, live_server, DEMO_EMAIL)
    assert len(demo["requirements"]) >= 8, demo["requirements"]
    _open(page, live_server, "/backlog/prioritisation", "[data-testid='moscow-help']")
    page.wait_for_load_state("networkidle")
    body = page.locator("main").inner_text()
    assert "No requirements to prioritise" not in body
    for name in demo["requirements"]:
        assert name in body, name
    # A requirement moved on the board is the one the backlog shows.
    page.get_by_role("tab", name="WSJF").click()
    page.wait_for_timeout(300)
    for name in demo["requirements"]:
        assert page.get_by_role("cell", name=name).count() >= 1, name


def test_dates_use_one_format(page, live_server, demo):
    sign_in(page, live_server, DEMO_EMAIL)
    pages = [
        ("/capability-roadmap", "[data-testid='rdm-plateau-name']"),
        ("/genome/roadmap/", "[data-testid='roadmap-column']"),
        ("/enterprise/implementation/work-packages", "[data-testid='work-package-link']"),
        ("/enterprise/implementation/gap-analysis", "[data-testid='gap-stat-recorded']"),
    ]
    bad = {}
    for path, ready in pages:
        _open(page, live_server, path, ready)
        text = page.locator("main").inner_text()
        for label, pattern in DATE_PATTERNS.items():
            hits = pattern.findall(text)
            if hits:
                bad.setdefault(path, {})[label] = hits[:3]
    assert not bad, bad


def test_a_package_page_agrees_with_the_list_on_progress_and_dates(page, live_server, demo):
    sign_in(page, live_server, DEMO_EMAIL)
    _open(page, live_server, "/enterprise/implementation/work-packages", "[data-testid='work-package-link']")
    page.locator("[data-testid='work-package-link']", has_text="Procure handheld field testers").click()
    page.wait_for_selector("[data-testid='wp-progress']", timeout=PAGE_TIMEOUT)
    assert page.locator("[data-testid='wp-progress']").inner_text().strip() == "100%"
    dates = page.locator("[data-testid='wp-dates']").inner_text().strip()
    assert re.fullmatch(r"\d\d [A-Z][a-z]{2} 20\d\d to \d\d [A-Z][a-z]{2} 20\d\d", dates), dates
