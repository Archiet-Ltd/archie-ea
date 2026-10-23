"""What a repository's files say about the system it belongs to (no model call, no network).

The GitHub connector reads files and hands their text to the plain, documented
rules here. Every rule is a function of text and names, so each one is tested
on its own and a person can read why a proposal was made:

* ``dependency_names``: the package names a dependency manifest (package.json,
  requirements.txt, pyproject.toml, go.mod, pom.xml, build.gradle, Cargo.toml,
  Gemfile, composer.json) depends on.
* ``compose_references``: the images, build folders and service names a
  docker-compose file runs.
* ``workflow_references``: the repositories a deploy workflow reaches into.
* ``match_repository``: which repository of the organisation a reference names.
* ``parent_application``: whether a repository is a part of another one (its name
  starts with the other's name, or its description names it).
* ``group_from_topics``: the system a repository's topics put it in.

A reference that names no repository of the organisation (``react``, ``postgres``)
is dropped here, so only links between the organisation's own repositories reach
the review queue.
"""

from __future__ import annotations

import json
import re
from typing import Dict, Iterable, List, Optional, Tuple

DEPENDENCY_MANIFESTS = (
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Cargo.toml",
    "Gemfile",
    "composer.json",
)
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
MAX_FILE_CHARS = 200_000
MAX_WORKFLOWS = 5

# Words a service name ends with that do not say what it is called:
# ``payments-service`` is the application "payments".
_GENERIC_SUFFIXES = {"service", "services", "api", "app", "server", "backend", "web", "svc"}
_GROUP_TOPIC = re.compile(r"^(?:product|system|group|domain)-(.+)$")
_DEPLOY_WORD = re.compile(r"deploy|release|publish|rollout|promote", re.I)


def repo_key(name: str) -> str:
    """A name compared without case, underscores or dots: ``Shared_Utils`` is ``shared-utils``."""
    return re.sub(r"[-_.\s]+", "-", (name or "").strip().lower()).strip("-")


def _tokens(name: str) -> List[str]:
    return [t for t in re.split(r"[-_.\s]+", (name or "").lower()) if t]


def _clip(text: str) -> str:
    return (text or "")[:MAX_FILE_CHARS]


# --------------------------------------------------------------------- #
# Dependency manifests                                                   #
# --------------------------------------------------------------------- #


def _toml(text: str) -> dict:
    try:
        import tomllib

        return tomllib.loads(text)
    except Exception:  # an unreadable file names nothing
        return {}


