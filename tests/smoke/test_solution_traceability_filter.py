"""Journey, in a browser: a solution's traceability matrix (Slice S3, part 2).

An enterprise architect opens the traceability matrix filtered to one solution and sees
only the applications that solution carries -- not every application in the matrix.
Built on a fresh organisation with one application the solution carries and one it does
not, so the filter's exclusion is proven, not assumed.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SOLUTION_NAME = "Field Calibration Platform"
IN_SCOPE_APP = "Calibration Ledger Service"
OUT_OF_SCOPE_APP = "Unrelated Payroll System"


@pytest.fixture(scope="module")
def trace_org():
    org = create_fresh_org("enterprise_architect", platform_role="Architect")
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent
        from app.models.archimate_core import ArchiMateElement
        from app.models.solution_models import Solution, solution_applications

        solution = Solution(name=SOLUTION_NAME, organization_id=org["org_id"])
        db.session.add(solution)
        db.session.flush()

        in_scope_app = ApplicationComponent(name=IN_SCOPE_APP, organization_id=org["org_id"])
        out_scope_app = ApplicationComponent(name=OUT_OF_SCOPE_APP, organization_id=org["org_id"])
        db.session.add_all([in_scope_app, out_scope_app])
        db.session.flush()

        for app_el_name in (IN_SCOPE_APP, OUT_OF_SCOPE_APP):
            db.session.add(
                ArchiMateElement(
                    name=app_el_name,
                    type="ApplicationComponent",
                    layer="Application",
                    organization_id=org["org_id"],
                )
            )
        db.session.flush()

        db.session.execute(
            solution_applications.insert().values(
                solution_id=solution.id,
                application_component_id=in_scope_app.id,
                role="primary",
            )
        )
        db.session.commit()
        org["solution_id"] = solution.id
    return org


def _matrix_app_names(page):
    return page.eval_on_selector_all(
        "[data-testid='matrix-row'] [data-testid='matrix-app-name'], tbody tr td:first-child",
        "els => els.map(e => e.textContent.trim())",
    )


def test_architect_sees_only_the_solutions_applications_on_the_matrix(live_server, trace_org, page):
    email = trace_org["emails"]["enterprise_architect"]
    sign_in(page, live_server, email)

    page.goto(
        live_server + "/architecture/traceability?solution=%d" % trace_org["solution_id"],
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )
    expect(page.locator("body")).to_contain_text(IN_SCOPE_APP, timeout=PAGE_TIMEOUT)
    body_text = page.locator("body").inner_text()
    assert OUT_OF_SCOPE_APP not in body_text, (
        "the filtered matrix must not show an application the solution does not carry"
    )
