"""Journey, in a browser: the old solution migration-roadmap address (Slice S3, part 3).

The one roadmap already reads ?solution= and ?programme= (built earlier on this branch);
this proves the one old address this slice adds -- /solutions/<id>/migration-roadmap --
redirects into it with real content, not an empty or wrong page.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SOLUTION_NAME = "Field Calibration Platform"


@pytest.fixture(scope="module")
def roadmap_org():
    org = create_fresh_org("enterprise_architect", platform_role="Architect")
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.solution_models import Solution

        solution = Solution(name=SOLUTION_NAME, organization_id=org["org_id"])
        db.session.add(solution)
        db.session.commit()
        org["solution_id"] = solution.id
    return org


def test_old_migration_roadmap_address_redirects_to_the_one_roadmap(live_server, roadmap_org, page):
    email = roadmap_org["emails"]["enterprise_architect"]
    sign_in(page, live_server, email)

    page.goto(
        live_server + "/solutions/%d/migration-roadmap" % roadmap_org["solution_id"],
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )
    assert "/roadmap" in page.url and ("solution=%d" % roadmap_org["solution_id"]) in page.url, (
        "the old address must redirect into the one roadmap, filtered to the solution, landed on %r"
        % page.url
    )
    expect(page.locator("body")).to_contain_text(SOLUTION_NAME, timeout=PAGE_TIMEOUT)
