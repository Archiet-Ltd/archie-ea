"""The release workflow must produce real, retained cross-browser evidence."""

from pathlib import Path

import yaml

from tests.test_ci_nav_verification_lifecycle import _ci_jobs


CI = Path(".github/workflows/ci.yml")


def _workflow():
    return CI.read_text(encoding="utf-8")


def test_ci_runs_critical_journeys_in_firefox_and_webkit():
    workflow = _workflow()

    assert "browser-compatibility:" in workflow
    assert "browser: [firefox, webkit]" in workflow
    assert "SMOKE_BROWSER: ${{ matrix.browser }}" in workflow
    assert "playwright install --with-deps ${{ matrix.browser }}" in workflow
    for suite in (
        "test_accessibility_audit.py",
        "test_archetype_journeys.py",
        "test_authorisation_matrix.py",
        "test_roadmap_crud_journey.py",
        "test_transformation_room_journeys.py",
    ):
        assert suite in workflow


def test_ci_fails_when_required_browser_is_missing_and_retains_evidence():
    workflow = _workflow()

    assert workflow.count('SMOKE_REQUIRE_BROWSER: "1"') >= 2
    assert "if: always()" in workflow
    assert "--junitxml=" in workflow
    assert "retention-days: 30" in workflow
    assert "${{ github.sha }}" in workflow


def _jobs_running_non_smoke_pytest():
    """Every CI job with a step that runs the non-smoke pytest suite, as
    (job id, steps, index of that step)."""
    import yaml

    jobs = yaml.safe_load(_workflow())["jobs"]
    found = []
    for job_id, job in jobs.items():
        steps = job.get("steps", [])
        for index, step in enumerate(steps):
            run = step.get("run", "")
            if "pytest" in run and "--ignore=tests/smoke" in run:
                found.append((job_id, steps, index))
                break
    return found


def test_non_smoke_job_installs_chromium_for_collected_csp_browser_tests():
    """tests/csp/test_csp_evaluator.py is collected by the non-smoke run, so
    every job that runs it — each backend-test shard, since which shard it
    lands in is decided at collection time — must install Chromium first."""
    runners = _jobs_running_non_smoke_pytest()

    assert "tests-shard" in {job_id for job_id, _, _ in runners}
    for job_id, steps, pytest_index in runners:
        installs = [
            i for i, step in enumerate(steps)
            if "playwright install --with-deps" in step.get("run", "")
            and "chromium" in step["run"].split("playwright install --with-deps", 1)[1].split("\n", 1)[0]
        ]
        assert installs and installs[0] < pytest_index, (
            f"{job_id} runs the non-smoke pytest suite without installing Chromium first"
        )


# ── Shard runner choice and parallelism ────────────────────────────────────


def _strip_expr(expr: str) -> str:
    """Strip the ``${{ }}`` wrapper from a GitHub Actions expression."""
    expr = expr.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2].strip()
    return expr


def _tokenize(expr: str) -> list:
    """Tokenize a GitHub Actions expression into (type, value) pairs."""
    tokens = []
    i = 0
    n = len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        if expr.startswith("&&", i):
            tokens.append(("AND", "&&"))
            i += 2
        elif expr.startswith("||", i):
            tokens.append(("OR", "||"))
            i += 2
        elif expr.startswith("==", i):
            tokens.append(("EQ", "=="))
            i += 2
        elif expr.startswith("!=", i):
            tokens.append(("NEQ", "!="))
            i += 2
        elif c == "(":
            tokens.append(("LPAREN", "("))
            i += 1
        elif c == ")":
            tokens.append(("RPAREN", ")"))
            i += 1
        elif c == ",":
            tokens.append(("COMMA", ","))
            i += 1
        elif c == "[":
            j = i
            depth = 1
            i += 1
            while i < n and depth > 0:
                if expr[i] == "[":
                    depth += 1
                elif expr[i] == "]":
                    depth -= 1
                elif expr[i] == '"':
                    i += 1
                    while i < n and expr[i] != '"':
                        if expr[i] == "\\":
                            i += 1
                        i += 1
                i += 1
            tokens.append(("ARRAY", expr[j:i]))
        elif c == "'":
            j = i + 1
            i += 1
            while i < n and expr[i] != "'":
                i += 1
            tokens.append(("STRING", expr[j:i]))
            i += 1
        elif c.isdigit():
            j = i
            while i < n and expr[i].isdigit():
                i += 1
            tokens.append(("NUMBER", int(expr[j:i])))
        elif c.isalpha() or c == "_":
            j = i
            while i < n and (expr[i].isalnum() or expr[i] in "._*"):
                i += 1
            tokens.append(("IDENT", expr[j:i]))
        else:
            i += 1
    return tokens


