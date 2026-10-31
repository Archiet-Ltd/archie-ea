"""GitHub repositories are evidence for the application components of an organisation.

The GitHub connector reads an organisation's repositories, teams and code-owner
files. A repository that matches an existing application component adds facts to
it (owning team or code owner, last activity, main language, archived flag).
A repository that matches nothing becomes a classified proposal in the approval
queue; nothing becomes an application until a person approves.

Everything is fed by a recorded set of GitHub REST responses
(``tests/fixtures/github_acme_org.json``: 12 repositories, 3 teams, 3 code-owner
files); no test touches the network. Two real organisations are used throughout.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

# The trigger and guard installers other suites use, so the event log and the
# entity history are live exactly as they are in production.
from tests.test_entity_history_trigger import _entity_history_trigger  # noqa: F401
from tests.test_event_log import _install_guards  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_session")

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "github_acme_org.json")
SECRET = "ghp_s3cr3t-token-do-not-log-4417"
API = "https://api.github.com/"

MATCHING = {
    # repository -> the name of the component that already exists in the model
    "payments-service": "Payments Service",
    "customer-portal": "Customer Portal",
    "billing-api": "Billing API",
    "inventory-service": "Inventory Service",
    "legacy-crm": "Legacy CRM",
}
UNMATCHED_CLASS = {
    "orders-worker": "application",
    "mobile-app": "application",
    "shared-utils": "library",
    "infra-terraform": "infrastructure",
    "engineering-handbook": "documentation",
    "old-prototype": "ignore",
    "archive-2019": "ignore",
}


# --------------------------------------------------------------------- #
# Fakes: replay recorded GitHub responses instead of calling the network #
# --------------------------------------------------------------------- #


class _Resp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = {"X-RateLimit-Remaining": "4990", **(headers or {})}
        self.reason = {200: "OK", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 500: "Server Error"}.get(
            status, "Status"
        )
        self.text = json.dumps(body)
        self.is_redirect = 300 <= status < 400

    def json(self):
        return self._body


@pytest.fixture
def github(app, monkeypatch):
    """Serve recorded GitHub responses in place of ``requests.Session.request``.

    ``state.bundle`` is a private copy a test may edit; ``state.overrides`` maps
    a path (optionally ``path?page=n``) to ``(status, body, headers)``;
    ``state.calls`` records every request, with the Authorization header sent.
    """
    import requests

    with open(_FIXTURE) as handle:
        bundle = json.load(handle)["responses"]
    # Add the account type response for the organization
    bundle["users/acme"] = {"type": "Organization", "login": "acme"}
    state = SimpleNamespace(bundle=copy.deepcopy(bundle), overrides={}, calls=[])
    monkeypatch.setitem(app.config, "ALLOW_EXTERNAL_API_CALLS", True)

    def fake_request(self, method=None, url=None, params=None, headers=None, **kw):
        assert url.startswith(API), f"a request left for an unexpected host: {url}"
        path = url[len(API):].strip("/")
        params = dict(params or {})
        auth = {**self.headers, **(headers or {})}.get("Authorization")
        state.calls.append({"method": method, "path": path, "params": params, "auth": auth})
        page = int(params.get("page", 1))
        for key in (f"{path}?page={page}", path):
            if key in state.overrides:
                return _Resp(*state.overrides[key])
        if path not in state.bundle:
            return _Resp(404, {"message": "Not Found"})
        body = state.bundle[path]
        if path.endswith("/repos") or path.endswith("/teams"):
            per_page = int(params.get("per_page", 30))
            body = body[(page - 1) * per_page : page * per_page]
        return _Resp(200, body)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    return state


# --------------------------------------------------------------------- #
# Helpers                                                                #
# --------------------------------------------------------------------- #


@pytest.fixture
def org_a(make_org):
    return make_org("gh-a")


@pytest.fixture
def org_b(make_org):
    return make_org("gh-b")


def _user(db_session, org, email, first="Test", last="Person"):
    from app.models.user import User

    user = User(first_name=first, last_name=last, email=email, organization_id=org.id)
    db_session.add(user)
    db_session.flush()
    return user


def _make_admin(db_session, org_id, label):
    from tests.test_connector_approval_flow import _make_admin

    return _make_admin(db_session, org_id, label)


def _estate(db_session, org, names=None, suffix=""):
    """The organisation's existing application components (an element and a portfolio row each)."""
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import create_backbone_element

    made = {}
    for repo, name in (names or MATCHING).items():
        element = create_backbone_element(
            element_type="ApplicationComponent", layer="Application", name=name, organization_id=org.id
        )
        component = ApplicationComponent(name=name, organization_id=org.id, archimate_element_id=element.id)
        db_session.add(component)
        db_session.flush()
        made[repo] = SimpleNamespace(element=element, component=component)
    return made


