"""GitHub connector: an organisation's repositories are evidence, not applications.

A repository is a source of facts and of proposals, not a one-to-one copy of an
application: one application can have many repositories, a monorepo can hold
many applications, and libraries, documentation and forks are not applications
at all. So the connector does two things with each repository it reads, and
never creates an application by itself.

Matched repositories add facts. A repository is matched to an existing
application component of the connector's organisation by the identifier
crosswalk (``github`` + ``<organisation>/<repository>``) and, the first time, by
the repository name read as a component name (``payments-service`` is
``payments service``), through ``MatcherService``. A match adds, through the one
element property writer (``PropertyService``): the last push (last activity), the
main language, the archived flag, the code owners and the owning team. The
repository that supplied them and the time it was read are the crosswalk row's
own ``external_id`` and ``last_seen``; there is no second provenance record.
Nothing is renamed or overwritten except these properties.

Owners. Code owners are read from the repository's CODEOWNERS file (first of
``.github/CODEOWNERS``, ``CODEOWNERS``, ``docs/CODEOWNERS``). The owners of the
repository are the owners on its last ``*`` rule; with no ``*`` rule, the owners
that every rule names; otherwise none. The owning team is the one team that has
admin access to the repository; zero or several teams means no owning team. Both
are recorded as facts. A person is written to the one owner record
(``ApplicationOwner``, primary) only when exactly one code owner is an e-mail
address that belongs to exactly one user of the organisation; a team or a
GitHub handle is never turned into a person. A primary owner a person recorded
is never overwritten: the connector queues an ``owner_proposal`` in the approval
queue instead. Unowned repositories stay unowned.

Unmatched repositories become proposals in the approval queue (one per
repository, never repeated once decided), each with a classification decided by
``classify_repository``. Approving an ``application`` proposal creates the
application through the existing application writer; the other classes record
the decision only.

Building the first model. From an empty model the same read derives a whole
first model, still only as proposals (see ``github_model`` for the file rules):

* an ``application`` is a deployable repository (or, in a monorepo, a folder
  under ``apps/`` or ``services/`` with a deployment file); its top-level system
  lands as the Application type, grouped by the system its topics name or by its
  monorepo;
* a ``component`` is a deployable repository that is a part of an application
  (its name starts with the application's, or its description names it); it
  lands as a component of that application, joined by a composition;
* a ``repository_relationship`` proposal is a link the code states between two
  of the organisation's repositories: a dependency manifest naming the other's
  package, a docker-compose service, or a deploy workflow that reaches into it.
  Links to libraries, infrastructure and documentation are not proposed, since
  those are not in the model;
* owners come from the code-owners file and the teams, as above, and are applied
  when the application lands.

Every proposal carries the repository, the file that shows it and the commit it
was read at. When a proposal is approved, the element or relationship records
that as its origin (``source_kind`` ``connector``; ``source_ref`` is JSON naming
the repository, commit and file), and the answers that walk it cite it.
``approve_all_proposals`` lands every pending proposal in dependency order
(applications, parts, the rest, then links); a re-sync proposes only what is new.

A repository that disappears from a complete listing, or is archived, is
flagged on the component (``github_repository_missing``, ``github_archived``);
nothing is deleted. A listing that fails part-way, or is empty, flags nothing.

The token is read from the per-organisation vault, sent only in the request
header and scrubbed from every message the client logs or returns.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.connectors import github_model
from app.models.archimate_element_types import is_application_kind
from app.services.api_clients.github_client import GitHubAPIClient
from app.services.connector_framework import (
    BaseConnector,
    ConnectorType,
    ConnectorWriteRefused,
    FieldMapping,
)

logger = logging.getLogger(__name__)

SOURCE_SYSTEM = "github"
# The approval queue's ``source_table`` for everything this connector queues, and
# the ``source_table`` it stamps on the owner rows it writes (so a row a person
# recorded can be told from one the connector wrote).
PROPOSAL_SOURCE = "github_connector"
OP_CLASSIFY = "repository_classification"
OP_OWNER = "owner_proposal"
OP_RELATE = "repository_relationship"
GITHUB_PROPOSAL_OPERATIONS = frozenset({OP_CLASSIFY, OP_OWNER, OP_RELATE})
CLASSIFICATIONS = ("application", "component", "library", "infrastructure", "documentation", "ignore")
MAX_SUBFOLDERS = 12  # folders read under apps/ and services/ in one monorepo
PROPOSAL_DUE_DAYS = 30

PAGE_SIZE = 100
MAX_PAGES = 50  # 5,000 items per listing
RATE_LIMIT_PER_MINUTE = 80  # 5,000 requests an hour is the authenticated allowance
MAX_CODEOWNERS_BYTES = 200_000

_ORG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def is_valid_github_name(name) -> bool:
    """True for a GitHub organisation or user name that is safe to put in a request path.
    GitHub uses the same login rules for both."""
    return isinstance(name, str) and bool(_ORG_RE.match(name.strip()))


class GitHubConnectorError(RuntimeError):
    """A GitHub read that failed. The message is safe to log and to show: it never
    carries a response body or a credential."""


# --------------------------------------------------------------------- #
# Reading a repository: plain, documented rules (no model call)          #
# --------------------------------------------------------------------- #

_DOC_NAME = re.compile(r"(^|[-_.])(docs?|documentation|wiki|handbook|runbooks?|playbooks?|guides?)([-_.]|$)", re.I)
_INFRA_NAME = re.compile(
    r"(^|[-_.])(infra|infrastructure|terraform|iac|helm|charts?|ansible|k8s|kubernetes|pulumi|cdk)([-_.]|$)", re.I
)
_LIB_NAME = re.compile(
    r"(^|[-_.])(lib|libs|library|sdk|client|clients|common|commons|shared|utils?|core|plugins?)([-_.]|$)", re.I
)
_DOC_TOPICS = {"documentation", "docs", "wiki", "handbook", "runbook"}
_INFRA_TOPICS = {"terraform", "infrastructure", "iac", "helm", "ansible", "kubernetes", "pulumi"}
_LIB_TOPICS = {"library", "sdk", "package"}
_DOC_STRUCTURE = {"docs", "mkdocs.yml", "book.toml", "docusaurus.config.js", "conf.py"}
_DOC_LIKE_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
_INFRA_FILES = {
    "terragrunt.hcl", "chart.yaml", "helmfile.yaml", "ansible.cfg", "kustomization.yaml",
    "pulumi.yaml", "cdk.json", "skaffold.yaml",
}
_DEPLOY_FILES = {
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "procfile",
    "serverless.yml", "serverless.yaml", "app.yaml", "manifest.yml", "fly.toml",
}
_MONOREPO_DIRS = ("apps", "packages", "services", "projects")


def humanise_repository_name(name: str) -> str:
    """``payments-service`` -> ``payments service``: how a repository name is read as a component name."""
    return re.sub(r"[-_.\s]+", " ", name or "").strip()


def _title(name: str) -> str:
    return " ".join(word[:1].upper() + word[1:] for word in humanise_repository_name(name).split())


def classify_repository(repo: Dict[str, Any], root_names: List[str]) -> Tuple[str, str]:
    """Classify a repository as ``application``, ``library``, ``infrastructure``,
    ``documentation`` or ``ignore``, with the reason, from its metadata and the
    names in its root folder. The first rule that applies wins:

    1. ignore: archived, a fork, a template, or empty.
    2. documentation: a name or topic that says so, or no code and only a
       documentation structure (docs/, mkdocs.yml, ...).
    3. infrastructure: a name, topic or file that says so (*.tf, Chart.yaml,
       terragrunt.hcl, ...) or the HCL language.
    4. application: deployment files in the root (Dockerfile, docker-compose,
       Procfile, serverless.yml, ...).
    5. library: a name or topic that says so (lib, sdk, client, common, shared,
       utils, core, plugin, ...).
    6. application: any other repository with code.
    7. ignore: nothing recognisable.

    An application whose root has apps/, packages/, services/ or projects/ is
    noted as a possible monorepo: the parts are for a person to split.
    """
    name = repo.get("name") or ""
    language = repo.get("language")
    topics = {str(t).lower() for t in (repo.get("topics") or [])}
    lower = [n.lower() for n in root_names]

    if repo.get("archived"):
        return "ignore", "the repository is archived"
    if repo.get("fork"):
        return "ignore", "the repository is a fork of another repository"
    if repo.get("is_template"):
        return "ignore", "the repository is a template"
    if not root_names and not repo.get("size"):
        return "ignore", "the repository is empty"

    if _DOC_NAME.search(name) or topics & _DOC_TOPICS:
        return "documentation", "its name or topics describe documentation"
    if (
        language is None
        and set(lower) & _DOC_STRUCTURE
        and all(n in _DOC_STRUCTURE or n.endswith(_DOC_LIKE_SUFFIXES) or n == "license" for n in lower)
    ):
        return "documentation", "it has no code and only a documentation structure"

    infra_file = next((n for n in lower if n in _INFRA_FILES or n.endswith(".tf")), None)
    if _INFRA_NAME.search(name) or topics & _INFRA_TOPICS or infra_file or language == "HCL":
        why = f"it has {infra_file}" if infra_file else "its name, topics or language describe infrastructure"
        return "infrastructure", why

    hint = ""
    parts = [d for d in _MONOREPO_DIRS if d in lower]
    if parts:
        hint = (
            f"; the root has {', '.join(parts)} folders, so it may hold several applications "
            "for a person to split"
        )
    deploy_file = next((n for n in root_names if n.lower() in _DEPLOY_FILES), None)
    if deploy_file:
        return "application", f"it has a deployment file ({deploy_file}){hint}"

    if _LIB_NAME.search(name) or topics & _LIB_TOPICS:
        return "library", "its name or topics describe a library"

    if language is not None:
        return "application", f"it contains {language} code and nothing that marks it as a library, infrastructure or documentation{hint}"
    return "ignore", "no code, infrastructure or documentation was recognised"


def default_code_owners(text: str) -> List[str]:
    """The owners of a repository as a whole, from the text of its CODEOWNERS file.

    The owners on the last ``*`` rule; with no ``*`` rule, the owners that every
    rule names; otherwise none. A rule with no owners clears ownership for its
    pattern, so it counts as naming nobody.
    """
    rules: List[Tuple[str, List[str]]] = []
    for line in (text or "").splitlines():
        body = re.split(r"(?:^|\s)#", line, maxsplit=1)[0].strip()
        if not body:
            continue
        tokens = body.split()
        owners = [t for t in tokens[1:] if "@" in t]
        rules.append((tokens[0], list(dict.fromkeys(owners))))
    stars = [owners for pattern, owners in rules if pattern == "*"]
    if stars:
        return stars[-1]
    if rules and all(owners == rules[0][1] for _p, owners in rules):
        return rules[0][1]
    return []


def execute_github_proposal(approval, data_service=None) -> Dict[str, Any]:
    """Apply one approved proposal of this connector (called by the approval service)."""
    try:
        payload = json.loads(approval.operation_payload or "{}")
    except (TypeError, ValueError):
        return {"success": False, "error": "Invalid GitHub proposal payload"}
    if approval.operation_type == OP_OWNER:
        return _execute_owner_proposal(approval, payload)
    if approval.operation_type == OP_CLASSIFY:
        return _execute_classification(approval, payload, data_service)
    if approval.operation_type == OP_RELATE:
        return _execute_relationship(approval, payload)
    return {"success": False, "error": f"Unsupported GitHub proposal: {approval.operation_type}"}


def _execute_owner_proposal(approval, payload) -> Dict[str, Any]:
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.utils.tenant_users import user_in_org

    org_id = approval.organization_id
    component = ApplicationComponent.query.filter_by(
        id=payload.get("application_id"), organization_id=org_id
    ).first()
    user = user_in_org(payload.get("user_id"), org_id)
    if component is None or user is None or approval.entity_id != component.id:
        return {"success": False, "error": "The application or the person is no longer in this organisation"}
    rows = ApplicationOwner.query.filter_by(
        application_id=component.id, organization_id=org_id, ownership_type="primary"
    ).all()
    for row in rows:
        if row.user_id != user.id:
            db.session.delete(row)
    if not any(row.user_id == user.id for row in rows):
        db.session.add(
            ApplicationOwner(
                application_id=component.id,
                user_id=user.id,
                organization_id=org_id,
                ownership_type="primary",
                assigned_by=approval.approved_by_id,
            )
        )
    db.session.flush()
    return {"success": True, "application_id": component.id, "owner_user_id": user.id}


def _identity(repository: Optional[str], path: Optional[str] = None) -> str:
    """The crosswalk identifier of a repository, or of a folder of a monorepo."""
    return f"{repository}#{path}" if path else str(repository)


_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")


def file_url(repository, commit, file) -> "str | None":
    """The address of a file at a commit on GitHub, built from a stored origin.

    Only parts that are plainly an owner, a repository and a hex commit are used; the path is
    percent-encoded. Anything else gives no address, never a guessed one."""
    parts = str(repository or "").split("/")
    if len(parts) != 2 or not file or not commit or not _SAFE_SHA.match(str(commit)):
        return None
    if any(not _SAFE_NAME.match(p) or set(p) == {"."} for p in parts):
        return None
    segments = [seg for seg in str(file).split("/") if seg not in ("", ".", "..")]
    if not segments:
        return None
    from urllib.parse import quote

    return "https://github.com/%s/%s/blob/%s/%s" % (parts[0], parts[1], commit, "/".join(quote(seg, safe="") for seg in segments))


def origin_ref(repository, commit, file=None, path=None, detail=None) -> str:
    """The JSON a connector-written element or relationship keeps in ``source_ref``."""
    body = {"system": SOURCE_SYSTEM, "repository": repository, "commit": commit, "file": file}
    if path:
        body["path"] = path
    if detail:
        body["detail"] = detail
    return json.dumps(body, sort_keys=True)


def stamp_origin(row, *, repository, commit, file=None, path=None, detail=None) -> None:
    """Record on an element or relationship where it came from: the repository, the
    commit it was read at and the file that shows it (the provenance origin columns)."""
    row.source_kind = "connector"
    row.source_ref = origin_ref(repository, commit, file, path, detail)
    short = f" @ {commit[:7]}" if commit else ""
    where = f"{file}" if file else ""
    row.source_label = f"GitHub {repository}{short}" + (f" ({where})" if where else "")
    row.source_recorded_at = datetime.utcnow()


def _element_for_repository(org_id, repository, path=None):
    """The element a repository (or monorepo folder) became in this organisation, else None."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.external_identity_crosswalk import ExternalIdentityCrosswalk

    if not repository:
        return None
    link = ExternalIdentityCrosswalk.query.filter_by(
        organization_id=org_id, source_system=SOURCE_SYSTEM, external_id=_identity(repository, path)
    ).first()
    if link is None:
        return None
    element = db.session.get(ArchiMateElement, link.element_id)
    if element is None or element.organization_id != org_id or getattr(element, "deleted_at", None):
        return None
    return element


