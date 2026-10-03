#!/usr/bin/env python
"""Runner-side helpers for the production deploy workflow (standard library only).

`.github/workflows/deploy.yml` wraps `scripts/deploy_verified.sh` so a session
that has GitHub access but no SSH access to the droplet can deploy. Everything
here exists so the parts of that workflow that can be checked without a
droplet are ordinary Python with unit tests, not shell embedded in YAML.

    python scripts/deploy_workflow.py preflight [--check-environment production]
                                                [--require-branch main]
    python scripts/deploy_workflow.py setup-ssh --dir DIR --host IP [--user root]
    python scripts/deploy_workflow.py filter-log            (stdin -> stdout)
    python scripts/deploy_workflow.py classify-log LOG --rc N --sha SHA
    python scripts/deploy_workflow.py summary               (markdown on stdout)

preflight    Refuses (exit 1, nothing written) unless the requested ref is a
             full lowercase 40-character SHA, the workflow was dispatched from
             main, the commit is on origin/main's first-parent history (so it
             was itself a commit on main, not only part of a merged branch),
             no branch or tag named like the SHA (or origin/<sha>) exists
             (a name that merely starts with the SHA counts; defence in depth:
             deploy_verified.sh itself resolves a 40-hex ref as an object), and
             every job in ci.yml that is not in EXCLUDED_CHECKS has concluded
             `success` for that exact commit. It also reads the `production`
             environment's protection rules and refuses if the reviewers are
             absent or the rules cannot be read. There is deliberately no
             override.
setup-ssh    Writes the deploy key, the pinned host key, an ssh config with
             StrictHostKeyChecking yes, and an `ssh` wrapper that forces that
             config and repeats the pinning options ahead of its arguments,
             because deploy_verified.sh calls plain `ssh` with fixed options and
             offers no way to pass a key or a known_hosts file.
filter-log   Lets through only lines that begin like deploy_verified.sh's own
             status lines. It matches by prefix and cannot tell who wrote a
             line. This repository is public, so its Actions logs are
             world-readable; anything else the droplet prints (compose output,
             tracebacks) is withheld rather than trusted to contain no secret.
classify-log Reads the raw script output and exit status and says what
             happened: verified, rolled back, rollback failed, or no rollback
             possible. Exit 0 only when the requested commit is verified.
summary      Renders the job summary from validated environment values.

Only the requested commit's identity, enum-valued outcomes and public facts
are ever printed. Secrets are read from the environment by `setup-ssh` alone
and are written to files, never to output.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

SHA_RE = re.compile(r"[0-9a-f]{40}")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ENV_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
HOST_RE = re.compile(r"[0-9]{1,3}(\.[0-9]{1,3}){3}")
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")

# Every job in .github/workflows/ci.yml except the ones in EXCLUDED_CHECKS, by
# the name GitHub reports for its check run. tests/test_deploy_workflow.py fails
# when ci.yml gains, loses or renames a job without this list or the exclusions
# following, so neither can rot quietly.
REQUIRED_CHECKS = (
    "Secret scan (gitleaks)",
    "Static gates (compile + ratchets)",
    "Boot health (no database)",
    "Tests (pytest + coverage)",
    # The backend-test matrix shards that job combines, one per matrix.shard
    # value in ci.yml (a count kept in step with it by the test named above).
    *(f"Tests (pytest + coverage) \u2014 shard {shard}" for shard in range(12)),
    "Database gates (schema drift)",
    "SAST (bandit)",
    "Browser journeys (one per archetype)",
    "Browser compatibility (firefox)",
    "Browser compatibility (webkit)",
    "Level 10 archetype walkthrough",
    "Dependency CVEs (pip-audit)",
)

# Jobs in ci.yml that are deliberately not required, with the reason. Adding a
# name here is a decision to let a deploy proceed while that job is red.
EXCLUDED_CHECKS = {
    "Build immutable release image": (
        "builds and pushes a GHCR image, which production does not run: it deploys "
        "a bind-mounted source checkout (see the header of scripts/deploy_verified.sh). "
        "A registry or buildx outage must not block a deploy that never uses the image."
    ),
    "Fast lane (static + boot + db + changed tests)": (
        "runs only on non-labeled PR events without the full-ci label; its name is "
        "distinct from every required-check name, so it cannot satisfy a required-check "
        "requirement.  The full suite runs on push to main and on full-ci labeled PRs."
    ),
    "Quarantined tests (non-blocking)": (
        "continue-on-error: true and runs only quarantined tests; it cannot fail the "
        "workflow.  The blocking shards use CI_QUARANTINE_MODE=exclude, so a "
        "non-quarantined test failure in any shard still fails the run."
    ),
}
CHECK_APP_SLUG = "github-actions"
MAX_CHECK_PAGES = 10
API_TIMEOUT_SECONDS = 20


# ---------------------------------------------------------------------------
# Small validators
# ---------------------------------------------------------------------------
def validate_ref(value: str) -> str | None:
    """Return a problem description, or None when `value` is a full SHA."""
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        return (
            "ref must be a full 40-character lowercase hexadecimal commit SHA "
            "(branch names, tags and short SHAs are refused)"
        )
    return None


def parse_dry_run(value: str) -> bool | None:
    """'true' -> True, 'false' -> False, anything else -> None (refused)."""
    if value == "true":
        return True
    if value == "false":
        return False
    return None


# ---------------------------------------------------------------------------
# GitHub API access (injectable for tests)
# ---------------------------------------------------------------------------
class ApiResponse:
    def __init__(self, status: int, body, headers: dict, error: str = ""):
        self.status = status
        self.body = body
        self.headers = headers
        self.error = error

    def message(self) -> str:
        if isinstance(self.body, dict) and isinstance(self.body.get("message"), str):
            return self.body["message"][:200]
        return self.error[:200]


def api_get(url: str, token: str, opener=None) -> ApiResponse:
    """GET a GitHub API URL. Never raises; a transport failure is status 0."""
    if not url.startswith("https://"):
        return ApiResponse(0, None, {}, "refusing a non-https API URL")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": "Bearer " + token,
            "User-Agent": "archie-ea-deploy-workflow",
        },
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=API_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", "replace")
            headers = {k.lower(): v for k, v in dict(response.headers).items()}
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        headers = {k.lower(): v for k, v in dict(exc.headers or {}).items()}
        status = exc.code
    except Exception as exc:  # network failure, timeout, TLS failure
        return ApiResponse(0, None, {}, "%s: %s" % (type(exc).__name__, exc))
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    return ApiResponse(status, body, headers)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
def git(args: list[str], runner=subprocess.run):
    return runner(["git", *args], capture_output=True, text=True)


def check_ancestry(sha: str, main_ref: str = "origin/main", runner=subprocess.run) -> list[str]:
    """The commit must exist here and be reachable from origin/main."""
    if git(["cat-file", "-e", sha + "^{commit}"], runner).returncode != 0:
        return ["commit %s is not present in this repository" % sha]
    peeled = git(["rev-parse", "--verify", "--quiet", sha + "^{commit}"], runner)
    if peeled.returncode != 0 or (peeled.stdout or "").strip() != sha:
        return ["%s is not itself a commit (a tag object's SHA is refused)" % sha]
    if git(["rev-parse", "--verify", "--quiet", main_ref], runner).returncode != 0:
        return ["cannot resolve %s in this checkout; cannot prove the commit is on main" % main_ref]
    result = git(["merge-base", "--is-ancestor", sha, main_ref], runner)
    if result.returncode == 0:
        return []
    if result.returncode == 1:
        return ["commit %s is not an ancestor of %s; only commits on main can be deployed" % (sha, main_ref)]
    return ["git merge-base failed (exit %d); cannot prove the commit is on main" % result.returncode]


def check_first_parent(sha: str, main_ref: str = "origin/main", runner=subprocess.run) -> list[str]:
    """The commit must be on main's first-parent history.

    Being an ancestor of main is not enough: a commit that reached main only as
    the tip of a merged pull-request branch is an ancestor, but its checks ran
    on a merge preview of that branch, and no push-triggered run of CI ever ran
    on its own tree. A commit on the first-parent chain was itself the tip of
    main at some point, which is when CI runs on it.
    """
    listing = git(["rev-list", "--first-parent", main_ref], runner)
    if listing.returncode != 0:
        return ["git rev-list failed (exit %d); cannot prove the commit is on the first-parent history of %s"
                % (listing.returncode, main_ref)]
    if sha in set((listing.stdout or "").split()):
        return []
    return [
        "commit %s is reachable from %s only through a merged branch (it is not on the first-parent history), "
        "so it was never itself a commit on main and CI never ran on its own tree as a push to main; "
        "deploy a commit that was on main, such as the merge commit that brought it in" % (sha, main_ref)
    ]


# Ref namespaces, under refs/, in which a name built from the SHA could be looked
# up instead of the commit. The real control is in deploy_verified.sh, which
# resolves a 40-hex ref as an object and never as origin/<sha>; refusing these
# here as well is defence in depth and stops the request before it is approved.
SHADOW_REF_TEMPLATES = ("heads/%s", "tags/%s", "tags/origin/%s", "heads/origin/%s")


def check_no_shadow_refs(repo: str, sha: str, token: str, api_base: str, opener=None) -> list[str]:
    """Refuse if any branch or tag is named like the SHA (or origin/<sha>).

    An API failure is a refusal, not a pass.
    """
    problems = []
    for template in SHADOW_REF_TEMPLATES:
        url = "%s/repos/%s/git/matching-refs/%s" % (api_base, repo, template % sha)
        response = api_get(url, token, opener)
        if response.status != 200 or not isinstance(response.body, list):
            problems.append("could not check refs named like the SHA under %s (HTTP %s %s)"
                            % (template % "<sha>", response.status, response.message()))
        elif response.body:
            names = [str(r.get("ref", "?"))[:120] for r in response.body if isinstance(r, dict)][:3]
            problems.append("a ref named like the SHA exists (%s); refused so nothing can stand in for the checked commit "
                            "(a name that merely starts with the SHA is enough; rename or delete that ref)"
                            % ", ".join(names))
    return problems


def fetch_check_runs(repo: str, sha: str, token: str, api_base: str, opener=None):
    """All check runs for a commit, following pagination a bounded number of times."""
    runs: list = []
    for page in range(1, MAX_CHECK_PAGES + 1):
        url = "%s/repos/%s/commits/%s/check-runs?per_page=100&page=%d" % (api_base, repo, sha, page)
        response = api_get(url, token, opener)
        if response.status != 200 or not isinstance(response.body, dict):
            return None, "checks API returned HTTP %s %s" % (response.status, response.message())
        batch = response.body.get("check_runs")
        if not isinstance(batch, list):
            return None, "checks API returned an unexpected payload"
        runs.extend(batch)
        total = response.body.get("total_count")
        if len(batch) < 100 or (isinstance(total, int) and len(runs) >= total):
            return runs, ""
    return None, "more than %d check runs; refusing to guess" % (MAX_CHECK_PAGES * 100)


def evaluate_checks(check_runs: list, required=REQUIRED_CHECKS) -> list[str]:
    """Problems for every required check that is missing or not a success.

    Only runs created by GitHub Actions count, so another app cannot satisfy a
    requirement by publishing a check with the same name. When a name has
    several runs (a re-run), the newest one decides.
    """
    latest: dict = {}
    for run in check_runs:
        if not isinstance(run, dict):
            continue
        app = run.get("app") or {}
        if app.get("slug") != CHECK_APP_SLUG:
            continue
        name = run.get("name")
        run_id = run.get("id")
        if not isinstance(name, str) or not isinstance(run_id, int):
            continue
        if name not in latest or run_id > latest[name]["id"]:
            latest[name] = run
    problems = []
    for name in required:
        run = latest.get(name)
        if run is None:
            problems.append("no CI check named %r has run for this commit" % name)
        elif run.get("status") != "completed":
            problems.append("CI check %r has not finished (status: %s)" % (name, run.get("status")))
        elif run.get("conclusion") != "success":
            problems.append("CI check %r concluded %s, not success" % (name, run.get("conclusion")))
    return problems


def evaluate_environment(response: ApiResponse, name: str):
    """Classify the environment's protection as (state, detail).

    ok           required reviewers and a deployment-branch policy are present
    unprotected  the environment exists but jobs would run without approval
    missing      the environment does not exist (or is invisible to the token)
    unknown      the API could not answer; the caller refuses, because the
                 reviewer requirement cannot be confirmed
    """
    if response.status == 200 and isinstance(response.body, dict):
        rules = response.body.get("protection_rules") or []
        reviewer_rules = [r for r in rules if isinstance(r, dict) and r.get("type") == "required_reviewers"]
        reviewers = 0
        for rule in reviewer_rules:
            listed = rule.get("reviewers")
            reviewers += len(listed) if isinstance(listed, list) else 0
        if not reviewer_rules or reviewers == 0:
            return "unprotected", "environment %r has no required reviewers, so its jobs would run without approval" % name
        policy = response.body.get("deployment_branch_policy")
        if not policy:
            return "unprotected", "environment %r can be deployed from any branch; restrict it to main" % name
        kind = "custom branches" if policy.get("custom_branch_policies") else "protected branches"
        return "ok", "required reviewers: %d, deployment branches: %s" % (reviewers, kind)
    if response.status == 404:
        return "missing", "environment %r does not exist, or the workflow token cannot see it" % name
    accepted = response.headers.get("x-accepted-github-permissions", "")
    extra = " (token permissions accepted: %s)" % accepted if accepted else ""
    return "unknown", (
        "environment settings could not be read: HTTP %s %s%s; refused because the "
        "required-reviewer setting cannot be confirmed (see deploy/DEPLOY_WORKFLOW.md)"
        % (response.status, response.message(), extra)
    )


def write_output(name: str, value: str) -> None:
    """Append one validated key=value pair to $GITHUB_OUTPUT."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path and "\n" not in value and "\r" not in value:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("%s=%s\n" % (name, value))


