"""A connector read joins the model only when a person approves it, and says where it came from.

An architect's organisation has read one GitHub repository. Nothing is in the
model until she opens the review page and clicks Approve all. Afterwards the
application's page says where it came from, an impact question about it cites
that source, a second organisation sees none of it, and reading the same
repository again proposes nothing new.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _new_page(browser):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    # A first sign-in shows the one-time welcome tour over every page.
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _repository(tag):
    return {
        "id": int(uuid.uuid4().int % 10**9),
        "name": "billing-%s" % tag,
        "full_name": "acme/billing-%s" % tag,
        "description": "Invoices and payments",
        "html_url": "https://github.com/acme/billing-%s" % tag,
        "language": "Python",
        "stargazers_count": 3,
        "forks_count": 1,
        "archived": False,
        "owner": {"login": "platform-%s" % tag, "type": "Organization"},
    }


def _read_repository(org_id, repository):
    """One GitHub read of the repository into the organisation; returns the facts it proposed."""
    from app import create_app, db
    from app.integrations.fact_store import record_facts
    from app.integrations.mappings.github import GitHubMapping

    app = create_app("testing")
    with app.app_context():
        proposed = record_facts(mapping=GitHubMapping(), item=repository, organization_id=org_id)
        count = len(proposed)
        db.session.remove()
    return count


def _model_facts(org_id, name):
    """(elements named *name*, integration facts on record) in the organisation, read straight from the database."""
    from app import create_app, db
    from app.models.integration_fact import IntegrationFact
    from app.models.models import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        elements = ArchiMateElement.query.filter_by(organization_id=org_id, name=name).all()
        facts = IntegrationFact.query.filter_by(organization_id=org_id).count()
        out = ([e.id for e in elements], facts)
        db.session.remove()
    return out


def _fact_read_at(org_id):
    from app import create_app, db
    from app.models.integration_fact import IntegrationFact

    app = create_app("testing")
    with app.app_context():
        read_at = IntegrationFact.query.filter_by(organization_id=org_id, path="element").one().source_read_at
        db.session.remove()
    return read_at


def _fact_ids(org_id):
    from app import create_app, db
    from app.models.integration_fact import IntegrationFact

    app = create_app("testing")
    with app.app_context():
        ids = [f.id for f in IntegrationFact.query.filter_by(organization_id=org_id)]
        db.session.remove()
    return ids


def _applied_links(org_id):
    """The links approved facts wrote, as each fact recorded them."""
    from app import create_app, db
    from app.models.integration_fact import IntegrationFact

    app = create_app("testing")
    with app.app_context():
        ids = {f.applied_relationship_id for f in IntegrationFact.query.filter_by(organization_id=org_id)
               if f.applied_relationship_id is not None}
        db.session.remove()
    return ids


def _forget_answers(org_id):
    """Remove the answers the journey stored; the shared test database outlives the run."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        db.session.execute(db.text("DELETE FROM answer_records WHERE organization_id = :org"), {"org": org_id})
        db.session.commit()
        db.session.remove()


def _opened(page, base, path):
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return response.status if response is not None else None


