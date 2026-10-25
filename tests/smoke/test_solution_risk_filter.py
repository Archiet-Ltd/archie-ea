"""Journey, in a browser: a solution's risks on the one risk register (Slice S3, part 1).

A risk owner opens "Field Calibration Platform"'s risks and sees exactly its own six risks
on the register -- not the whole organisation's. The old per-solution address still works,
landing on the same filtered view. Built on a fresh organisation (never the shared demo
seed, which has an unrelated pre-existing seeding defect on this branch) so this journey's
own setup cannot be blamed for a product bug or vice versa.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SOLUTION_NAME = "Field Calibration Platform"
RISK_COUNT = 6


@pytest.fixture(scope="module")
def risk_org():
    org = create_fresh_org("risk", platform_role="Architect")
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.risk import Risk, RiskStatus
        from app.models.solution_models import Solution

        solution = Solution(name=SOLUTION_NAME, organization_id=org["org_id"])
        db.session.add(solution)
        db.session.flush()
        for i in range(RISK_COUNT):
            db.session.add(
                Risk(
                    title="%s risk %d" % (SOLUTION_NAME, i + 1),
                    description="Seeded for the solution risk filter journey.",
                    likelihood=((i % 5) + 1),
                    impact=(((i + 2) % 5) + 1),
                    status=RiskStatus.OPEN,
                    organization_id=org["org_id"],
                    solution_id=solution.id,
                )
            )
        # A risk outside the solution, in the same organisation, that the filter must exclude.
        db.session.add(
            Risk(
                title="Unrelated organisation-wide risk",
                description="Must not appear once filtered to the solution.",
                likelihood=3,
                impact=3,
                status=RiskStatus.OPEN,
                organization_id=org["org_id"],
                solution_id=None,
            )
        )
        db.session.commit()
        org["solution_id"] = solution.id
    return org


def _row_ids(page):
    return page.eval_on_selector_all(
        "[data-risk-row]",
        "els => els.map(e => e.getAttribute('data-risk-row'))",
    )


def test_risk_owner_sees_exactly_the_solutions_risks_on_the_register(live_server, risk_org, page):
    email = risk_org["emails"]["risk"]
    sign_in(page, live_server, email)

    page.goto(
        live_server + "/risks/?solution=%d" % risk_org["solution_id"],
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )
    expect(page.locator("body")).to_contain_text(SOLUTION_NAME, timeout=PAGE_TIMEOUT)

    row_ids = _row_ids(page)
    assert len(row_ids) == RISK_COUNT, (
        "filtered register must show exactly the solution's %d risks, found %d: %r"
        % (RISK_COUNT, len(row_ids), row_ids)
    )


def test_old_solution_risks_address_redirects_to_the_same_filtered_view(live_server, risk_org, page):
    email = risk_org["emails"]["risk"]
    sign_in(page, live_server, email)

    page.goto(
        live_server + "/solutions/%d/risks" % risk_org["solution_id"],
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )
    assert "/risks/" in page.url and ("solution=%d" % risk_org["solution_id"]) in page.url, (
        "the old address must redirect into the filtered register, landed on %r" % page.url
    )
    row_ids = _row_ids(page)
    assert len(row_ids) == RISK_COUNT, (
        "the redirected page must show the same filtered content, not an empty or wrong page"
    )
