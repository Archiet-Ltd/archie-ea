"""An enterprise architect opens a GitHub-imported application with no owner and is told why, then picks one.

The person: an enterprise architect whose organisation imported a repository as an application. Nobody is
named as code owner, and two members made the recent commits in equal measure. The goals: the application
page says in one sentence why no owner was set and offers both people; one click on Make owner records that
person as the primary owner, the page shows who it is and where that came from, and the owner is still there
after a reload. A second application whose committers are strangers says that instead and offers nobody. A
committer who belongs only to another organisation is never offered.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _commits(*pairs, login=None):
    out = []
    for email, count in pairs:
        for _ in range(count):
            n = len(out)
            item = {"sha": "%07xabc" % n, "commit": {"author": {"email": email, "date": "2026-10-%02dT09:00:00Z" % (1 + n % 27)}}}
            if login:
                item["author"] = {"login": login}
            out.append(item)
    return out


def _import(org, name, repository, commits, summary=None, owners=()):
    from app import create_app, db
    from app.connectors import github
    from app.models.application_portfolio import ApplicationComponent

    with create_app("testing").app_context():
        row = ApplicationComponent(name=name, organization_id=org["org_id"], lifecycle_status="planning")
        db.session.add(row)
        db.session.commit()
        if summary is None:
            summary = github.summarise_commits(org["org_id"], repository, commits)
        github._finish_application(
            org["org_id"], row.id,
            {"repository": repository, "commit": "abc123def456", "evidence_file": "Dockerfile",
             "owners": list(owners), "commit_owners": summary},
            org["user_ids"]["enterprise_architect"])
        db.session.commit()
        app_id = row.id
        db.session.remove()
    return app_id


@pytest.fixture(scope="module")
def estate(live_server):
    from app import create_app, db
    from app.connectors import github
    from app.models.user import User

    tag = uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect", extra_roles=("application_manager", "solution_architect"))
    other = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        names = {}
        for role, first, last in (("application_manager", "Ada", "Obi"), ("solution_architect", "Sam", "Lee")):
            user = db.session.get(User, org["user_ids"][role])
            user.first_name, user.last_name = first, last
            names[role] = "%s %s" % (first, last)
        db.session.commit()
        db.session.remove()
    stranger = "bea.roe.%s@example.org" % tag
    with create_app("testing").app_context():
        outsider = db.session.get(User, other["user_ids"]["enterprise_architect"])
        outsider.email = stranger
        db.session.commit()
        db.session.remove()
    ada, sam = org["emails"]["application_manager"], org["emails"]["solution_architect"]
    shared = _import(org, "Archie Ea %s" % tag, "acme/archie-ea-%s" % tag,
                     _commits((ada, 10), (sam, 10), (stranger, 30)))
    nobody = _import(org, "Billing Hub %s" % tag, "acme/billing-hub-%s" % tag, _commits(("dev@vendor.example", 8)))
    leader = _import(org, "Ledger Core %s" % tag, "acme/ledger-core-%s" % tag, _commits((ada, 15), (sam, 5)))
    busy_repo = "acme/monorepo-%s" % tag
    busy = _import(org, "Monorepo %s" % tag, busy_repo, [], summary={
        "repository": busy_repo, "days": 90, "oldest": None, "newest": None, "member_commits": 0, "candidates": [],
        "unavailable": "No owner was suggested: monorepo-%s has too many commits in the last 90 days to name one person from them." % tag})
    stale = _import(org, "Pay Gateway %s" % tag, "acme/pay-gateway-%s" % tag, _commits((ada, 10), (sam, 10)))
    handle_repo = "acme/account-%s" % tag
    by_account = _import(org, "Account Hub %s" % tag, handle_repo, _commits((ada, 6), ("dev@vendor.example", 2), login="anioko")
                         + _commits((sam, 4), login="sam-l"), owners=["@Anioko"])
    no_account = _import(org, "Quiet Hub %s" % tag, "acme/quiet-%s" % tag,
                         _commits((stranger, 5), login="anioko"), owners=["@Anioko"])
    closed_repo = "acme/closed-%s" % tag
    closed = _import(org, "Closed Hub %s" % tag, closed_repo, [],
                     summary=github._unreadable_summary(closed_repo, "HTTP 403"), owners=["@Anioko"])
    team = _import(org, "Team Hub %s" % tag, "acme/team-%s" % tag, _commits((stranger, 5), login="platform"),
                   owners=["@acme/platform"])
    return {"closed": closed, "team": team, "by_account": by_account, "no_account": no_account, "handle_repo": handle_repo, "busy": busy, "stale": stale, "stale_repo": "acme/pay-gateway-%s" % tag,
            "email": org["emails"]["enterprise_architect"], "shared": shared, "nobody": nobody,
            "leader": leader, "leader_repo": "acme/ledger-core-%s" % tag, "tag": tag, "names": names, "repo": "acme/archie-ea-%s" % tag}


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


def test_two_members_share_the_commits_and_the_architect_picks_one(architect, live_server, estate):
    ada, sam = estate["names"]["application_manager"], estate["names"]["solution_architect"]
    architect.goto("%s/applications/%s" % (live_server, estate["shared"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    reason = architect.get_by_test_id("need-owner-reason")
    expect(reason).to_be_visible(timeout=PAGE_TIMEOUT)
    assert reason.inner_text().strip() == "Recent commits are shared: %s (10), %s (10)." % (ada, sam), reason.inner_text()
    expect(architect.get_by_test_id("owner-reason")).to_have_text(reason.inner_text().strip())
    buttons = architect.get_by_test_id("owner-candidates").get_by_role("button", name="Make owner")
    assert buttons.count() == 2
    assert "Bea" not in architect.locator("main").inner_text()
    _walk(architect, "two-candidates")

    architect.get_by_role("button", name="Make owner: %s" % sam).click()
    owner = architect.get_by_test_id("application-owner")
    expect(owner).to_contain_text(sam, timeout=PAGE_TIMEOUT)
    source = architect.get_by_test_id("owner-source")
    expect(source).to_contain_text("From commit history: 10 of 20 commits in the last 90 days")
    expect(source).to_contain_text(estate["repo"])
    assert architect.get_by_test_id("need-owner-reason").count() == 0
    _walk(architect, "owner-recorded")

    architect.reload(wait_until="domcontentloaded")
    expect(architect.get_by_test_id("application-owner")).to_contain_text(sam, timeout=PAGE_TIMEOUT)
    expect(architect.get_by_test_id("owner-source")).to_contain_text("10 of 20 commits")
    assert ada not in architect.get_by_test_id("application-owner").inner_text()


def test_no_committer_is_a_member_and_the_page_says_so(architect, live_server, estate):
    architect.goto("%s/applications/%s" % (live_server, estate["nobody"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    reason = architect.get_by_test_id("need-owner-reason")
    expect(reason).to_have_text(
        "No code owner or recent committer of billing-hub-%s is a member of this organisation." % estate["tag"],
        timeout=PAGE_TIMEOUT)
    assert architect.get_by_test_id("owner-candidates").count() == 0
    expect(architect.get_by_test_id("owner-reason")).to_be_visible()
    _walk(architect, "no-candidates")


def test_one_clear_leader_is_the_owner_and_the_page_says_where_that_came_from(architect, live_server, estate):
    ada = estate["names"]["application_manager"]
    architect.goto("%s/applications/%s" % (live_server, estate["leader"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    owner = architect.get_by_test_id("application-owner")
    expect(owner).to_contain_text(ada, timeout=PAGE_TIMEOUT)
    source = architect.get_by_test_id("owner-source")
    expect(source).to_contain_text("From commit history: 15 of 20 commits in the last 90 days")
    expect(source).to_contain_text(estate["leader_repo"])
    assert architect.get_by_test_id("need-owner-reason").count() == 0
    assert architect.get_by_test_id("owner-candidates").count() == 0
    owner.scroll_into_view_if_needed()
    _walk(architect, "owner-set-automatically")
    architect.reload(wait_until="domcontentloaded")
    expect(architect.get_by_test_id("application-owner")).to_contain_text(ada, timeout=PAGE_TIMEOUT)
    expect(architect.get_by_test_id("owner-source")).to_contain_text("15 of 20 commits")


def test_a_repository_with_too_many_commits_keeps_its_application_and_says_why_no_owner_was_named(
        architect, live_server, estate):
    reason = ("No owner was suggested: monorepo-%s has too many commits in the last 90 days "
              "to name one person from them." % estate["tag"])
    architect.goto("%s/applications/%s" % (live_server, estate["busy"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(architect.get_by_test_id("need-owner-reason")).to_have_text(reason, timeout=PAGE_TIMEOUT)
    assert architect.get_by_test_id("owner-candidates").count() == 0
    expect(architect.locator("main")).to_contain_text("Monorepo %s" % estate["tag"])
    _walk(architect, "too-many-commits")
    architect.reload(wait_until="domcontentloaded")
    expect(architect.get_by_test_id("need-owner-reason")).to_have_text(reason, timeout=PAGE_TIMEOUT)


def test_pressing_make_owner_after_someone_else_became_owner_changes_nothing_and_says_so(
        architect, live_server, estate):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.architecture_assistant.property_service import PropertyService
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User
    from app.services.element_ownership_service import set_application_owner

    ada, sam = estate["names"]["application_manager"], estate["names"]["solution_architect"]
    architect.goto("%s/applications/%s" % (live_server, estate["stale"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(architect.get_by_test_id("owner-candidates")).to_be_visible(timeout=PAGE_TIMEOUT)
    # While this page is open, a colleague makes Sam the primary owner, with a recorded source.
    sentence = "From commit history: 10 of 20 commits in the last 90 days (%s)" % estate["stale_repo"]
    with create_app("testing").app_context():
        component = db.session.get(ApplicationComponent, estate["stale"])
        sam_user = User.query.filter(User.first_name == "Sam", User.organization_id == component.organization_id).first()
        set_application_owner(component, sam_user.id, "primary", organization_id=component.organization_id)
        element = db.session.get(ArchiMateElement, component.archimate_element_id)
        PropertyService().merge_element_properties(
            element, {"github_owner_source": {"user_id": sam_user.id, "text": sentence}}, source="github")
        db.session.commit()
        db.session.remove()
    architect.get_by_role("button", name="Make owner: %s" % ada).click()
    notice = architect.get_by_text("This application already has a primary owner, %s. Nothing was changed." % sam)
    expect(notice).to_be_visible(timeout=PAGE_TIMEOUT)
    owner = architect.get_by_test_id("application-owner")
    expect(owner).to_contain_text(sam)
    assert ada not in owner.inner_text()
    expect(architect.get_by_test_id("owner-source")).to_contain_text(sentence)
    _walk(architect, "already-has-owner")
    architect.reload(wait_until="domcontentloaded")
    expect(architect.get_by_test_id("application-owner")).to_contain_text(sam, timeout=PAGE_TIMEOUT)
    assert ada not in architect.get_by_test_id("application-owner").inner_text()
    expect(architect.get_by_test_id("owner-source")).to_contain_text(sentence)


def test_a_code_owner_account_is_the_member_its_commits_show_on_the_page_and_the_list(architect, live_server, estate):
    ada = estate["names"]["application_manager"]
    architect.goto("%s/applications/%s" % (live_server, estate["by_account"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    owner = architect.get_by_test_id("application-owner")
    expect(owner).to_contain_text(ada, timeout=PAGE_TIMEOUT)
    source = architect.get_by_test_id("owner-source")
    expect(source).to_contain_text("GitHub names @Anioko as code owner; 6 of that account's 8 commits")
    expect(source).to_contain_text(estate["handle_repo"])
    assert architect.get_by_test_id("need-owner-reason").count() == 0
    owner.scroll_into_view_if_needed()
    _walk(architect, "account-is-owner")
    architect.reload(wait_until="domcontentloaded")
    expect(architect.get_by_test_id("application-owner")).to_contain_text(ada, timeout=PAGE_TIMEOUT)
    architect.goto("%s/applications/" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    cell = architect.locator('[data-testid="app-owner-%s"], [data-testid="app-owner-mobile-%s"]' % (
        estate["by_account"], estate["by_account"])).locator("visible=true").first
    expect(cell).to_contain_text(ada, timeout=PAGE_TIMEOUT)
    cell.scroll_into_view_if_needed()
    _walk(architect, "account-is-owner-list")
    footer_text = architect.locator("body").evaluate(
        "(b, id) => Array.from(b.querySelectorAll('[data-footer-for=\"' + id + '\"]')).map(e => e.textContent).join(' ')",
        str(estate["by_account"]))
    assert "GitHub commit history" in footer_text, footer_text
    assert "Owner was entered by hand" not in footer_text and "entered by the team" not in footer_text, footer_text


@pytest.mark.parametrize("key,start", [
    ("closed", "No owner was set: GitHub names @Anioko as code owner"),
    ("team", "No owner was set: GitHub names @acme/platform as code owner"),
])
def test_a_named_owner_with_nobody_to_pick_gives_a_reason_and_no_prompt(architect, live_server, estate, key, start):
    architect.goto("%s/applications/%s" % (live_server, estate[key]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    reason = architect.get_by_test_id("need-owner-reason")
    expect(reason).to_contain_text(start, timeout=PAGE_TIMEOUT)
    assert "Pick the person" not in reason.inner_text()
    reason.scroll_into_view_if_needed()
    _walk(architect, "nobody-to-pick-%s" % key)
    architect.goto("%s/applications/" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row_reason = architect.locator('[data-testid="app-owner-reason-%s"], [data-testid="app-owner-reason-mobile-%s"]' % (
        estate[key], estate[key])).locator("visible=true").first
    expect(row_reason).to_contain_text(start, timeout=PAGE_TIMEOUT)
    assert "Pick the person" not in row_reason.inner_text()


def test_a_code_owner_account_with_no_member_email_gives_the_reason_on_the_page_and_the_list(architect, live_server, estate):
    reason = ("No owner was set: GitHub names @Anioko as code owner, and none of that account's commits "
              "in the last 90 days use the e-mail of a member of this organisation.")
    architect.goto("%s/applications/%s" % (live_server, estate["no_account"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(architect.get_by_test_id("need-owner-reason")).to_have_text(reason, timeout=PAGE_TIMEOUT)
    architect.get_by_test_id("need-owner-reason").scroll_into_view_if_needed()
    _walk(architect, "account-no-evidence")
    architect.reload(wait_until="domcontentloaded")
    expect(architect.get_by_test_id("need-owner-reason")).to_have_text(reason, timeout=PAGE_TIMEOUT)
    architect.goto("%s/applications/" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row_reason = architect.locator('[data-testid="app-owner-reason-%s"], [data-testid="app-owner-reason-mobile-%s"]' % (
        estate["no_account"], estate["no_account"])).locator("visible=true").first
    expect(row_reason).to_have_text(reason, timeout=PAGE_TIMEOUT)
    row_reason.scroll_into_view_if_needed()
    _walk(architect, "account-no-evidence-list")
