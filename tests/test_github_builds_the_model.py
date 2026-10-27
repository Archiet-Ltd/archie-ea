"""Connecting GitHub builds a first model of the organisation by itself.

Five steps, each proved on the recorded organisation (``tests/fixtures/github_acme_org.json``, with its
"job0" section: four more repositories, topics, and the text of the files that name links):

1. connect: a read-only token on one page starts the first sync and the page reports it;
2. derive: every repository becomes a proposal with a reason, a commit and an owner, and the links the
   code states (dependency manifests, docker-compose, deploy workflows) become relationship proposals;
3. approve: one action lands everything, each application, part and link citing its repository and commit;
4. answer: "what breaks if <application> fails" is answered from those links with the same citations;
5. re-sync: a second read adds only what is new and changes nothing a person edited.

No test touches the network. The token used here is a plain placeholder.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import text

# The fixtures of the connector suite: recorded GitHub responses, the vault, the sync runner.
from tests.test_entity_history_trigger import _entity_history_trigger  # noqa: F401
from tests.test_event_log import _install_guards  # noqa: F401
from tests.test_github_connector import (  # noqa: F401
    _FIXTURE,
    _proposals,
    _payload,
    _snapshot,
    _user,
    connect,
    github,
    org_a,
    org_b,
    sync,
)

pytestmark = pytest.mark.usefixtures("db_session")

TOKEN = "placeholder-read-only-token"
SHA = re.compile(r"^[0-9a-f]{40}$")


# --------------------------------------------------------------------- #
# The recorded organisation, extended                                    #
# --------------------------------------------------------------------- #


def _entry(name, kind):
    return {"name": name, "path": name, "sha": "a" * 40, "type": kind}


def _file_response(path, body):
    return {
        "content": base64.b64encode(body.encode()).decode(),
        "encoding": "base64",
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "sha": "b" * 40,
        "size": len(body),
        "type": "file",
    }


def extend_with_job0(bundle):
    """Add the "job0" section of the fixture to a private copy of the recorded responses."""
    with open(_FIXTURE) as handle:
        job0 = json.load(handle)["job0"]
    repos = bundle["orgs/acme/repos"]
    template = copy.deepcopy(repos[0])
    template["topics"] = []
    for spec in job0["extra_repositories"]:
        repo = copy.deepcopy(template)
        repo.update(
            id=spec["id"],
            name=spec["name"],
            full_name=f"acme/{spec['name']}",
            html_url=f"https://github.com/acme/{spec['name']}",
            node_id=f"R_{spec['id']}",
            language=spec["language"],
            description=spec.get("description"),
            pushed_at=spec["pushed_at"],
            updated_at=spec["pushed_at"],
        )
        repos.append(repo)
        bundle[f"repos/acme/{spec['name']}/contents"] = [
            _entry(n, "dir" if n in ("src", "cmd", "services", ".github") else "file") for n in spec["root"]
        ]
    for repo in repos:
        if repo["name"] in job0["topics"]:
            repo["topics"] = job0["topics"][repo["name"]]
        repo_id = repo["id"]
        bundle[f"repos/{repo['full_name']}/commits/{repo['default_branch']}"] = {"sha": f"{repo_id:08x}" + "c0de" * 8}
    for path, names in job0["directories"].items():
        org, repo, rest = path.split("/", 2)
        bundle[f"repos/{org}/{repo}/contents/{rest}"] = [
            _entry(n, "file" if "." in n else "dir") for n in names
        ]
    for path, body in job0["files"].items():
        org, repo, rel = path.split("/", 2)
        bundle[f"repos/{org}/{repo}/contents/{rel}"] = _file_response(rel, body)
        folder, _, name = rel.rpartition("/")
        listing_key = f"repos/{org}/{repo}/contents" + (f"/{folder}" if folder else "")
        listing = bundle.setdefault(listing_key, [])
        if not any(item["name"] == name for item in listing):
            listing.append(_entry(name, "file"))
        if folder:  # every folder on the way is listed in its parent
            parts = folder.split("/")
            for depth in range(len(parts)):
                parent = f"repos/{org}/{repo}/contents" + ("/" + "/".join(parts[:depth]) if depth else "")
                items = bundle.setdefault(parent, [])
                if not any(item["name"] == parts[depth] for item in items):
                    items.append(_entry(parts[depth], "dir"))
    return bundle


@pytest.fixture
def acme(github):
    """The recorded organisation with the job0 section applied."""
    extend_with_job0(github.bundle)
    return github


@pytest.fixture
def people(db_session, org_a):
    from tests.test_connector_approval_flow import _make_admin

    return SimpleNamespace(
        one=_user(db_session, org_a, "owner.one@example.test", "Olive", "One"),
        two=_user(db_session, org_a, "person.two@example.test", "Pat", "Two"),
        admin=_make_admin(db_session, org_a.id, "Founder"),
    )


@pytest.fixture
def first_sync(db_session, org_a, people, acme, connect, sync):
    cfg = connect(org_a, token=TOKEN)
    result = sync(org_a, cfg)
    return SimpleNamespace(cfg=cfg, result=result)


def _by_repo(org_id, operation="repository_classification", status=None):
    return [(_payload(a), a) for a in _proposals(org_id, operation, status)]


def _element(org_id, name):
    from app.models import ArchiMateElement

    return ArchiMateElement.query.filter_by(organization_id=org_id, name=name).one()


def _count(db_session, table, org_id, extra=""):
    """Rows of one organisation by plain SQL (a request context left behind by an earlier call
    would narrow an ORM query to its own organisation)."""
    return db_session.execute(
        text(f"SELECT count(*) FROM {table} WHERE organization_id = :o {extra}"), {"o": org_id}
    ).scalar()


def _approve_all(tenant_ctx, db_session, org, admin):
    from app.connectors.github import approve_all_proposals

    db_session.commit()
    with tenant_ctx(org.id):
        return approve_all_proposals(org.id, admin.id)


# --------------------------------------------------------------------- #
# Reading the files: plain rules                                         #
# --------------------------------------------------------------------- #


def test_dependency_manifests_name_their_dependencies():
    from app.connectors.github_model import dependency_names

    assert dependency_names("package.json", '{"dependencies": {"@acme/a": "1", "react": "18"}, "devDependencies": {"vite": "5"}}') == [
        "@acme/a", "react", "vite",
    ]
    assert dependency_names("requirements.txt", "acme-shared==1.0\n# note\n-r other.txt\nrequests[socks]>=2\n") == [
        "acme-shared", "requests",
    ]
    assert dependency_names("pyproject.toml", '[project]\ndependencies = ["acme-core>=1", "httpx"]\n') == ["acme-core", "httpx"]
    assert dependency_names("go.mod", "module github.com/acme/x\n\nrequire (\n\tgithub.com/acme/y v1.2.3\n\tgithub.com/z/q v0.1.0 // indirect\n)\n") == [
        "github.com/acme/y", "github.com/z/q",
    ]
    assert dependency_names("pom.xml", "<dependency><groupId>com.acme</groupId><artifactId>pay</artifactId></dependency>") == ["com.acme:pay"]
    assert dependency_names("build.gradle", "implementation 'com.acme:pay:2.0'\n") == ["com.acme:pay"]
    assert dependency_names("package.json", "not json") == []


def test_a_reference_names_a_repository_of_the_organisation_or_nothing():
    from app.connectors.github_model import match_repository

    repos = {"payments-service": "acme/payments-service", "shared-utils": "acme/shared-utils"}
    match = lambda ref: match_repository(ref, "acme", repos)  # noqa: E731
    assert match("@acme/payments-service") == "acme/payments-service"
    assert match("github.com/acme/shared-utils/v2") == "acme/shared-utils"
    assert match("ghcr.io/acme/payments-service") == "acme/payments-service"
    assert match("com.acme:payments-service") == "acme/payments-service"
    assert match("shared_utils") == "acme/shared-utils"
    assert match("acme-shared-utils") == "acme/shared-utils"
    assert match("@other/payments-service") is None, "another owner's package is not ours"
    assert match("com.other:payments-service") is None
    assert match("react") is None
    assert match("") is None


def test_compose_and_workflow_references():
    from app.connectors.github_model import compose_references, workflow_references

    refs = compose_references(
        "services:\n  web:\n    build: ../web-app\n  api:\n    image: ghcr.io/acme/billing-api:2.0\n  db:\n    image: postgres:16\n"
    )
    assert "ghcr.io/acme/billing-api" in refs and "web-app" in refs and "db" in refs

    deploy = "name: Deploy\njobs:\n  go:\n    uses: acme/worker/.github/workflows/d.yml@main\n"
    assert workflow_references("deploy.yml", deploy, "acme") == ["acme/worker"]
    assert workflow_references("ci.yml", "name: CI\njobs:\n  test:\n    uses: acme/worker/.github/workflows/d.yml@main\n", "acme") == [], (
        "a workflow that does not say deploy names nothing"
    )
    other = "name: Deploy\njobs:\n  go:\n    uses: other/worker/.github/workflows/d.yml@main\n"
    assert workflow_references("deploy.yml", other, "acme") == []


def test_a_part_of_an_application_is_found_by_its_name_or_its_description():
    from app.connectors.github_model import group_from_topics, parent_application

    apps = ["payments-service", "customer-portal", "billing-api", "orders-worker"]
    assert parent_application("payments-worker", None, apps)[0] == "payments-service"
    assert parent_application("customer-portal-ui", None, apps)[0] == "customer-portal"
    assert parent_application("invoice-renderer", "Renders PDF invoices for billing-api", apps)[0] == "billing-api"
    assert parent_application("payments", None, apps)[0] is None, "a name equal to the stem is not a part"
    assert parent_application("mobile-app", "The mobile client", apps)[0] is None
    assert group_from_topics(["python", "product-shop"]) == "Shop"
    assert group_from_topics(["python"]) is None


# --------------------------------------------------------------------- #
# Derive                                                                 #
# --------------------------------------------------------------------- #


def test_the_fixture_proposes_applications_parts_and_links_each_with_a_reason(
    db_session, org_a, first_sync
):
    from app.models.ai_chat_crud_approval import ApprovalStatus

    assert first_sync.result["success"] is True, first_sync.result
    assert first_sync.result["records_processed"] == 16
    assert first_sync.result["records_created"] == 0
    assert first_sync.result["records_failed"] == 0
    assert first_sync.result["relationships_proposed"] == 5

    pending = _by_repo(org_a.id, status=ApprovalStatus.PENDING)
    classes = {}
    for payload, _approval in pending:
        classes.setdefault(payload["classification"], []).append(payload["name"])
        assert payload["reason"], "every proposal says why"
    assert sorted(classes["application"]) == sorted(
        ["Payments Service", "Customer Portal", "Billing Api", "Inventory Service", "Orders Worker", "Mobile App", "Auth", "Gateway"]
    )
    assert sorted(classes["component"]) == ["Customer Portal Ui", "Invoice Renderer", "Payments Worker"]
    assert classes["library"] == ["Shared Utils"]
    assert classes["infrastructure"] == ["Infra Terraform"]
    assert classes["documentation"] == ["Engineering Handbook"]
    assert sorted(classes["ignore"]) == ["Archive 2019", "Legacy Crm", "Old Prototype"]
    assert len(pending) == 17

    by_name = {p["name"]: p for p, _a in pending}
    for payload in by_name.values():
        assert SHA.match(payload["commit"] or ""), payload["name"]
    assert by_name["Orders Worker"]["evidence_file"] == "Dockerfile"
    assert by_name["Payments Worker"]["parent"] == "acme/payments-service"
    assert "payments-service" in by_name["Payments Worker"]["reason"]
    assert by_name["Invoice Renderer"]["parent"] == "acme/billing-api"
    assert by_name["Customer Portal"]["group"] == "Shop"
    assert by_name["Payments Service"]["group"] == "Shop"
    assert by_name["Auth"]["group"] == "Platform Monorepo" and by_name["Auth"]["path"] == "services/auth"
    assert by_name["Gateway"]["repository"] == "acme/platform-monorepo"
    assert "owner.one@example.test" in by_name["Payments Service"]["owners"]
    assert by_name["Payments Service"]["owning_team"] == "acme/payments-team"


def test_the_links_the_code_states_become_relationship_proposals(db_session, org_a, first_sync):
    edges = {
        (p["from"], p["to"], p["kind"]): p for p, _a in _by_repo(org_a.id, "repository_relationship")
    }
    assert set(edges) == {
        ("acme/customer-portal", "acme/payments-service", "dependency"),
        ("acme/customer-portal", "acme/billing-api", "dependency"),
        ("acme/billing-api", "acme/payments-service", "dependency"),
        ("acme/orders-worker", "acme/inventory-service", "dependency"),
        ("acme/payments-service", "acme/payments-worker", "deploys"),
    }, "a dependency on a library (shared-utils) is not a link between modelled things"
    npm = edges[("acme/customer-portal", "acme/payments-service", "dependency")]
    assert npm["file"] == "package.json" and SHA.match(npm["commit"])
    assert npm["relationship_type"] == "Serving" and npm["source_name"] == "Payments Service"
    assert edges[("acme/customer-portal", "acme/billing-api", "dependency")]["file"] == "docker-compose.yml"
    assert edges[("acme/billing-api", "acme/payments-service", "dependency")]["file"] == "pom.xml"
    assert edges[("acme/orders-worker", "acme/inventory-service", "dependency")]["file"] == "go.mod"
    deploy = edges[("acme/payments-service", "acme/payments-worker", "deploys")]
    assert deploy["file"] == ".github/workflows/deploy.yml" and deploy["relationship_type"] == "Association"


def test_nothing_is_in_the_model_until_it_is_approved(db_session, org_a, first_sync):
    from app.models import ArchiMateElement, ArchiMateRelationship

    assert ArchiMateElement.query.filter_by(organization_id=org_a.id).count() == 0
    assert ArchiMateRelationship.query.filter_by(organization_id=org_a.id).count() == 0


# --------------------------------------------------------------------- #
# Approve                                                                #
# --------------------------------------------------------------------- #


@pytest.fixture
def approved(db_session, org_a, people, first_sync, tenant_ctx):
    result = _approve_all(tenant_ctx, db_session, org_a, people.admin)
    return SimpleNamespace(result=result, people=people)


def test_approve_all_lands_the_model_with_the_origin_of_every_fact(db_session, org_a, approved):
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent

    assert approved.result["failed"] == [], approved.result
    assert approved.result["approved"] == 17 + 5

    applications = ArchiMateElement.query.filter_by(organization_id=org_a.id, type="Application").all()
    components = ArchiMateElement.query.filter_by(organization_id=org_a.id, type="ApplicationComponent").all()
    assert len(applications) == 8
    assert sorted(e.name for e in components) == ["Customer Portal Ui", "Invoice Renderer", "Payments Worker"]
    rows = ApplicationComponent.query.filter_by(organization_id=org_a.id).all()
    assert len(rows) == 8, "only an Application has an inventory row"
    assert {r.archimate_element_id for r in rows} == {e.id for e in applications}

    for element in applications + components:
        assert element.source_kind == "connector"
        origin = json.loads(element.source_ref)
        assert origin["system"] == "github" and origin["repository"].startswith("acme/")
        assert SHA.match(origin["commit"])
        assert element.source_label.startswith("GitHub acme/")
        assert element.source_recorded_at is not None
    portal = _element(org_a.id, "Customer Portal")
    assert json.loads(portal.source_ref)["repository"] == "acme/customer-portal"
    assert json.loads(_element(org_a.id, "Auth").source_ref)["repository"] == "acme/platform-monorepo"

    relationships = ArchiMateRelationship.query.filter_by(organization_id=org_a.id).all()
    kinds = sorted(r.type for r in relationships)
    assert kinds == ["Association"] + ["Composition"] * 3 + ["Serving"] * 4
    for rel in relationships:
        assert rel.source_kind == "connector"
        origin = json.loads(rel.source_ref)
        assert SHA.match(origin["commit"]) and origin["repository"].startswith("acme/")
    served = [r for r in relationships if r.type == "Serving"]
    pay = _element(org_a.id, "Payments Service")
    assert sorted(r.target_id for r in served if r.source_id == pay.id) == sorted(
        [_element(org_a.id, "Customer Portal").id, _element(org_a.id, "Billing Api").id]
    ), "the supplier serves its dependents"
    composed = {(r.source_id, r.target_id) for r in relationships if r.type == "Composition"}
    assert (pay.id, _element(org_a.id, "Payments Worker").id) in composed


def test_approved_applications_take_their_owners_from_the_code_owners(db_session, org_a, approved, people):
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    def owners(name):
        element = _element(org_a.id, name)
        row = ApplicationComponent.query.filter_by(archimate_element_id=element.id).one()
        return [(o.user_id, o.ownership_type, o.source_table) for o in ApplicationOwner.query.filter_by(application_id=row.id)]

    assert owners("Payments Service") == [(people.one.id, "primary", "github_connector")]
    assert owners("Inventory Service") == [(people.two.id, "primary", "github_connector")]
    assert owners("Customer Portal") == [], "a team is a fact, never turned into a person"
    portal = _element(org_a.id, "Customer Portal")
    assert portal.acm_properties["github_owning_team"]["value"] == "acme/web-team"
    assert portal.acm_properties["github_main_language"]["value"] == "TypeScript"


def test_a_part_cannot_land_before_its_application(db_session, org_a, people, first_sync, tenant_ctx):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    part = next(a for p, a in _by_repo(org_a.id) if p["name"] == "Payments Worker")
    db_session.commit()
    with tenant_ctx(org_a.id):
        result = AIChatApprovalService(user_id=people.admin.id).approve_and_execute(part.id)
    assert result.get("success") is False
    assert "application it belongs to" in json.dumps(result)


def test_a_changed_classification_is_honoured_by_approve_all(db_session, org_a, people, first_sync, tenant_ctx):
    from app.connectors.github import change_classification
    from app.models import ArchiMateElement

    mobile = next(a for p, a in _by_repo(org_a.id) if p["name"] == "Mobile App")
    with tenant_ctx(org_a.id):
        change_classification(org_a.id, mobile.id, "library", people.admin.id)
    assert _payload(mobile)["classification"] == "library" and "changed" in _payload(mobile)["reason"]
    _approve_all(tenant_ctx, db_session, org_a, people.admin)
    assert ArchiMateElement.query.filter_by(organization_id=org_a.id, name="Mobile App").count() == 0
    assert ArchiMateElement.query.filter_by(organization_id=org_a.id, type="Application").count() == 7


# --------------------------------------------------------------------- #
# Answer                                                                 #
# --------------------------------------------------------------------- #


def test_approve_all_keeps_each_application_and_link_in_the_fact_store(db_session, org_a, approved, make_org):
    from app.integrations.fact_store import sources_for_elements, sources_for_relationships
    from app.models import ArchiMateRelationship

    other = make_org("gh-facts-other")
    pay = _element(org_a.id, "Payments Service")
    source = sources_for_elements([pay.id], org_a.id)[pay.id]
    assert (source["system"], source["item"]) == ("github", "acme/payments-service")
    assert source["url"].startswith("https://github.com/acme/payments-service") and source["read_at"]
    links = ArchiMateRelationship.query.filter_by(organization_id=org_a.id, source_kind="connector").all()
    assert links
    found = sources_for_relationships({link.id for link in links}, org_a.id)
    assert set(found) == {link.id for link in links}
    assert all(s["repository"].startswith("acme/") and s["url"].startswith("https://github.com/acme/") for s in found.values())
    # another organisation is told nothing
    assert sources_for_elements([pay.id], other.id) == {}
    assert sources_for_relationships({link.id for link in links}, other.id) == {}


def _origin_of(citation):
    return citation.get("source") or {}


def test_what_breaks_if_an_application_fails_cites_repositories_and_commits(
    db_session, org_a, approved, tenant_ctx
):
    from app.modules.intelligence.services import answer_envelope

    pay = _element(org_a.id, "Payments Service")
    db_session.commit()
    with tenant_ctx(org_a.id):
        record = answer_envelope.ask_and_store("impact", {"element_id": pay.id, "max_depth": 3})
        body = answer_envelope.present(record)

    names = {e["name"] for e in body["elements"].values()}
    assert {"Customer Portal", "Billing Api", "Payments Worker", "Customer Portal Ui", "Invoice Renderer"} <= names
    assert "Orders Worker" not in names and "Inventory Service" not in names

    cited = [c for c in body["citations"] if c["type"] == "relationship"]
    assert cited, body["citations"]
    origins = [_origin_of(c) for c in cited]
    assert all(o.get("repository", "").startswith("acme/") and SHA.match(o.get("commit", "")) for o in origins)
    npm = [o for o in origins if o["repository"] == "acme/customer-portal" and o["file"] == "package.json"]
    assert len(npm) == 1 and "payments-service" in npm[0]["label"] + npm[0].get("detail", "")
    assert {o["repository"] for o in origins} >= {"acme/customer-portal", "acme/billing-api"}


def test_the_assistant_connector_route_returns_the_same_citations(
    app, db_session, org_a, approved, people, client, login_as
):
    pay = _element(org_a.id, "Payments Service")
    db_session.commit()
    login_as(client, people.admin)
    response = client.get(f"/api/v1/intelligence/impact/{pay.id}?max_depth=3")
    assert response.status_code == 200, response.data[:300]
    body = response.get_json()
    data = body.get("data", body)
    sources = [rel for row in data["rows"] for rel in [row["relation"]] if rel.get("origins")]
    assert sources, "the route carries where each link came from"
    found = {o["repository"] for rel in sources for o in rel["origins"].values()}
    assert {"acme/customer-portal", "acme/billing-api"} <= found
    assert SHA.match(next(iter(sources[0]["origins"].values()))["commit"])


def test_the_assistant_answer_tool_returns_the_citations(db_session, org_a, approved, people, tenant_ctx):
    from app.modules.ai_chat.tools.executor import ToolExecutor

    pay = _element(org_a.id, "Payments Service")
    db_session.commit()
    with tenant_ctx(org_a.id):
        executor = ToolExecutor(user_id=people.admin.id)
        out = executor._tool_answer({"question": "impact", "element_id": pay.id})
    assert out["success"] is True, out
    citations = out["result"]["citations"]
    assert any(_origin_of(c).get("repository") == "acme/customer-portal" for c in citations)


# --------------------------------------------------------------------- #
# Re-sync                                                                #
# --------------------------------------------------------------------- #


def test_a_second_sync_creates_nothing_and_keeps_what_a_person_edited(
    db_session, org_a, approved, people, acme, first_sync, sync, tenant_ctx
):
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    pay = _element(org_a.id, "Payments Service")
    pay.name = "Payments Platform"  # a person renames it
    row = ApplicationComponent.query.filter_by(archimate_element_id=pay.id).one()
    for owner in ApplicationOwner.query.filter_by(application_id=row.id).all():
        db_session.delete(owner)
    db_session.add(
        ApplicationOwner(
            application_id=row.id, user_id=people.admin.id, organization_id=org_a.id, ownership_type="primary",
            assigned_by=people.admin.id,
        )
    )
    db_session.flush()
    before = _snapshot(db_session, org_a.id)
    counts = (
        ArchiMateElement.query.filter_by(organization_id=org_a.id).count(),
        ArchiMateRelationship.query.filter_by(organization_id=org_a.id).count(),
    )

    again = sync(org_a, first_sync.cfg)

    assert again["records_created"] == 0 and again["records_failed"] == 0
    assert again["records_proposed"] == 0 and again["relationships_proposed"] == 0
    after = _snapshot(db_session, org_a.id)
    assert (
        ArchiMateElement.query.filter_by(organization_id=org_a.id).count(),
        ArchiMateRelationship.query.filter_by(organization_id=org_a.id).count(),
    ) == counts
    assert after["components"] == before["components"]
    assert after["owners"] == before["owners"], "the person's owner stays"
    assert db_session.get(ArchiMateElement, pay.id).name == "Payments Platform"
    # The connector offers the code owner as a proposal instead of overwriting the person's choice.
    offered = _proposals(org_a.id, "owner_proposal")
    assert [(_payload(a)["user_id"], _payload(a)["application_id"]) for a in offered] == [(people.one.id, row.id)]


def test_a_second_sync_adds_only_what_is_new(db_session, org_a, approved, acme, first_sync, sync):
    from app.models.ai_chat_crud_approval import ApprovalStatus

    repos = acme.bundle["orgs/acme/repos"]
    fresh = copy.deepcopy(repos[0])
    fresh.update(id=7099, name="notifier", full_name="acme/notifier", html_url="https://github.com/acme/notifier",
                 node_id="R_7099", language="Go", description="Sends notifications", topics=[])
    repos.append(fresh)
    acme.bundle["repos/acme/notifier/contents"] = [_entry("Dockerfile", "file"), _entry("go.mod", "file")]
    acme.bundle["repos/acme/notifier/commits/main"] = {"sha": "00001b63" + "c0de" * 8}
    acme.bundle["repos/acme/notifier/contents/go.mod"] = _file_response(
        "go.mod", "module github.com/acme/notifier\n\nrequire github.com/acme/billing-api v1.0.0\n"
    )

    again = sync(org_a, first_sync.cfg)

    assert again["records_proposed"] == 1 and again["relationships_proposed"] == 1
    new = _by_repo(org_a.id, status=ApprovalStatus.PENDING)
    assert [p["name"] for p, _a in new] == ["Notifier"]
    edge = _by_repo(org_a.id, "repository_relationship", ApprovalStatus.PENDING)
    assert [(p["from"], p["to"]) for p, _a in edge] == [("acme/notifier", "acme/billing-api")]


def test_a_repository_matches_an_existing_application_element(db_session, org_a, people, acme, connect, sync):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.services.archimate_backbone import create_backbone_element

    element = create_backbone_element(
        element_type="Application", layer="Application", name="Payments Service", organization_id=org_a.id
    )
    db_session.add(ApplicationComponent(name="Payments Service", organization_id=org_a.id, archimate_element_id=element.id))
    db_session.flush()

    result = sync(org_a, connect(org_a, token=TOKEN))

    assert result["records_updated"] >= 1
    names = [p["name"] for p, _a in _by_repo(org_a.id, status=ApprovalStatus.PENDING)]
    assert "Payments Service" not in names, "the existing application is matched, not proposed again"
    db_session.refresh(element)
    assert element.acm_properties["github_main_language"]["value"] == "Python"


# --------------------------------------------------------------------- #
# Isolation and the token                                                #
# --------------------------------------------------------------------- #


def test_another_organisation_sees_none_of_it(
    db_session, org_a, org_b, people, acme, first_sync, approved, connect, sync, tenant_ctx
):
    from app.connectors.github import approve_all_proposals, pending_review
    from app.models import ArchiMateElement
    from app.modules.intelligence.services import answer_envelope
    from tests.test_connector_approval_flow import _make_admin

    admin_b = _make_admin(db_session, org_b.id, "Other")
    pay = _element(org_a.id, "Payments Service")
    connect(org_b, token="placeholder-other-token")
    db_session.commit()

    with tenant_ctx(org_b.id):
        assert pending_review(org_b.id)["total"] == 0
        assert approve_all_proposals(org_b.id, admin_b.id)["approved"] == 0
        with pytest.raises(answer_envelope.AnswerNotAskable):
            answer_envelope.ask_and_store("impact", {"element_id": pay.id})
    assert _count(db_session, "archimate_elements", org_b.id) == 0

    sync(org_b, connect(org_b, token="placeholder-other-token"))
    with tenant_ctx(org_b.id):
        review = pending_review(org_b.id)
    assert review["total"] == 17 + 5, "organisation B has its own proposals from its own read"
    assert {a.organization_id for _p, a in _by_repo(org_b.id)} == {org_b.id}
    assert _count(db_session, "archimate_elements", org_a.id) > 0
    assert _count(db_session, "archimate_elements", org_b.id) == 0, "B approved nothing"


def test_the_token_is_in_the_vault_and_in_no_proposal_or_element(db_session, org_a, approved):
    blob = json.dumps(
        [
            tuple(r)
            for r in db_session.execute(
                text(
                    "SELECT operation_payload, summary FROM ai_chat_crud_approvals WHERE organization_id = :o"
                ),
                {"o": org_a.id},
            ).fetchall()
        ]
        + [tuple(r) for r in db_session.execute(
            text("SELECT CAST(acm_properties AS text), CAST(custom_properties AS text), source_ref, source_label "
                 "FROM archimate_elements WHERE organization_id = :o"), {"o": org_a.id}).fetchall()]
        + [tuple(r) for r in db_session.execute(
            text("SELECT source_ref, source_label, description FROM archimate_relationships WHERE organization_id = :o"),
            {"o": org_a.id}).fetchall()],
        default=str,
    )
    assert TOKEN not in blob


# --------------------------------------------------------------------- #
# Connect page, status, review screen                                    #
# --------------------------------------------------------------------- #


@pytest.fixture
def signed_in(client, login_as, people):
    login_as(client, people.admin)
    return client


def test_connect_page_takes_a_token_starts_the_first_sync_and_reports_it(
    app, db_session, org_a, people, acme, signed_in, tenant_ctx, login_as
):
    from app.models.connector_config import ConnectorConfig
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.job_queue_service import get_job_queue_service

    org_id, admin_id = org_a.id, people.admin.id
    page = signed_in.get("/admin/connectors/github")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'name="api_token"' in html and 'type="password"' in html and "read-only" in html.lower()
    assert signed_in.get("/admin/connectors/github/status").get_json()["state"] == "not_connected"

    posted = signed_in.post("/admin/connectors/github", data={"organization": "acme", "api_token": TOKEN})
    assert posted.status_code in (302, 303), posted.data[:300]
    assert TOKEN not in posted.get_data(as_text=True)
    cfg = ConnectorConfig.query.filter_by(organization_id=org_id, connector_type="github").one()
    assert cfg.config == {"organization": "acme"}, "no secret on the configuration row"
    with tenant_ctx(org_id):
        assert OrgCredentialVault().get_masked(org_id, "github", "api_token") is not None

    waiting = signed_in.get("/admin/connectors/github/status").get_json()
    assert waiting["state"] == "waiting" and waiting["organization"] == "acme"
    assert TOKEN not in json.dumps(waiting)

    # The worker picks the job up.
    service = get_job_queue_service()
    job = service.list_jobs(org_id, task="github_first_sync")[0]
    db_session.commit()
    job_id, task, job_payload = job.id, job.task, dict(job.payload)
    service.update_job_status(job_id, "in_progress")
    reading = signed_in.get("/admin/connectors/github/status").get_json()
    assert reading["state"] == "reading"
    outcome = service._execute_task(task, job_payload, job_id)  # what the worker does with the job
    service.update_job_status(job_id, "completed", result=outcome)
    assert outcome["proposals"] == 17 + 5
    login_as(signed_in, admin_id)  # the worker's session was cleared; the page's reader signs in afresh

    done = signed_in.get("/admin/connectors/github/status").get_json()
    assert done["state"] == "done"
    assert done["counts"]["pending"] == 22 and done["counts"]["repositories"] == 16
    assert TOKEN not in json.dumps(done)


def test_the_review_screen_groups_the_proposals_and_approves_them_in_one_action(
    app, db_session, org_a, people, first_sync, signed_in
):
    from app.models import ArchiMateElement

    page = signed_in.get("/admin/connectors/github/review")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for heading in ("Shop", "Platform Monorepo", "Parts of applications", "Links between them", "Not added to the model"):
        assert heading in html, heading
    assert "Approve all" in html and "Payments Service" in html and "package.json" in html
    assert TOKEN not in html

    assert ArchiMateElement.query.filter_by(organization_id=org_a.id).count() == 0
    done = signed_in.post("/admin/connectors/github/review/approve-all")
    assert done.status_code in (302, 303), done.data[:300]
    assert ArchiMateElement.query.filter_by(organization_id=org_a.id, type="Application").count() == 8

    status = signed_in.get("/admin/connectors/github/status").get_json()
    assert status["counts"]["applications"] == 8 and status["counts"]["components"] == 3
    assert status["counts"]["relationships"] == 8 and status["counts"]["pending"] == 0


def test_a_person_can_change_or_skip_one_item_before_approving(
    app, db_session, org_a, people, first_sync, signed_in
):
    from app.models import ArchiMateElement
    from app.models.ai_chat_crud_approval import ApprovalStatus

    mobile = next(a for p, a in _by_repo(org_a.id) if p["name"] == "Mobile App")
    gateway = next(a for p, a in _by_repo(org_a.id) if p["name"] == "Gateway")
    changed = signed_in.post(f"/admin/connectors/github/review/{mobile.id}/classify", data={"classification": "ignore"})
    skipped = signed_in.post(f"/admin/connectors/github/review/{gateway.id}/skip")
    assert changed.status_code in (302, 303) and skipped.status_code in (302, 303)
    refused = signed_in.post(f"/admin/connectors/github/review/{mobile.id}/classify", data={"classification": "toaster"})
    assert refused.status_code in (302, 303, 400)
    db_session.refresh(mobile)
    assert _payload(mobile)["classification"] == "ignore"
    db_session.refresh(gateway)
    assert gateway.status == ApprovalStatus.REJECTED

    signed_in.post("/admin/connectors/github/review/approve-all")
    names = {e.name for e in ArchiMateElement.query.filter_by(organization_id=org_a.id, type="Application")}
    assert "Mobile App" not in names and "Gateway" not in names and "Auth" in names


def test_the_review_screen_of_another_organisation_is_empty(
    app, db_session, org_a, org_b, people, first_sync, client, login_as
):
    from tests.test_connector_approval_flow import _make_admin

    admin_b = _make_admin(db_session, org_b.id, "Other")
    first = next(a for _p, a in _by_repo(org_a.id))
    db_session.commit()
    login_as(client, admin_b)
    html = client.get("/admin/connectors/github/review").get_data(as_text=True)
    assert "Payments Service" not in html and "Approve all" not in html
    assert client.post(f"/admin/connectors/github/review/{first.id}/skip").status_code in (302, 303, 404)
    db_session.refresh(first)
    assert first.status.value == "pending", "another organisation's proposal cannot be skipped"
    assert client.post("/admin/connectors/github/review/approve-all").status_code in (302, 303)
    assert _count(db_session, "ai_chat_crud_approvals", org_a.id, "AND LOWER(CAST(status AS text)) = 'pending' AND operation_type <> 'owner_proposal'") == 22, (
        "organisation A still holds every proposal"
    )