def _person_in_org(org_id, owners: List[str]):
    """The one user of the organisation named by an e-mail code owner, else None."""
    from app.models.user import User

    emails = {o.lower() for o in owners or [] if not o.startswith("@") and "@" in o}
    found = {}
    for email in emails:
        for user in db.session.execute(
            db.select(User).where(db.func.lower(User.email) == email).where(User.organization_id == org_id)
        ).scalars():
            found[user.id] = user
    return next(iter(found.values())) if len(found) == 1 else None


def proposal_blocker(approval) -> Optional[str]:
    """Why a pending proposal cannot be approved yet (said before it is claimed, so it stays
    pending), else None: a part needs its application in the model, a link needs both ends."""
    try:
        payload = json.loads(approval.operation_payload or "{}")
    except (TypeError, ValueError):
        return None
    org_id = approval.organization_id
    if approval.operation_type == OP_CLASSIFY and payload.get("classification") == "component":
        if _element_for_repository(org_id, payload.get("parent")) is None:
            return (
                f"The application it belongs to ({payload.get('parent_name') or payload.get('parent')}) "
                "is not in the model yet. Approve that application first."
            )
    if approval.operation_type == OP_RELATE:
        for end in ("source", "target"):
            if _element_for_repository(org_id, payload.get(end)) is None:
                return f"{payload.get(end)} is not in the model yet, so this link cannot be added. Approve it first."
    return None