def write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def run_preflight(env, args, runner=subprocess.run, opener=None, out=print) -> int:
    problems: list[str] = []
    ref = env.get("DEPLOY_REF", "")
    dry_run = parse_dry_run(env.get("DEPLOY_DRY_RUN", ""))
    repo = env.get("GITHUB_REPOSITORY", "")
    token = env.get("GITHUB_TOKEN", "")
    api_base = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

    if dry_run is None:
        problems.append("dry_run must be exactly 'true' or 'false'")
    if not REPO_RE.fullmatch(repo):
        problems.append("GITHUB_REPOSITORY is missing or malformed")
    if not token:
        problems.append("no GitHub token was provided to the pre-flight step")
    bad_ref = validate_ref(ref)
    if bad_ref:
        problems.append(bad_ref)
    if args.require_branch:
        expected = "refs/heads/" + args.require_branch
        if env.get("GITHUB_REF", "") != expected:
            problems.append(
                "the workflow must be dispatched from %s (dispatched from %r)"
                % (args.require_branch, env.get("GITHUB_REF", ""))
            )

    env_note = ("not requested" if not args.check_environment
                else "not evaluated (the request was refused before the environment was read)")
    ci_note = "not evaluated"
    if not problems:
        main_ref = "origin/" + (args.require_branch or "main")
        ancestry = check_ancestry(ref, main_ref, runner)
        problems += ancestry
        if not ancestry:
            problems += check_first_parent(ref, main_ref, runner)
        problems += check_no_shadow_refs(repo, ref, token, api_base, opener)
        runs, fetch_problem = fetch_check_runs(repo, ref, token, api_base, opener)
        if runs is None:
            problems.append(fetch_problem)
        else:
            ci_problems = evaluate_checks(runs)
            problems += ci_problems
            ci_note = "%d of %d required checks succeeded" % (len(REQUIRED_CHECKS) - len(ci_problems), len(REQUIRED_CHECKS))
        if args.check_environment:
            state, detail = evaluate_environment(
                api_get("%s/repos/%s/environments/%s" % (api_base, repo, args.check_environment), token, opener),
                args.check_environment,
            )
            if state == "ok":
                env_note = "verified (%s)" % detail
            else:
                env_note = "REFUSED: %s" % detail
                problems.append(detail)

    mode = "dry-run" if dry_run else "deploy"
    lines = ["### Pre-flight", ""]
    lines.append("- Requested commit: `%s`" % (ref if not bad_ref else "(invalid)"))
    lines.append("- Mode: %s" % ("dry run (verify only, nothing is changed)" if dry_run else "real deploy" if dry_run is False else "invalid"))
    lines.append("- CI: %s" % ci_note)
    lines.append("- Environment protection: %s" % env_note)
    if problems:
        lines += ["", "**Refused.**", ""] + ["- " + p for p in problems]
    write_summary("\n".join(lines))

    if problems:
        out("PRE-FLIGHT REFUSED:")
        for problem in problems:
            out("  - " + problem)
        return 1
    out("PRE-FLIGHT OK: %s (%s); CI: %s; environment: %s" % (ref, mode, ci_note, env_note))
    write_output("sha", ref)
    write_output("mode", mode)
    return 0