def test_a_github_read_joins_the_model_only_when_approved_and_cites_its_source(browser, live_server, request):
    base = live_server
    tag = uuid.uuid4().hex[:6]
    repository = _repository(tag)
    app_name, team_name = repository["name"], repository["owner"]["login"]

    org_a = create_fresh_org("solution_architect")
    org_b = create_fresh_org("solution_architect")
    request.addfinalizer(lambda: _forget_answers(org_a["org_id"]))
    architect = org_a["emails"]["solution_architect"]
    outsider = org_b["emails"]["solution_architect"]

    # The read proposes the repository and its owning team; the model holds neither yet.
    assert _read_repository(org_a["org_id"], repository) == 2
    assert _model_facts(org_a["org_id"], app_name)[0] == []
    assert _model_facts(org_a["org_id"], team_name)[0] == []

    context, page = _new_page(browser)
    sign_in(page, base, architect)
    assert _opened(page, base, "/integrations/review") == 200
    expect(page.get_by_role("heading", name="Integration review")).to_be_visible()
    expect(page.locator("body")).to_contain_text(app_name)
    expect(page.locator("[data-fact-id]")).to_have_count(2)
    expect(page.locator("body")).to_contain_text(team_name)

    # Approve all: the page reloads with nothing left to review.
    page.get_by_role("button", name="Approve all").click()
    expect(page.get_by_text("Nothing to review")).to_be_visible(timeout=PAGE_TIMEOUT)
    app_ids, _ = _model_facts(org_a["org_id"], app_name)
    team_ids, _ = _model_facts(org_a["org_id"], team_name)
    assert len(app_ids) == 1 and len(team_ids) == 1, "approving put the application and its team in the model"
    app_id = app_ids[0]

    # The application's page says where it came from, and the link is the repository.
    assert _opened(page, base, "/architecture/application/ApplicationComponent/%d" % app_id) == 200
    source = page.locator("[data-integration-source]")
    expect(source).to_be_visible()
    read_at = _fact_read_at(org_a["org_id"])
    read_on = "%d %s %d" % (read_at.day, read_at.strftime("%B"), read_at.year)
    expect(source).to_have_text(
        "From GitHub, %s, read %s" % (app_name, read_on), use_inner_text=True, timeout=PAGE_TIMEOUT
    )
    assert source.get_by_role("link", name=app_name).get_attribute("href") == repository["html_url"]

    # The team owns it: the team's own page names the same source.
    assert _opened(page, base, "/architecture/business/BusinessActor/%d" % team_ids[0]) == 200
    expect(page.locator("[data-integration-source]")).to_contain_text("From GitHub, %s" % app_name)

    # Ask what is affected by the application: the answer cites the repository it rests on.
    status, body = api(page, "POST", "/api/v1/intelligence/answers",
                       {"question": "impact", "element_id": app_id, "direction": "both"})
    assert status == 201, body
    answer = body["data"]
    assert answer["rows"], "the owning team is affected by the application"
    sourced = [c for c in answer["citations"] if c.get("source")]
    assert {c["type"] for c in sourced} == {"relationship", "element"}
    # The link is cited by the fact that wrote it: the team's assignment the approval made.
    assert {c["id"] for c in sourced if c["type"] == "relationship"} == _applied_links(org_a["org_id"])
    for citation in sourced:
        assert citation["source"]["system"] == "github"
        assert citation["source"]["item"] == app_name
        assert citation["source"]["url"] == repository["html_url"]
        assert citation["source"]["read_at"]
    status, stored = api(page, "GET", answer["api_url"])
    assert status == 200 and stored["data"]["citations"] == answer["citations"]

    # What the team's failure affects names the team as its owner, as a person owner would be named.
    status, body = api(page, "POST", "/api/v1/intelligence/answers",
                       {"question": "impact", "element_id": team_ids[0], "direction": "downstream"})
    assert status == 201, body
    affected = [row for row in body["data"]["rows"] if row["element_id"] == app_id]
    assert affected and team_name in [o["name"] for o in affected[0]["owners"]]

    # A second organisation sees none of it.
    other_context, other = _new_page(browser)
    sign_in(other, base, outsider)
    assert _opened(other, base, "/integrations/review") == 200
    expect(other.get_by_text("Nothing to review")).to_be_visible()
    assert _opened(other, base, "/architecture/application/ApplicationComponent/%d" % app_id) == 404
    status, refused = api(other, "POST", "/api/v1/intelligence/answers",
                          {"question": "impact", "element_id": app_id, "direction": "both"})
    assert status == 404, refused
    status, result = api(other, "POST", "/integrations/approve", {"fact_ids": _fact_ids(org_a["org_id"])})
    assert result["approved"] == [] and status in (200, 400)
    assert _model_facts(org_b["org_id"], app_name) == ([], 0)

    # Reading the same repository again proposes nothing new, and the review stays empty.
    assert _read_repository(org_a["org_id"], repository) == 0
    assert _model_facts(org_a["org_id"], app_name)[1] == 2
    assert _opened(page, base, "/integrations/review") == 200
    expect(page.get_by_text("Nothing to review")).to_be_visible()
    assert len(_model_facts(org_a["org_id"], app_name)[0]) == 1

    other_context.close()
    context.close()


def test_rejecting_a_github_read_keeps_it_out_of_the_model(browser, live_server):
    base = live_server
    tag = uuid.uuid4().hex[:6]
    repository = _repository(tag)
    org = create_fresh_org("solution_architect")

    assert _read_repository(org["org_id"], repository) == 2
    context, page = _new_page(browser)
    sign_in(page, base, org["emails"]["solution_architect"])
    assert _opened(page, base, "/integrations/review") == 200
    page.get_by_placeholder("Reason for rejecting (optional)").fill("Not one of ours")
    page.get_by_role("button", name="Reject all").click()
    expect(page.get_by_text("Nothing to review")).to_be_visible(timeout=PAGE_TIMEOUT)

    assert _model_facts(org["org_id"], repository["name"])[0] == []
    # A rejected read is not proposed again.
    assert _read_repository(org["org_id"], repository) == 0
    context.close()