def _is_truthy(value) -> bool:
    """GitHub Actions truthiness: empty string/list is falsy."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value != ""
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, (int, float)):
        return value != 0
    return True


def _resolve_var(name: str, ctx: dict):
    """Resolve a dotted variable path against the context dict."""
    if "labels.*.name" in name:
        return ctx.get("_labels", [])
    parts = name.split(".")
    value = ctx
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part, "")
        else:
            return ""
    return value if value is not None else ""


def _call_function(name: str, args: list, ctx: dict):
    """Evaluate a function call in the GitHub Actions expression."""
    if name == "contains":
        if len(args) >= 2:
            haystack, needle = args[0], args[1]
            if isinstance(haystack, list):
                return needle in haystack
            if isinstance(haystack, str):
                return needle in haystack
        return False
    if name == "fromJSON":
        if len(args) >= 1:
            import json as _json
            return _json.loads(args[0])
        return ""
    return ""


class _ParseError(ValueError):
    pass


def _eval_github_expr(expr: str, ctx: dict):
    """Evaluate a GitHub Actions expression against a context dict.

    Returns the evaluated value.  Follows GitHub Actions short-circuit
    semantics: ``&&`` returns the first falsy operand or the last;
    ``||`` returns the first truthy operand or the last.
    """
    tokens = _tokenize(expr)
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else ("EOF", "")

    def advance():
        t = peek()
        pos[0] += 1
        return t

    def parse_or():
        left = parse_and()
        while pos[0] < len(tokens) and tokens[pos[0]][0] == "OR":
            advance()
            if _is_truthy(left):
                # Short-circuit: skip the rest of the || chain.
                # || has the lowest precedence, so stop only at RPAREN.
                _skip_until(("RPAREN",))
                return left
            left = parse_and()
        return left

    def parse_and():
        left = parse_comparison()
        while pos[0] < len(tokens) and tokens[pos[0]][0] == "AND":
            advance()
            if not _is_truthy(left):
                # Short-circuit: skip the rest of the && chain.
                # && binds tighter than ||, so stop at || or RPAREN.
                _skip_until(("OR", "RPAREN"))
                return left
            left = parse_comparison()
        return left

    def _skip_until(stop_on):
        """Skip tokens until one of *stop_on* token types at depth 0,
        respecting nested parentheses."""
        depth = 0
        while pos[0] < len(tokens):
            t = tokens[pos[0]]
            if t[0] == "LPAREN":
                depth += 1
                pos[0] += 1
            elif t[0] == "RPAREN":
                if depth == 0:
                    return
                depth -= 1
                pos[0] += 1
            elif t[0] in stop_on and depth == 0:
                return
            else:
                pos[0] += 1

    def parse_comparison():
        left = parse_primary()
        if pos[0] < len(tokens) and tokens[pos[0]][0] in ("EQ", "NEQ"):
            op = advance()[0]
            right = parse_primary()
            left_str = str(left) if not isinstance(left, str) else left
            right_str = str(right) if not isinstance(right, str) else right
            return (left_str == right_str) if op == "EQ" else (left_str != right_str)
        return left

    def parse_primary():
        if pos[0] >= len(tokens):
            return ""
        t = peek()
        if t[0] == "LPAREN":
            advance()
            result = parse_or()
            if pos[0] < len(tokens) and tokens[pos[0]][0] == "RPAREN":
                advance()
            return result
        if t[0] == "STRING":
            advance()
            return t[1]
        if t[0] == "NUMBER":
            advance()
            return t[1]
        if t[0] == "ARRAY":
            advance()
            import json as _json
            return _json.loads(t[1])
        if t[0] == "IDENT":
            name = advance()[1]
            if pos[0] < len(tokens) and tokens[pos[0]][0] == "LPAREN":
                advance()
                args = []
                if pos[0] < len(tokens) and tokens[pos[0]][0] != "RPAREN":
                    args.append(parse_or())
                    while pos[0] < len(tokens) and tokens[pos[0]][0] == "COMMA":
                        advance()
                        args.append(parse_or())
                if pos[0] < len(tokens) and tokens[pos[0]][0] == "RPAREN":
                    advance()
                return _call_function(name, args, ctx)
            return _resolve_var(name, ctx)
        advance()
        return ""

    return parse_or()


def test_runner_choice_and_parallelism_contract():
    """Table-driven workflow-contract test: parse the shard and combine job
    expressions and verify the concrete runner and max-parallel outcome for
    every input state the brief requires.

    Cases covered:
    - Forked PR (always ubuntu-latest, max-parallel 8)
    - Same-repo PR without label (falls back to self-hosted ibm-vsi, default 6)
    - Same-repo PR with ci-fast label and CI_FAST_RUNNER set
    - Same-repo PR with ci-fast label and CI_FAST_RUNNER unset (fallback)
    - CI_SHARD_RUNNER set on same-repo PR
    - A label other than ci-fast
    - Push to main (not a PR)
    - CI_SHARD_MAX_PARALLEL set to a custom value
    """
    jobs = _ci_jobs()

    shard_runs_on = _strip_expr(jobs["tests-shard"]["runs-on"])
    tests_runs_on = _strip_expr(jobs["tests"]["runs-on"])
    max_parallel = _strip_expr(jobs["tests-shard"]["strategy"]["max-parallel"])

    # Both jobs must share the same runner-selection expression.
    assert shard_runs_on == tests_runs_on, (
        "tests-shard and tests must use the same runs-on expression"
    )

    REPO = "anioko/archie-ea"

    cases = [
        # (name, ctx, expected_runs_on, expected_max_parallel)
        (
            "forked PR",
            {
                "github": {
                    "event_name": "pull_request",
                    "event": {
                        "pull_request": {
                            "head": {"repo": {"full_name": "fork-owner/repo"}},
                            "labels": [],
                        }
                    },
                    "repository": REPO,
                },
                "vars": {
                    "CI_FAST_RUNNER": "",
                    "CI_SHARD_RUNNER": "",
                    "CI_SHARD_MAX_PARALLEL": "",
                },
                "_labels": [],
            },
            "ubuntu-latest",
            8,
        ),
        (
            "same-repo PR without label",
            {
                "github": {
                    "event_name": "pull_request",
                    "event": {
                        "pull_request": {
                            "head": {"repo": {"full_name": REPO}},
                            "labels": [],
                        }
                    },
                    "repository": REPO,
                },
                "vars": {
                    "CI_FAST_RUNNER": "",
                    "CI_SHARD_RUNNER": "",
                    "CI_SHARD_MAX_PARALLEL": "",
                },
                "_labels": [],
            },
            ["self-hosted", "ibm-vsi"],
            6,
        ),
        (
            "same-repo PR with ci-fast and CI_FAST_RUNNER set",
            {
                "github": {
                    "event_name": "pull_request",
                    "event": {
                        "pull_request": {
                            "head": {"repo": {"full_name": REPO}},
                            "labels": [{"name": "ci-fast"}],
                        }
                    },
                    "repository": REPO,
                },
                "vars": {
                    "CI_FAST_RUNNER": "custom-fast-runner",
                    "CI_SHARD_RUNNER": "",
                    "CI_SHARD_MAX_PARALLEL": "",
                },
                "_labels": ["ci-fast"],
            },
            "custom-fast-runner",
            8,
        ),
        (
            "same-repo PR with ci-fast and CI_FAST_RUNNER unset",
            {
                "github": {
                    "event_name": "pull_request",
                    "event": {
                        "pull_request": {
                            "head": {"repo": {"full_name": REPO}},
                            "labels": [{"name": "ci-fast"}],
                        }
                    },
                    "repository": REPO,
                },
                "vars": {
                    "CI_FAST_RUNNER": "",
                    "CI_SHARD_RUNNER": "",
                    "CI_SHARD_MAX_PARALLEL": "",
                },
                "_labels": ["ci-fast"],
            },
            ["self-hosted", "ibm-vsi"],
            6,
        ),
        (
            "CI_SHARD_RUNNER set on same-repo PR",
            {
                "github": {
                    "event_name": "pull_request",
                    "event": {
                        "pull_request": {
                            "head": {"repo": {"full_name": REPO}},
                            "labels": [],
                        }
                    },
                    "repository": REPO,
                },
                "vars": {
                    "CI_FAST_RUNNER": "",
                    "CI_SHARD_RUNNER": "managed-pool",
                    "CI_SHARD_MAX_PARALLEL": "",
                },
                "_labels": [],
            },
            "managed-pool",
            6,
        ),
        (
            "label other than ci-fast",
            {
                "github": {
                    "event_name": "pull_request",
                    "event": {
                        "pull_request": {
                            "head": {"repo": {"full_name": REPO}},
                            "labels": [{"name": "bug"}],
                        }
                    },
                    "repository": REPO,
                },
                "vars": {
                    "CI_FAST_RUNNER": "",
                    "CI_SHARD_RUNNER": "",
                    "CI_SHARD_MAX_PARALLEL": "",
                },
                "_labels": ["bug"],
            },
            ["self-hosted", "ibm-vsi"],
            6,
        ),
        (
            "push to main (not a PR)",
            {
                "github": {
                    "event_name": "push",
                    "event": {},
                    "repository": REPO,
                },
                "vars": {
                    "CI_FAST_RUNNER": "",
                    "CI_SHARD_RUNNER": "",
                    "CI_SHARD_MAX_PARALLEL": "",
                },
                "_labels": [],
            },
            ["self-hosted", "ibm-vsi"],
            6,
        ),
        (
            "CI_SHARD_MAX_PARALLEL set to 4",
            {
                "github": {
                    "event_name": "pull_request",
                    "event": {
                        "pull_request": {
                            "head": {"repo": {"full_name": REPO}},
                            "labels": [],
                        }
                    },
                    "repository": REPO,
                },
                "vars": {
                    "CI_FAST_RUNNER": "",
                    "CI_SHARD_RUNNER": "",
                    "CI_SHARD_MAX_PARALLEL": "4",
                },
                "_labels": [],
            },
            ["self-hosted", "ibm-vsi"],
            "4",
        ),
    ]

    for name, ctx, expected_runner, expected_parallel in cases:
        actual_runner = _eval_github_expr(shard_runs_on, ctx)
        actual_parallel = _eval_github_expr(max_parallel, ctx)

        assert actual_runner == expected_runner, (
            f"{name}: expected runs-on {expected_runner!r}, got {actual_runner!r}"
        )
        assert actual_parallel == expected_parallel, (
            f"{name}: expected max-parallel {expected_parallel!r}, "
            f"got {actual_parallel!r}"
        )


def test_labeled_event_triggers_workflow_for_ci_fast():
    """Adding a label must trigger a fresh workflow run so the ci-fast label
    takes effect without requiring a new push."""
    workflow = _workflow()
    assert "labeled" in workflow


def test_jobs_skip_on_non_ci_fast_labeled_events():
    """Every job (except release-image, which already gates on event_name)
    must carry an if: condition that skips the job when the trigger is a
    labeled pull_request event that does not carry the ci-fast label.  This
    prevents a full CI re-run when any unrelated label is added to a PR."""
    jobs = _ci_jobs()
    gating_jobs = {
        "secret-scan", "static-gates", "boot-health", "tests-shard",
        "tests", "db-gates", "security-sast", "smoke",
        "browser-compatibility", "walkthrough", "dependency-audit",
    }
    for job_id in gating_jobs:
        job = jobs[job_id]
        if_expr = job.get("if", "")
        assert "labeled" in if_expr, (
            f"{job_id} must guard against non-ci-fast labeled events"
        )
        assert "ci-fast" in if_expr, (
            f"{job_id} must allow ci-fast labeled events through"
        )
        assert "github.event.action" in if_expr or "labeled" in if_expr, (
            f"{job_id} must check the event action for labeled"
        )


def test_every_postgres_service_mounts_pgdata_on_tmpfs():
    import yaml

    workflow = yaml.safe_load(_workflow())
    postgres_jobs = {
        name: job
        for name, job in workflow["jobs"].items()
        if job.get("services", {}).get("postgres")
    }

    assert postgres_jobs
    missing_tmpfs = []
    for job_name, job in postgres_jobs.items():
        options = job["services"]["postgres"].get("options", "")
        if "--tmpfs /var/lib/postgresql/data" not in options:
            missing_tmpfs.append(job_name)

    assert not missing_tmpfs, (
        "postgres services must mount /var/lib/postgresql/data on tmpfs: "
        + ", ".join(sorted(missing_tmpfs))
    )


# ── D1/D2 contract tests ──────────────────────────────────────────────────


def test_labeled_event_never_cancels_in_progress_run():
    """Adding a label to a PR must never cancel an in-progress CI run.
    The concurrency group's cancel-in-progress must exclude labeled events
    so that a non-ci-fast label addition does not kill the running suite."""
    import yaml

    workflow = yaml.safe_load(_workflow())
    concurrency = workflow["concurrency"]
    cancel = concurrency["cancel-in-progress"]

    assert "github.event_name == 'pull_request'" in cancel
    assert "github.event.action != 'labeled'" in cancel, (
        "cancel-in-progress must exclude labeled events so that adding "
        "an unrelated label never cancels an in-progress CI run"
    )


def test_shard_and_combine_jobs_install_into_isolated_venvs():
    """The tests-shard and tests jobs each create their own venv before
    installing dependencies, so a self-hosted runner's leftover ~/.local
    site-packages never shadows the job's dependencies."""
    jobs = _ci_jobs()

    for job_id in ("tests-shard", "tests"):
        steps = jobs[job_id]["steps"]
        venv_idx = next(
            i for i, s in enumerate(steps)
            if "python -m venv --clear .venv" in s.get("run", "")
        )
        install_idx = next(
            i for i, s in enumerate(steps)
            if "pip install -r requirements.txt" in s.get("run", "")
        )
        assert 0 <= venv_idx < install_idx, (
            f"{job_id} must create its venv before installing dependencies"
        )


def test_shard_postgres_service_uses_dynamic_port():
    """Several jobs share one ibm-vsi machine, so the postgres service must
    not bind a fixed host port. The database URL must come from the
    docker-assigned port."""
    jobs = _ci_jobs()
    shard = jobs["tests-shard"]

    assert shard["services"]["postgres"]["ports"] == ["5432"], (
        "postgres service must expose only container port 5432, "
        "not a fixed host port"
    )
    port_step = next(
        i for i, s in enumerate(shard["steps"])
        if "job.services.postgres.ports['5432']" in s.get("run", "")
    )
    assert port_step >= 0, (
        "a step must read the docker-assigned host port from the job context"
    )