# ---------------------------------------------------------------------------
# SSH material
# ---------------------------------------------------------------------------
KEY_HEADER_RE = re.compile(r"-----BEGIN (OPENSSH|RSA|EC|DSA)? ?PRIVATE KEY-----")
KNOWN_HOSTS_TYPES = (
    "ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521", "ssh-rsa", "rsa-sha2-256", "rsa-sha2-512",
)


def validate_known_hosts(text: str, host: str) -> list[str]:
    """Every entry must be a plain, unhashed key line for exactly this host."""
    entries = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not entries:
        return ["DROPLET_KNOWN_HOSTS contains no host key entries"]
    problems = []
    for entry in entries:
        fields = entry.split()
        if entry.startswith("@") or entry.startswith("|"):
            problems.append("DROPLET_KNOWN_HOSTS has a marker or hashed entry; use plain '<ip> <type> <key>' lines")
        elif len(fields) < 3 or fields[1] not in KNOWN_HOSTS_TYPES:
            problems.append("DROPLET_KNOWN_HOSTS has a line that is not '<host> <key-type> <key>'")
        elif fields[0] != host:
            problems.append("DROPLET_KNOWN_HOSTS entries must name exactly %s (no host lists, patterns or other hosts)" % host)
    return problems


def hard_ssh_options(directory: str) -> list[tuple[str, str]]:
    """Options that pin the connection.

    They are written to the ssh config and also passed on the command line by
    the wrapper, ahead of the caller's own arguments. OpenSSH keeps the first
    value it obtains for an option and command-line options are obtained before
    any config file, so a caller's own -o option cannot loosen them; putting
    them in the config alone would not stop that.
    """
    return [
        ("IdentitiesOnly", "yes"),
        ("StrictHostKeyChecking", "yes"),
        ("UserKnownHostsFile", directory + "/known_hosts"),
        ("GlobalKnownHostsFile", "/dev/null"),
        ("UpdateHostKeys", "no"),
        ("VerifyHostKeyDNS", "no"),
        ("BatchMode", "yes"),
        ("PasswordAuthentication", "no"),
        ("KbdInteractiveAuthentication", "no"),
        ("ForwardAgent", "no"),
        ("ForwardX11", "no"),
        ("ClearAllForwardings", "yes"),
    ]