def _facts_from_payload(payload) -> Dict[str, Any]:
    facts: Dict[str, Any] = {"github_archived": False}
    if payload.get("last_activity"):
        facts["github_last_activity"] = payload["last_activity"]
    if payload.get("language"):
        facts["github_main_language"] = payload["language"]
    if payload.get("owners"):
        facts["github_code_owners"] = payload["owners"]
    if payload.get("owning_team"):
        facts["github_owning_team"] = payload["owning_team"]
    if payload.get("group"):
        facts["github_group"] = payload["group"]
    return facts


def _execute_classification(approval, payload, data_service) -> Dict[str, Any]:
    classification = payload.get("classification")
    if classification not in ("application", "component"):
        return {"success": True, "acknowledged": True, "classification": classification}
    org_id = approval.organization_id
    path = payload.get("path")
    if _element_for_repository(org_id, payload.get("repository"), path) is not None:
        return {"success": True, "acknowledged": True, "classification": classification, "already_in_model": True}
    if classification == "component":
        return _create_component(approval, payload)
    if data_service is None:
        return {"success": False, "error": "No application writer is available"}
    result = data_service.create_application(
        {"name": payload.get("name"), "description": payload.get("description") or ""}
    )
    if result.get("success"):
        _finish_application(org_id, result.get("application_id"), payload, approval.approved_by_id)
    return result


