"""DevOps push service — pushes generated artefacts to GitHub or Azure DevOps.

COM-018: Enterprise architects commit generated OpenAPI specs / FastAPI stubs
directly to a Git repository so developers can start work immediately.

Usage::

    from app.services.devops_push_service import DevOpsPushService

    svc = DevOpsPushService()
    result = svc.push(org_id, solution_id, solution_slug, generated_files)
    # {"pr_url": "https://github.com/.../pull/42", "branch": "arch/my-solution-1714000000"}

All HTTP calls use ``requests`` (no new heavy dependencies) and time out after
15 seconds.  Every method returns a ``dict`` — never raises.
"""
import base64
import logging
import re
import time

import requests
from flask import current_app

logger = logging.getLogger(__name__)

_TIMEOUT = 15  # seconds for every outbound HTTP call


class DevOpsPushService:
    """Push generated code to GitHub or Azure DevOps Git."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(
        self,
        org_id: int,
        solution_id: int,
        solution_slug: str,
        generated_files: dict,
    ) -> dict:
        """Auto-select provider from org config and push.

        Returns:
            ``{"pr_url": str, "branch": str}`` on success, or
            ``{"error": str}`` when unconfigured / disabled.
        """
        config = self._get_config(org_id)
        if not config or not config.enabled:
            return {"error": "not_configured"}

        if config.provider == "azure_devops":
            return self.push_to_azure_devops(org_id, solution_id, solution_slug, generated_files)
        return self.push_to_github(org_id, solution_id, solution_slug, generated_files)

    def push_to_github(
        self,
        org_id: int,
        solution_id: int,
        solution_slug: str,
        generated_files: dict,
    ) -> dict:
        """Create a branch, commit all generated files, and open a pull request.

        Steps:
        1. ``GET /repos/{owner}/{repo}/git/ref/heads/{base_branch}`` — get SHA
        2. ``POST /repos/{owner}/{repo}/git/refs``                    — create branch
        3. ``PUT  /repos/{owner}/{repo}/contents/{path}``             — commit each file
        4. ``POST /repos/{owner}/{repo}/pulls``                       — open PR

        Returns:
            ``{"pr_url": str, "branch": str}`` on success, or
            ``{"error": str}`` on failure / missing config.
        """
        config = self._get_config(org_id)
        if not config:
            return {"error": "not_configured"}

        token = config.access_token
        if not token:
            return {"error": "not_configured"}

        if not generated_files:
            return {"branch": None, "pr_url": None, "files_committed": 0}

        repo_url = config.repo_url or ""
        owner, repo = _parse_github_owner_repo(repo_url)
        if not owner or not repo:
            return {"error": f"Cannot parse GitHub owner/repo from repo_url: {repo_url!r}"}

        base_branch = config.default_base_branch or "main"
        branch_name = f"arch/{solution_slug}-{int(time.time())}"
        blueprint_url = _build_blueprint_url(solution_id)

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        api_base = "https://api.github.com"

        # ── Step 1: get base branch SHA ────────────────────────────────
        try:
            ref_resp = requests.get(
                f"{api_base}/repos/{owner}/{repo}/git/ref/heads/{base_branch}",
                headers=headers,
                timeout=_TIMEOUT,
            )
            ref_resp.raise_for_status()
            sha = ref_resp.json()["object"]["sha"]
        except Exception as exc:
            logger.error("GitHub get-ref failed for %s/%s: %s", owner, repo, exc)
            return {"error": f"Failed to get base branch ref: {exc}"}

        # ── Step 2: create branch ──────────────────────────────────────
        try:
            branch_resp = requests.post(
                f"{api_base}/repos/{owner}/{repo}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch_name}", "sha": sha},
                timeout=_TIMEOUT,
            )
            branch_resp.raise_for_status()
        except Exception as exc:
            logger.error("GitHub create-branch failed: %s", exc)
            return {"error": f"Failed to create branch: {exc}"}

        # ── Step 3: commit each file ───────────────────────────────────
        for file_path, content in generated_files.items():
            encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
            try:
                put_resp = requests.put(
                    f"{api_base}/repos/{owner}/{repo}/contents/{file_path.lstrip('/')}",
                    headers=headers,
                    json={
                        "message": f"feat(arch): add generated artefact {file_path}",
                        "content": encoded,
                        "branch": branch_name,
                    },
                    timeout=_TIMEOUT,
                )
                put_resp.raise_for_status()
            except Exception as exc:
                logger.error("GitHub put-file %s failed: %s", file_path, exc)
                return {"error": f"Failed to commit {file_path}: {exc}"}

        # ── Step 4: open pull request ──────────────────────────────────
        try:
            pr_resp = requests.post(
                f"{api_base}/repos/{owner}/{repo}/pulls",
                headers=headers,
                json={
                    "title": f"[arch] Generated artefacts — solution #{solution_id}",
                    "body": (
                        f"Generated artefacts from the Entelim architecture blueprint.\n\n"
                        f"**Blueprint:** {blueprint_url}\n\n"
                        f"This pull request was opened automatically by the Code Workbench."
                    ),
                    "head": branch_name,
                    "base": base_branch,
                },
                timeout=_TIMEOUT,
            )
            pr_resp.raise_for_status()
            pr_url = pr_resp.json().get("html_url", "")
        except Exception as exc:
            logger.error("GitHub create-PR failed: %s", exc)
            return {"error": f"Failed to create pull request: {exc}"}

        logger.info("GitHub PR opened: %s (branch %s)", pr_url, branch_name)
        return {"pr_url": pr_url, "branch": branch_name}

    def push_to_azure_devops(
        self,
        org_id: int,
        solution_id: int,
        solution_slug: str,
        generated_files: dict,
    ) -> dict:
        """Create a branch, commit all files in one push, and open a PR.

        Uses the Azure DevOps REST API v7.1:
        - ``POST …/pushes``        — create branch + commit all files atomically
        - ``POST …/pullrequests``  — open pull request

        Returns:
            ``{"pr_url": str, "branch": str}`` on success, or
            ``{"error": str}`` on failure / missing config.
        """
        config = self._get_config(org_id)
        if not config:
            return {"error": "not_configured"}

        token = config.access_token
        if not token:
            return {"error": "not_configured"}

        repo_url = config.repo_url or ""
        ado_org, project, repo = _parse_ado_url(repo_url)
        if not ado_org or not project or not repo:
            return {"error": f"Cannot parse Azure DevOps org/project/repo from: {repo_url!r}"}

        base_branch = config.default_base_branch or "main"
        branch_name = f"arch/{solution_slug}-{int(time.time())}"
        blueprint_url = _build_blueprint_url(solution_id)

        repo_base = (
            f"https://dev.azure.com/{ado_org}/{project}/_apis/git/repositories/{repo}"
        )
        api_ver = "api-version=7.1"
        # Azure DevOps uses Basic auth with an empty username and the PAT as password
        basic_token = base64.b64encode(f":{token}".encode()).decode("ascii")
        headers = {
            "Authorization": f"Basic {basic_token}",
            "Content-Type": "application/json",
        }

        # ── Step 1: get base branch object ID ─────────────────────────
        try:
            refs_resp = requests.get(
                f"{repo_base}/refs?filter=heads/{base_branch}&{api_ver}",
                headers=headers,
                timeout=_TIMEOUT,
            )
            refs_resp.raise_for_status()
            refs_data = refs_resp.json()
            old_object_id = (
                refs_data["value"][0]["objectId"]
                if refs_data.get("value")
                else "0" * 40
            )
        except Exception as exc:
            logger.error("ADO get-refs failed: %s", exc)
            return {"error": f"Failed to get base branch: {exc}"}

        # ── Step 2: build file changes for the push payload ───────────
        changes = [
            {
                "changeType": "add",
                "item": {"path": f"/{file_path.lstrip('/')}"},
                "newContent": {
                    "content": content,
                    "contentType": "rawtext",
                },
            }
            for file_path, content in generated_files.items()
        ]

        # ── Step 3: push branch + all files atomically ─────────────────
        push_payload = {
            "refUpdates": [
                {
                    "name": f"refs/heads/{branch_name}",
                    "oldObjectId": old_object_id,
                }
            ],
            "commits": [
                {
                    "comment": (
                        f"feat(arch): generated artefacts for solution #{solution_id}\n\n"
                        f"Blueprint: {blueprint_url}"
                    ),
                    "changes": changes,
                }
            ],
        }
        try:
            push_resp = requests.post(
                f"{repo_base}/pushes?{api_ver}",
                headers=headers,
                json=push_payload,
                timeout=_TIMEOUT,
            )
            push_resp.raise_for_status()
        except Exception as exc:
            logger.error("ADO push failed: %s", exc)
            return {"error": f"Failed to push files: {exc}"}

        # ── Step 4: open pull request ──────────────────────────────────
        try:
            pr_payload = {
                "title": f"[arch] Generated artefacts — solution #{solution_id}",
                "description": (
                    f"Generated artefacts from the Entelim architecture blueprint.\n\n"
                    f"Blueprint: {blueprint_url}"
                ),
                "sourceRefName": f"refs/heads/{branch_name}",
                "targetRefName": f"refs/heads/{base_branch}",
            }
            pr_resp = requests.post(
                f"{repo_base}/pullrequests?{api_ver}",
                headers=headers,
                json=pr_payload,
                timeout=_TIMEOUT,
            )
            pr_resp.raise_for_status()
            pr_data = pr_resp.json()
            pr_id = pr_data.get("pullRequestId", "")
            pr_url = (
                f"https://dev.azure.com/{ado_org}/{project}/_git/{repo}/pullrequest/{pr_id}"
            )
        except Exception as exc:
            logger.error("ADO create-PR failed: %s", exc)
            return {"error": f"Failed to create pull request: {exc}"}

        logger.info("Azure DevOps PR opened: %s (branch %s)", pr_url, branch_name)
        return {"pr_url": pr_url, "branch": branch_name}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_config(self, org_id: int):
        """Return the DevOpsConnectorConfig for *org_id*, or ``None``."""
        if not org_id:
            return None
        try:
            from app.models.connector_config import DevOpsConnectorConfig
            return DevOpsConnectorConfig.query.filter_by(
                organization_id=org_id,
                connector_type="devops",
            ).first()
        except Exception as exc:
            logger.warning("DevOpsConnectorConfig lookup failed: %s", exc)
            return None


# ----------------------------------------------------------------------
# Module-level URL helpers
# ----------------------------------------------------------------------

def _parse_github_owner_repo(repo_url: str):
    """Extract ``(owner, repo)`` from a GitHub URL or ``owner/repo`` shorthand.

    >>> _parse_github_owner_repo("https://github.com/acme/myrepo")
    ('acme', 'myrepo')
    >>> _parse_github_owner_repo("acme/myrepo")
    ('acme', 'myrepo')
    """
    if not repo_url:
        return None, None
    repo_url = repo_url.rstrip("/").rstrip(".git")
    match = re.search(r"github\.com[/:]([^/]+)/([^/]+)$", repo_url)
    if match:
        return match.group(1), match.group(2)
    # Shorthand: owner/repo
    parts = repo_url.split("/")
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return None, None


def _parse_ado_url(repo_url: str):
    """Extract ``(organization, project, repo)`` from an Azure DevOps URL.

    Supports:
    - ``https://dev.azure.com/{org}/{project}/_git/{repo}``
    - ``https://{org}.visualstudio.com/{project}/_git/{repo}``

    >>> _parse_ado_url("https://dev.azure.com/acme/myproject/_git/myrepo")
    ('acme', 'myproject', 'myrepo')
    """
    if not repo_url:
        return None, None, None
    repo_url = repo_url.rstrip("/")
    match = re.search(r"dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/]+)$", repo_url)
    if match:
        return match.group(1), match.group(2), match.group(3)
    match = re.search(r"([^.]+)\.visualstudio\.com/([^/]+)/_git/([^/]+)$", repo_url)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None, None, None


def _build_blueprint_url(solution_id: int) -> str:
    """Build a full URL to the solution blueprint page."""
    try:
        base = current_app.config.get("BASE_URL", "").rstrip("/")
        if base:
            return f"{base}/solutions/{solution_id}"
        from flask import request as _req
        return f"{_req.host_url.rstrip('/')}/solutions/{solution_id}"
    except Exception:
        return f"/solutions/{solution_id}"