def render_ssh_config(directory: str) -> str:
    lines = [
        "# Written by scripts/deploy_workflow.py setup-ssh for one workflow run.",
        "Host *",
        '    IdentityFile "%s/id_deploy"' % directory,
    ]
    for key, value in hard_ssh_options(directory):
        lines.append("    %s %s" % (key, '"%s"' % value if key == "UserKnownHostsFile" else value))
    lines += ["    ServerAliveInterval 30", "    ServerAliveCountMax 40", ""]
    return "\n".join(lines)


def render_ssh_wrapper(real_ssh: str, directory: str) -> str:
    forced = " ".join("-o %s" % shlex.quote("%s=%s" % pair) for pair in hard_ssh_options(directory))
    return (
        "#!/bin/sh\n"
        "# The pinning options come before \"$@\" so a caller's -o cannot override them.\n"
        "exec %s -F %s %s \"$@\"\n" % (shlex.quote(real_ssh), shlex.quote(os.path.join(directory, "config")), forced)
    )


def _write_private(path: str, content: str, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, mode)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    os.chmod(path, mode)


def setup_ssh(directory: str, host: str, user: str, key: str, known_hosts: str,
              real_ssh: str | None = None, keygen=subprocess.run) -> list[str]:
    """Write key, pinned host key, config and ssh wrapper. Returns problems."""
    problems = []
    if not HOST_RE.fullmatch(host):
        problems.append("host must be an IPv4 address")
    if not USER_RE.fullmatch(user):
        problems.append("user is malformed")
    key = key.replace("\r\n", "\n").strip("\n") + "\n" if key.strip() else ""
    if not key:
        problems.append("DROPLET_DEPLOY_KEY is empty (is the production environment's secret set?)")
    elif not KEY_HEADER_RE.search(key) or "ENCRYPTED" in key:
        problems.append("DROPLET_DEPLOY_KEY is not an unencrypted PEM/OpenSSH private key")
    problems += validate_known_hosts(known_hosts, host) if HOST_RE.fullmatch(host) else []
    real_ssh = real_ssh or shutil.which("ssh")
    if not real_ssh:
        problems.append("no ssh client on this runner")
    if problems:
        return problems

    if os.path.exists(directory):
        shutil.rmtree(directory)
    os.makedirs(os.path.join(directory, "bin"), mode=0o700)
    os.chmod(directory, 0o700)
    key_path = os.path.join(directory, "id_deploy")
    _write_private(key_path, key, 0o600)
    _write_private(os.path.join(directory, "known_hosts"), known_hosts.strip() + "\n", 0o600)
    _write_private(os.path.join(directory, "config"), render_ssh_config(directory), 0o600)
    wrapper_path = os.path.join(directory, "bin", "ssh")
    _write_private(wrapper_path, render_ssh_wrapper(real_ssh, directory), 0o700)

    # Prove the key parses without printing it: -y derives the public half and
    # fails on a malformed or passphrase-protected key.
    if shutil.which("ssh-keygen"):
        check = keygen(["ssh-keygen", "-y", "-P", "", "-f", key_path], capture_output=True, text=True)
        if check.returncode != 0:
            shutil.rmtree(directory, ignore_errors=True)
            return ["DROPLET_DEPLOY_KEY could not be parsed as an unencrypted private key"]
    return []