def _finish_application(org_id, application_id, payload, assigned_by) -> None:
    """Make an approved application what the model means by one: the Application type, its
    origin (repository, commit, file), its crosswalk link, its facts and its owner."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService

    row = ApplicationComponent.query.filter_by(id=application_id, organization_id=org_id).first()
    if row is None or not row.archimate_element_id:
        return
    element = db.session.get(ArchiMateElement, row.archimate_element_id)
    if element is None or element.organization_id != org_id:
        return
    element.type = "Application"  # a top-level system; its parts are components
    if not row.component_type:
        row.component_type = "application"  # what the review showed it to be: a repository classified as an application
    stamp_origin(
        element,
        repository=payload.get("repository"),
        commit=payload.get("commit"),
        file=payload.get("evidence_file"),
        path=payload.get("path"),
    )
    CrosswalkService.write_link(
        SOURCE_SYSTEM, _identity(payload.get("repository"), payload.get("path")), element.id, org_id=org_id
    )
    db.session.flush()
    _write_facts(element, _facts_from_payload(payload))
    _assign_owner_from_payload(org_id, row, payload.get("owners") or [], assigned_by)


def _assign_owner_from_payload(org_id, row, owners, assigned_by) -> None:
    """Make the one person a code owner names the primary owner of a new application."""
    from app.models.application_owner import ApplicationOwner

    user = _person_in_org(org_id, owners)
    if user is None:
        return
    if ApplicationOwner.query.filter_by(application_id=row.id, organization_id=org_id, ownership_type="primary").first():
        return
    db.session.add(
        ApplicationOwner(
            application_id=row.id,
            user_id=user.id,
            organization_id=org_id,
            ownership_type="primary",
            assigned_by=assigned_by,
            source_table=PROPOSAL_SOURCE,
        )
    )
    db.session.flush()


def _create_component(approval, payload) -> Dict[str, Any]:
    """An approved part: a component of its application (no inventory row), joined to it by a composition."""
    from app.models import ArchiMateRelationship
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService
    from app.services.archimate_backbone import create_backbone_element

    org_id = approval.organization_id
    parent = _element_for_repository(org_id, payload.get("parent"))
    if parent is None:
        return {"success": False, "error": proposal_blocker(approval)}
    element = create_backbone_element(
        element_type="ApplicationComponent",
        layer="Application",
        name=payload.get("name") or "",
        description=payload.get("description") or None,
        organization_id=org_id,
    )
    stamp_origin(
        element,
        repository=payload.get("repository"),
        commit=payload.get("commit"),
        file=payload.get("evidence_file"),
        path=payload.get("path"),
    )
    CrosswalkService.write_link(SOURCE_SYSTEM, _identity(payload.get("repository"), payload.get("path")), element.id, org_id=org_id)
    _write_facts(element, _facts_from_payload(payload))
    detail = f"{payload.get('name')} is a part of {parent.name}: {payload.get('reason')}"
    link = ArchiMateRelationship(
        type="Composition",
        source_id=parent.id,
        target_id=element.id,
        organization_id=org_id,
        description=detail[:2000],
    )
    stamp_origin(link, repository=payload.get("repository"), commit=payload.get("commit"), file=payload.get("evidence_file"), detail=detail)
    db.session.add(link)
    db.session.flush()
    return {"success": True, "element_id": element.id, "composition_id": link.id}


def _execute_relationship(approval, payload) -> Dict[str, Any]:
    """An approved link the code states, between two elements that are in the model."""
    from app.models import ArchiMateRelationship

    org_id = approval.organization_id
    blocker = proposal_blocker(approval)
    if blocker:
        return {"success": False, "error": blocker}
    source = _element_for_repository(org_id, payload.get("source"))
    target = _element_for_repository(org_id, payload.get("target"))
    kind = payload.get("relationship_type")
    if kind not in ("Serving", "Association", "Flow"):
        return {"success": False, "error": "Unsupported relationship type"}
    existing = ArchiMateRelationship.query.filter_by(
        organization_id=org_id, source_id=source.id, target_id=target.id, type=kind
    ).first()
    if existing is not None:
        return {"success": True, "acknowledged": True, "relationship_id": existing.id, "already_in_model": True}
    detail = payload.get("detail") or payload.get("summary") or ""
    link = ArchiMateRelationship(
        type=kind,
        source_id=source.id,
        target_id=target.id,
        organization_id=org_id,
        description=detail[:2000] or None,
        custom_properties={"github": {"kind": payload.get("kind"), "from": payload.get("from"), "to": payload.get("to")}},
    )
    stamp_origin(link, repository=payload.get("from"), commit=payload.get("commit"), file=payload.get("file"), detail=detail)
    db.session.add(link)
    db.session.flush()
    return {"success": True, "relationship_id": link.id}


# --------------------------------------------------------------------- #
# The connector                                                          #
# --------------------------------------------------------------------- #


class GitHubConnector(BaseConnector):
    """Reads one GitHub organisation's repositories as evidence for the model."""

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.ALM

    def get_required_config_fields(self) -> List[str]:
        return ["organization", "api_token"]

    def get_field_mappings(self) -> List[FieldMapping]:
        return [
            FieldMapping("full_name", "external_id", required=True),
            FieldMapping("name", "name", required=True),
            FieldMapping("id", "repository_id"),
            FieldMapping("html_url", "url"),
            FieldMapping("description", "description"),
            FieldMapping("language", "language"),
            FieldMapping("pushed_at", "pushed_at"),
            FieldMapping("archived", "archived", default_value=False),
            FieldMapping("fork", "fork", default_value=False),
            FieldMapping("is_template", "is_template", default_value=False),
            FieldMapping("topics", "topics", default_value=[]),
            FieldMapping("size", "size", default_value=0),
        ]

    # ----- configuration and client ------------------------------------

    def _organization(self) -> str:
        organization = (self.config.config or {}).get("organization")
        if not isinstance(organization, str) or not _ORG_RE.match(organization.strip()):
            raise GitHubConnectorError("The GitHub organisation or account name in the connector settings is not valid")
        return organization.strip()

    def _account_type(self, client: GitHubAPIClient) -> str:
        """Detect whether the configured name is a User or Organization.
        Returns 'User' or 'Organization'. Caches the result in config."""
        config = self.config.config or {}
        cached = config.get("account_type")
        if cached in ("User", "Organization"):
            return cached
        organization = self._organization()
        data = self._get(client, f"users/{organization}")
        account_type = data.get("type")
        if account_type not in ("User", "Organization"):
            raise GitHubConnectorError(f"GitHub returned an unexpected account type: {account_type}")
        # Persist the detected type so we don't re-detect on every sync
        # Create a clean config without the token (token is in the vault)
        clean_config = {k: v for k, v in config.items() if k != "api_token"}
        clean_config["account_type"] = account_type
        from app.services.connector_framework import _load_org_connector, apply_connector_change
        from app.services.connector_framework import CONNECTOR_ACTION_UPDATE
        existing = _load_org_connector(getattr(self.config, "organization_id", None), connector_type="github")
        if existing:
            apply_connector_change(
                org_id=getattr(self.config, "organization_id", None),
                action=CONNECTOR_ACTION_UPDATE,
                payload={"connector_type": "github", "connector_id": existing.id, "name": "GitHub", "config": clean_config, "status": "active"},
                delegated=True,
            )
        return account_type

    def _client(self) -> GitHubAPIClient:
        settings = self.config.config or {}
        token = settings.get("api_token")
        if not token:
            raise GitHubConnectorError("No GitHub token is stored for this organisation or account")
        base_url = self._checked_base_url() if settings.get("base_url") else None
        return GitHubAPIClient(api_token=token, base_url=base_url, rate_limit_per_minute=RATE_LIMIT_PER_MINUTE)

    def _failure(self, response) -> GitHubConnectorError:
        status = getattr(response, "status_code", None)
        if status is None:
            return GitHubConnectorError((response.error or "The GitHub request failed")[:300])
        if status == 401:
            return GitHubConnectorError("GitHub rejected the token (HTTP 401)")
        if status in (403, 429):
            return GitHubConnectorError(
                f"GitHub refused the request (HTTP {status}): the token may lack read access "
                "to the organisation or account, or the API rate limit was reached"
            )
        if status == 404:
            return GitHubConnectorError("GitHub could not find it, or the token cannot see it (HTTP 404)")
        return GitHubConnectorError(f"The GitHub request failed (HTTP {status})")

    def _get(self, client: GitHubAPIClient, path: str, params: Optional[dict] = None):
        response = client.get(path, params=params, use_cache=False)
        if not response.success:
            raise self._failure(response)
        return response.data

    def _get_paged(self, client: GitHubAPIClient, path: str) -> List[dict]:
        items: List[dict] = []
        for page in range(1, MAX_PAGES + 1):
            data = self._get(client, path, {"per_page": PAGE_SIZE, "page": page})
            if not isinstance(data, list):
                raise GitHubConnectorError("GitHub returned an unexpected response")
            items.extend(item for item in data if isinstance(item, dict))
            if len(data) < PAGE_SIZE:
                return items
        raise GitHubConnectorError(f"More than {MAX_PAGES * PAGE_SIZE} items were listed; the read was stopped")

    def _admin_teams(self, client, organization: str) -> Dict[str, List[str]]:
        """repository full name -> slugs of the teams that have admin access to it."""
        admins: Dict[str, List[str]] = {}
        for team in self._get_paged(client, f"orgs/{organization}/teams"):
            slug = team.get("slug")
            if not isinstance(slug, str) or not _SLUG_RE.match(slug):
                continue
            for repo in self._get_paged(client, f"orgs/{organization}/teams/{slug}/repos"):
                permissions = repo.get("permissions") or {}
                if (permissions.get("admin") or repo.get("role_name") == "admin") and repo.get("full_name"):
                    admins.setdefault(repo["full_name"], []).append(slug)
        return admins

    # ----- the BaseConnector interface -----------------------------------

    async def test_connection(self) -> bool:
        try:
            client = self._client()
            organization = self._organization()
            account_type = self._account_type(client)
            if account_type == "Organization":
                self._get(client, f"orgs/{organization}")
            else:
                self._get(client, f"users/{organization}")
            return True
        except Exception as exc:  # the reason is ours and safe; the page shows only pass or fail
            logger.warning("GitHub connection test failed: %s", exc)
            return False

    async def incremental_sync(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "ignored", "records_processed": 0, "reason": "GitHub events are not read"}

    async def batch_sync(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """Read every repository of the organisation or account and write what it is evidence for.

        ``since`` is not used: GitHub has no updated-since filter for an
        organisation's repositories, and noticing a deleted repository needs the
        whole listing.
        """
        org_id = getattr(self.config, "organization_id", None)
        if not org_id:
            raise ConnectorWriteRefused("A connector sync cannot write to the model without an organisation.")
        organization = self._organization()
        client = self._client()
        account_type = self._account_type(client)

        if account_type == "Organization":
            repos_path = f"orgs/{organization}/repos"
        else:
            # For a personal account, read public repos via /users/{name}/repos
            # and private repos via /user/repos with affiliation parameter
            repos_path = f"users/{organization}/repos"
        repos = self._get_paged(client, repos_path)
        notes: List[str] = []
        admins: Dict[str, List[str]] = {}
        if account_type == "Organization":
            try:
                admins = self._admin_teams(client, organization)
            except GitHubConnectorError as exc:
                admins = {}
                notes.append(f"Team ownership was not read: {exc}")
        else:
            notes.append("Team ownership is not available for a personal account")

        # Oldest push first, so when several repositories are evidence for one
        # component the most recently active one supplies its facts, every run.
        repos.sort(key=lambda r: (r.get("pushed_at") or "", r.get("full_name") or ""))
        evidence = _RepositoryEvidence(self, client, org_id, organization, admins, repos)
        result = self.persist_records(
            repos,
            source_system=SOURCE_SYSTEM,
            to_element=lambda raw: self._repo_to_record(raw, organization),
            evidence=evidence,
        )
        # The links the code states between the repositories, as proposals.
        try:
            with db.session.begin_nested():
                result["relationships_proposed"] = evidence.propose_relationships()
        except GitHubConnectorError as exc:
            result["relationships_proposed"] = 0
            notes.append(f"Links between repositories were not read: {exc}")
        listed = {r.get("full_name", "").lower() for r in repos}
        result["records_flagged_missing"] = self._flag_missing(org_id, organization, listed)
        if notes:
            if result["status"] == "completed":
                result["status"] = "partial"
            result["error_message"] = "; ".join(filter(None, [result.get("error_message"), *notes]))
        return result

    def _repo_to_record(self, raw: Dict[str, Any], organization: str) -> Dict[str, Any]:
        mapped = self._apply_field_mappings(raw)
        full_name = str(mapped["external_id"])
        if not _REPO_RE.match(full_name) or full_name.split("/")[0].lower() != organization.lower():
            raise ValueError("the repository name is not valid for this organisation")
        name = humanise_repository_name(mapped["name"])
        if not name:
            raise ValueError("the repository has no name")
        return {
            "external_id": full_name,
            "name": name,
            "element_type": "ApplicationComponent",
            "description": None,
            "repo": mapped,
        }

    def _flag_missing(self, org_id: int, organization: str, listed: set) -> int:
        """Flag components whose repository was in an earlier read but not in this complete one."""
        if not listed:
            return 0  # an empty listing is more likely lost visibility than a deleted organisation
        from app.models.archimate_core import ArchiMateElement
        from app.models.external_identity_crosswalk import ExternalIdentityCrosswalk

        flagged = 0
        links = ExternalIdentityCrosswalk.query.filter_by(organization_id=org_id, source_system=SOURCE_SYSTEM).all()
        for link in links:
            if not link.external_id.lower().startswith(organization.lower() + "/"):
                continue
            if link.external_id.lower() in listed:
                continue
            element = db.session.get(ArchiMateElement, link.element_id)
            if element is None or element.organization_id != org_id or getattr(element, "deleted_at", None):
                continue
            if _write_facts(element, {"github_repository_missing": True}):
                flagged += 1
        return flagged


def _write_facts(element, updates: Dict[str, Any]) -> bool:
    """Write the facts that differ from what the element already holds, through the one
    element property writer. Returns True when anything changed."""
    from app.modules.architecture_assistant.property_service import PropertyService

    current = element.acm_properties or {}
    changed = {}
    for key, value in updates.items():
        entry = current.get(key)
        if not isinstance(entry, dict) or entry.get("value") != value:
            changed[key] = value
    if not changed:
        return False
    PropertyService().merge_element_properties(element, changed, source=SOURCE_SYSTEM)
    db.session.flush()
    return True


class _RepositoryEvidence:
    """What the persistence step does with a matched and with an unmatched repository."""

    def __init__(
        self,
        connector: GitHubConnector,
        client,
        org_id: int,
        organization: str,
        admins: Dict[str, List[str]],
        repos: Optional[List[Dict[str, Any]]] = None,
    ):
        self.connector = connector
        self.client = client
        self.org_id = org_id
        self.organization = organization
        self.admins = admins
        self.repos = {r["full_name"]: r for r in (repos or []) if isinstance(r.get("full_name"), str)}
        self.names = {github_model.repo_key(r.get("name", "")): full for full, r in self.repos.items()}
        self.matched_repos: set = set()
        self._roots: Dict[str, List[str]] = {}
        self._commits: Dict[str, Optional[str]] = {}
        self._dirs: Dict[Tuple[str, str], List[dict]] = {}
        self._texts: Dict[Tuple[str, str], Optional[str]] = {}
        self._derived: Dict[str, Dict[str, Any]] = {}

    # ----- reading more of a repository (all cached, all read-only) -------

    def _commit(self, full_name: str) -> Optional[str]:
        """The commit the default branch is at: what every fact read from this repository is cited to."""
        if full_name not in self._commits:
            repo = self.repos.get(full_name) or {}
            branch = repo.get("default_branch") or "main"
            sha = None
            if isinstance(branch, str) and re.match(r"^[A-Za-z0-9._/-]+$", branch):
                response = self.client.get(f"repos/{full_name}/commits/{branch}", use_cache=False)
                if response.success and isinstance(response.data, dict):
                    value = response.data.get("sha")
                    sha = value if isinstance(value, str) and re.match(r"^[0-9a-f]{40}$", value) else None
                elif getattr(response, "status_code", None) not in (404, 409, 422):
                    raise self.connector._failure(response)
            self._commits[full_name] = sha
        return self._commits[full_name]

    def _list_dir(self, full_name: str, path: str) -> List[dict]:
        """The entries of one folder (name and type); an absent folder is empty."""
        key = (full_name, path)
        if key not in self._dirs:
            response = self.client.get(f"repos/{full_name}/contents/{path}", use_cache=False)
            if response.success and isinstance(response.data, list):
                self._dirs[key] = [i for i in response.data if isinstance(i, dict) and i.get("name")]
            elif getattr(response, "status_code", None) == 404:
                self._dirs[key] = []
            else:
                raise self.connector._failure(response)
        return self._dirs[key]

    def _text(self, full_name: str, path: str) -> Optional[str]:
        """The text of one file, or None when it is absent or not text."""
        key = (full_name, path)
        if key not in self._texts:
            response = self.client.get(f"repos/{full_name}/contents/{path}", use_cache=False)
            text = None
            if response.success and isinstance(response.data, dict):
                data = response.data
                if data.get("encoding") == "base64" and isinstance(data.get("content"), str):
                    raw = base64.b64decode(data["content"])[: github_model.MAX_FILE_CHARS]
                    text = raw.decode("utf-8", errors="replace")
            elif getattr(response, "status_code", None) != 404:
                raise self.connector._failure(response)
            self._texts[key] = text
        return self._texts[key]

    @staticmethod
    def _evidence_file(root: List[str]) -> Optional[str]:
        """The file in the root folder that most plainly shows how a repository is deployed or built."""
        lower = {n.lower(): n for n in root}
        for name in sorted(_DEPLOY_FILES):
            if name in lower:
                return lower[name]
        for name in github_model.DEPENDENCY_MANIFESTS:
            if name.lower() in lower:
                return lower[name.lower()]
        return None

    # ----- deriving what a repository is -----------------------------------

    def _sub_applications(self, full_name: str, root: List[str]) -> List[Dict[str, Any]]:
        """Folders under apps/ and services/ that hold a deployment file: the applications of a monorepo."""
        found: List[Dict[str, Any]] = []
        names = {n.lower(): n for n in root}
        for top in ("apps", "services"):
            if top not in names:
                continue
            folders = [e for e in self._list_dir(full_name, names[top]) if e.get("type") == "dir"]
            for entry in folders[:MAX_SUBFOLDERS]:
                path = f"{names[top]}/{entry['name']}"
                inside = [str(i["name"]) for i in self._list_dir(full_name, path)]
                deploy = next((n for n in inside if n.lower() in _DEPLOY_FILES), None)
                if deploy:
                    found.append({"path": path, "name": _title(str(entry["name"])), "file": f"{path}/{deploy}"})
        return found

    def _derive(self, full_name: str) -> Dict[str, Any]:
        """What the plain rules make of one repository: its class and reason, its monorepo
        applications, and the application it is a part of."""
        if full_name in self._derived:
            return self._derived[full_name]
        repo = self.repos.get(full_name) or {}
        root = self._root(full_name)
        classification, reason = classify_repository(repo, root)
        info: Dict[str, Any] = {
            "classification": classification, "reason": reason, "subs": [], "parent": None, "parent_reason": None,
            "root": root,
        }
        self._derived[full_name] = info  # set early: the parent search reads other repositories
        try:
            self._derive_structure(full_name, repo, root, classification, info)
        except GitHubConnectorError:
            self._derived.pop(full_name, None)  # never cache half an answer
            raise
        return info

    def _derive_structure(self, full_name, repo, root, classification, info) -> None:
        """The monorepo folders and the parent application of one repository, added to *info*."""
        if classification == "application":
            info["subs"] = self._sub_applications(full_name, root)
        if classification == "application" and not info["subs"]:
            candidates = [
                other for other in self.repos
                if other != full_name and self._stands_alone(other)
            ]
            parent, why = github_model.parent_application(
                repo.get("name", ""), repo.get("description"),
                [self.repos[o]["name"] for o in candidates],
            )
            if parent:
                parent_full = self.names.get(github_model.repo_key(parent))
                for _ in range(3):  # a part of a part belongs to the top-level application
                    above = self._derive(parent_full).get("parent") if parent_full else None
                    if not above:
                        break
                    parent_full = above
                if parent_full and parent_full != full_name:
                    info.update(classification="component", parent=parent_full, parent_reason=why,
                                reason=f"it is deployable and {why}")

    def _stands_alone(self, full_name: str) -> bool:
        """``_base_application``, where a repository that cannot be read is simply not one: one
        unreadable repository fails its own record, never another repository's."""
        try:
            return self._base_application(full_name)
        except GitHubConnectorError as exc:
            logger.warning("GitHub repository %s could not be read for its parts (%s)", full_name, exc)
            return False

    def _base_application(self, full_name: str) -> bool:
        """True for a repository that is a deployable application of its own (not a monorepo of several)."""
        repo = self.repos.get(full_name) or {}
        classification, _why = classify_repository(repo, self._root(full_name))
        if classification != "application":
            return False
        return not self._sub_applications(full_name, self._root(full_name))

    def _modelled(self, full_name: str) -> bool:
        """True when a repository is, or will be proposed as, an application or a part in the model."""
        if full_name in self.matched_repos:
            return True
        if full_name not in self.repos:
            return False
        info = self._derive(full_name)
        return info["classification"] in ("application", "component") and not info["subs"]

    # ----- reading a repository -----------------------------------------

    def _root(self, full_name: str) -> List[str]:
        if full_name not in self._roots:
            response = self.client.get(f"repos/{full_name}/contents", use_cache=False)
            if response.success and isinstance(response.data, list):
                self._roots[full_name] = [str(i.get("name")) for i in response.data if isinstance(i, dict) and i.get("name")]
            elif getattr(response, "status_code", None) == 404:
                self._roots[full_name] = []  # an empty repository answers 404
            else:
                raise self.connector._failure(response)
        return self._roots[full_name]

    def _code_owners(self, full_name: str, root: List[str]) -> List[str]:
        locations = [
            path
            for path, folder in ((".github/CODEOWNERS", ".github"), ("CODEOWNERS", "CODEOWNERS"), ("docs/CODEOWNERS", "docs"))
            if folder in root
        ]
        for path in locations:
            response = self.client.get(f"repos/{full_name}/contents/{path}", use_cache=False)
            if not response.success:
                if getattr(response, "status_code", None) == 404:
                    continue
                raise self.connector._failure(response)
            data = response.data if isinstance(response.data, dict) else {}
            if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
                continue
            raw = base64.b64decode(data["content"])[:MAX_CODEOWNERS_BYTES]
            return default_code_owners(raw.decode("utf-8", errors="replace"))
        return []

    def _owning_team(self, full_name: str) -> Optional[str]:
        teams = self.admins.get(full_name, [])
        return f"{self.organization}/{teams[0]}" if len(teams) == 1 else None

    # ----- matched: add facts -------------------------------------------

    def matched(self, element, record: Dict[str, Any]) -> bool:
        repo, full_name = record["repo"], record["external_id"]
        self.matched_repos.add(full_name)
        root = self._root(full_name)
        owners = self._code_owners(full_name, root)

        facts: Dict[str, Any] = {"github_archived": bool(repo.get("archived"))}
        if repo.get("pushed_at"):
            facts["github_last_activity"] = repo["pushed_at"]
        if repo.get("language"):
            facts["github_main_language"] = repo["language"]
        if owners:
            facts["github_code_owners"] = owners
        team = self._owning_team(full_name)
        if team:
            facts["github_owning_team"] = team
        if (element.acm_properties or {}).get("github_repository_missing", {}).get("value"):
            facts["github_repository_missing"] = False

        changed = _write_facts(element, facts)
        return self._apply_owner(element, full_name, owners) or changed

    def _person_for(self, owners: List[str]):
        """The one user of the organisation named by an e-mail code owner, else None."""
        return _person_in_org(self.org_id, owners)

    def _apply_owner(self, element, full_name: str, owners: List[str]) -> bool:
        from app.models.application_owner import ApplicationOwner
        from app.models.application_portfolio import ApplicationComponent

        user = self._person_for(owners)
        if user is None:
            return False
        component = ApplicationComponent.query.filter_by(
            archimate_element_id=element.id, organization_id=self.org_id
        ).first()
        if component is None:
            return False
        primaries = ApplicationOwner.query.filter_by(
            application_id=component.id, organization_id=self.org_id, ownership_type="primary"
        ).all()
        if any(row.user_id == user.id for row in primaries):
            return False
        if any(row.source_table != PROPOSAL_SOURCE for row in primaries):
            self._propose_owner(component, user, full_name)  # a person's choice is never overwritten
            return False
        for row in primaries:
            db.session.delete(row)
        db.session.add(
            ApplicationOwner(
                application_id=component.id,
                user_id=user.id,
                organization_id=self.org_id,
                ownership_type="primary",
                source_table=PROPOSAL_SOURCE,
            )
        )
        db.session.flush()
        return True

    # ----- proposals ----------------------------------------------------

    def _propose_owner(self, component, user, full_name: str) -> None:
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval
        from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

        earlier = AIChatCRUDApproval.query.filter_by(
            organization_id=self.org_id, source_table=PROPOSAL_SOURCE, operation_type=OP_OWNER, entity_id=component.id
        ).all()
        if any(json.loads(a.operation_payload).get("user_id") == user.id for a in earlier):
            return  # proposed before; whatever was decided stands
        person = " ".join(p for p in (user.first_name, user.last_name) if p) or user.email
        create_approval_record(
            organization_id=self.org_id,
            operation_type=OP_OWNER,
            entity_type="application",
            entity_id=component.id,
            summary=(
                f"{person} is named as code owner of {full_name} on GitHub. "
                f"Make them the primary owner of {component.name}?"
            ),
            operation_payload={
                "application_id": component.id,
                "user_id": user.id,
                "user_email": user.email,
                "repository": full_name,
            },
            source_table=PROPOSAL_SOURCE,
            expiry_minutes=PROPOSAL_DUE_DAYS * 24 * 60,
        )

    def unmatched(self, record: Dict[str, Any], match) -> str:
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval
        from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

        repo, full_name = record["repo"], record["external_id"]
        repository_id = repo.get("repository_id")
        if not isinstance(repository_id, int):
            raise ValueError("the repository has no id")
        earlier = AIChatCRUDApproval.query.filter(
            AIChatCRUDApproval.organization_id == self.org_id,
            AIChatCRUDApproval.source_table == PROPOSAL_SOURCE,
            AIChatCRUDApproval.operation_type == OP_CLASSIFY,
        ).all()
        # source_id cannot hold a repository id (its unique key spans organisations and a
        # monorepo has several proposals), so the id is compared as a parsed value, never as text.
        proposed_paths = set()
        for earlier_proposal in earlier:
            try:
                earlier_payload = json.loads(earlier_proposal.operation_payload or "{}")
            except (TypeError, ValueError):
                continue
            if earlier_payload.get("repository_id") == repository_id:
                proposed_paths.add(earlier_payload.get("path"))

        info = self._derive(full_name)
        classification, reason = info["classification"], info["reason"]
        root = info["root"]
        if info["subs"]:
            # A monorepo is not one application: each folder that deploys is.
            entries = [
                {"path": sub["path"], "name": sub["name"], "classification": "application",
                 "reason": f"{sub['path']} has a deployment file ({sub['file'].rsplit('/', 1)[-1]}) in {full_name}",
                 "evidence_file": sub["file"], "group": _title(repo.get("name") or "")}
                for sub in info["subs"]
            ]
        else:
            entries = [
                {"path": None, "name": _title(repo.get("name") or ""), "classification": classification,
                 "reason": reason, "evidence_file": self._evidence_file(root),
                 "group": github_model.group_from_topics(repo.get("topics") or [])}
            ]
        entries = [e for e in entries if e["path"] not in proposed_paths]
        if not entries:
            return "skipped"  # proposed before, pending or decided: never asked twice

        commit = self._commit(full_name)
        owners = self._code_owners(full_name, root)
        team = self._owning_team(full_name)
        parent_full = info["parent"]
        parent_name = _title(self.repos[parent_full].get("name") or "") if parent_full in self.repos else None
        for entry in entries:
            kind = entry["classification"]
            payload = {
                "repository_id": repository_id,
                "repository": full_name,
                "url": repo.get("url"),
                "classification": kind,
                "reason": entry["reason"],
                "name": entry["name"],
                "description": repo.get("description") or "",
                "language": repo.get("language"),
                "last_activity": repo.get("pushed_at"),
                "possible_match": match.matched_name if getattr(match, "matched_element_id", None) else None,
                "commit": commit,
                "evidence_file": entry["evidence_file"],
                "path": entry["path"],
                "group": entry["group"],
                "parent": parent_full if kind == "component" else None,
                "parent_name": parent_name if kind == "component" else None,
                "owners": owners,
                "owning_team": team,
            }
            if kind == "application":
                outcome = "Approving adds it as an application."
            elif kind == "component":
                outcome = f"Approving adds it as a part of {parent_name}."
            else:
                outcome = "Approving records the decision; nothing is added to the model."
            where = f"{full_name}/{entry['path']}" if entry["path"] else full_name
            create_approval_record(
                organization_id=self.org_id,
                operation_type=OP_CLASSIFY,
                entity_type="repository",
                summary=f"GitHub repository {where} looks like {_a(kind)}: {entry['reason']}. {outcome}",
                operation_payload=payload,
                source_table=PROPOSAL_SOURCE,
                expiry_minutes=PROPOSAL_DUE_DAYS * 24 * 60,
            )
        return "proposed"

    # ----- links between repositories ------------------------------------

    def _edges_from(self, full_name: str) -> List[Dict[str, Any]]:
        """The links one repository's files state to other repositories of the organisation."""
        root = self._root(full_name)
        lower = {n.lower(): n for n in root}
        found: List[Dict[str, Any]] = []

        def add(target, kind, relationship_type, file, detail):
            if target and target != full_name:
                found.append({"from": full_name, "to": target, "kind": kind, "relationship_type": relationship_type,
                              "file": file, "detail": detail})

        for manifest in github_model.DEPENDENCY_MANIFESTS:
            if manifest.lower() not in lower:
                continue
            text = self._text(full_name, lower[manifest.lower()])
            for dependency in github_model.dependency_names(manifest, text or ""):
                target = github_model.match_repository(dependency, self.organization, self.names)
                add(target, "dependency", "Serving", lower[manifest.lower()], f"{manifest} names {dependency}")
        for compose in github_model.COMPOSE_FILES:
            if compose.lower() not in lower:
                continue
            text = self._text(full_name, lower[compose.lower()])
            for reference in github_model.compose_references(text or ""):
                target = github_model.match_repository(reference, self.organization, self.names)
                add(target, "dependency", "Serving", lower[compose.lower()], f"{compose} runs {reference}")
        if ".github" in lower:
            workflows = [
                e for e in self._list_dir(full_name, f"{lower['.github']}/workflows")
                if str(e.get("name", "")).lower().endswith((".yml", ".yaml"))
            ][: github_model.MAX_WORKFLOWS]
            for entry in workflows:
                path = f"{lower['.github']}/workflows/{entry['name']}"
                text = self._text(full_name, path)
                for reference in github_model.workflow_references(str(entry["name"]), text or "", self.organization):
                    target = github_model.match_repository(reference, self.organization, self.names)
                    add(target, "deploys", "Association", path, f"{entry['name']} deploys {reference}")
        return found

    def propose_relationships(self) -> int:
        """Queue one proposal per link the code states between two repositories that are (or will be)
        in the model. A link already proposed, pending or decided, is never proposed again."""
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval
        from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

        known = {
            (p.get("from"), p.get("to"), p.get("kind"))
            for p in (
                json.loads(a.operation_payload or "{}")
                for a in AIChatCRUDApproval.query.filter_by(
                    organization_id=self.org_id, source_table=PROPOSAL_SOURCE, operation_type=OP_RELATE
                ).all()
            )
        }
        created = 0
        for full_name in self.repos:
            try:
                if not self._modelled(full_name):
                    continue
                edges = self._edges_from(full_name)
            except GitHubConnectorError as exc:  # this repository's files could not be read: no links from it
                logger.warning("GitHub repository %s could not be read for links (%s)", full_name, exc)
                continue
            for edge in edges:
                key = (edge["from"], edge["to"], edge["kind"])
                try:
                    if key in known or not self._modelled(edge["to"]):
                        continue
                except GitHubConnectorError:
                    continue
                known.add(key)
                # "A depends on B" is B serving A; a deploy link is read from the deployer to what it deploys.
                supplier, consumer = (edge["to"], edge["from"]) if edge["kind"] == "dependency" else (edge["from"], edge["to"])
                names = {r: _title(self.repos[r].get("name") or "") for r in (supplier, consumer) if r in self.repos}
                if edge["kind"] == "dependency":
                    summary = f"{edge['from']} depends on {edge['to']} ({edge['detail']})."
                else:
                    summary = f"{edge['from']} deploys {edge['to']} ({edge['detail']})."
                payload = dict(
                    edge,
                    source=supplier,
                    target=consumer,
                    source_name=names.get(supplier),
                    target_name=names.get(consumer),
                    commit=self._commit(edge["from"]),
                    summary=summary,
                )
                create_approval_record(
                    organization_id=self.org_id,
                    operation_type=OP_RELATE,
                    entity_type="repository_link",
                    summary=summary + " Approving adds the link to the model.",
                    operation_payload=payload,
                    source_table=PROPOSAL_SOURCE,
                    expiry_minutes=PROPOSAL_DUE_DAYS * 24 * 60,
                )
                created += 1
        return created


def _a(classification: str) -> str:
    return ("an " if classification[:1] in "aeiou" else "a ") + classification


# --------------------------------------------------------------------- #
# The review queue: grouped, approved in one action, changed per item    #
# --------------------------------------------------------------------- #

_OTHER_CLASSES = ("library", "infrastructure", "documentation", "ignore")
_APPROVE_ORDER = {"application": 0, "component": 1}  # then the decisions that add nothing, then links


def _pending(org_id, operation_type=None):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    query = AIChatCRUDApproval.query.filter_by(
        organization_id=org_id, source_table=PROPOSAL_SOURCE, status=ApprovalStatus.PENDING
    )
    if operation_type is not None:
        query = query.filter_by(operation_type=operation_type)
    return query.order_by(AIChatCRUDApproval.id).all()


def _item(approval) -> Dict[str, Any]:
    payload = json.loads(approval.operation_payload or "{}")
    commit = payload.get("commit") or ""
    return dict(payload, id=approval.id, short_commit=commit[:7], operation_type=approval.operation_type)


def pending_review(org_id) -> Dict[str, Any]:
    """The pending proposals of one organisation, grouped for the review screen:
    applications by system, parts, links, and the decisions that add nothing to the
    model. ``total`` is what approve-all would approve (owner changes are separate)."""
    classified = [_item(a) for a in _pending(org_id, OP_CLASSIFY)]
    links = [_item(a) for a in _pending(org_id, OP_RELATE)]
    owners = [_item(a) for a in _pending(org_id, OP_OWNER)]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in classified:
        if item.get("classification") == "application":
            groups.setdefault(item.get("group") or "Other applications", []).append(item)
    ordered = sorted(groups.items(), key=lambda kv: (kv[0] == "Other applications", kv[0].lower()))
    return {
        "applications": [{"group": name, "items": items} for name, items in ordered],
        "parts": [i for i in classified if i.get("classification") == "component"],
        "links": links,
        "others": [i for i in classified if i.get("classification") in _OTHER_CLASSES],
        "owner_changes": owners,
        "total": len(classified) + len(links),
        "applications_total": sum(len(items) for items in groups.values()),
    }


def approve_all_proposals(org_id, approver_id) -> Dict[str, Any]:
    """Approve every pending repository and link proposal of the organisation, applications
    first, then their parts, then the decisions, then the links, each through the one
    approval service (so the approver, audit trail and self-approval rule are the same as
    for a single approval). Owner changes are left for a person to decide one by one.
    A proposal that cannot land yet stays pending and is reported with the reason."""
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    items = _pending(org_id, OP_CLASSIFY) + _pending(org_id, OP_RELATE)

    def rank(approval):
        if approval.operation_type == OP_RELATE:
            return 3
        kind = json.loads(approval.operation_payload or "{}").get("classification")
        return _APPROVE_ORDER.get(kind, 2)

    items.sort(key=lambda a: (rank(a), a.id))
    service = AIChatApprovalService(user_id=approver_id)
    ids = [a.id for a in items]
    approved, failed = 0, []
    for approval_id in ids:
        result = service.approve_and_execute(approval_id)
        if result.get("success"):
            approved += 1
        else:
            failed.append({"id": approval_id, "error": str(result.get("error") or "not approved")[:300]})
    return {"approved": approved, "failed": failed, "total": len(ids)}


def change_classification(org_id, approval_id, classification, user_id=None) -> Dict[str, Any]:
    """Change what one pending repository proposal says it is, before it is approved."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    if classification not in CLASSIFICATIONS:
        raise ValueError("That is not something a repository can be classified as.")
    approval = AIChatCRUDApproval.query.filter_by(
        id=approval_id, organization_id=org_id, source_table=PROPOSAL_SOURCE, operation_type=OP_CLASSIFY,
        status=ApprovalStatus.PENDING,
    ).first()
    if approval is None:
        raise LookupError("That proposal is not waiting for a decision in this organisation.")
    payload = json.loads(approval.operation_payload or "{}")
    if classification == "component" and not payload.get("parent"):
        raise ValueError("A part needs the application it belongs to; this repository has none.")
    before = payload.get("classification")
    if before != classification:
        payload["classification"] = classification
        payload["reason"] = f"{payload.get('reason')}; changed from {before} to {classification} by a person"
        approval.operation_payload = json.dumps(payload)
        db.session.flush()
    return payload


def skip_proposal(org_id, approval_id, user_id) -> Dict[str, Any]:
    """Decline one pending proposal (it is not asked again)."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    approval = AIChatCRUDApproval.query.filter_by(
        id=approval_id, organization_id=org_id, source_table=PROPOSAL_SOURCE
    ).first()
    if approval is None:
        raise LookupError("That proposal is not in this organisation.")
    return AIChatApprovalService(user_id=user_id).reject_approval(approval_id, "Skipped on the GitHub review screen")