@pytest.fixture
def estate_a(db_session, org_a):
    """Org A: five components, two people who appear in code-owner files, and one
    person-confirmed owner on the inventory service."""
    from app.models.application_owner import ApplicationOwner

    estate = _estate(db_session, org_a)
    users = SimpleNamespace(
        one=_user(db_session, org_a, "owner.one@example.test", "Olive", "One"),
        two=_user(db_session, org_a, "person.two@example.test", "Pat", "Two"),
        # An administrator: also the person who approves proposals in these tests
        # (the Community plan admits three people, so no fourth user is made).
        existing=_make_admin(db_session, org_a.id, "Existing"),
    )
    db_session.add(
        ApplicationOwner(
            application_id=estate["inventory-service"].component.id,
            user_id=users.existing.id,
            organization_id=org_a.id,
            ownership_type="primary",
            assigned_by=users.existing.id,
        )
    )
    db_session.flush()
    return SimpleNamespace(components=estate, users=users)


@pytest.fixture
def connect(db_session, tenant_ctx):
    def _connect(org, config=None, token=SECRET):
        from app.models.connector_config import ConnectorConfig
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        cfg = ConnectorConfig(
            organization_id=org.id,
            connector_type="github",
            name=f"GitHub for {org.id}",
            config=dict(config if config is not None else {"organization": "acme"}),
            status="active",
        )
        db_session.add(cfg)
        db_session.flush()
        with tenant_ctx(org.id):
            OrgCredentialVault().store(org.id, "github", "api_token", token)
        return cfg

    return _connect


@pytest.fixture
def sync(db_session, tenant_ctx):
    def _sync(org, cfg):
        from app.services.connector_framework import run_connector_sync

        with tenant_ctx(org.id):
            result = run_connector_sync(org_id=org.id, connector_id=cfg.id, delegated=True)
        db_session.flush()
        return result

    return _sync


def _prop(db_session, element, key):
    db_session.refresh(element)
    entry = (element.acm_properties or {}).get(key)
    return None if entry is None else entry.get("value")


def _snapshot(db_session, org_id):
    """Everything a sync could change for an organisation, as comparable values."""
    q = lambda sql: [tuple(r) for r in db_session.execute(text(sql), {"o": org_id}).fetchall()]  # noqa: E731
    return {
        "elements": q(
            "SELECT id, name, type, layer, description, CAST(acm_properties AS text), "
            "CAST(custom_properties AS text) FROM archimate_elements WHERE organization_id = :o ORDER BY id"
        ),
        "components": q("SELECT id, name FROM application_components WHERE organization_id = :o ORDER BY id"),
        "owners": q(
            "SELECT id, application_id, user_id, ownership_type, source_table FROM application_owners "
            "WHERE organization_id = :o ORDER BY id"
        ),
        "approvals": q("SELECT id, operation_type, status FROM ai_chat_crud_approvals WHERE organization_id = :o ORDER BY id"),
        "crosswalk": q(
            "SELECT external_id, element_id, confidence FROM external_identity_crosswalk "
            "WHERE organization_id = :o AND source_system = 'github' ORDER BY external_id"
        ),
    }


def _proposals(org_id, operation_type="repository_classification", status=None):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    query = AIChatCRUDApproval.query.filter_by(
        organization_id=org_id, source_table="github_connector", operation_type=operation_type
    )
    if status is not None:
        query = query.filter_by(status=status)
    return query.order_by(AIChatCRUDApproval.id).all()


def _payload(approval):
    return json.loads(approval.operation_payload)


def _sync_log(cfg):
    from app.models.connector_config import SyncLog

    return SyncLog.query.filter_by(connector_id=cfg.id).order_by(SyncLog.started_at.desc()).first()


@pytest.fixture
def synced(db_session, org_a, estate_a, github, connect, sync):
    cfg = connect(org_a)
    result = sync(org_a, cfg)
    return SimpleNamespace(cfg=cfg, result=result)


# --------------------------------------------------------------------- #
# Acceptance 1 - twelve repositories, five components                    #
# --------------------------------------------------------------------- #


def test_five_matching_repositories_gain_facts_and_seven_become_proposals(
    db_session, org_a, estate_a, github, connect, sync
):
    from app.models.ai_chat_crud_approval import ApprovalStatus

    before = _snapshot(db_session, org_a.id)
    result = sync(org_a, connect(org_a))

    assert result["success"] is True, result
    assert result["records_processed"] == 12
    assert result["records_updated"] == 5
    assert result["records_created"] == 0
    assert result["records_proposed"] == 7
    assert result["records_failed"] == 0

    after = _snapshot(db_session, org_a.id)
    # No application and no element was created: only the five existing ones carry facts.
    assert [row[0] for row in after["elements"]] == [row[0] for row in before["elements"]]
    assert after["components"] == before["components"]

    for repo, made in estate_a.components.items():
        assert _prop(db_session, made.element, "github_main_language") is not None, repo
        assert _prop(db_session, made.element, "github_last_activity") is not None, repo
        assert _prop(db_session, made.element, "github_archived") is (repo == "legacy-crm"), repo
    pay = estate_a.components["payments-service"].element
    assert _prop(db_session, pay, "github_main_language") == "Python"
    assert _prop(db_session, pay, "github_last_activity") == "2026-10-10T09:15:00Z"
    assert db_session.get(type(pay), pay.id).acm_properties["github_main_language"]["source"] == "github"

    # Seven classified proposals, all pending, none an application yet.
    pending = _proposals(org_a.id, status=ApprovalStatus.PENDING)
    assert len(pending) == 7
    classes = {_payload(a)["repository"]: _payload(a)["classification"] for a in pending}
    assert classes == {f"acme/{repo}": cls for repo, cls in UNMATCHED_CLASS.items()}
    for approval in pending:
        assert _payload(approval)["reason"], "every proposal says why it was classified so"


