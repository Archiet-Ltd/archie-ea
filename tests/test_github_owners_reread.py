"""An application imported before owners were worked out from commits gets its owner, or the plain reason it has none,
on the connector's next read and through one command, without being imported again."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

pytestmark = pytest.mark.usefixtures("db_session")

API = "https://api.github.com/"
REPOSITORY = "acme/archie-ea"
TOKEN = "ghp_reread-token-for-tests-9931"


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.headers = {"X-RateLimit-Remaining": "4990"}
        self.reason = "OK" if status == 200 else "Status"
        self.text = json.dumps(body)
        self.is_redirect = False

    def json(self):
        return self._body


@pytest.fixture
def github(app, monkeypatch):
    """GitHub as one organisation 'acme' with one repository; ``state.commits`` is its history and
    ``state.commit_status`` the answer to reading it."""
    import requests

    stamp = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = SimpleNamespace(commits=[], commit_status=200, calls=[], codeowners=None)
    repo = {"name": "archie-ea", "full_name": REPOSITORY, "archived": False, "pushed_at": stamp,
            "default_branch": "main", "language": "Python"}
    monkeypatch.setitem(app.config, "ALLOW_EXTERNAL_API_CALLS", True)

    def fake_request(self, method=None, url=None, params=None, headers=None, **kw):
        path = url[len(API):].strip("/")
        params = dict(params or {})
        state.calls.append(path)
        if path == "users/acme":
            return _Resp(200, {"type": "Organization", "login": "acme"})
        if path == "orgs/acme/repos":
            return _Resp(200, [repo] if params.get("page", 1) == 1 else [])
        if path == f"repos/{REPOSITORY}/contents":
            return _Resp(200, [{"name": ".github", "type": "dir"}] if state.codeowners else [])
        if path == f"repos/{REPOSITORY}/contents/.github/CODEOWNERS" and state.codeowners:
            import base64
            return _Resp(200, {"encoding": "base64", "content": base64.b64encode(state.codeowners.encode()).decode()})
        if path == f"repos/{REPOSITORY}/commits":
            if state.commit_status != 200:
                return _Resp(state.commit_status, {"message": "refused"})
            page, size = params.get("page", 1), params["per_page"]
            return _Resp(200, state.commits[(page - 1) * size:page * size])
        return _Resp(404, {"message": "Not Found"})

    monkeypatch.setattr(requests.Session, "request", fake_request)
    return state


def commit_list(*pairs):
    out = []
    for email, count in pairs:
        for _ in range(count):
            when = datetime.utcnow() - timedelta(days=1, hours=len(out))
            out.append({"sha": f"{len(out):07x}cccc", "commit": {"author": {"email": email, "date": when.strftime("%Y-%m-%dT%H:%M:%SZ")}}})
    return out


def account_commits(login, *pairs):
    """Commits made by one GitHub account: ``(email, n)`` pairs, each commit carrying the account's login."""
    out = commit_list(*pairs)
    for item in out:
        item["author"] = {"login": login}
    return out


def make_user(db_session, org, first, last):
    from app.models.user import User

    user = User(email=f"{first.lower()}-{uuid.uuid4().hex[:8]}@example.com", first_name=first, last_name=last,
                organization_id=org.id, confirmed=True, enterprise_role="application_manager")
    db_session.add(user)
    db_session.flush()
    return user


def imported_application(db_session, org):
    """An application the connector imported before owners were read from commits: it names its
    repository and holds no commit fact and no owner."""
    from app.connectors.github import stamp_origin
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import create_backbone_element

    element = create_backbone_element(
        element_type="ApplicationComponent", layer="Application", name="Archie Ea", organization_id=org.id)
    stamp_origin(element, repository=REPOSITORY, commit="abc1234def", file="Dockerfile")
    component = ApplicationComponent(name="Archie Ea", organization_id=org.id, archimate_element_id=element.id)
    db_session.add(component)
    db_session.flush()
    return element, component


