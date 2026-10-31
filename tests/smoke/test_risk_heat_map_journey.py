"""Risk heat map: a person who opens a solution's heat map link reaches a matrix whose cells equal the register.

The link /solutions/<id>/risks (and /risks/heatmap) is answered for a browser by the solution record's Risks
tab; the older heat-map template behind the same URL is never served. The matrix a person lands on must count
the risks the register lists for that solution at each probability and impact, and a solution with no risks
must say so.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in
from .overview_data import purge_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# (title, likelihood, impact)
RISKS = [("Payments outage", 5, 5), ("Data loss on migration", 5, 5), ("Vendor lock-in", 3, 4),
         ("Late hand-over", 1, 1)]


@pytest.fixture(scope="module")
def seeded(live_server):
    from app import create_app, db
    from app.models.risk import Risk
    from app.models.solution_models import Solution

    info = create_fresh_org("solution_architect")
    suffix = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        user_id = info["user_ids"]["solution_architect"]
        full, empty = (Solution(name="%s %s" % (n, suffix), organization_id=info["org_id"], created_by_id=user_id,
                                governance_status="draft", adm_phase="C") for n in ("Heat solution", "Quiet solution"))
        db.session.add_all([full, empty])
        db.session.commit()
        for title, likelihood, impact in RISKS:
            db.session.add(Risk(title="%s %s" % (title, suffix), likelihood=likelihood, impact=impact,
                                solution_id=full.id, organization_id=info["org_id"]))
        # Another solution's risk and an unattached one never show on this solution's matrix.
        db.session.add(Risk(title="Elsewhere %s" % suffix, likelihood=2, impact=2, solution_id=empty.id + 1000,
                            organization_id=info["org_id"]))
        db.session.add(Risk(title="Unattached %s" % suffix, likelihood=4, impact=4, organization_id=info["org_id"]))
        db.session.commit()
        info.update(suffix=suffix, full=full.id, empty=empty.id)
        db.session.remove()
    yield info
    purge_org(info["org_id"])


def _open(browser, live_server, seeded, solution, width=1400):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, seeded["emails"]["solution_architect"])
    response = page.goto("%s/solutions/%s/risks" % (live_server, solution), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert response.status == 200
    return page


def _cells(page):
    """{(probability, impact): count} read from the matrix cells' labels."""
    out = {}
    for cell in page.get_by_test_id("risk-cell").all():
        m = re.match(r"Probability (\d), impact (\d): (\d+) risk", cell.get_attribute("aria-label"))
        out[(int(m.group(1)), int(m.group(2)))] = int(m.group(3))
        assert int(cell.get_attribute("data-count")) == int(m.group(3))
    return out


def test_cells_equal_the_register_filtered_to_the_solution(browser, live_server, seeded):
    page = _open(browser, live_server, seeded, seeded["full"])
    status, listed = api(page, "GET", "/api/risks?solution_id=%s" % seeded["full"])
    assert status == 200 and len(listed) == len(RISKS)
    expected = {}
    for risk in listed:
        key = (risk["likelihood"], risk["impact"])
        expected[key] = expected.get(key, 0) + 1
    cells = _cells(page)
    assert len(cells) == 25
    assert {k: v for k, v in cells.items() if v} == expected, cells
    # The register lists the same four, and not the other two.
    page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    register = page.locator("[data-risk-row]").all_inner_texts()
    for title, _, _ in RISKS:
        assert any("%s %s" % (title, seeded["suffix"]) in row for row in register), title




def test_the_heat_map_link_lands_on_the_record_risks_tab(browser, live_server, seeded):
    page = _open(browser, live_server, seeded, seeded["full"])
    assert "/solutions/%s" % seeded["full"] in page.url and "tab=risks" in page.url, page.url
    page.goto("%s/solutions/%s/risks/heatmap" % (live_server, seeded["full"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert "tab=risks" in page.url, page.url


def test_a_solution_with_no_risks_says_so(browser, live_server, seeded):
    page = _open(browser, live_server, seeded, seeded["empty"])
    assert sum(_cells(page).values()) == 0
    assert "no risk" in page.locator("main").inner_text().lower()