def test_a_rerun_changes_nothing(db_session, org_a, estate_a, github, connect, sync):
    cfg = connect(org_a)
    first = sync(org_a, cfg)
    snapshot = _snapshot(db_session, org_a.id)
    second = sync(org_a, cfg)

    assert first["records_updated"] == 5 and first["records_proposed"] == 7
    assert second["records_updated"] == 0
    assert second["records_proposed"] == 0
    assert second["records_created"] == 0
    assert second["records_failed"] == 0
    assert _snapshot(db_session, org_a.id) == snapshot


def test_source_repository_and_read_time_are_recorded_in_the_crosswalk(
    db_session, org_a, estate_a, synced
):
    from app.models.external_identity_crosswalk import ExternalIdentityCrosswalk

    rows = ExternalIdentityCrosswalk.query.filter_by(organization_id=org_a.id, source_system="github").all()
    assert {r.external_id for r in rows} == {f"acme/{repo}" for repo in MATCHING}
    by_id = {r.external_id: r for r in rows}
    for repo, made in estate_a.components.items():
        row = by_id[f"acme/{repo}"]
        assert row.element_id == made.element.id
        assert row.last_seen is not None, "the time the repository was read"


# --------------------------------------------------------------------- #
# Owners                                                                 #
# --------------------------------------------------------------------- #


def test_a_code_owner_who_is_a_person_in_the_organisation_becomes_the_owner(
    db_session, org_a, estate_a, synced
):
    from app.models.application_owner import ApplicationOwner

    rows = ApplicationOwner.query.filter_by(
        application_id=estate_a.components["payments-service"].component.id, organization_id=org_a.id
    ).all()
    assert [(r.user_id, r.ownership_type, r.source_table) for r in rows] == [
        (estate_a.users.one.id, "primary", "github_connector")
    ]


def test_no_owner_is_invented(db_session, org_a, estate_a, synced):
    from app.models.application_owner import ApplicationOwner

    for repo in ("customer-portal", "billing-api", "legacy-crm"):
        component = estate_a.components[repo].component
        assert ApplicationOwner.query.filter_by(application_id=component.id).count() == 0, repo
    portal = estate_a.components["customer-portal"].element
    assert _prop(db_session, portal, "github_code_owners") == ["@acme/web-team"]
    assert _prop(db_session, portal, "github_owning_team") == "acme/web-team"
    billing = estate_a.components["billing-api"].element
    assert _prop(db_session, billing, "github_owning_team") == "acme/platform-team"
    assert _prop(db_session, billing, "github_code_owners") is None
    crm = estate_a.components["legacy-crm"].element
    assert _prop(db_session, crm, "github_owning_team") is None, "a team with only push access does not own it"
    assert _prop(db_session, crm, "github_code_owners") is None


def test_a_person_confirmed_owner_is_proposed_not_overwritten(db_session, org_a, estate_a, synced):
    from app.models.application_owner import ApplicationOwner
    from app.models.ai_chat_crud_approval import ApprovalStatus

    component = estate_a.components["inventory-service"].component
    rows = ApplicationOwner.query.filter_by(application_id=component.id).all()
    assert [r.user_id for r in rows] == [estate_a.users.existing.id], "the confirmed owner stays"

    proposals = _proposals(org_a.id, "owner_proposal", ApprovalStatus.PENDING)
    assert len(proposals) == 1
    payload = _payload(proposals[0])
    assert payload["user_id"] == estate_a.users.two.id
    assert payload["application_id"] == component.id
    assert proposals[0].organization_id == org_a.id


def test_approving_the_owner_proposal_replaces_the_owner(
    db_session, org_a, estate_a, synced, tenant_ctx
):
    from app.models.application_owner import ApplicationOwner
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    proposal = _proposals(org_a.id, "owner_proposal")[0]
    approver = estate_a.users.existing
    db_session.commit()
    with tenant_ctx(org_a.id):
        result = AIChatApprovalService(user_id=approver.id).approve_and_execute(proposal.id)
    assert result.get("success") is True, result
    rows = ApplicationOwner.query.filter_by(
        application_id=estate_a.components["inventory-service"].component.id
    ).all()
    assert [(r.user_id, r.ownership_type) for r in rows] == [(estate_a.users.two.id, "primary")]


def test_a_change_in_the_code_owner_file_moves_a_connector_written_owner(
    db_session, org_a, estate_a, github, connect, sync
):
    from app.models.application_owner import ApplicationOwner
    import base64

    cfg = connect(org_a)
    sync(org_a, cfg)
    key = "repos/acme/payments-service/contents/.github/CODEOWNERS"
    body = dict(github.bundle[key])
    body["content"] = base64.encodebytes(b"* person.two@example.test\n").decode()
    github.bundle[key] = body

    sync(org_a, cfg)

    rows = ApplicationOwner.query.filter_by(
        application_id=estate_a.components["payments-service"].component.id
    ).all()
    assert [(r.user_id, r.source_table) for r in rows] == [(estate_a.users.two.id, "github_connector")]