def dependency_names(filename: str, text: str) -> List[str]:
    """The package names a dependency manifest depends on, in file order, as the
    manifest writes them (``@acme/payments``, ``github.com/acme/orders``,
    ``com.acme:billing``, ``shared-utils``)."""
    text = _clip(text)
    base = (filename or "").rsplit("/", 1)[-1]
    names: List[str] = []

    if base == "package.json":
        try:
            data = json.loads(text)
        except ValueError:
            return []
        if isinstance(data, dict):
            for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                block = data.get(section)
                if isinstance(block, dict):
                    names.extend(str(k) for k in block)
    elif base.startswith("requirements") and base.endswith(".txt"):
        for line in text.splitlines():
            body = line.split("#", 1)[0].strip()
            if not body or body.startswith("-"):
                continue
            git = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?(?:[@#\s]|$)", body)
            if git:
                names.append(git.group(1))
                continue
            match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", body)
            if match:
                names.append(match.group(1))
    elif base == "pyproject.toml":
        data = _toml(text)
        project = data.get("project") or {}
        for item in project.get("dependencies") or []:
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", str(item))
            if match:
                names.append(match.group(1))
        poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
        names.extend(str(k) for k in poetry if str(k).lower() != "python")
    elif base == "go.mod":
        for line in text.splitlines():
            body = line.split("//", 1)[0].strip()
            if body.startswith("module ") or body in ("require (", ")", "require(") or not body:
                continue
            body = body[len("require "):] if body.startswith("require ") else body
            match = re.match(r"([\w.-]+(?:/[\w.~-]+)+)\s+v[\w.+-]+", body)
            if match:
                names.append(match.group(1))
    elif base == "pom.xml":
        for group, artifact in re.findall(
            r"<groupId>\s*([^<\s]+)\s*</groupId>\s*<artifactId>\s*([^<\s]+)\s*</artifactId>", text
        ):
            names.append(f"{group}:{artifact}")
    elif base in ("build.gradle", "build.gradle.kts"):
        for group, artifact in re.findall(r"""['"]([\w.-]+):([\w.-]+):[^'"]*['"]""", text):
            names.append(f"{group}:{artifact}")
    elif base == "Cargo.toml":
        data = _toml(text)
        for section in ("dependencies", "dev-dependencies", "build-dependencies"):
            block = data.get(section)
            if isinstance(block, dict):
                names.extend(str(k) for k in block)
    elif base == "Gemfile":
        names.extend(re.findall(r"""^\s*gem\s+['"]([\w.-]+)['"]""", text, re.M))
    elif base == "composer.json":
        try:
            data = json.loads(text)
        except ValueError:
            return []
        if isinstance(data, dict):
            for section in ("require", "require-dev"):
                block = data.get(section)
                if isinstance(block, dict):
                    names.extend(str(k) for k in block if "/" in str(k))
    return list(dict.fromkeys(names))


# --------------------------------------------------------------------- #
# docker-compose and workflows                                           #
# --------------------------------------------------------------------- #


def _yaml(text: str):
    try:
        import yaml

        return yaml.safe_load(_clip(text))
    except Exception:
        return None


def compose_references(text: str) -> List[str]:
    """What a docker-compose file runs, as references to match against repositories:
    each service's image (``ghcr.io/acme/billing-api:2.0``), build folder
    (``../billing-api``) and service name."""
    data = _yaml(text)
    services = data.get("services") if isinstance(data, dict) else None
    refs: List[str] = []
    if isinstance(services, dict):
        for name, spec in services.items():
            refs.append(str(name))
            if isinstance(spec, dict):
                image = spec.get("image")
                if isinstance(image, str):
                    refs.append(image.split("@", 1)[0].rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image)
                build = spec.get("build")
                context = build.get("context") if isinstance(build, dict) else build
                if isinstance(context, str) and context.strip() not in (".", "./"):
                    refs.append(context.strip().rstrip("/").rsplit("/", 1)[-1])
    return list(dict.fromkeys(refs))


def workflow_references(filename: str, text: str, organization: str) -> List[str]:
    """The ``<organisation>/<repository>`` names a deploy workflow reaches into: a
    reusable workflow it calls, a repository it checks out, a ``-R`` target or a
    dispatch call. A workflow whose file name, name or job names do not say deploy,
    release or publish names nothing."""
    text = _clip(text)
    data = _yaml(text)
    says_deploy = bool(_DEPLOY_WORD.search(filename or ""))
    if isinstance(data, dict):
        says_deploy = says_deploy or bool(_DEPLOY_WORD.search(str(data.get("name") or "")))
        jobs = data.get("jobs")
        if isinstance(jobs, dict):
            says_deploy = says_deploy or any(_DEPLOY_WORD.search(str(j)) for j in jobs)
    if not says_deploy:
        return []
    org = re.escape(organization)
    patterns = (
        rf"uses:\s*({org}/[\w.-]+)[/@\s]",
        rf"repository:\s*['\"]?({org}/[\w.-]+)",
        rf"(?:-R|--repo)\s+({org}/[\w.-]+)",
        rf"repos/({org}/[\w.-]+)/dispatches",
    )
    found: List[str] = []
    for pattern in patterns:
        found.extend(m for m in re.findall(pattern, text, re.I))
    return list(dict.fromkeys(found))


# --------------------------------------------------------------------- #
# Which repository does a reference name?                                #
# --------------------------------------------------------------------- #


def match_repository(reference: str, organization: str, repositories: Dict[str, str]) -> Optional[str]:
    """The full name of the repository of *organization* that *reference* names.

    *repositories* maps ``repo_key(name)`` to the full name (``acme/orders-worker``).
    A reference that carries an owner (``@acme/x``, ``github.com/acme/x``,
    ``ghcr.io/acme/x``, ``com.acme:x``) must name this organisation; a bare name
    must be exactly a repository's name (or ``<organisation>-<name>``)."""
    ref = (reference or "").strip().strip("'\"")
    if not ref:
        return None
    org = organization.lower()
    owner: Optional[str] = None
    name = ref

    if ":" in ref and "/" not in ref:  # maven/gradle group:artifact
        group, name = ref.split(":", 1)
        parts = group.lower().split(".")
        if org not in parts:
            return None
        owner = org
    else:
        ref = re.sub(r"^(?:git\+)?(?:https?://|ssh://|git@)", "", ref)
        ref = ref.replace("github.com:", "github.com/")
        segments = [s for s in ref.split("/") if s]
        if segments and segments[0].lower() in ("github.com", "ghcr.io", "docker.io", "registry.hub.docker.com"):
            segments = segments[1:]
        elif segments and "." in segments[0] and not segments[0].startswith("@"):
            segments = segments[1:]  # some other registry host
        if segments and segments[0].startswith("@"):
            owner = segments[0][1:].lower()
            segments = segments[1:]
        elif len(segments) >= 2:
            owner = segments[0].lower()
            segments = segments[1:]
        name = segments[0] if segments else ""
        name = re.sub(r"\.git$", "", name)
        if owner is not None and owner != org:
            return None
    key = repo_key(name)
    if not key:
        return None
    if key in repositories:
        return repositories[key]
    prefix = org + "-"
    if key.startswith(prefix) and key[len(prefix):] in repositories:
        return repositories[key[len(prefix):]]
    return None


# --------------------------------------------------------------------- #
# Parts of an application, and groups                                    #
# --------------------------------------------------------------------- #


def _stem(name: str) -> List[str]:
    tokens = _tokens(name)
    while len(tokens) > 1 and tokens[-1] in _GENERIC_SUFFIXES:
        tokens.pop()
    return tokens


def parent_application(
    name: str, description: Optional[str], applications: Iterable[str]
) -> Tuple[Optional[str], Optional[str]]:
    """The application a repository is a part of, with the reason.

    ``applications`` are the names of the repositories that are applications. A
    repository is a component of one when its name starts with that application's
    name (``payments-worker`` of ``payments-service``: the name without the
    generic ending ``service``), or its description names the application.
    The longest matching name wins; a repository is never its own parent."""
    mine = _tokens(name)
    best: Optional[Tuple[int, str, str]] = None
    for other in applications:
        if repo_key(other) == repo_key(name):
            continue
        stem = _stem(other)
        if stem and len(mine) > len(stem) and mine[: len(stem)] == stem:
            if best is None or len(stem) > best[0]:
                best = (len(stem), other, f"its name starts with {'-'.join(stem)}, the name of {other}")
    if best is not None:
        return best[1], best[2]
    text = (description or "").lower()
    for other in applications:
        if repo_key(other) == repo_key(name):
            continue
        if re.search(rf"(?<![\w-]){re.escape(other.lower())}(?![\w-])", text):
            return other, f"its description names {other}"
    return None, None


def group_from_topics(topics: Iterable[str]) -> Optional[str]:
    """The system a repository's topics put it in: ``product-shop`` is the group ``Shop``."""
    for topic in topics or []:
        match = _GROUP_TOPIC.match(str(topic).lower())
        if match:
            return " ".join(w[:1].upper() + w[1:] for w in _tokens(match.group(1)))
    return None
