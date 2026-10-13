"""AI Systems Register: a person with general permission assigns an owner with
the row's search picker, and the saved owner is shown after a reload."""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .test_authorisation_matrix import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_architect_assigns_ai_system_owner_with_picker(page, live_server, seeded):
    from app import create_app, db
    from app.models.user import User
    from app.modules.enterprise_genome.services.ai_systems_seed import register_ai_system

    app = create_app("testing")
    name = "Owner journey system %s" % uuid.uuid4().hex[:6]
    with app.app_context():
        owner = db.session.get(User, seeded["ids"]["business_architect_user"])
        owner_name = (owner.first_name or owner.email).strip()
        register_ai_system(
            db.session, seeded["ids"]["org"], name=name,
            provider="anthropic", model_id="claude-opus-5",
        )
        db.session.commit()

    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/genome/ai-systems", wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    row = page.locator("tr", has_text=name)
    row.locator("[data-owner-search]").fill(owner_name[:3])
    result = row.locator("[data-owner-results] button").first
    result.wait_for(timeout=PAGE_TIMEOUT)
    result.click()
    with page.expect_response(
        lambda r: r.request.method == "POST" and "/owners" in r.url
    ) as saved:
        row.locator("[data-owner-save]").click()
    assert saved.value.status == 201

    page.goto(live_server + "/genome/ai-systems", wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    assert owner_name in page.locator("tr", has_text=name).inner_text()