# --------------------------------------------------------------------- #
# Proposals                                                              #
# --------------------------------------------------------------------- #


def test_approving_an_application_proposal_is_what_creates_the_application(
    db_session, org_a, estate_a, synced, tenant_ctx
):
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    assert ApplicationComponent.query.filter_by(organization_id=org_a.id, name="Orders Worker").count() == 0
    proposal = next(a for a in _proposals(org_a.id) if _payload(a)["repository"] == "acme/orders-worker")
    library = next(a for a in _proposals(org_a.id) if _payload(a)["repository"] == "acme/shared-utils")
    approver = estate_a.users.existing
    db_session.commit()
    with tenant_ctx(org_a.id):
        service = AIChatApprovalService(user_id=approver.id)
        created = service.approve_and_execute(proposal.id)
        acknowledged = service.approve_and_execute(library.id)

    assert created.get("success") is True, created
    assert ApplicationComponent.query.filter_by(organization_id=org_a.id, name="Orders Worker").count() == 1
    assert acknowledged.get("success") is True, acknowledged
    assert ApplicationComponent.query.filter_by(organization_id=org_a.id, name="Shared Utils").count() == 0, (
        "a library proposal records the decision and creates nothing"
    )


def test_a_decided_proposal_is_not_proposed_again(db_session, org_a, estate_a, github, connect, sync, tenant_ctx):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    cfg = connect(org_a)
    sync(org_a, cfg)
    ignore = next(a for a in _proposals(org_a.id) if _payload(a)["repository"] == "acme/old-prototype")
    approver = estate_a.users.existing
    db_session.commit()
    with tenant_ctx(org_a.id):
        AIChatApprovalService(user_id=approver.id).reject_approval(ignore.id, "not ours")
    second = sync(org_a, cfg)
    assert second["records_proposed"] == 0
    assert len(_proposals(org_a.id)) == 7


def test_an_earlier_proposal_with_the_repository_id_last_is_found_by_its_parsed_id(
    db_session, org_a, estate_a, github, connect, sync
):
    """Found by the parsed repository id, whatever the payload's key order or separators."""
    cfg = connect(org_a)
    sync(org_a, cfg)
    before = _proposals(org_a.id)
    assert before
    for a in before:
        payload = _payload(a)
        repository_id = payload.pop("repository_id")
        payload["repository_id"] = repository_id  # last key: no trailing comma
        a.operation_payload = json.dumps(payload, separators=(",", ":"))
    db_session.flush()
    second = sync(org_a, cfg)
    assert second["records_proposed"] == 0
    assert len(_proposals(org_a.id)) == len(before)


def test_classification_rules(app):
    from app.connectors.github import classify_repository

    def repo(name, language="Python", **kw):
        return {"name": name, "language": language, "archived": False, "fork": False,
                "is_template": False, "topics": [], "size": 100, **kw}

    cases = [
        (repo("x", archived=True), ["README.md"], "ignore"),
        (repo("x", fork=True), ["Dockerfile"], "ignore"),
        (repo("x", is_template=True), ["Dockerfile"], "ignore"),
        (repo("x", language=None, size=0), [], "ignore"),
        (repo("team-docs"), ["README.md", "package.json"], "documentation"),
        (repo("x", topics=["runbook"]), ["README.md"], "documentation"),
        (repo("x", language=None), ["README.md", "docs", "mkdocs.yml"], "documentation"),
        (repo("platform-config", language="HCL"), ["main.tf"], "infrastructure"),
        (repo("deploy-charts", language="Smarty"), ["Chart.yaml", "templates"], "infrastructure"),
        (repo("x", topics=["terraform"]), ["README.md"], "infrastructure"),
        (repo("orders-api", language="Go"), ["Dockerfile", "go.mod"], "application"),
        (repo("x", language="Java"), ["pom.xml", "docker-compose.yml"], "application"),
        (repo("payments-lib"), ["pyproject.toml", "Dockerfile"], "application"),
        (repo("shared-utils"), ["pyproject.toml"], "library"),
        (repo("x", topics=["sdk"]), ["package.json"], "library"),
        (repo("billing-client", language="Java"), ["pom.xml"], "library"),
        (repo("mobile-app", language="Kotlin"), ["build.gradle", "app"], "application"),
        (repo("x", language=None), ["notes.txt"], "ignore"),
    ]
    for item, root, expected in cases:
        got, reason = classify_repository(item, root)
        assert got == expected, (item["name"], root, got, reason)
        assert reason


def test_a_monorepo_is_one_proposal_with_its_parts_listed(app):
    from app.connectors.github import classify_repository

    item = {"name": "platform", "language": "TypeScript", "archived": False, "fork": False,
            "is_template": False, "topics": [], "size": 5000}
    classification, reason = classify_repository(item, ["apps", "packages", "package.json", "Dockerfile"])
    assert classification == "application"
    assert "apps" in reason and "packages" in reason, "the reviewer is told where the parts are"