# --------------------------------------------------------------------- #
# Connect: the first sync, and what the page reports about it            #
# --------------------------------------------------------------------- #

FIRST_SYNC_TASK = "github_first_sync"


def run_first_sync(org_id, connector_id) -> Dict[str, Any]:
    """Run the connector's read for one organisation (the job the connect page starts and the
    re-sync button restarts). Returns counts only: no repository content and no credential."""
    from app.jobs.tenant_safe_job import tenant_scope
    from app.services.connector_framework import run_connector_sync

    with tenant_scope(org_id):
        result = run_connector_sync(org_id=org_id, connector_id=connector_id, delegated=True)
        db.session.commit()
    return {
        "success": bool(result.get("success")),
        "repositories": result.get("records_processed", 0),
        "proposals": pending_total(org_id),
        "relationships_proposed": result.get("relationships_proposed", 0),
        "failed": result.get("records_failed", 0),
        "error": (result.get("error") or result.get("error_message") or None) if not result.get("success") else None,
    }


class ConnectRefused(ValueError):
    """The connection was not saved; the message says why, in words a person can act on."""


def connect_and_start(org_id, organization, token, *, task=None, extra=None) -> int:
    """Store the read-only token in the organisation's vault, save the connection and queue the
    first read. Returns the connector id. The one place a GitHub connection is made, for the
    connectors page and for sign-up. ``task`` and ``extra`` let sign-up queue the read together
    with approving what it finds; the token goes to the vault and nowhere else."""
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.connector_framework import (
        CONNECTOR_ACTION_CREATE,
        CONNECTOR_ACTION_UPDATE,
        _load_org_connector,
        apply_connector_change,
    )
    from app.services.job_queue_service import get_job_queue_service

    organization = (organization or "").strip()
    token = (token or "").strip()
    existing = _load_org_connector(org_id, connector_type="github")
    vault = OrgCredentialVault()
    has_token = vault.get_masked(org_id, "github", "api_token") is not None
    # A GitHub App installation (app/connectors/github_app.py) is also a connection
    # with nothing to paste: the state and the installation are confirmed with
    # GitHub before this is ever called, so its presence here is enough.
    has_installation = vault.get_masked(org_id, "github", "installation_id") is not None
    if not is_valid_github_name(organization):
        raise ConnectRefused("Enter your GitHub organisation or account name exactly as it appears in github.com/<name>.")
    if not token and not has_token and not has_installation:
        raise ConnectRefused("Paste a read-only GitHub token to connect.")
    if token and (len(token) > 255 or any(ch.isspace() for ch in token)):
        raise ConnectRefused("That does not look like a GitHub token.")

    config = dict((existing.config or {}) if existing else {})
    config["organization"] = organization
    payload = {
        "connector_type": "github",
        "connector_id": existing.id if existing else None,
        "name": "GitHub",
        "config": config,
        "status": "active",
        "credential_provided": bool(token),
    }
    cfg = apply_connector_change(
        org_id=org_id,
        action=CONNECTOR_ACTION_UPDATE if existing else CONNECTOR_ACTION_CREATE,
        payload=payload,
        delegated=True,
    )
    if token:
        OrgCredentialVault().store(org_id=org_id, connector_type="github", credential_type="api_token", value=token)
    db.session.commit()
    get_job_queue_service().create_job(
        "GitHub: read repositories",
        task or FIRST_SYNC_TASK,
        {"connector_id": cfg.id, **(extra or {})},
        organization_id=org_id,
    )
    return cfg.id


