"""A chief technology officer whose startup just connected GitHub sees who owns each imported application.

The person: the only member of a fresh organisation, which connected GitHub and imported three repositories.
The goals: the application whose CODEOWNERS file names one account shows that account as owner with where it
came from; the application whose commits one account leads shows that account with the commit count; the
application with neither says why it has no owner; the applications list and the ownership coverage page
count only that third application as having no owner. Neither named account is a member of the organisation.
"""

import os
import re
import uuid
from unittest import mock

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _import(org, name, repository, code_owners=None):
    """An application the connector imported: it names its repository and the commit it was read at."""
    from app import create_app, db
    from app.connectors import github
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import create_backbone_element

    with create_app("testing").app_context():
        element = create_backbone_element(
            element_type="ApplicationComponent", layer="Application", name=name, organization_id=org["org_id"])
        github.stamp_origin(element, repository=repository, commit="abc123def456", file="Dockerfile")
        if code_owners:
            github._write_facts(element, {"github_code_owners": code_owners})
        row = ApplicationComponent(name=name, organization_id=org["org_id"], archimate_element_id=element.id,
                                   lifecycle_status="planning", component_type="application")
        db.session.add(row)
        db.session.commit()
        app_id = row.id
        db.session.remove()
    return app_id


def _commits(*triples):
    out = []
    for login, count in triples:
        for _ in range(count):
            n = len(out)
            out.append({"sha": "%07xabc" % n, "author": {"login": login},
                        "commit": {"author": {"email": "%s@users.example.org" % login,
                                              "date": "2026-10-%02dT09:00:00Z" % (1 + n % 27)}}})
    return out


@pytest.fixture(scope="module")
def estate(live_server):
    from app import create_app, db
    from app.models.connector_config import ConnectorConfig

    tag = uuid.uuid4().hex[:6]
    org = create_fresh_org("cto")
    with create_app("testing").app_context():
        db.session.add(ConnectorConfig(organization_id=org["org_id"], connector_type="github", name="GitHub",
                                       status="active", config={"organization": "acme"}))
        db.session.commit()
        db.session.remove()
    coded = _import(org, "Ledger %s" % tag, "acme/ledger-%s" % tag, ["@Anioko"])
    led = _import(org, "Archie Ea %s" % tag, "acme/archie-ea-%s" % tag)
    neither = _import(org, "Billing Hub %s" % tag, "acme/billing-hub-%s" % tag)
    return {"org": org, "email": org["emails"]["cto"], "coded": coded, "led": led,
            "neither": neither, "tag": tag}


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
    owner = page.get_by_test_id("application-owner")
    if owner.count():
        owner.first.scroll_into_view_if_needed()
    page.screenshot(path=os.path.join(folder, "%s-%s.png" % (os.environ.get("WALK_MODE", "wide-light"), name)))


def _run_owner_step(estate):
    from types import SimpleNamespace

    from app import create_app
    from app.commands.owners_from_commits import run_for_organisation
    from app.connectors import github

    tag, org_id = estate["tag"], estate["org"]["org_id"]
    by_repo = {
        "acme/ledger-" + tag: _commits(("Anioko", 3), ("zed", 3)),
        "acme/archie-ea-" + tag: _commits(("Bea-Roe", 8), ("zed", 2)),
        "acme/billing-hub-" + tag: _commits(("one", 3), ("two", 3)),
    }

    def history(self, full_name):
        return github.summarise_commits(org_id, full_name, by_repo[full_name])

    fake = SimpleNamespace(_client=lambda: None, _organization=lambda: "acme")
    with create_app("testing").app_context(), \
            mock.patch("app.services.connector_framework.build_connector", return_value=fake), \
            mock.patch.object(github._RepositoryEvidence, "_commit_history", history):
        return run_for_organisation(org_id)