def test_code_owner_rule(app):
    from app.connectors.github import default_code_owners

    assert default_code_owners("# c\n* @a/team me@x.test\n/docs/ @docs\n") == ["@a/team", "me@x.test"]
    assert default_code_owners("*.js @js\n* @first\n* @second\n") == ["@second"], "the last * rule wins"
    assert default_code_owners("/src/ @same\n/lib/ @same\n") == ["@same"], "owners on every rule"
    assert default_code_owners("/src/ @one\n/lib/ @two\n") == []
    assert default_code_owners("") == []
    assert default_code_owners("# only a comment\n") == []
    assert default_code_owners("* \n") == []


# --------------------------------------------------------------------- #
# Acceptance 2 - two organisations                                       #
# --------------------------------------------------------------------- #


def test_two_organisations_with_the_same_repository_name_stay_separate(
    db_session, org_a, org_b, estate_a, github, connect, sync
):
    from app.models.application_owner import ApplicationOwner

    estate_b = _estate(db_session, org_b, {"payments-service": "Payments Service"})
    cfg_a, cfg_b = connect(org_a), connect(org_b)

    sync(org_a, cfg_a)
    a_after_first = _snapshot(db_session, org_a.id)
    result_b = sync(org_b, cfg_b)

    assert result_b["records_updated"] == 1
    assert result_b["records_proposed"] == 11
    assert _snapshot(db_session, org_a.id) == a_after_first, "syncing B changed nothing in A"

    b_element = estate_b["payments-service"].element
    assert _prop(db_session, b_element, "github_main_language") == "Python"
    a_element = estate_a.components["payments-service"].element
    assert a_element.id != b_element.id

    links = db_session.execute(
        text("SELECT organization_id, element_id FROM external_identity_crosswalk "
             "WHERE source_system = 'github' AND external_id = 'acme/payments-service'")
    ).fetchall()
    assert {tuple(r) for r in links} == {(org_a.id, a_element.id), (org_b.id, b_element.id)}

    # The code-owner file names a person of organisation A; B must not receive them.
    assert ApplicationOwner.query.filter_by(organization_id=org_b.id).count() == 0
    assert {a.organization_id for a in _proposals(org_b.id)} == {org_b.id}
    assert len(_proposals(org_b.id)) == 11


# --------------------------------------------------------------------- #
# Acceptance 3 - the token                                               #
# --------------------------------------------------------------------- #


def _everything_written_down(caplog, cfg, result, db_session):
    log = _sync_log(cfg)
    return "\n".join(
        [
            caplog.text,
            json.dumps(result, default=str),
            (log.error_message or "") if log else "",
            json.dumps(log.to_dict(), default=str) if log and hasattr(log, "to_dict") else "",
        ]
    )


def test_the_token_comes_from_the_vault_and_goes_only_in_the_header(
    db_session, org_a, estate_a, github, connect, sync, caplog
):
    caplog.set_level(logging.DEBUG)
    cfg = connect(org_a)
    result = sync(org_a, cfg)

    assert {c["auth"] for c in github.calls} == {f"token {SECRET}"}
    assert all(SECRET not in c["path"] and SECRET not in json.dumps(c["params"]) for c in github.calls)
    assert SECRET not in _everything_written_down(caplog, cfg, result, db_session)
    assert SECRET not in json.dumps(cfg.config), "the configuration row holds settings, never the secret"


def test_the_token_is_not_in_logs_or_errors_when_github_echoes_it(
    db_session, org_a, estate_a, github, connect, sync, caplog
):
    caplog.set_level(logging.DEBUG)
    github.overrides["orgs/acme/repos"] = (401, {"message": f"Bad credentials for {SECRET}"}, {})
    cfg = connect(org_a)
    result = sync(org_a, cfg)

    assert result["success"] is False
    assert result.get("error")
    assert SECRET not in _everything_written_down(caplog, cfg, result, db_session)


def test_the_token_is_not_in_the_log_when_one_repository_fails(
    db_session, org_a, estate_a, github, connect, sync, caplog
):
    caplog.set_level(logging.DEBUG)
    github.overrides["repos/acme/orders-worker/contents"] = (500, {"message": f"oops {SECRET}"}, {})
    cfg = connect(org_a)
    result = sync(org_a, cfg)

    assert result["records_failed"] == 1
    assert result["records_updated"] == 5 and result["records_proposed"] == 6
    assert result["status"] == "partial"
    assert SECRET not in _everything_written_down(caplog, cfg, result, db_session)
    assert "1 of 12" in (_sync_log(cfg).error_message or "")


# --------------------------------------------------------------------- #
# Reading GitHub: pagination, rate limits, deletions, addresses          #
# --------------------------------------------------------------------- #


def test_every_page_is_read(db_session, org_a, estate_a, github, connect, sync, monkeypatch):
    import app.connectors.github as module

    monkeypatch.setattr(module, "PAGE_SIZE", 5)
    result = sync(org_a, connect(org_a))

    assert result["records_processed"] == 12
    assert result["records_updated"] == 5 and result["records_proposed"] == 7
    pages = [c["params"].get("page") for c in github.calls if c["path"] == "orgs/acme/repos"]
    assert pages == [1, 2, 3]
    assert all(c["params"].get("per_page") == 5 for c in github.calls if c["path"] == "orgs/acme/repos")