def pending_total(org_id) -> int:
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    return AIChatCRUDApproval.query.filter(
        AIChatCRUDApproval.organization_id == org_id,
        AIChatCRUDApproval.source_table == PROPOSAL_SOURCE,
        AIChatCRUDApproval.status == ApprovalStatus.PENDING,
        AIChatCRUDApproval.operation_type.in_([OP_CLASSIFY, OP_RELATE]),
    ).count()


def model_counts(org_id) -> Dict[str, int]:
    """What the connector has put in the organisation's model so far."""
    from app.models import ArchiMateElement, ArchiMateRelationship

    by_type = dict(
        db.session.execute(
            db.select(ArchiMateElement.type, db.func.count(ArchiMateElement.id))
            .where(ArchiMateElement.organization_id == org_id, ArchiMateElement.source_kind == "connector")
            .group_by(ArchiMateElement.type)
        ).all()
    )
    return {
        "applications": by_type.get("Application", 0),
        # a component is an application-kind element that is not itself an Application
        "components": sum(n for kind, n in by_type.items() if is_application_kind(kind) and kind != "Application"),
        "relationships": ArchiMateRelationship.query.filter_by(organization_id=org_id, source_kind="connector").count(),
    }


def create_github_connector(config) -> GitHubConnector:
    """Factory, as the other connectors provide."""
    return GitHubConnector(config)