def cmd_setup_ssh(args) -> int:
    problems = setup_ssh(
        args.dir, args.host, args.user,
        os.environ.get("DROPLET_DEPLOY_KEY", ""), os.environ.get("DROPLET_KNOWN_HOSTS", ""),
    )
    if problems:
        print("SSH SETUP REFUSED:")
        for problem in problems:
            print("  - " + problem)
        return 1
    print("ssh material written for %s@%s (key mode 600, host key pinned, StrictHostKeyChecking yes)" % (args.user, args.host))
    if shutil.which("ssh-keygen"):
        pinned = subprocess.run(["ssh-keygen", "-lf", os.path.join(args.dir, "known_hosts")], capture_output=True, text=True)
        for line in (pinned.stdout or "").splitlines():
            print("pinned host key: " + line.strip())
    return 0


# ---------------------------------------------------------------------------
# Log handling
# ---------------------------------------------------------------------------
# Lines deploy_verified.sh itself prints, plus ssh's own client-side diagnostics.
# Anchored at the start so a line beginning with '::' (a workflow command) can
# never pass through.
ALLOWED_LINE = re.compile(
    r"^(?:"
    r"== (?:deploying|waiting up to|verifying|--skip-deploy|target commit|AUTO-ROLLBACK|summary)\b.*"
    r"|OK: .*"
    r"|DEPLOY-VERIFY FAIL: .*"
    r"|DEPLOY (?:VERIFIED|FAILED|NOT VERIFIED)\b.*"
    r"|CRITICAL: .*"
    r"|RESOLVED_COMMIT=[0-9a-f]{40}"
    r"|already at [0-9a-f]{40}; checkout skipped.*"
    r"|WARNING: DEPLOY_VERIFY_EMAIL/DEPLOY_VERIFY_PASSWORD not set.*"
    r"|version endpoint: \{.*\}"
    r"|/\S*? -> /\S*?(?:OK: .*)?"  # mount lines; the script prints no newline before its OK line
    r"|ssh: .*"
    r"|Host key verification failed\.?"
    r"|Permission denied.*"
    r"|kex_exchange_identification.*"
    r"|Connection (?:timed out|refused|closed|reset).*"
    r"|@{5,}.*|WARNING: (?:REMOTE HOST IDENTIFICATION|POSSIBLE DNS).*"
    r")$"
)
MAX_LINE = 400