def test_a_rate_limited_listing_writes_nothing_and_flags_nothing(
    db_session, org_a, estate_a, github, connect, sync, monkeypatch
):
    import app.connectors.github as module

    cfg = connect(org_a)
    sync(org_a, cfg)  # a complete first run
    snapshot = _snapshot(db_session, org_a.id)
    monkeypatch.setattr(module, "PAGE_SIZE", 5)
    github.overrides["orgs/acme/repos?page=2"] = (
        403, {"message": "API rate limit exceeded"}, {"X-RateLimit-Remaining": "0"})

    result = sync(org_a, cfg)

    assert result["success"] is False
    assert "403" in result["error"]
    assert _snapshot(db_session, org_a.id) == snapshot, "a half-read listing must not mark anything missing"


def test_a_repository_gone_from_the_listing_is_flagged_not_deleted(
    db_session, org_a, estate_a, github, connect, sync
):
    from app.models.archimate_core import ArchiMateElement

    cfg = connect(org_a)
    sync(org_a, cfg)
    full = list(github.bundle["orgs/acme/repos"])
    github.bundle["orgs/acme/repos"] = [r for r in full if r["name"] != "billing-api"]

    result = sync(org_a, cfg)

    billing = estate_a.components["billing-api"].element
    assert db_session.get(ArchiMateElement, billing.id).deleted_at is None
    assert _prop(db_session, billing, "github_repository_missing") is True
    assert result["records_flagged_missing"] == 1
    assert _prop(db_session, estate_a.components["payments-service"].element, "github_repository_missing") is None

    github.bundle["orgs/acme/repos"] = full
    sync(org_a, cfg)
    assert _prop(db_session, billing, "github_repository_missing") is False


def test_an_empty_listing_flags_nothing(db_session, org_a, estate_a, github, connect, sync):
    cfg = connect(org_a)
    sync(org_a, cfg)
    snapshot = _snapshot(db_session, org_a.id)
    github.bundle["orgs/acme/repos"] = []

    result = sync(org_a, cfg)

    assert result["records_flagged_missing"] == 0
    assert _snapshot(db_session, org_a.id) == snapshot


def test_an_archived_repository_is_flagged_not_deleted(db_session, org_a, estate_a, github, connect, sync):
    cfg = connect(org_a)
    sync(org_a, cfg)
    crm = estate_a.components["legacy-crm"]
    assert _prop(db_session, crm.element, "github_archived") is True
    for repo in github.bundle["orgs/acme/repos"]:
        if repo["name"] == "payments-service":
            repo["archived"] = True

    sync(org_a, cfg)

    assert _prop(db_session, estate_a.components["payments-service"].element, "github_archived") is True
    assert db_session.get(type(crm.element), crm.element.id).deleted_at is None


def test_a_base_address_that_is_not_allowed_is_refused_before_any_request(
    db_session, org_a, estate_a, github, connect, sync
):
    cfg = connect(org_a, {"organization": "acme", "base_url": "http://169.254.169.254/api/v3"})
    result = sync(org_a, cfg)

    assert result["success"] is False
    assert github.calls == []


def test_a_connector_with_no_organisation_writes_nothing(db_session, org_a, estate_a, github):
    from app.connectors.github import GitHubConnector
    from app.models.connector_config import ConnectorConfig
    from app.services.connector_framework import ConnectorWriteRefused, run_coroutine_sync

    runtime = ConnectorConfig(
        id="x", connector_type="github", name="x", organization_id=None,
        config={"organization": "acme", "api_token": SECRET},
    )
    with pytest.raises(ConnectorWriteRefused):
        run_coroutine_sync(GitHubConnector(runtime).batch_sync())
    assert github.calls == []


def test_a_deployment_that_blocks_outbound_calls_says_so(
    db_session, org_a, estate_a, github, connect, sync, app, monkeypatch
):
    monkeypatch.setitem(app.config, "ALLOW_EXTERNAL_API_CALLS", False)
    cfg = connect(org_a)
    result = sync(org_a, cfg)

    assert result["success"] is False
    assert "disabled" in result["error"].lower()
    assert github.calls == []
    assert _snapshot(db_session, org_a.id)["approvals"] == []


def test_an_organisation_name_that_could_change_the_path_is_refused(
    db_session, org_a, estate_a, github, connect, sync
):
    cfg = connect(org_a, {"organization": "../../user"})
    result = sync(org_a, cfg)
    assert result["success"] is False
    assert github.calls == []


# --------------------------------------------------------------------- #
# Acceptance 4 - the allowlist and the connectors page                   #
# --------------------------------------------------------------------- #


def test_github_is_a_permitted_connector_type(app):
    from app.modules.intelligence.services.connector_allowlist import (
        PERMITTED_CONNECTOR_TYPES,
        assert_connector_permitted,
    )

    assert "github" in PERMITTED_CONNECTOR_TYPES
    with app.test_request_context("/"):
        assert_connector_permitted("github")