def connect(db_session, tenant_ctx, org):
    from app.models.connector_config import ConnectorConfig
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    cfg = ConnectorConfig(organization_id=org.id, connector_type="github", name="GitHub", status="active",
                          config={"organization": "acme"})
    db_session.add(cfg)
    db_session.flush()
    with tenant_ctx(org.id):
        OrgCredentialVault().store(org.id, "github", "api_token", TOKEN)
    return cfg


def primaries(db_session, component):
    from app.models.application_owner import ApplicationOwner

    return ApplicationOwner.query.filter_by(
        application_id=component.id, organization_id=component.organization_id, ownership_type="primary").all()


def reload(db_session, keys):
    """The command works in its own scope, which ends the session: read both again by key."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    return db_session.get(ArchiMateElement, keys[0]), db_session.get(ApplicationComponent, keys[1])


def fact(db_session, element, key):
    db_session.refresh(element)
    entry = (element.acm_properties or {}).get(key)
    return entry.get("value") if isinstance(entry, dict) else None


def read(db_session, tenant_ctx, org, cfg):
    from app.services.connector_framework import run_connector_sync

    with tenant_ctx(org.id):
        result = run_connector_sync(org_id=org.id, connector_id=cfg.id, delegated=True)
    db_session.flush()
    return result


def test_the_next_read_names_the_owner_of_an_application_imported_before_and_leaves_the_other_organisation(
    db_session, make_org, tenant_ctx, github
):
    org_a, org_b = make_org("rr-a"), make_org("rr-b")
    ada = make_user(db_session, org_a, "Ada", "Obi")
    make_user(db_session, org_b, "Bea", "Roe")
    element, component = imported_application(db_session, org_a)
    other_element, other_component = imported_application(db_session, org_b)
    cfg = connect(db_session, tenant_ctx, org_a)
    github.commits = commit_list((ada.email, 8), ("stranger@example.org", 2))

    read(db_session, tenant_ctx, org_a, cfg)

    assert [(r.user_id, r.source_table) for r in primaries(db_session, component)] == [(ada.id, "github_connector")]
    assert fact(db_session, element, "github_owner_source")["text"].startswith("From commit history: 8 of 8 commits")
    assert primaries(db_session, other_component) == []
    assert fact(db_session, other_element, "github_commit_owners") is None


def test_a_read_that_cannot_open_the_commits_records_why_there_is_no_owner(db_session, make_org, tenant_ctx, github, app):
    from app.connectors.github_gaps import application_gaps

    org = make_org("rr-refused")
    element, component = imported_application(db_session, org)
    cfg = connect(db_session, tenant_ctx, org)
    github.commit_status = 403

    read(db_session, tenant_ctx, org, cfg)

    reason = ("No owner was suggested: the commits of archie-ea could not be read because "
              "the GitHub connection has no access to it.")
    assert primaries(db_session, component) == []
    assert fact(db_session, element, "github_owner_source")["text"] == reason
    with app.test_request_context():
        owner_gap = [g for g in application_gaps(org.id, [component])[component.id]["gaps"] if g["key"] == "owner"][0]
    assert owner_gap["note"] == reason


def test_the_command_names_the_owner_once_and_the_second_run_changes_nothing(db_session, make_org, tenant_ctx, github):
    from app.commands.owners_from_commits import owners_from_commits_command

    org_a, org_b = make_org("rr-cmd-a"), make_org("rr-cmd-b")
    ada = make_user(db_session, org_a, "Ada", "Obi")
    element, component = imported_application(db_session, org_a)
    _, other_component = imported_application(db_session, org_b)
    connect(db_session, tenant_ctx, org_a)
    github.commits = commit_list((ada.email, 5))
    db_session.commit()
    runner, org_a_id, keys, ada_id = CliRunner(), org_a.id, (element.id, component.id), ada.id
    other_id = other_component.id

    first = runner.invoke(owners_from_commits_command, ["--org", str(org_a_id)])
    second = runner.invoke(owners_from_commits_command, ["--org", str(org_a_id)])

    assert first.exit_code == 0, first.output
    assert f"org {org_a_id}: Archie Ea ({REPOSITORY}): owner set. From commit history: 5 of 5 commits" in first.output
    assert f"org {org_a_id}: Archie Ea ({REPOSITORY}): already owned." in second.output
    assert "Nothing changed." in second.output
    element, component = reload(db_session, keys)
    assert [r.user_id for r in primaries(db_session, component)] == [ada_id]
    from sqlalchemy import text
    assert db_session.execute(
        text("SELECT count(*) FROM application_owners WHERE application_id = :a"), {"a": other_id}).scalar() == 0


def test_the_command_without_a_github_connection_records_that_reason_and_repeats_quietly(db_session, make_org):
    from app.commands.owners_from_commits import owners_from_commits_command

    org = make_org("rr-none")
    element, component = imported_application(db_session, org)
    db_session.commit()
    runner, org_id, keys = CliRunner(), org.id, (element.id, component.id)

    first = runner.invoke(owners_from_commits_command, ["--org", str(org_id)])
    second = runner.invoke(owners_from_commits_command, ["--org", str(org_id)])

    reason = "No owner was suggested: the commits of archie-ea could not be read because GitHub is not connected for this organisation."
    assert reason in first.output and "reason recorded" in first.output
    assert "unchanged" in second.output and "Nothing changed." in second.output
    element, component = reload(db_session, keys)
    assert fact(db_session, element, "github_owner_source")["text"] == reason


def test_the_application_list_gives_the_reason_instead_of_a_bare_no_owner(db_session, make_org, client, login_as):
    org = make_org("rr-list")
    admin = make_user(db_session, org, "Ada", "Obi")
    element, component = imported_application(db_session, org)
    db_session.commit()
    login_as(client, admin)

    page = client.get("/applications/").get_data(as_text=True)
    assert "the recent commits of archie-ea have not been read" in page

    from app.connectors import github
    github.apply_commit_history(org.id, element, component, github._unreadable_summary(REPOSITORY, "HTTP 403"))
    db_session.commit()
    page = client.get("/applications/").get_data(as_text=True)
    assert "the commits of archie-ea could not be read because the GitHub connection has no access to it." in page


def test_a_code_owner_account_becomes_the_member_whose_email_its_own_commits_carry(
    db_session, make_org, tenant_ctx, github
):
    org_a, org_b = make_org("rr-acct-a"), make_org("rr-acct-b")
    ada = make_user(db_session, org_a, "Ada", "Obi")
    elsewhere = make_user(db_session, org_b, "Cy", "Dee")
    element, component = imported_application(db_session, org_a)
    other_element, other_component = imported_application(db_session, org_b)
    cfg = connect(db_session, tenant_ctx, org_a)
    github.codeowners = "* @Anioko\n"
    # @Anioko made 6 commits with Ada's address; two other accounts made more under strangers' addresses.
    github.commits = (account_commits("anioko", (ada.email, 6)) + account_commits("someone", ("a@vendor.example", 4))
                      + account_commits("other", ("b@vendor.example", 3)) + account_commits("cy", (elsewhere.email, 9)))

    read(db_session, tenant_ctx, org_a, cfg)

    assert [(r.user_id, r.source_table) for r in primaries(db_session, component)] == [(ada.id, "github_connector")]
    text = fact(db_session, element, "github_owner_source")["text"]
    assert text.startswith("From commit history: GitHub names @Anioko as code owner; 6 of that account's 6 commits")
    assert ada.email in text
    assert primaries(db_session, other_component) == []


def test_a_code_owner_account_whose_commits_use_no_member_email_becomes_a_named_owner(
    db_session, make_org, tenant_ctx, github, app
):
    from app.connectors.github_gaps import application_gaps

    org, other = make_org("rr-acct-none"), make_org("rr-acct-none-b")
    make_user(db_session, org, "Ada", "Obi")
    stranger = make_user(db_session, other, "Cy", "Dee")  # a member of another organisation, same e-mail
    element, component = imported_application(db_session, org)
    cfg = connect(db_session, tenant_ctx, org)
    github.codeowners = "* @Anioko\n"
    github.commits = account_commits("anioko", (stranger.email, 7))

    read(db_session, tenant_ctx, org, cfg)

    assert [(r.person_name, r.user_id) for r in primaries(db_session, component)] == [("@Anioko", None)]
    source = fact(db_session, element, "github_owner_source")
    assert source["user_id"] is None and source["text"].startswith("Named by GitHub: code owner in acme/archie-ea CODEOWNERS")
    with app.test_request_context():
        gaps = application_gaps(org.id, [component]).get(component.id, {"gaps": []})["gaps"]
    assert [g for g in gaps if g["key"] == "owner"] == []


def test_a_team_is_never_turned_into_a_person(db_session, make_org, tenant_ctx, github):
    org = make_org("rr-team")
    ada = make_user(db_session, org, "Ada", "Obi")
    element, component = imported_application(db_session, org)
    cfg = connect(db_session, tenant_ctx, org)
    github.codeowners = "* @acme/platform\n"
    # Ada's address is on the commits of an account called "platform" and one called "acme", but the
    # members' commits are split evenly with Sam, so no one leads and only the team name could pick Ada.
    sam = make_user(db_session, org, "Sam", "Lee")
    github.commits = (account_commits("platform", (ada.email, 4)) + account_commits("acme", (ada.email, 4))
                      + account_commits("sam-l", (sam.email, 8)))

    read(db_session, tenant_ctx, org, cfg)

    assert primaries(db_session, component) == []
    assert (fact(db_session, element, "github_owner_source") or {}).get("user_id") is None
    from app.connectors.github import code_owner_handles, handle_owner

    reloaded = type(element).query.get(element.id)
    assert code_owner_handles(reloaded) == []  # a team is not an account
    # Even if a team name were looked up as an account, Ada's address on it must not make her the owner.
    summary = {"days": 90, "logins": {"acme/platform": {"commits": 4, "members": {str(ada.id): 4}}}}
    assert handle_owner(org.id, summary, code_owner_handles(reloaded))[0] is None


def test_a_named_owner_with_nobody_to_pick_gives_a_reason_and_no_prompt(db_session, make_org, tenant_ctx, github, app):
    from app.connectors.github_gaps import application_gaps

    org = make_org("rr-nopick")
    make_user(db_session, org, "Ada", "Obi")
    team_el, team_comp = imported_application(db_session, org)
    cfg = connect(db_session, tenant_ctx, org)
    github.codeowners = "* @acme/platform\n"
    # two accounts share the commits evenly, so no one author leads them
    github.commits = account_commits("platform", ("a@vendor.example", 3)) + account_commits("other", ("b@vendor.example", 3))
    read(db_session, tenant_ctx, org, cfg)
    with app.test_request_context():
        note = [g for g in application_gaps(org.id, [team_comp])[team_comp.id]["gaps"] if g["key"] == "owner"][0]["note"]
    assert note.startswith("No owner was set: GitHub names @acme/platform as code owner")
    assert "Pick the person" not in note

    org2 = make_org("rr-nopick-b")
    make_user(db_session, org2, "Bo", "Ray")
    element, component = imported_application(db_session, org2)
    cfg2 = connect(db_session, tenant_ctx, org2)
    github.codeowners = "* @Anioko @Bea\n"
    github.commit_status = 403
    read(db_session, tenant_ctx, org2, cfg2)
    with app.test_request_context():
        note = [g for g in application_gaps(org2.id, [component])[component.id]["gaps"] if g["key"] == "owner"][0]["note"]
    assert note.startswith("No owner was set: GitHub names @Anioko, @Bea as code owner")
    assert "could not be read" in note
    assert "Pick the person" not in note


def test_two_accounts_pointing_at_different_members_name_nobody(db_session, make_org, tenant_ctx, github):
    org = make_org("rr-two")
    ada, sam = make_user(db_session, org, "Ada", "Obi"), make_user(db_session, org, "Sam", "Lee")
    element, component = imported_application(db_session, org)
    cfg = connect(db_session, tenant_ctx, org)
    github.codeowners = "* @one @two\n"
    github.commits = account_commits("one", (ada.email, 5)) + account_commits("two", (sam.email, 5))

    read(db_session, tenant_ctx, org, cfg)

    # The two members share the commits evenly, so there is no leader either.
    assert primaries(db_session, component) == []
    assert "point at more than one member" in fact(db_session, element, "github_owner_source")["text"]


def test_the_command_applies_the_account_rule_and_the_second_run_changes_nothing(db_session, make_org, tenant_ctx, github):
    from app.commands.owners_from_commits import owners_from_commits_command

    org = make_org("rr-acct-cmd")
    ada = make_user(db_session, org, "Ada", "Obi")
    element, component = imported_application(db_session, org)
    connect(db_session, tenant_ctx, org)
    from app.connectors.github import _write_facts
    _write_facts(element, {"github_code_owners": ["@Anioko"]})  # what an earlier read of CODEOWNERS recorded
    github.commits = account_commits("anioko", (ada.email, 3), ("dev@vendor.example", 1))
    db_session.commit()
    runner, org_id, keys, ada_id = CliRunner(), org.id, (element.id, component.id), ada.id

    first = runner.invoke(owners_from_commits_command, ["--org", str(org_id)])
    second = runner.invoke(owners_from_commits_command, ["--org", str(org_id)])

    assert "owner set. From commit history: GitHub names @Anioko as code owner; 3 of that account's 4 commits" in first.output
    assert "already owned." in second.output and "Nothing changed." in second.output
    element, component = reload(db_session, keys)
    assert [r.user_id for r in primaries(db_session, component)] == [ada_id]


def test_the_list_footer_says_an_owner_set_from_commit_history_came_from_it(
    db_session, make_org, tenant_ctx, github, client, login_as
):
    org = make_org("rr-footer")
    ada = make_user(db_session, org, "Ada", "Obi")
    element, component = imported_application(db_session, org)
    cfg = connect(db_session, tenant_ctx, org)
    github.codeowners = "* @Anioko\n"
    github.commits = account_commits("anioko", (ada.email, 6))
    read(db_session, tenant_ctx, org, cfg)
    assert [r.user_id for r in primaries(db_session, component)] == [ada.id]
    db_session.commit()
    login_as(client, ada)

    page = client.get("/applications/").get_data(as_text=True)

    assert "GitHub commit history" in page
    assert "Owner was entered by hand" not in page


def test_another_organisations_list_footer_never_shows_the_commit_history_label(
    db_session, make_org, tenant_ctx, github, client, login_as
):
    from app.services.element_ownership_service import set_element_owner

    org_a, org_b = make_org("rr-footer-a"), make_org("rr-footer-b")
    ada = make_user(db_session, org_a, "Ada", "Obi")
    bo = make_user(db_session, org_b, "Bo", "Lin")
    element, component = imported_application(db_session, org_a)
    _, other_component = imported_application(db_session, org_b)
    set_element_owner(element_type="application", element_id=other_component.id, user_id=bo.id,
                      organization_id=org_b.id, ownership_type="primary", assigned_by=bo.id)
    cfg = connect(db_session, tenant_ctx, org_a)
    github.codeowners = "* @Anioko\n"
    github.commits = account_commits("anioko", (ada.email, 6))
    read(db_session, tenant_ctx, org_a, cfg)
    assert [r.user_id for r in primaries(db_session, component)] == [ada.id]
    db_session.commit()

    login_as(client, ada)
    assert "GitHub commit history" in client.get("/applications/").get_data(as_text=True)
    login_as(client, bo)
    page = client.get("/applications/").get_data(as_text=True)

    assert "Bo Lin" in page
    assert "GitHub commit history" not in page