def filter_stream(lines, write) -> int:
    """Pass allowed lines to `write`; return the number withheld. Never raises."""
    withheld = 0
    for raw in lines:
        try:
            line = raw.rstrip("\r\n")
            if ALLOWED_LINE.match(line):
                write(line[:MAX_LINE] + ("..." if len(line) > MAX_LINE else ""))
            elif line.strip():
                withheld += 1
        except Exception:
            withheld += 1
    return withheld


def cmd_filter_log(_args) -> int:
    # Draining stdin to EOF matters more than printing: if this process exited
    # early, the writer upstream (deploy_verified.sh) would die on SIGPIPE in
    # the middle of a deploy.
    state = {"ok": True}

    def write(text: str) -> None:
        if not state["ok"]:
            return
        try:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
        except OSError:
            state["ok"] = False

    stream = (line.decode("utf-8", "replace") for line in sys.stdin.buffer)
    withheld = filter_stream(stream, write)
    if withheld:
        write("[%d further lines of droplet output withheld: this repository's Actions logs are public]" % withheld)
    return 0


VERIFIED_LINE = re.compile(r"^DEPLOY VERIFIED: commit ([0-9a-f]{40}) is running")
ROLLED_BACK_LINE = re.compile(r"^DEPLOY FAILED, ROLLED BACK SUCCESSFULLY: ")
ROLLBACK_FAILED_LINE = re.compile(r"^CRITICAL: .*automatic rollback to ")
NO_KNOWN_GOOD_LINE = re.compile(r"^== AUTO-ROLLBACK: no different known-good commit on record")
ROLLBACK_ATTEMPT_LINE = re.compile(r"^== AUTO-ROLLBACK: .*redeploying last known-good commit ")