def test_the_connectors_page_lists_github_with_its_real_state(
    db_session, org_a, estate_a, github, connect, sync
):
    from app.services.connector_framework import CONNECTOR_TYPE_LABELS, list_org_connectors

    assert CONNECTOR_TYPE_LABELS["github"] == "GitHub"
    cfg = connect(org_a)
    before = [c for c in list_org_connectors(org_a.id) if c["id"] == cfg.id][0]
    assert before["connector_type_label"] == "GitHub"
    assert before["last_sync"] is None and before["last_sync_status"] is None
    assert before["credential_masked"] == "******"
    assert SECRET not in json.dumps(before, default=str)

    sync(org_a, cfg)
    after = [c for c in list_org_connectors(org_a.id) if c["id"] == cfg.id][0]
    assert after["status"] == "active"
    assert after["last_sync"] is not None
    assert after["last_sync_status"] == "completed"
    log = _sync_log(cfg)
    assert (log.records_processed, log.records_updated, log.records_created) == (12, 5, 0)


def test_a_failed_sync_shows_as_an_error_not_as_healthy(db_session, org_a, estate_a, github, connect, sync):
    from app.services.connector_framework import list_org_connectors

    github.overrides["orgs/acme/repos"] = (401, {"message": "Bad credentials"}, {})
    cfg = connect(org_a)
    sync(org_a, cfg)
    view = [c for c in list_org_connectors(org_a.id) if c["id"] == cfg.id][0]
    assert view["status"] == "error"
    assert view["last_sync_status"] == "error"


def test_the_client_refuses_a_redirect_and_scrubs_the_token(app):
    import requests

    from app.services.api_clients.base_client import APIClientError
    from app.services.api_clients.github_client import GitHubAPIClient

    client = GitHubAPIClient(api_token=SECRET)
    response = requests.Response()
    response.status_code = 301
    hook = client.session.hooks["response"][-1]
    with pytest.raises(APIClientError):
        hook(response)
    assert SECRET not in client._scrub(f"bad {SECRET} token")


def test_the_connector_is_registered_in_the_framework(app):
    from app.connectors.github import GitHubConnector
    from app.services.connector_framework import _CONNECTOR_CLASSES, _register_concrete_connectors

    _register_concrete_connectors()
    assert _CONNECTOR_CLASSES["github"] is GitHubConnector


# --------------------------------------------------------------------- #
# Personal account support                                              #
# --------------------------------------------------------------------- #


