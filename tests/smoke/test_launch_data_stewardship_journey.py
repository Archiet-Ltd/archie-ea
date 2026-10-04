"""A data architect raises a data issue, resolves it, and reaches the
glossary and retention-breaches pages.

Covers the four new data-governance templates introduced with R1-B81
(data_issues.html, new_data_issue.html, glossary.html,
retention_breaches.html), none of which had a browser smoke journey
before this.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in


def _entity_for(org_id):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.process_data import DataDomain, DataEntity

        domain = DataDomain(name="Smoke Domain %s" % org_id, organization_id=org_id)
        db.session.add(domain)
        db.session.flush()
        entity = DataEntity(name="Smoke Entity %s" % org_id, organization_id=org_id, domain_id=domain.id)
        db.session.add(entity)
        db.session.commit()
        entity_id = entity.id
        db.session.remove()
    return entity_id


def test_architect_raises_and_resolves_a_data_issue(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    entity_id = _entity_for(org["org_id"])
    title = "Smoke Issue %s" % org["suffix"]

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])

    page.goto(
        live_server + "/data-governance/issues/new?data_entity_id=%d" % entity_id,
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    page.fill("#title", title)
    page.get_by_role("button", name="Raise issue").click()
    page.wait_for_url(lambda url: url.endswith("/data-governance/issues"), timeout=PAGE_TIMEOUT)

    expect(page.get_by_text(title)).to_have_count(1)

    page.get_by_placeholder("Resolution notes").fill("Fixed in the smoke journey")
    page.get_by_role("button", name="Resolve").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("resolved")).to_have_count(1)

    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("resolved")).to_have_count(1)
    context.close()


def test_architect_reaches_glossary_and_retention_breaches(browser, live_server):
    org = create_fresh_org("enterprise_architect")

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])

    resp = page.goto(live_server + "/data-governance/glossary", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert resp.status == 200
    expect(page.get_by_role("heading", name="Glossary")).to_have_count(1)

    resp = page.goto(
        live_server + "/data-governance/retention-breaches", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    assert resp.status == 200
    expect(page.get_by_role("heading", name="Retention breaches")).to_have_count(1)
    context.close()