def classify_log(text: str, rc: int, sha: str) -> tuple[str, str]:
    """Return (deploy_result, rollback) for a real deploy's raw output.

    deploy_result: verified | rolled_back | rollback_failed | failed | contradiction
    rollback:      not-attempted | succeeded | failed | no-known-good | unknown
    Success requires BOTH exit status 0 and the script's own "DEPLOY VERIFIED"
    line naming exactly the requested commit.
    """
    lines = [ln.rstrip("\r") for ln in text.splitlines()]
    verified = [m.group(1) for m in (VERIFIED_LINE.match(ln) for ln in lines) if m]
    rolled_back = any(ROLLED_BACK_LINE.match(ln) for ln in lines)
    rollback_failed = any(ROLLBACK_FAILED_LINE.match(ln) for ln in lines)
    no_known_good = any(NO_KNOWN_GOOD_LINE.match(ln) for ln in lines)
    attempted = any(ROLLBACK_ATTEMPT_LINE.match(ln) for ln in lines)

    if rc == 0:
        if verified and verified[-1] == sha and not (rolled_back or rollback_failed or attempted):
            return "verified", "not-attempted"
        return "contradiction", "unknown"
    if verified:
        return "contradiction", "unknown"
    if rolled_back:
        return "rolled_back", "succeeded"
    if rollback_failed:
        return "rollback_failed", "failed"
    if attempted:
        return "failed", "unknown"
    if no_known_good:
        return "failed", "no-known-good"
    return "failed", "not-attempted"