def _make_personal_account_fixture(github, account_type="User"):
    """Create a fixture bundle for a personal account with two repositories."""
    bundle = copy.deepcopy(github.bundle)
    # Add the account type response
    bundle["users/octocat"] = {"type": account_type, "login": "octocat"}
    # Two repositories under the personal account
    bundle["users/octocat/repos"] = [
        {
            "archived": False,
            "created_at": "2022-01-04T09:00:00Z",
            "default_branch": "main",
            "description": "Personal payments service",
            "disabled": False,
            "fork": False,
            "full_name": "octocat/payments-service",
            "html_url": "https://github.com/octocat/payments-service",
            "id": 8001,
            "is_template": False,
            "language": "Python",
            "name": "payments-service",
            "node_id": "R_8001",
            "open_issues_count": 3,
            "owner": {"id": 2, "login": "octocat", "type": account_type},
            "private": True,
            "pushed_at": "2026-10-10T09:15:00Z",
            "size": 1200,
            "topics": [],
            "updated_at": "2026-10-10T09:15:00Z",
            "visibility": "private",
        },
        {
            "archived": False,
            "created_at": "2022-01-04T09:00:00Z",
            "default_branch": "main",
            "description": "Personal customer portal",
            "disabled": False,
            "fork": False,
            "full_name": "octocat/customer-portal",
            "html_url": "https://github.com/octocat/customer-portal",
            "id": 8002,
            "is_template": False,
            "language": "TypeScript",
            "name": "customer-portal",
            "node_id": "R_8002",
            "open_issues_count": 3,
            "owner": {"id": 2, "login": "octocat", "type": account_type},
            "private": True,
            "pushed_at": "2026-10-12T14:02:00Z",
            "size": 1200,
            "topics": [],
            "updated_at": "2026-10-12T14:02:00Z",
            "visibility": "private",
        },
    ]
    # Repository contents
    bundle["repos/octocat/payments-service/contents"] = [
        {"name": "Dockerfile", "path": "Dockerfile", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
        {"name": "pyproject.toml", "path": "pyproject.toml", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
        {"name": "src", "path": "src", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "dir"},
        {"name": "README.md", "path": "README.md", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
    ]
    bundle["repos/octocat/customer-portal/contents"] = [
        {"name": "Dockerfile", "path": "Dockerfile", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
        {"name": "package.json", "path": "package.json", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
        {"name": "src", "path": "src", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
        {"name": "README.md", "path": "README.md", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
    ]
    # No teams for personal account
    bundle["users/octocat/teams"] = []
    return bundle


@pytest.fixture
def github_personal(app, monkeypatch):
    """Serve recorded GitHub responses for a personal account (User type)."""
    import requests

    with open(_FIXTURE) as handle:
        bundle = json.load(handle)["responses"]
    # Replace with personal account fixture
    bundle = _make_personal_account_fixture(SimpleNamespace(bundle=bundle), "User")
    state = SimpleNamespace(bundle=bundle, overrides={}, calls=[])
    monkeypatch.setitem(app.config, "ALLOW_EXTERNAL_API_CALLS", True)

    def fake_request(self, method=None, url=None, params=None, headers=None, **kw):
        assert url.startswith(API), f"a request left for an unexpected host: {url}"
        path = url[len(API):].strip("/")
        params = dict(params or {})
        auth = {**self.headers, **(headers or {})}.get("Authorization")
        state.calls.append({"method": method, "path": path, "params": params, "auth": auth})
        page = int(params.get("page", 1))
        for key in (f"{path}?page={page}", path):
            if key in state.overrides:
                return _Resp(*state.overrides[key])
        if path not in state.bundle:
            return _Resp(404, {"message": "Not Found"})
        body = state.bundle[path]
        if path.endswith("/repos") or path.endswith("/teams"):
            per_page = int(params.get("per_page", 30))
            body = body[(page - 1) * per_page : page * per_page]
        return _Resp(200, body)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    return state


@pytest.fixture
def github_unknown_account(app, monkeypatch):
    """Serve GitHub responses for a name that is neither User nor Organization."""
    import requests

    with open(_FIXTURE) as handle:
        bundle = json.load(handle)["responses"]
    # Unknown account type
    bundle["users/unknown"] = {"type": "Bot", "login": "unknown"}
    state = SimpleNamespace(bundle=bundle, overrides={}, calls=[])
    monkeypatch.setitem(app.config, "ALLOW_EXTERNAL_API_CALLS", True)

    def fake_request(self, method=None, url=None, params=None, headers=None, **kw):
        assert url.startswith(API), f"a request left for an unexpected host: {url}"
        path = url[len(API):].strip("/")
        params = dict(params or {})
        auth = {**self.headers, **(headers or {})}.get("Authorization")
        state.calls.append({"method": method, "path": path, "params": params, "auth": auth})
        page = int(params.get("page", 1))
        for key in (f"{path}?page={page}", path):
            if key in state.overrides:
                return _Resp(*state.overrides[key])
        if path not in state.bundle:
            return _Resp(404, {"message": "Not Found"})
        body = state.bundle[path]
        if path.endswith("/repos") or path.endswith("/teams"):
            per_page = int(params.get("per_page", 30))
            body = body[(page - 1) * per_page : page * per_page]
        return _Resp(200, body)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    return state


def test_personal_account_reads_through_user_repos_path_and_produces_proposals(
    db_session, org_a, github_personal, connect, sync
):
    """A name GitHub reports as a User is read through /users/{name}/repos and produces proposals."""
    from app.connectors import github as gh

    # Connect with the personal account name
    cfg = connect(org_a, {"organization": "octocat"})
    result = sync(org_a, cfg)

    assert result["success"] is True, result
    assert result["records_processed"] == 2
    assert result["records_proposed"] == 2  # both become proposals (no existing components to match)
    assert result["records_failed"] == 0

    # Verify the repositories were read from the user path
    user_repos_calls = [c for c in github_personal.calls if c["path"] == "users/octocat/repos"]
    assert user_repos_calls, "should have called /users/octocat/repos"
    org_repos_calls = [c for c in github_personal.calls if c["path"] == "orgs/octocat/repos"]
    assert not org_repos_calls, "should not have called /orgs/octocat/repos"

    # Verify account type was detected
    account_type_calls = [c for c in github_personal.calls if c["path"] == "users/octocat"]
    assert account_type_calls, "should have called /users/octocat to detect account type"

    # Verify teams were not read
    teams_calls = [c for c in github_personal.calls if c["path"] == "users/octocat/teams"]
    assert not teams_calls, "should not have called /users/octocat/teams for a personal account"

    # Verify proposals were created with correct classification
    from app.models.ai_chat_crud_approval import ApprovalStatus
    pending = _proposals(org_a.id, status=ApprovalStatus.PENDING)
    assert len(pending) == 2
    classifications = {_payload(a)["repository"]: _payload(a)["classification"] for a in pending}
    assert classifications == {
        "octocat/payments-service": "application",
        "octocat/customer-portal": "application",
    }


def test_organisation_still_reads_through_org_path_with_teams(
    db_session, org_a, github, connect, sync
):
    """An organisation still reads through /orgs/{name}/repos with its teams."""
    cfg = connect(org_a)
    result = sync(org_a, cfg)

    assert result["success"] is True, result
    assert result["records_processed"] == 12

    # Verify the repositories were read from the org path
    org_repos_calls = [c for c in github.calls if c["path"] == "orgs/acme/repos"]
    assert org_repos_calls, "should have called /orgs/acme/repos"
    user_repos_calls = [c for c in github.calls if c["path"] == "users/acme/repos"]
    assert not user_repos_calls, "should not have called /users/acme/repos for an organisation"

    # Verify teams were read
    teams_calls = [c for c in github.calls if c["path"] == "orgs/acme/teams"]
    assert teams_calls, "should have called /orgs/acme/teams for an organisation"


def test_unknown_account_type_produces_error_message(
    db_session, org_a, github_unknown_account, connect, sync
):
    """A name GitHub reports as neither User nor Organization produces an error message."""
    from app.connectors import github as gh

    cfg = connect(org_a, {"organization": "unknown"})
    result = sync(org_a, cfg)

    assert result["success"] is False
    assert "unexpected account type" in result.get("error", "")
