"""An enterprise architect opens applications imported before owners were worked out and is told who owns them, or why not.

The person: an enterprise architect whose organisation connected GitHub and imported two repositories before
the owner was read from commits. The goals: after the connector's owner step runs, the application whose
commits one member leads shows that member as owner with where it came from; the application whose commits
GitHub refused to show says in one sentence why it has no owner; the applications list says the same, and
nothing is a bare "No owner". Both are still there after a reload.
"""

import os
import uuid
from unittest import mock

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _import(org, name, repository):
    """An application the connector imported before commits were read: it names its repository and holds no commit fact."""
    from app import create_app, db
    from app.connectors.github import stamp_origin
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import create_backbone_element

    with create_app("testing").app_context():
        element = create_backbone_element(
            element_type="ApplicationComponent", layer="Application", name=name, organization_id=org["org_id"])
        stamp_origin(element, repository=repository, commit="abc123def456", file="Dockerfile")
        row = ApplicationComponent(name=name, organization_id=org["org_id"], archimate_element_id=element.id,
                                   lifecycle_status="planning")
        db.session.add(row)
        db.session.commit()
        app_id = row.id
        db.session.remove()
    return app_id


@pytest.fixture(scope="module")
def estate(live_server):
    from app import create_app, db
    from app.models.connector_config import ConnectorConfig
    from app.models.user import User

    tag = uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect", extra_roles=("application_manager",))
    with create_app("testing").app_context():
        ada = db.session.get(User, org["user_ids"]["application_manager"])
        ada.first_name, ada.last_name = "Ada", "Obi"
        db.session.add(ConnectorConfig(organization_id=org["org_id"], connector_type="github", name="GitHub",
                                       status="active", config={"organization": "acme"}))
        db.session.commit()
        db.session.remove()
    led = _import(org, "Archie Ea %s" % tag, "acme/archie-ea-%s" % tag)
    refused = _import(org, "Billing Hub %s" % tag, "acme/billing-hub-%s" % tag)
    return {"org": org, "email": org["emails"]["enterprise_architect"], "ada_email": org["emails"]["application_manager"],
            "led": led, "refused": refused, "tag": tag}


@pytest.fixture()
def architect(browser, live_server, estate):
    narrow = os.environ.get("WALK_MODE") == "narrow-dark"
    context = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 390, "height": 900} if narrow else {"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, estate["email"])
    yield page
    context.close()


def _walk(page, name):
    folder = os.environ.get("WALK_DIR")
    if not folder:
        return
    os.makedirs(folder, exist_ok=True)
    if os.environ.get("WALK_MODE") == "narrow-dark":
        page.evaluate("() => document.documentElement.classList.add('dark')")
    page.screenshot(path=os.path.join(folder, "%s-%s.png" % (os.environ.get("WALK_MODE", "wide-light"), name)))


def _run_owner_step(estate):
    """The connector's owner step over the organisation's imported applications, with GitHub answering for one
    repository and refusing the other."""
    from types import SimpleNamespace

    from app import create_app
    from app.commands.owners_from_commits import run_for_organisation
    from app.connectors import github

    tag = estate["tag"]

    def history(self, full_name):
        if full_name.endswith("billing-hub-" + tag):
            raise github.GitHubConnectorError("GitHub refused the request (HTTP 403)")
        return github.summarise_commits(estate["org"]["org_id"], full_name, [
            {"sha": "%07xabc" % n, "commit": {"author": {"email": estate["ada_email"], "date": "2026-10-%02dT09:00:00Z" % (1 + n)}}}
            for n in range(6)])

    fake = SimpleNamespace(_client=lambda: None, _organization=lambda: "acme")
    with create_app("testing").app_context(), \
            mock.patch("app.services.connector_framework.build_connector", return_value=fake), \
            mock.patch.object(github._RepositoryEvidence, "_commit_history", history):
        return run_for_organisation(estate["org"]["org_id"])


def test_imported_applications_get_their_owner_or_the_reason_after_the_owner_step(architect, live_server, estate):
    page, tag = architect, estate["tag"]
    sfx = "-mobile" if os.environ.get("WALK_MODE") == "narrow-dark" else ""  # the narrow list draws its own cards
    page.goto("%s/applications/%s" % (live_server, estate["led"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("need-owner-reason")).to_contain_text("have not been read", timeout=PAGE_TIMEOUT)
    page.goto("%s/applications/" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("app-owner-reason%s-%s" % (sfx, estate["led"]))).to_contain_text("have not been read")
    _walk(page, "before-list")

    lines = _run_owner_step(estate)
    assert sorted(line["outcome"] for line in lines) == ["owner set", "reason recorded"], lines

    for _ in range(2):  # then again after a reload
        page.goto("%s/applications/%s" % (live_server, estate["led"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("application-owner")).to_contain_text("Ada Obi", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("owner-source")).to_contain_text("From commit history: 6 of 6 commits")
        expect(page.get_by_test_id("owner-source")).to_contain_text("acme/archie-ea-%s" % tag)
    _walk(page, "owner-with-source")

    reason = ("No owner was suggested: the commits of billing-hub-%s could not be read because "
              "the GitHub connection has no access to it." % tag)
    for _ in range(2):
        page.goto("%s/applications/%s" % (live_server, estate["refused"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("need-owner-reason")).to_have_text(reason, timeout=PAGE_TIMEOUT)
    _walk(page, "reason")

    page.goto("%s/applications/" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("app-owner-reason%s-%s" % (sfx, estate["refused"]))).to_have_text(reason)
    expect(page.get_by_test_id("app-owner%s-%s" % (sfx, estate["led"]))).to_have_text("Ada Obi")
    assert page.get_by_test_id("app-owner-reason%s-%s" % (sfx, estate["led"])).count() == 0
    _walk(page, "list")

    page.get_by_test_id("app-owner%s-%s" % (sfx, estate["refused"])).click()
    page.wait_for_load_state("domcontentloaded")
    assert "/applications/%s/edit" % estate["refused"] in page.url
    expect(page.locator("input[name='name']").first).to_have_value("Billing Hub %s" % tag, timeout=PAGE_TIMEOUT)