def cmd_classify_log(args) -> int:
    if validate_ref(args.sha):
        print("classify-log: --sha must be a full SHA", file=sys.stderr)
        return 2
    with open(args.log, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    result, rollback = classify_log(text, args.rc, args.sha)
    print("deploy_result=%s" % result)
    print("rollback=%s" % rollback)
    return 0 if result == "verified" else 1


# ---------------------------------------------------------------------------
# Job summary
# ---------------------------------------------------------------------------
OUTCOMES = {"success", "failure", "cancelled", "skipped"}


def _outcome(value: str) -> str:
    return value if value in OUTCOMES else ("skipped" if value == "" else "unknown")


def _sha_or(value: str, fallback: str = "unknown") -> str:
    return value if SHA_RE.fullmatch(value or "") else fallback


def render_summary(env) -> str:
    mode = env.get("SUMMARY_MODE", "")
    sha = _sha_or(env.get("SUMMARY_SHA", ""), "not accepted")
    before = _sha_or(env.get("SUMMARY_BEFORE_SHA", ""))
    after = _sha_or(env.get("SUMMARY_AFTER_SHA", ""))
    gate = _outcome(env.get("SUMMARY_GATE_OUTCOME", ""))
    ssh = _outcome(env.get("SUMMARY_SSH_OUTCOME", ""))
    dry = _outcome(env.get("SUMMARY_DRY_OUTCOME", ""))
    baseline = env.get("SUMMARY_BASELINE", "")
    deploy = _outcome(env.get("SUMMARY_DEPLOY_OUTCOME", ""))
    post = _outcome(env.get("SUMMARY_POST_OUTCOME", ""))
    result = env.get("SUMMARY_DEPLOY_RESULT", "")
    rollback = env.get("SUMMARY_ROLLBACK", "")
    run_url = env.get("SUMMARY_RUN_URL", "")

    if mode not in ("dry-run", "deploy") or gate != "success":
        verdict, rollback_text = "Refused before any change was made.", "none"
    elif ssh != "success":
        verdict, rollback_text = "Stopped before any change: the SSH setup or the connection to the droplet failed.", "none"
    elif mode == "dry-run":
        if dry == "success":
            verdict = "Dry run passed: the running deployment is healthy, correctly mounted and serving its own commit. Nothing was changed."
        else:
            verdict = "Dry run failed: the running deployment did not verify. Nothing was changed."
        rollback_text = "not applicable (dry run)"
    else:
        if deploy == "skipped":
            verdict, rollback_text = "Stopped before the deploy step. Nothing was deployed.", "none"
        elif result == "verified" and post == "success":
            verdict, rollback_text = "Deployed and verified: the requested commit is running.", "none"
        elif result == "verified":
            verdict, rollback_text = "Deployed and verified on the droplet, but the public-page check failed. Not reported as success.", "none"
        elif result == "rolled_back":
            verdict, rollback_text = "FAILED. The requested commit did not verify; production was rolled back to the previous commit and re-verified.", "performed and verified"
        elif result == "rollback_failed":
            verdict, rollback_text = "CRITICAL. The requested commit did not verify and the automatic rollback also failed. Production may be down and needs a person now.", "attempted and failed"
        elif rollback == "no-known-good":
            verdict, rollback_text = "FAILED. No rollback was possible because production was not verified healthy before this run.", "not possible"
        else:
            verdict, rollback_text = "FAILED. The deploy did not verify.", "not attempted" if rollback == "not-attempted" else "unknown"

    rows = [
        "## Production deploy",
        "",
        "| | |",
        "|---|---|",
        "| Requested commit | `%s` |" % sha,
        "| Running before | `%s` |" % before,
        "| Running after | `%s` |" % after,
        "| Mode | %s |" % {"dry-run": "dry run (verify only)", "deploy": "real deploy"}.get(mode, "not accepted"),
        "| Rollback | %s |" % rollback_text,
        "| Baseline before deploy | %s |" % (baseline if baseline in ("passed", "failed") else "not run"),
        "| Public-page check | %s |" % ("not run" if post == "skipped" else post),
        "| Run | %s |" % (run_url if run_url.startswith("https://") else "unavailable"),
        "",
        "**%s**" % verdict,
        "",
        "Output from the droplet is not shown in this log because the repository is public; "
        "only the deploy script's own status lines are.",
    ]
    return "\n".join(rows)


def cmd_summary(_args) -> int:
    print(render_summary(os.environ))
    return 0


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("preflight")
    pre.add_argument("--check-environment", default="", help="environment whose protection rules to read")
    pre.add_argument("--require-branch", default="", help="branch the workflow must be dispatched from")

    ssh = sub.add_parser("setup-ssh")
    ssh.add_argument("--dir", required=True)
    ssh.add_argument("--host", required=True)
    ssh.add_argument("--user", default="root")

    sub.add_parser("filter-log")

    cls = sub.add_parser("classify-log")
    cls.add_argument("log")
    cls.add_argument("--rc", type=int, required=True)
    cls.add_argument("--sha", required=True)

    sub.add_parser("summary")

    args = parser.parse_args(argv)
    if args.command == "preflight":
        if args.check_environment and not ENV_NAME_RE.fullmatch(args.check_environment):
            print("invalid environment name", file=sys.stderr)
            return 2
        return run_preflight(os.environ, args)
    return {
        "setup-ssh": cmd_setup_ssh,
        "filter-log": cmd_filter_log,
        "classify-log": cmd_classify_log,
        "summary": cmd_summary,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