def test_imported_applications_show_the_owner_github_names_and_only_the_third_has_none(architect, live_server, estate):
    page, tag = architect, estate["tag"]
    sfx = "-mobile" if os.environ.get("WALK_MODE") == "narrow-dark" else ""

    lines = _run_owner_step(estate)
    assert sorted(line["outcome"] for line in lines) == ["owner set", "owner set", "reason recorded"], lines
    again = _run_owner_step(estate)
    assert sorted(line["outcome"] for line in again) == ["already owned", "already owned", "reason recorded"] \
        or sorted(line["outcome"] for line in again) == ["already owned", "already owned", "unchanged"], again

    for _ in range(2):  # and again after a reload
        page.goto("%s/applications/%s" % (live_server, estate["coded"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("application-owner")).to_contain_text("@Anioko", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("owner-source")).to_have_text(
            "Named by GitHub: code owner in acme/ledger-%s CODEOWNERS at abc123d" % tag)
    _walk(page, "code-owner")

    for _ in range(2):
        page.goto("%s/applications/%s" % (live_server, estate["led"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("application-owner")).to_contain_text("@Bea-Roe", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("owner-source")).to_have_text(
            "Named by GitHub: leads the recent commits of acme/archie-ea-%s (8 of 10)" % tag)
    _walk(page, "commit-leader")

    page.goto("%s/applications/%s" % (live_server, estate["neither"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("main").get_by_text("No owner recorded").first).to_be_visible(timeout=PAGE_TIMEOUT)
    reason = page.get_by_test_id("need-owner-reason")
    expect(reason).to_have_text(
        "GitHub names no single code owner of billing-hub-%s, and no one author leads its commits in the last 90 days." % tag,
        timeout=PAGE_TIMEOUT)
    _walk(page, "neither")

    page.goto("%s/applications/" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("dq-no-owner")).to_have_text("1 of 3 have no owner", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("app-owner%s-%s" % (sfx, estate["coded"]))).to_have_text("@Anioko")
    expect(page.get_by_test_id("app-owner%s-%s" % (sfx, estate["led"]))).to_have_text("@Bea-Roe")
    _walk(page, "list")

    page.goto("%s/applications/ownership-coverage" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    main = page.locator("main")
    total = int(main.get_by_text("Total applications").locator("xpath=following-sibling::p[1]").inner_text())
    owned = int(main.get_by_text("With named owner").locator("xpath=following-sibling::p[1]").inner_text())
    assert (total, owned) == (3, 2)
    _walk(page, "coverage")

    page.goto("%s/applications/?no_owner=true" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert re.search(r"Billing Hub %s" % tag, page.locator("main").inner_text())
    assert "Archie Ea %s" % tag not in page.locator("main").inner_text()
    assert "Ledger %s" % tag not in page.locator("main").inner_text()


def test_a_team_beside_a_member_person_in_code_owners_shows_no_owner_from_codeowners(browser, live_server):
    """CODEOWNERS names a team and a person who is a member of the organisation: no single code owner
    is named, and the page says there is no owner rather than showing that person."""
    from types import SimpleNamespace

    from app import create_app, db
    from app.commands.owners_from_commits import run_for_organisation
    from app.connectors import github
    from app.models.connector_config import ConnectorConfig

    tag = uuid.uuid4().hex[:6]
    org = create_fresh_org("cto")
    email = org["emails"]["cto"]
    with create_app("testing").app_context():
        db.session.add(ConnectorConfig(organization_id=org["org_id"], connector_type="github", name="GitHub",
                                       status="active", config={"organization": "acme"}))
        db.session.commit()
        db.session.remove()
    repository = "acme/teamed-%s" % tag
    app_id = _import(org, "Teamed %s" % tag, repository, ["@acme/platform", "@ada-roe"])
    commits = []
    for login, address, count in (("ada-roe", email, 3), ("zed", "zed@users.example.org", 3)):
        for _ in range(count):
            n = len(commits)
            commits.append({"sha": "%07xabc" % n, "author": {"login": login},
                            "commit": {"author": {"email": address, "date": "2026-10-%02dT09:00:00Z" % (1 + n % 27)}}})

    def history(self, full_name):
        return github.summarise_commits(org["org_id"], full_name, commits)

    fake = SimpleNamespace(_client=lambda: None, _organization=lambda: "acme")
    with create_app("testing").app_context(), \
            mock.patch("app.services.connector_framework.build_connector", return_value=fake), \
            mock.patch.object(github._RepositoryEvidence, "_commit_history", history):
        run_for_organisation(org["org_id"])

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, email)
        page.goto("%s/applications/%s" % (live_server, app_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("main").get_by_text("No owner recorded").first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("application-owner")).not_to_contain_text("Named by GitHub: code owner")
        expect(page.get_by_test_id("application-owner")).not_to_contain_text("ada-roe")
        reason = page.get_by_test_id("need-owner-reason")
        expect(reason).to_contain_text("GitHub names no single code owner", timeout=PAGE_TIMEOUT)
    finally:
        context.close()
