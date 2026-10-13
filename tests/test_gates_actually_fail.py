"""Every gate must be watched failing, on every run — not once, by hand.

docs/TESTING_STANDARD.md rule 7 has always required it: "Reintroduce the defect,
watch the gate go red, restore, watch it go green. A checker nobody has seen fail
is just a number." The evidence-contract gate enforces that a checker CARRIES a
`Proven-against:` line, which is a claim that someone once did that. This file
turns the claim into a measurement that reruns forever.

Twice today a gate written in this session reported 0 while the defect it was
built for sat right there in the tree:

* the first `authz-widening` probe left the `Permission` import in place, so the
  gate correctly saw a permission check and stayed green — and for a few minutes
  I believed a fake gate was real;
* the first `ai-untrusted-content` probe wrote a broken f-string, the checker's
  `except SyntaxError` skipped the file, and it reported 0 for a defect that was
  present.

Both were caught by chance. A gate that cannot fail is worse than no gate,
because it is counted as coverage — which is the whole thesis of this codebase.

Every checker here accepts `--root`, so each case builds a MINIMAL synthetic
tree containing exactly the defect and runs the checker against it. Nothing in
the real repository is mutated, the cases are independent, and they can run in
parallel. Each case asserts both directions: the bad tree is non-zero AND the
clean tree is zero. Asserting only "red" would pass for a checker that returns a
positive count for everything.

Two checkers are deliberately absent from the --root convention, and naming
them is the point -- a hollow case in THIS file would defeat the file.

check_canonical_route.py reads a BOOTED url_map, because a static scan of
@route decorators cannot see a blueprint's url_prefix, cannot see which side
the USE_*_GUARDRAILS flags selected, and cannot see that init_blueprints
logged an import failure and carried on. Its collision logic is therefore kept
separate from the booting, and it IS pinned red-and-green below against a
hand-built two-blueprint Flask app.

check_evidence_contract.py reads real git history and the verify.py registry,
and has no --root, so a synthetic tree cannot drive it. Its rule-2 substance is covered directly instead, by
test_every_registered_checker_carries_its_proof below. Naming the exclusion is
the point -- a hollow case in THIS file would defeat the file.
"""

import json
import os
import subprocess
import sys

import pytest

NEWLINE = chr(10)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")


def _run_checker(script, root):
    """Run a checker against a synthetic tree and return its count."""
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script), "--count", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO,
    )
    trailing = (proc.stdout or "").strip().splitlines()
    assert trailing, (
        "%s produced no count for root=%s\nstdout=%r\nstderr=%r"
        % (script, root, proc.stdout, proc.stderr[:400])
    )
    try:
        return int(trailing[-1])
    except ValueError:
        raise AssertionError(
            "%s did not end with a count: %r (stderr=%r)"
            % (script, trailing[-1], proc.stderr[:400])
        )


def _write(root, relpath, content):
    path = root.join(*relpath.split("/"))
    path.dirpath().ensure_dir()
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Each case: (checker, builder(root, defective) -> None)
# The builder writes a tree that is defective when `defective` is True and
# otherwise identical but clean. Keeping one builder for both halves is
# deliberate: it makes the DIFFERENCE the thing under test, so a case cannot
# accidentally compare two unrelated trees.
# --------------------------------------------------------------------------


def _inline_handlers(root, defective):
    handler = ' onchange="this.form.submit()"' if defective else " data-autosubmit"
    _write(root, "app/templates/probe.html",
           "<form><select name='x'%s><option>1</option></select></form>" % handler)


def _nested_jinja(root, defective):
    inner = "{{ page_header(title='{{ x.name }}') }}" if defective else "{{ page_header(title=x.name) }}"
    _write(root, "app/templates/probe.html", inner)


def _credential_autofill(root, defective):
    extra = "" if defective else ' autocomplete="new-password"'
    _write(root, "app/templates/probe.html",
           '<input type="password" name="api_key"%s>' % extra)


def _unreachable_actions(root, defective):
    allowed = '{"approve"}' if defective else '{"approve", "archive"}'
    _write(root, "app/probe.py",
           "def handler(action):\n"
           "    valid = %s\n"
           "    if action not in valid:\n"
           "        return 400\n"
           "    if action == 'archive':\n"
           "        return 1\n" % allowed)


def _page_cost(root, defective):
    expr = "len(Model.query.all())" if defective else "Model.query.count()"
    _write(root, "app/probe.py", "def handler():\n    return %s\n" % expr)


def _canonical_store(root, defective):
    second = ('\n\nclass Shadow(db.Model):\n    __tablename__ = "widgets"\n'
              if defective else "")
    _write(root, "app/models/probe.py",
           'class Widget(db.Model):\n    __tablename__ = "widgets"\n' + second)


def _nullable_columns(root, defective):
    tail = "" if defective else ', server_default="x"'
    _write(root, "app/models/probe.py",
           "class Widget(db.Model):\n"
           "    name = db.Column(db.String(10), nullable=False%s)\n" % tail)


def _archimate_backbone(root, defective):
    sync = "" if defective else "    _sync_archimate_element(d)\n"
    _write(root, "app/probe.py",
           "def create():\n"
           "    d = Driver(name='x')\n"
           "    db.session.add(d)\n" + sync)


def _cache_tenancy(root, defective):
    key = "domain" if defective else "(org_id, domain)"
    _write(root, "app/probe.py",
           "_thing_cache = {}\n\n"
           "def get(domain, org_id):\n"
           "    org_id = current_org_id\n"
           "    _thing_cache[%s] = 1\n" % key)


def _ai_evidence_rules(root, defective):
    rules = "" if defective else "{_EVIDENCE_RULES}"
    _write(root, "app/modules/ai_chat/services/architect_persona_charters.py",
           '_EVIDENCE_RULES = """rules"""\n\n'
           'def build_architect_prompt(p):\n'
           '    return "your ONLY source for numbers"\n\n'
           'CHARTERS: Dict[str, str] = {\n'
           '    "cto": f"""You are the CTO persona.\n%s""",\n}\n' % rules)


CASES = [
    ("check_inline_handlers.py", _inline_handlers),
    ("check_nested_jinja.py", _nested_jinja),
    ("check_credential_autofill.py", _credential_autofill),
    ("check_unreachable_actions.py", _unreachable_actions),
    ("check_page_cost.py", _page_cost),
    ("check_canonical_store.py", _canonical_store),
    ("check_nullable_columns.py", _nullable_columns),
    ("check_archimate_backbone.py", _archimate_backbone),
    ("check_cache_tenancy.py", _cache_tenancy),
    ("check_ai_evidence_rules.py", _ai_evidence_rules),
]


# --------------------------------------------------------------------------
# Gates whose fixtures need more than one file. Written as real source strings
# rather than escaped one-liners: these builders ARE the specification of what
# each gate considers a defect, so they have to stay readable.
# --------------------------------------------------------------------------

_USER_MODEL = '''ROLE_CTO = "cto"
ROLE_EA = "enterprise_architect"

VALID_ROLES = [
    ROLE_CTO,
    ROLE_EA,
]
'''


def _persona_vocabularies(root, defective):
    """A role in VALID_ROLES with no IdP group can never be provisioned."""
    _write(root, "app/models/user.py", _USER_MODEL)
    cto_group = "" if defective else '    "CTO": "cto",\n'
    _write(root, "app/auth/sso.py",
           "DEFAULT_GROUP_ROLE_MAP = {\n"
           '    "EA-Architects": "enterprise_architect",\n'
           + cto_group +
           "}\n")
    _write(root, "app/modules/ai_chat/services/architect_persona_charters.py",
           'ARCHITECT_PERSONAS = (\n    "cto",\n    "enterprise_architect",\n)\n\n'
           "PERSONA_ALIASES: Dict[str, str] = {\n}\n")


def _journey_coverage(root, defective):
    """A persona with no journey that writes and asserts is unproven."""
    _write(root, "app/models/user.py", _USER_MODEL)
    if defective:
        body = "def test_nothing():\n    pass\n"
    else:
        body = ('def test_a_cto_does_their_job(client):\n'
                '    r = client.post("/x", json={"role": "cto"})\n'
                '    assert r.status_code == 201\n\n'
                'def test_an_ea_does_their_job(client):\n'
                '    r = client.post("/y", json={"role": "enterprise_architect"})\n'
                '    assert r.status_code == 201\n')
    _write(root, "tests/journeys/test_probe.py", body)


def _authz_widening(root, defective):
    """A role granted from a user-settable field with no permission check."""
    guard = "" if defective else "            if current_user.can(Permission.GENERAL):\n"
    _write(root, "app/_decorators_base.py",
           "def require_roles(*allowed):\n"
           "    def decorator(f):\n"
           "        def decorated_function(*a, **kw):\n"
           "            user_roles = set()\n"
           '            role = getattr(current_user, "enterprise_role", None)\n'
           + guard +
           "            user_roles.add(role)\n"
           "            return f(*a, **kw)\n"
           "        return decorated_function\n"
           "    return decorator\n")


def _ai_approval_honoured(root, defective):
    """A user preference must not decide whether AI writes need approval."""
    if defective:
        source = ("def send():\n"
                  "    runner = AgentRunner(user_id=1, "
                  'auto_execute=flask_session.get("agent_auto_execute", False))\n')
    else:
        source = ("def _allowed():\n"
                  '    return not current_app.config.get("REQUIRE_AI_APPROVAL", True)\n\n\n'
                  "def send():\n"
                  "    runner = AgentRunner(user_id=1, auto_execute=_allowed())\n")
    _write(root, "app/modules/ai_chat/routes/chat_core.py", source)


def _ai_untrusted_content(root, defective):
    """Retrieved content must be fenced before it reaches the system prompt."""
    if defective:
        line = '    ctx["system_prompt"] = f"Context: {_rag_ctx}" + ctx["system_prompt"]\n'
    else:
        line = ('    ctx["system_prompt"] = ctx["system_prompt"] + '
                'fence_untrusted("RAG", _rag_ctx)\n')
    _write(root, "app/modules/ai_chat/services/probe_service.py",
           "def build(ctx, _rag_ctx):\n" + line)


def _ai_tool_guard(root, defective):
    """No _tool_* handler may be reached outside the permission choke point."""
    _write(root, "app/modules/ai_chat/tools/registry.py",
           'TOOL_SCHEMAS = [\n    {"name": "create_thing", "mutates": True},\n]\n')
    _write(root, "app/modules/ai_chat/tools/executor.py",
           "class ToolExecutor:\n"
           "    def _tool_create_thing(self, args):\n"
           "        db.session.add(1)\n")
    call = ("    return ex._tool_create_thing({})\n" if defective
            else "    return ex.execute(call)\n")
    _write(root, "app/modules/ai_chat/services/caller.py",
           "def run(ex, call):\n" + call)


CASES += [
    ("check_persona_vocabularies.py", _persona_vocabularies),
    ("check_journey_coverage.py", _journey_coverage),
    ("check_authz_widening.py", _authz_widening),
    ("check_ai_approval_honoured.py", _ai_approval_honoured),
    ("check_ai_untrusted_content.py", _ai_untrusted_content),
    ("check_ai_tool_guard.py", _ai_tool_guard),
]


def _empty_state_cta(root, defective):
    """An empty state that names no next action is a dead end."""
    cta = "" if defective else ", cta_label='Add an application', cta_href='/apps/new'"
    _write(root, "app/templates/probe.html",
           "{% macro empty_state(icon, title, cta_label=None, cta_href=None) %}\n"
           "<div>{{ title }}</div>\n"
           "{% endmacro %}\n"
           "{{ empty_state(icon='layout-grid', "
           "title='No applications found.'" + cta + ") }}\n")


def _role_gate_coverage(root, defective):
    """A role in the delivery contract whose tags match no gate in the registry."""
    _write(root, "scripts/verify.py",
           "def build_gates(baseline):\n"
           "    return [\n"
           "        Gate('ai-tool-guard', 'd', 'ratchet', f, tags=['static', 'ai']),\n"
           "    ]\n")
    tags_cell = "-" if defective else "`ai`"
    _write(root, "docs/DELIVERY_CONTRACT.md",
           "| Role | Gate tags | Gates |\n"
           "|---|---|---|\n"
           "| AI / ML architect | " + tags_cell + " | 1 |\n")


CASES.append(("check_role_gate_coverage.py", _role_gate_coverage))


def _docs_drift(root, defective):
    """CLAUDE.md's stated gate count disagreeing with build_gates()."""
    _write(root, "scripts/verify.py",
           "def build_gates(baseline):\n"
           "    return [\n"
           "        Gate('compile', 'd', 'command', f, tags=['static']),\n"
           "        Gate('tests', 'd', 'command', f, tags=['runtime']),\n"
           "    ]\n")
    claimed = 1 if defective else 2
    _write(root, "CLAUDE.md",
           "## Verification\n\n"
           "All %d gates, in registry order (`scripts/verify.py`, `build_gates`):\n\n"
           "| Gate | Catches | Kind |\n"
           "|---|---|---|\n"
           "| `compile` | d | must pass |\n"
           "| `tests` | d | must pass (needs DB) |\n" % claimed)
    # No docs/DELIVERY_CONTRACT.md in this synthetic tree: check_docs_drift's
    # _read() returns [] on a missing file, so that half of the checker is a
    # silent no-op here and only the CLAUDE.md half is under test.


CASES.append(("check_docs_drift.py", _docs_drift))


def _unregistered_checks(root, defective):
    """A scripts/check_*.py file absent from verify.py's build_gates() registry."""
    _write(root, "scripts/verify.py",
           "def build_gates(baseline):\n"
           "    return [\n"
           "        Gate('compile', 'd', 'command', f, tags=['static']),\n"
           "    ]\n")
    _write(root, "scripts/check_probe_thing.py", "\"\"\"A probe checker.\"\"\"\n")
    if not defective:
        # "Registering" it is nothing more than the filename appearing as a
        # string literal somewhere in verify.py, matching every real Gate().
        _write(root, "scripts/verify.py",
               "def build_gates(baseline):\n"
               "    return [\n"
               "        Gate('compile', 'd', 'command', f, tags=['static']),\n"
               "        Gate('probe', 'd', 'zero', lambda: _run(['scripts/check_probe_thing.py'])),\n"
               "    ]\n")


CASES.append(("check_unregistered_checks.py", _unregistered_checks))


CASES.append(("check_empty_state_cta.py", _empty_state_cta))


def _business_layer_backbone(root, defective):
    """A capability with no ArchiMate element is invisible to the lenses."""
    sync = "" if defective else "    sync_archimate_element(cap)\n"
    _write(root, "app/probe.py",
           "def create():\n"
           "    cap = BusinessCapability(name='Billing')\n"
           "    db.session.add(cap)\n" + sync)


def _api_envelope(root, defective):
    """A handler that commits to no response shape forces callers to guess."""
    ret = ("    return jsonify({'items': []})\n" if defective
           else "    return success_response({'items': []})\n")
    _write(root, "app/probe.py",
           "@bp.route('/things')\n"
           "def list_things():\n" + ret)


CASES.append(("check_business_layer_backbone.py", _business_layer_backbone))
CASES.append(("check_api_envelope.py", _api_envelope))


def _untyped_property_writes(root, defective):
    """An element property write outside PropertyService bypasses typing."""
    if defective:
        body = "def f(element):\n    element.properties = '{}'\n"
    else:
        body = (
            "from app.modules.architecture_assistant.property_service import PropertyService\n\n"
            "def f(element):\n"
            "    PropertyService().set_element_property(element, 'rate_limit', '1000 req/min')\n"
        )
    _write(root, "app/probe.py", body)


CASES.append(("check_untyped_property_writes.py", _untyped_property_writes))


def _collapsed_nav_affordance(root, defective):
    """A collapsed rail with no tooltip is a row of unlabelled buttons."""
    named = "" if defective else ' title="Dashboard"'
    # The template must reference the collapse mechanism, because the gate
    # scopes itself to templates that actually collapse -- breadcrumbs and the
    # public navbar are icon-bearing nav that never does.
    _write(root, "app/templates/components/admin_sidebar.html",
           "<aside :style=\"{ width: $store.sidebar.collapsed ? '4rem' : '16rem' }\">"
           "<nav><a href='/x'" + named + ">"
           "<i data-lucide='layout-dashboard'></i>"
           "<span class='truncate'>Dashboard</span></a></nav></aside>")


def _nav_icon_ambiguity(root, defective):
    """Two destinations behind one icon in one persona's own menu."""
    second = "compass" if defective else "map"
    rows = [
        "ZONES = {",
        "    ROLE_ENTERPRISE_ARCHITECT: [",
        '        _link("Traceability", "trace.index", "compass"),',
        '        _link("Impact", "impact.index", "' + second + '"),',
        "    ],",
        "}",
    ]
    _write(root, "app/utils/role_access.py", NEWLINE.join(rows) + NEWLINE)


def _nav_label_clarity(root, defective):
    """One label naming two different destinations."""
    second = "Applications" if defective else "My Applications"
    rows = [
        "ZONES = {",
        "    ROLE_PORTFOLIO_MANAGER: [",
        '        _link("Applications", "apps.index", "list"),',
        '        _link("' + second + '", "apps.mine", "user"),',
        "    ],",
        "}",
    ]
    _write(root, "app/utils/role_access.py", NEWLINE.join(rows) + NEWLINE)


def _handoff_continuity(root, defective):
    """Work moved to a handoff state that no reachable surface reads back."""
    _write(root, "app/utils/role_access.py",
           "ZONES = {" + NEWLINE +
           "    ROLE_ARB_MEMBER: [" + NEWLINE +
           '        _link("ARB Dashboard", "arb.dashboard", "shield-check"),' + NEWLINE +
           "    ]," + NEWLINE +
           "}" + NEWLINE)
    _write(root, "app/modules/journey/routes/submit_routes.py",
           'journey_bp = Blueprint("journey", __name__)' + NEWLINE +
           "def submit(solution):" + NEWLINE +
           '    solution.governance_status = "pending_approval"' + NEWLINE)
    # The reachable ARB queue reads the state back only in the clean tree.
    reads = "" if defective else (
        '    return Review.query.filter_by(status="pending_approval").all()' + NEWLINE)
    _write(root, "app/modules/architecture/routes/arb_routes.py",
           'arb_bp = Blueprint("arb", __name__)' + NEWLINE +
           "def queue():" + NEWLINE +
           reads +
           "    return []" + NEWLINE)


def _metric_provenance(root, defective):
    """A proportion shown to the user that is written in the source."""
    value = "87" if defective else "_measured_coverage()"
    _write(root, "app/modules/reports/routes/coverage_routes.py",
           'reports_bp = Blueprint("reports", __name__)' + NEWLINE +
           "def _measured_coverage():" + NEWLINE +
           "    return Capability.query.filter_by(mapped=True).count()" + NEWLINE +
           "def coverage():" + NEWLINE +
           '    return jsonify({"coverage_percent": ' + value + "})" + NEWLINE)


CASES += [
    ("check_collapsed_nav_affordance.py", _collapsed_nav_affordance),
    ("check_nav_icon_ambiguity.py", _nav_icon_ambiguity),
    ("check_nav_label_clarity.py", _nav_label_clarity),
    ("check_handoff_continuity.py", _handoff_continuity),
    ("check_metric_provenance.py", _metric_provenance),
]


@pytest.mark.parametrize("script,builder", CASES, ids=[c[0] for c in CASES])
def test_the_gate_fires_on_its_own_defect(script, builder, tmpdir):
    """Red on the defect, green without it. Both halves, every run."""
    bad = tmpdir.mkdir("bad")
    builder(bad, defective=True)
    bad_count = _run_checker(script, bad)

    good = tmpdir.mkdir("good")
    builder(good, defective=False)
    good_count = _run_checker(script, good)

    assert bad_count > 0, (
        "%s reported 0 against a tree built to contain exactly the defect it "
        "exists to catch. The gate is decoration: it can be counted as coverage "
        "and can never fail. Fix the checker, or fix this fixture if the defect "
        "shape has moved." % script
    )
    assert good_count == 0, (
        "%s reported %d against the CLEAN tree. A checker that fires on correct "
        "code trains people to ignore it, which is worse than not having it."
        % (script, good_count)
    )
    assert bad_count > good_count


# --------------------------------------------------------------------------
# check_public_repo_hygiene.py's rule-3 extensions (review-record-id tokens,
# pipeline role words, commit messages): standalone rather than a CASES
# entry, the same reason check_evidence_contract.py and check_canonical_route
# are standalone (see this file's own docstring) -- the generic
# _run_checker(script, root) invocation has no room for the extra --rule
# flag these need, and the commit-message half needs a real git repository
# inside tmpdir, not just files.
# --------------------------------------------------------------------------


def _run_hygiene_checker_raw(root, rule, rev_range=None):
    """The unparsed subprocess result, for a test that needs to inspect
    the return code or stderr directly rather than assert a count exists."""
    cmd = [sys.executable, os.path.join(SCRIPTS, "check_public_repo_hygiene.py"),
           "--count", "--root", str(root), "--rule", rule]
    if rev_range:
        cmd += ["--range", rev_range]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)


def _run_hygiene_checker(root, rule, rev_range=None):
    proc = _run_hygiene_checker_raw(root, rule, rev_range=rev_range)
    trailing = (proc.stdout or "").strip().splitlines()
    assert trailing, (
        "check_public_repo_hygiene.py --rule %s produced no count for root=%s\n"
        "stdout=%r\nstderr=%r" % (rule, root, proc.stdout, proc.stderr[:400])
    )
    try:
        return int(trailing[-1])
    except ValueError:
        raise AssertionError(
            "check_public_repo_hygiene.py --rule %s did not end with a count: %r "
            "(stderr=%r)" % (rule, trailing[-1], proc.stderr[:400])
        )


def test_public_repo_hygiene_record_id_gate_fires_on_its_own_defect(tmpdir):
    """A review-record-id token (D2-7 shape) committed to a tracked source file."""  # hygiene-ok: names the probe shape used below, not a real hit
    bad = tmpdir.mkdir("bad")
    _write(bad, "app/probe.py", '# See D2-7 for the reasoning behind this default.\n')  # hygiene-ok: deliberate probe content, written to a tmpdir this checker never scans
    bad_count = _run_hygiene_checker(bad, "content")

    good = tmpdir.mkdir("good")
    _write(good, "app/probe.py", "# The reasoning behind this default is explained inline.\n")
    good_count = _run_hygiene_checker(good, "content")

    assert bad_count > 0, "a 'D2-7'-shaped token in a tracked .py file was not flagged"  # hygiene-ok: quoting the probe shape in the assertion message, not a real hit
    assert good_count == 0, "the checker fired on a clean file"
    assert bad_count > good_count


def test_public_repo_hygiene_record_id_allowlist_excludes_standards_prefixes(tmpdir):
    """CVE-/RFC-/ISO-/IEC-/UTF- never match the pattern in the first place
    (three-plus-letter prefixes, the pattern caps at two) -- proving that
    directly, not just asserting it, since a future edit to the pattern
    could silently start matching them."""
    root = tmpdir.mkdir("standards")
    _write(root, "app/probe.py",
           "# CVE-2024-12345, RFC-6902, ISO-8859, IEC-61508, UTF-8: none of these "
           "are review record ids.\n")
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_role_word_gate_fires_on_its_own_defect(tmpdir):
    """A pipeline role word committed to a tracked source file."""
    bad = tmpdir.mkdir("bad")
    _write(bad, "app/probe.py",
           "# D-5 (refuter): fixed in round-3, per the brief and the build report.\n")  # hygiene-ok: deliberate probe content, written to a tmpdir this checker never scans
    bad_count = _run_hygiene_checker(bad, "content")

    good = tmpdir.mkdir("good")
    _write(good, "app/probe.py", "# Fixed in the third pass, per the requirements.\n")
    good_count = _run_hygiene_checker(good, "content")

    assert bad_count > 0, "pipeline role words in a tracked .py file were not flagged"
    assert good_count == 0, "the checker fired on a clean file"
    assert bad_count > good_count


def test_public_repo_hygiene_role_word_round_needs_a_digit(tmpdir):
    """'round' alone (a table round, a review round with no number, the
    ordinary English word) must not fire -- only round<N> does."""
    root = tmpdir.mkdir("round")
    _write(root, "app/probe.py",
           "# Table is round; go another round of edits before the next round.\n")
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_content_scan_ignores_executable_code(tmpdir):
    """The content scan is narrowed to comments, docstrings and string
    literals -- an identifier that happens to spell out a role word in
    executable code (not a comment, not a string) must not fire."""
    root = tmpdir.mkdir("code")
    _write(root, "app/probe.py", "x = orchestrator_service()\n")
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_content_scan_still_catches_comments_and_docstrings(tmpdir):
    """The same probes, moved into a comment and a docstring, still fire --
    proving the narrowing above excludes code, not everything."""
    root = tmpdir.mkdir("still-fires")
    _write(root, "app/probe.py",
           '"""per the brief, round 2"""\n'  # hygiene-ok: probe data for a tmpdir file this checker never scans, not a real hit
           "# orchestrator said so\n")  # hygiene-ok: probe data for a tmpdir file this checker never scans, not a real hit
    assert _run_hygiene_checker(root, "content") >= 2


def test_public_repo_hygiene_content_scan_reads_fstring_literal_text(tmpdir):
    """An f-string's own literal text tokenises as one or more
    FSTRING_MIDDLE tokens on the interpreter this codebase runs, never as a
    single STRING token the way every other string literal does -- it must
    still be scanned, not skipped because of the expression it
    interpolates."""
    root = tmpdir.mkdir("fstring")
    _write(root, "app/probe.py",
           'x = 1\ny = f"per the brief, round 2 {x}"\n')  # hygiene-ok: probe data for the f-string scan test, not a real hit
    assert _run_hygiene_checker(root, "content") >= 2


def test_public_repo_hygiene_content_scan_fstring_expression_itself_is_not_scanned(tmpdir):
    """The `{expression}` part of an f-string is code, not literal text --
    a role word spelled only inside the interpolated expression (an
    identifier, not the surrounding text) must not fire, matching the same
    code/comment boundary the narrowing already draws for plain code."""
    root = tmpdir.mkdir("fstring-clean")
    _write(root, "app/probe.py",
           'x = 1\ny = f"value is {x}"\n')
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_html_scans_inline_script_comments(tmpdir):
    """A `//` comment inside an inline <script> body is JavaScript, not
    markup -- scanned with the JS comment rules, not dropped because the
    surrounding file is .html."""
    root = tmpdir.mkdir("inline-script")
    _write(root, "app/templates/probe.html",
           "<script>\n"
           "  document.addEventListener('DOMContentLoaded', function () {\n"
           "    // Round-2 hardening: guard against an unresolved value\n"  # hygiene-ok: probe data for the inline-<script> scan test, not a real hit
           "  });\n"
           "</script>\n")
    assert _run_hygiene_checker(root, "content") >= 1


_TEXT_ATTR_CASES = [
    ("title", 'title="the builder wrote this"'),  # hygiene-ok: this table's own label, not a real hit
    ("alt", 'alt="D2-7 diagram"'),  # hygiene-ok: this table's own label, not a real hit
    ("placeholder", 'placeholder="round-3 notes"'),  # hygiene-ok: this table's own label, not a real hit
    ("aria-label", 'aria-label="refuter note"'),  # hygiene-ok: this table's own label, not a real hit
]


@pytest.mark.parametrize(
    "label, attr", _TEXT_ATTR_CASES, ids=[c[0] for c in _TEXT_ATTR_CASES],
)
def test_public_repo_hygiene_html_scans_text_bearing_attribute_values(tmpdir, label, attr):
    """title=/alt=/placeholder=/aria-label= attribute values are text a
    user reads or a screen reader announces, not markup -- scanned with the
    same rule as a visible text node, not dropped along with the tag that
    carries them."""
    root = tmpdir.mkdir("attr-%s" % label.replace("-", "_"))
    _write(root, "app/templates/probe.html", "<input %s>\n" % attr)
    assert _run_hygiene_checker(root, "content") >= 1, "row %r (%r) was not caught" % (label, attr)


def test_public_repo_hygiene_html_other_attribute_values_are_not_scanned(tmpdir):
    """An attribute outside the four text-bearing ones -- href, class,
    data-* -- is markup, not text a user reads, and stays unscanned even
    when its value happens to be record-id- or role-word-shaped."""
    root = tmpdir.mkdir("attr-not-text-bearing")
    _write(root, "app/templates/probe.html",
           '<a href="/refuter-path" class="round-3-badge" data-id="D-001">x</a>\n')  # hygiene-ok: probe data for the non-text-attribute negative control, not a real hit
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_html_data_title_is_not_read_as_title(tmpdir):
    """A hyphen is itself a word boundary in regex terms, so a bare
    word-boundary anchor on "title" also matches the "title" inside
    "data-title" -- a real, different attribute this scan must not read."""
    root = tmpdir.mkdir("attr-data-title")
    _write(root, "app/templates/probe.html",
           '<button data-title="the builder wrote this">x</button>\n')  # hygiene-ok: probe data for the data-title negative control, not a real hit
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_html_unspaced_attribute_is_still_read(tmpdir):
    """A whitespace-only lookbehind misses a text-bearing attribute with no
    space before it -- a real, if unusual, shape a hand-written or
    minified template can carry -- while `data-title` (above) must still
    stay clean; both are exercised in the one probe."""
    root = tmpdir.mkdir("attr-unspaced")
    _write(root, "app/templates/probe.html",
           '<a href="x"title="the builder wrote this" data-title="unrelated">link</a>\n')  # hygiene-ok: probe data for the unspaced-attribute test, not a real hit
    assert _run_hygiene_checker(root, "content") >= 1


def test_public_repo_hygiene_j2_codegen_file_is_not_scanned_as_html(tmpdir):
    """A `.j2` template whose base name carries a non-HTML extension
    (`.go.j2`, here) generates source in another language entirely -- its
    `//` comment is not markup, and reading it as HTML "visible text"
    would scan executable code line for line, contrary to this module's
    own "never executable code" claim."""
    root = tmpdir.mkdir("codegen-j2")
    _write(root, "app/templates/go_chi/saga_orchestrator.go.j2",
           "// NewOrchestrator creates a new orchestrator.\nfunc x() {}\n")  # hygiene-ok: probe data for the codegen-.j2 negative control, not a real hit
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_html_j2_template_is_still_scanned_as_html(tmpdir):
    """A `.j2` template whose base name IS `.html` (an ordinary Jinja HTML
    template) keeps the full visible-text and attribute scan -- the
    codegen exclusion above is narrow, not a blanket exemption for every
    `.j2` file."""
    root = tmpdir.mkdir("html-j2")
    _write(root, "app/templates/probe.html.j2",
           '<p title="the builder wrote this">x</p>\n')  # hygiene-ok: probe data for the .html.j2 positive control, not a real hit
    assert _run_hygiene_checker(root, "content") >= 1


def test_public_repo_hygiene_bare_j2_fragment_is_still_scanned_as_html(tmpdir):
    """A bare `.j2` fragment (no second extension at all) is this
    codebase's own convention for a Jinja-only include -- still markup,
    still scanned the same as `.html`."""
    root = tmpdir.mkdir("bare-j2")
    _write(root, "app/templates/probe.j2",
           '<p title="the builder wrote this">x</p>\n')  # hygiene-ok: probe data for the bare-.j2 positive control, not a real hit
    assert _run_hygiene_checker(root, "content") >= 1


def test_public_repo_hygiene_html_attribute_shaped_text_node_counts_once(tmpdir):
    """The attribute pattern is matched only inside a real tag's own span --
    a visible text node that happens to contain the same `attr="value"`
    shape, outside any tag, is read once as visible text, not a second
    time as if it were an attribute."""
    root = tmpdir.mkdir("attr-text-node-once")
    _write(root, "app/templates/probe.html",
           '<p>set title="refuter note" in config</p>\n')  # hygiene-ok: probe data for the double-count negative control, not a real hit
    count = _run_hygiene_checker(root, "content")
    assert count == 1, "expected exactly one hit, got %d (double-counted?)" % count


def test_public_repo_hygiene_html_scans_visible_text_nodes(tmpdir):
    """The literal text a browser renders between tags is content, not
    code -- scanned like a string literal, not dropped along with the tags
    and expressions around it."""
    root = tmpdir.mkdir("visible-text")
    _write(root, "app/templates/probe.html",
           '<div class="mt-4 p-2">\n'
           "  <p>Fixed in round-3, per the build report.</p>\n"  # hygiene-ok: probe data for the visible-text scan test, not a real hit
           "</div>\n")
    assert _run_hygiene_checker(root, "content") >= 1


def test_public_repo_hygiene_html_visible_text_ignores_tags_and_jinja_code(tmpdir):
    """A tag, its attributes, and a `{{ }}`/`{% %}` Jinja expression or
    statement are markup and code respectively, not visible text -- a role
    word or record-id-shaped value reachable only through one of those must
    not fire."""
    root = tmpdir.mkdir("visible-text-clean")
    _write(root, "app/templates/probe.html",
           '<div data-orchestrator-id="D-99" class="builder-panel">\n'  # hygiene-ok: probe data for the visible-text negative control, not a real hit
           "  {{ builder_orchestrator_label }}\n"
           "  {% if round_number %}{{ round_number }}{% endif %}\n"
           "  <p>Ordinary page copy.</p>\n"
           "</div>\n")
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_record_id_ignores_regex_character_class(tmpdir):
    """A record-id-shaped token immediately inside [ ] is a regex character
    class, not a review record id."""
    root = tmpdir.mkdir("charclass")
    _write(root, "app/probe.py", "# valid = bool(re.match(r'[A-Z0-9]+', token))\n")
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_orchestrator_word_ignores_identifiers(tmpdir):
    """'orchestrator' as a fragment of an identifier or class name (a
    product-code shape) must not fire, even inside a docstring/comment."""  # hygiene-ok: this docstring quotes the word it tests, not a real hit
    root = tmpdir.mkdir("orchestrator-identifiers")  # hygiene-ok: probe directory name for the identifier test, not a real hit
    _write(root, "app/probe.py",
           '"""Wraps UnifiedSeedOrchestrator and workflow_orchestrator_service '
           'and dual_agent_orchestrator."""\n')
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_product_terms_allowlisted(tmpdir):
    """PRODUCT_TERMS phrases are masked before the role-word check runs, so
    the product's own vocabulary for the orchestration feature and the
    decision-brief / codegen-brief feature never fires."""
    root = tmpdir.mkdir("product-terms")
    _write(root, "app/probe.py",
           '# The seed orchestrator and the workflow orchestrator both call\n'
           '# the dual-agent orchestrator; orchestration is the shared term.\n'
           '# The decision brief and the codegen brief render from the same data.\n')
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_content_scan_fails_not_passes_on_an_unparseable_python_file(tmpdir):
    """A .py file with a syntax error must not silently contribute zero
    hits and let the run pass as if it had been scanned -- the exact shape
    of gap this file's own module docstring opens with (a broken f-string,
    `except SyntaxError`, a checker reporting 0 for a defect that was
    present). It must fail loud instead, the same as an unreadable commit
    history or an untrusted zero-file `git ls-files` read."""
    root = tmpdir.mkdir("unparseable")
    _write(root, "app/broken.py", "def broken(:\n    pass\n")
    _write(root, "app/probe.py", "# orchestrator said so\n")  # hygiene-ok: probe data for a tmpdir file this checker never scans, not a real hit
    proc = _run_hygiene_checker_raw(root, "content")
    assert proc.returncode == 2, (
        "expected a non-zero exit when a tracked .py file could not be tokenised, "
        "got %d\nstdout=%r\nstderr=%r" % (proc.returncode, proc.stdout, proc.stderr)
    )
    trailing = (proc.stdout or "").strip().splitlines()
    parsed_as_count = bool(trailing) and trailing[-1].strip().lstrip("-").isdigit()
    assert not parsed_as_count, (
        "the checker printed a parseable count for a tree containing an unparseable "
        "file; a caller parsing stdout would treat this as a clean/partial pass "
        "instead of a failure"
    )
    assert "broken.py" in proc.stderr and "could not be tokenised" in proc.stderr


# Rule 3's source half also runs the same three attribution checks the
# commit-message half always ran (an attribution trailer, a generated-with
# footer, a named assistant product) plus the generic, unnamed-tool
# disclosure both halves share -- a disclosure written into a tracked
# comment or docstring is the same disclosure as one written into the
# commit that carried it, and the two must not drift apart on what counts.
_SOURCE_ATTRIBUTION_CASES = [
    ("Written with Claude Code", "app/probe.py", "# Written with Claude Code\n"),  # hygiene-ok: probe data for the source-half attribution test, not a real hit
    ("Generated with Codex", "app/probe.py", "# Generated with Codex\n"),  # hygiene-ok: probe data for the source-half attribution test, not a real hit
    ("Co-Authored-By: Kilo", "app/probe.py", "# Co-Authored-By: Kilo\n"),  # hygiene-ok: probe data for the source-half attribution test, not a real hit
    ("Generated by an AI coding assistant", "app/probe.py",  # hygiene-ok: probe data for the source-half attribution test, not a real hit
     "# Generated by an AI coding assistant.\n"),  # hygiene-ok: probe data for the source-half attribution test, not a real hit
]


@pytest.mark.parametrize(
    "label, relpath, content", _SOURCE_ATTRIBUTION_CASES, ids=[c[0] for c in _SOURCE_ATTRIBUTION_CASES]
)
def test_public_repo_hygiene_source_half_catches_attribution_disclosures(tmpdir, label, relpath, content):
    _write(tmpdir, relpath, content)
    count = _run_hygiene_checker(tmpdir, "content")
    assert count > 0, "row %r (%r) was not caught in tracked source" % (label, content)


def test_public_repo_hygiene_source_half_assistant_name_needs_a_cue_word(tmpdir):
    """This codebase is itself an AI-integration product that names
    Claude/Codex-family models as ordinary first-class vocabulary in its
    own LLM-routing code -- a bare product name with no attribution-shaped
    cue word on the same line is not a disclosure and must stay clean,
    unlike the commit-message half, which has no such vocabulary to
    protect and fires on the bare name alone."""
    root = tmpdir.mkdir("source-bare-product-name")
    _write(root, "app/probe.py", 'DEFAULT_MODEL = "claude-3-opus"  # provider default, not a disclosure\n')
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_source_half_generic_assistant_needs_coding(tmpdir):
    """This codebase's own domain vocabulary pairs a cue word with a bare
    "AI"/"LLM" constantly and legitimately (content the PRODUCT generates
    for a user), so the generic-assistant check requires "coding
    assistant" specifically, not a bare "AI" or "LLM" mention."""
    root = tmpdir.mkdir("source-llm-generated-product-vocabulary")
    _write(root, "app/probe.py", '"""LLM-generated strategic recommendation with user feedback."""\n')
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_source_half_attribution_is_excusable(tmpdir):
    """Unlike the commit-message half, the source half's escape hatch
    reaches these three checks too -- the checker's own comments and this
    file's own probe fixtures need a way to quote the shape without being
    read as a real disclosure."""
    root = tmpdir.mkdir("source-attribution-excused")
    _write(root, "app/probe.py",
           "# Written with Claude Code  # hygiene-ok: quoting the disclosure shape for a test\n")
    assert _run_hygiene_checker(root, "content") == 0


# Every id shape and role-word form the process actually emits, one probe
# line each: (label, relative path, file content). Each must be caught
# (count > 0) when scanned with --rule content. An attribution trailer,
# a generated-with footer and an assistant-product mention are ALSO a
# content hit now (see test_public_repo_hygiene_source_half_catches_attribution_disclosures
# above); this table stays to record-ids and role words specifically.
_FALSE_NEGATIVE_CASES = [
    ("D2-7", "app/probe.py", "# See D2-7 for the reasoning.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("T4-3", "app/probe.py", "# See T4-3 for the reasoning.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("D-ALL-1", "app/probe.py", "# See D-ALL-1 for the reasoning.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("D-105-2", "app/probe.py", "# See D-105-2 for the reasoning.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("T-FIX-103", "app/probe.py", "# See T-FIX-103 for the reasoning.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("T-DR-1", "app/probe.py", "# See T-DR-1 for the reasoning.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("T-S1", "app/probe.py", "# See T-S1 for the reasoning.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("Refuter's", "app/probe.py", "# Refuter's notes go here.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("REFUTER:", "app/probe.py", "# REFUTER: see above.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("(refuter)", "app/probe.py", "# fixed (refuter) noted.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("Round 3", "app/probe.py", "# Round 3 changes.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("round-3", "app/probe.py", "# round-3 changes.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("per the brief", "app/probe.py", "# per the brief, see above.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("tech lead (space)", "app/probe.py", "# tech lead approved this.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("qa-lead", "app/probe.py", "# qa-lead approved this.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("builder", "app/probe.py", "# the builder wrote this.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("solution-architect", "app/probe.py", "# solution-architect signed off.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("product-manager", "app/probe.py", "# product-manager approved.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("build-report", "app/probe.py", "# build-report attached.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("Round three", "app/probe.py", "# Round three changes.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("D3: bare beside a role word", "app/probe.py", "# D3: (refuter) noted.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("T-004b (trailing sub-item letter)", "app/probe.py", "# See T-004b for the reasoning.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("T-S1b (segment shape, trailing letter)", "app/probe.py", "# See T-S1b for the reasoning.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("T-CONS-R1 (interior letter segment, letter+digit ending)", "app/probe.py", "# See T-CONS-R1 for the reasoning.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("t-004 (lowercase)", "app/probe.py", "# See t-004 for the reasoning.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
    ("d-all-1 (lowercase)", "app/probe.py", "# See d-all-1 for the reasoning.\n"),  # hygiene-ok: probe data for the false-negative table test, not a real hit
]


@pytest.mark.parametrize(
    "label, relpath, content", _FALSE_NEGATIVE_CASES, ids=[c[0] for c in _FALSE_NEGATIVE_CASES]
)
def test_public_repo_hygiene_false_negative_table(tmpdir, label, relpath, content):
    """Every id shape and role-word form the process emits is still caught."""
    _write(tmpdir, relpath, content)
    count = _run_hygiene_checker(tmpdir, "content")
    assert count > 0, "row %r (%r) was not caught" % (label, content)


def test_public_repo_hygiene_record_id_aircraft_designation_is_a_known_limitation(tmpdir):
    """A two-digit letter-dash-digits shape is indistinguishable from a  # hygiene-ok: describing this test's own probe shape, not a real hit
    genuine two-digit finding reference by shape alone -- both are one  # hygiene-ok: describing this test's own probe shape, not a real hit
    process letter, a dash, and two digits, and nothing in the surrounding
    text marks one as a military aircraft designation and the other as a
    review record id. RECORD_ID_PATTERN's own comment already says this
    shape "cannot always be told apart from a genuine business reference
    number by shape alone"; recorded here as a known limitation, not
    silently dropped, rather than narrowed in a way that would also stop
    catching a real two-digit finding id."""
    root = tmpdir.mkdir("aircraft-designation")
    _write(root, "app/probe.py", "# F-16 and F-35 aircraft.\n")  # hygiene-ok: deliberate probe content, written to a tmpdir this checker never scans
    count = _run_hygiene_checker(root, "content")
    assert count == 2, (
        "expected the aircraft-designation collision to still read as two "
        "record-id-shaped hits (got %d) -- if a future pattern change makes "
        "this 0, move the row to the false-positive table instead of "
        "deleting this test" % count
    )


# Business reference numbers, standards prefixes, arithmetic, regex
# character classes and product vocabulary that must never be mistaken for
# a review record id or a role word. Each must be clean (count == 0).
_FALSE_POSITIVE_CASES = [
    ("ISO-27001", "app/probe.py", "# ISO-27001 certification details.\n"),
    ("UTF-8/RFC-6902/CVE-2024-1/SHA-256/SOC-2/GPT-4", "app/probe.py",
     "# UTF-8, RFC-6902, CVE-2024-1, SHA-256, SOC-2, GPT-4.\n"),
    ("class=\"mt-4 p-2\"", "templates/probe.html", '<div class="mt-4 p-2">Content</div>\n'),
    ("E-2", "app/probe.py", "# See E-2 for the reasoning.\n"),
    ("A-20", "app/probe.py", "# See A-20 for the reasoning.\n"),
    ("AD-001", "app/probe.py", "# The AD-001 decision record.\n"),
    ("x = A-1", "app/probe.py", "x = A-1\n"),
    ("return N-1", "app/probe.py", "def _probe():\n    return N - 1\n"),
    ("Q1-2026", "app/probe.py", "# Reported in Q1-2026.\n"),
    ("FY-2025", "app/probe.py", "# Budget for FY-2025.\n"),
    ("[A-Z0-9]", "app/probe.py", "# valid = bool(re.match(r'[A-Z0-9]+', token))\n"),
    ("SA-1", "app/probe.py", "# See SA-1 for the reasoning.\n"),
    ("US-1", "app/probe.py", "# See US-1 for the reasoning.\n"),
    ("H-1", "app/probe.py", "# See H-1 for the reasoning.\n"),
    ("P-1", "app/probe.py", "# See P-1 for the reasoning.\n"),
    ("L-1", "app/probe.py", "# See L-1 for the reasoning.\n"),
    ("B-2", "app/probe.py", "# See B-2 for the reasoning.\n"),
    ("ID-1", "app/probe.py", "# See ID-1 for the reasoning.\n"),
    ("IE-1", "app/probe.py", "# See IE-1 for the reasoning.\n"),
    ("DE-3", "app/probe.py", "# See DE-3 for the reasoning.\n"),
    ("BS-7799", "app/probe.py", "# BS-7799 certification details.\n"),
    ("seed orchestrator", "app/probe.py", "# The seed orchestrator handles this.\n"),
    ("the decision-brief feature", "app/probe.py",
     '# raise ValueError("acknowledged unknown code is not present on the decision brief")\n'),
    ("round 2 of funding", "app/probe.py", "# closed round 2 of funding this quarter.\n"),
    ('WCAG "A-11"', "app/probe.py", "# WCAG A-11 contrast requirement.\n"),
    ("DEL-D-001", "app/probe.py", '    "deliverable_code": "DEL-D-001",\n'),
    ("DEL-F-001", "app/probe.py", '    "DEL-D-001", "DEL-E-001", "DEL-F-001", "DEL-G-001",\n'),
    # A bare single digit after a bare dash, with none of the three other
    # structural signals the false-negative table's own dashed rows above
    # carry (a leading digit before the dash, a letter segment, or a second
    # dash-digits segment) to mark it as an id -- an ordinary English
    # fraction or count this codebase's own prose has used unprompted, not a
    # review record id. This is exactly the shape that, unguarded, turned
    # this checker's own commit history into new false positives the moment
    # the commit-message half started running these same patterns.
    ("T-1 (bare single digit)", "app/probe.py", "# about T-1 day later.\n"),
    ("T-0 (bare single digit)", "app/probe.py", "# excluded T-0 from the scan.\n"),
    ("D-0 (bare single digit)", "app/probe.py", "# excluded D-0 from the scan.\n"),
    # "the builder" alone is a role-word hit (see the false-negative table  # hygiene-ok: describing the pattern being tested, not a real hit
    # above); "the builder pattern" is the unrelated Gang-of-Four design
    # pattern name, and must not be.
    ("the builder pattern", "app/probe.py", "# Add the builder pattern for reports.\n"),
    # A `//` immediately after `:` is a URL scheme separator, not the start
    # of a line comment -- the whole rest of the line (including an
    # otherwise role-word-shaped path segment) must not be read as if it
    # sat inside a comment.
    ("https:// url containing round-N", "app/static/probe.js",
     'var u = "https://example.com/round-2";\n'),  # hygiene-ok: probe data for the URL-scheme false-positive test, not a real hit
]


@pytest.mark.parametrize(
    "label, relpath, content", _FALSE_POSITIVE_CASES, ids=[c[0] for c in _FALSE_POSITIVE_CASES]
)
def test_public_repo_hygiene_false_positive_table(tmpdir, label, relpath, content):
    """None of these are a review record id or a role word."""
    _write(tmpdir, relpath, content)
    count = _run_hygiene_checker(tmpdir, "content")
    assert count == 0, "row %r (%r) was incorrectly flagged" % (label, content)


# Bare task, finding and deliverable references -- this programme's own
# record-id-shaped ids, not prefixed by "DEL-" -- must be caught, not
# allowlisted away: a prior, broader form of RECORD_ID_ALLOWLIST_PREFIXES
# excluded these by accident, alongside the genuinely different "DEL-D-"/
# "DEL-F-" deliverable-code shape covered by the false-positive table above.
_BARE_ID_STILL_CAUGHT_CASES = [
    ("T-003", "app/probe.py", "T_ROUTE = 'T-003'\n"),  # hygiene-ok: probe data for the bare-id regression test, not a real hit
    ("F-07", "app/probe.py", 'label = "F-07"\n'),  # hygiene-ok: probe data for the bare-id regression test, not a real hit
    ("D-01", "app/probe.py", "# D-01: root cause identified below.\n"),  # hygiene-ok: probe data for the bare-id regression test, not a real hit
]


@pytest.mark.parametrize(
    "label, relpath, content", _BARE_ID_STILL_CAUGHT_CASES,
    ids=[c[0] for c in _BARE_ID_STILL_CAUGHT_CASES],
)
def test_public_repo_hygiene_record_id_bare_task_and_finding_refs_still_fire(tmpdir, label, relpath, content):
    _write(tmpdir, relpath, content)
    count = _run_hygiene_checker(tmpdir, "content")
    assert count > 0, "row %r (%r) was not caught" % (label, content)


# The DEL- deliverable-code context check reads the text immediately before  # hygiene-ok: quoting this test's own example shapes, not a real hit
# a matched deliverable-shaped token -- but must do so only when "DEL-"  # hygiene-ok: quoting this test's own example shapes, not a real hit
# itself starts at a word boundary. A longer word that merely ends the same
# way (a model name, say) is not a deliverable code and must still be caught.
def test_public_repo_hygiene_del_context_check_has_a_left_boundary(tmpdir):
    _write(tmpdir, "app/probe.py", '# See MODEL-D-001 for the reasoning.\n')  # hygiene-ok: probe data for the left-boundary regression test, not a real hit
    count = _run_hygiene_checker(tmpdir, "content")
    assert count > 0, "the probe row was wrongly allowlisted"  # hygiene-ok: quoting this test's own failure message, not a real hit


def test_public_repo_hygiene_checker_passes_its_own_scan_unexempted(tmpdir):
    """The checker's own source is exempt from the content rule by
    filename (SELF_NAME) when scanned against this repository -- copied
    into a tree under a different filename, so that exemption does not
    apply, it still has to come up clean on its own terms, every quoted
    pattern shape and allowlist entry marked hygiene-ok."""
    checker_path = os.path.join(SCRIPTS, "check_public_repo_hygiene.py")
    with open(checker_path, encoding="utf-8") as fh:
        source = fh.read()
    _write(tmpdir, "app/hygiene_checker_copy.py", source)
    assert _run_hygiene_checker(tmpdir, "content") == 0


def _git(root, *args):
    proc = subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, text=True)
    assert proc.returncode == 0, "git %s failed: %s" % (" ".join(args), proc.stderr)
    return proc.stdout


def _init_repo_with_commit(root, message):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "probe@example.com")
    _git(root, "config", "user.name", "Probe")
    _write(root, "README.md", "probe\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", message)


def _init_repo_with_commit_authored_by(root, message, author):
    """Same as `_init_repo_with_commit`, but with an explicit `--author`,
    for a probe that discloses itself in the commit's author name (a
    coding tool's own default author-name suffix) rather than the message
    body -- see test_public_repo_hygiene_commit_message_catches_assistant_name_in_author_name."""
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "probe@example.com")
    _git(root, "config", "user.name", "Probe")
    _write(root, "README.md", "probe\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", message, "--author", author)


def test_public_repo_hygiene_commit_message_gate_fires_on_its_own_defect(tmpdir):
    """A pipeline role word in a commit MESSAGE (not a file), in a real,
    synthetic git repository -- the shape check_evidence_contract.py's own
    git-reading half cannot be driven by --root at all; this checker can,
    because it takes one."""
    bad = tmpdir.mkdir("bad")
    _init_repo_with_commit(bad, "Fix the timeout (round-1 refuter finding D4)")  # hygiene-ok: deliberate probe commit message, in a synthetic tmpdir repo this checker never scans
    bad_count = _run_hygiene_checker(bad, "commits")

    good = tmpdir.mkdir("good")
    _init_repo_with_commit(good, "Fix the timeout on the retry path")
    good_count = _run_hygiene_checker(good, "commits")

    assert bad_count > 0, "a pipeline role word in a commit message was not flagged"
    assert good_count == 0, "the checker fired on a clean commit message"
    assert bad_count > good_count


def test_public_repo_hygiene_commit_message_range_excludes_the_base(tmpdir):
    """A --range scan reports only the commits under review, not the base's
    own history -- the shape gate_public_repo_hygiene_commit_messages relies
    on (base..HEAD): a bad commit already on the base branch must not make
    every review of every later branch fail forever."""
    root = tmpdir.mkdir("range")
    _init_repo_with_commit(root, "Fix the timeout on the retry path (refuter finding)")  # hygiene-ok: deliberate probe commit message, in a synthetic tmpdir repo this checker never scans
    base_sha = _git(root, "rev-parse", "HEAD").strip()
    _write(root, "second.txt", "second\n")
    _git(root, "add", "second.txt")
    _git(root, "commit", "-q", "-m", "Add second.txt (tech-lead approved)")  # hygiene-ok: deliberate probe commit message, in a synthetic tmpdir repo this checker never scans

    full_count = _run_hygiene_checker(root, "commits")
    ranged_count = _run_hygiene_checker(root, "commits", rev_range="%s..HEAD" % base_sha)

    assert full_count == 2, "expected one hit on each of the two commits, got %d" % full_count
    assert ranged_count == 1, (
        "a --range scan counted %d hits; it should see only the commit made after "
        "the range's base, not the base's own history" % ranged_count
    )


def test_public_repo_hygiene_commit_message_catches_co_authored_by(tmpdir):
    """The attribution trailer specifically, independent of any role word."""
    bad = tmpdir.mkdir("bad")
    _init_repo_with_commit(
        bad, "Fix the timeout on the retry path\n\nCo-Authored-By: Example <e@example.com>\n"  # hygiene-ok: probe data for a tmpdir git repo this checker never scans, not a real hit
    )
    good = tmpdir.mkdir("good")
    _init_repo_with_commit(good, "Fix the timeout on the retry path")

    assert _run_hygiene_checker(bad, "commits") > 0
    assert _run_hygiene_checker(good, "commits") == 0


def test_public_repo_hygiene_commit_message_catches_generated_with_footer(tmpdir):
    """A free-text 'Generated with ...' footer line, the shape several  # hygiene-ok: describing this test's own probe shape, not a real hit
    coding tools append instead of (or beside) an attribution trailer."""
    bad = tmpdir.mkdir("bad")
    _init_repo_with_commit(
        bad,
        "Fix the timeout on the retry path\n\n"
        "\U0001F916 Generated with [an assistant](https://example.com)\n",  # hygiene-ok: probe data for a tmpdir git repo this checker never scans, not a real hit
    )
    good = tmpdir.mkdir("good")
    _init_repo_with_commit(good, "Fix the timeout on the retry path")

    assert _run_hygiene_checker(bad, "commits") > 0
    assert _run_hygiene_checker(good, "commits") == 0


_ASSISTANT_PRODUCT_NAME_CASES = [
    ("Claude", "Reviewed-by: Claude\n"),  # hygiene-ok: probe data for the assistant-name table test, not a real hit
    ("Codex", "Reviewed-by: Codex\n"),  # hygiene-ok: probe data for the assistant-name table test, not a real hit
    ("Kilo", "Reviewed-by: Kilo\n"),  # hygiene-ok: probe data for the assistant-name table test, not a real hit
]


@pytest.mark.parametrize(
    "label, trailer_line", _ASSISTANT_PRODUCT_NAME_CASES,
    ids=[c[0] for c in _ASSISTANT_PRODUCT_NAME_CASES],
)
def test_public_repo_hygiene_commit_message_catches_assistant_product_name_on_trailer_line(tmpdir, label, trailer_line):
    """Each assistant product name, as a whole word on its own trailer
    line, independent of the attribution-trailer and generated-with checks."""
    root = tmpdir.mkdir("assistant-%s" % label.lower())
    _init_repo_with_commit(root, "Fix the timeout on the retry path\n\n" + trailer_line)
    assert _run_hygiene_checker(root, "commits") > 0, "row %r (%r) was not caught" % (label, trailer_line)


_ASSISTANT_PRODUCT_NAME_PROSE_CASES = [
    ("Co-authored by Claude Code", "Fix the timeout on the retry path\n\nCo-authored by Claude Code\n"),  # hygiene-ok: probe data for the assistant-name prose test, not a real hit
    ("Written with Claude Code", "Fix the timeout on the retry path\n\nWritten with Claude Code\n"),  # hygiene-ok: probe data for the assistant-name prose test, not a real hit
]


@pytest.mark.parametrize(
    "label, message", _ASSISTANT_PRODUCT_NAME_PROSE_CASES,
    ids=[c[0] for c in _ASSISTANT_PRODUCT_NAME_PROSE_CASES],
)
def test_public_repo_hygiene_commit_message_catches_assistant_product_name_in_prose(tmpdir, label, message):
    """An assistant product name is caught anywhere in the message, not
    only on a `Key: value` trailer line -- an ordinary sentence naming the
    tool discloses exactly what the trailer/footer checks above exist to
    catch."""
    root = tmpdir.mkdir("assistant-prose")
    _init_repo_with_commit(root, message)
    assert _run_hygiene_checker(root, "commits") > 0, "row %r was not caught" % (label,)


def test_public_repo_hygiene_commit_message_assistant_product_name_fires_on_an_unrelated_mention_too(tmpdir):
    """An assistant product name mentioned anywhere in the message -- not
    only on a trailer or footer line -- is a hit; the checker no longer
    tries to tell an unrelated mention (turning a feature on or off, a
    comparison) apart from a disclosure by the shape of the line it sits
    on."""
    root = tmpdir.mkdir("assistant-prose-unrelated")
    _init_repo_with_commit(root, "Disable the Claude review-comment bot in the editor settings")  # hygiene-ok: probe data for a tmpdir git repo this checker never scans, not a real hit
    assert _run_hygiene_checker(root, "commits") > 0


def test_public_repo_hygiene_commit_message_copilot_is_this_products_own_feature_name(tmpdir):
    """"Copilot" is this product's own in-app AI feature name, not (only)
    an unrelated coding tool that happens to share the word -- dropped
    from the assistant-name list entirely, so an ordinary commit about the
    feature is not mistaken for a disclosure."""
    root = tmpdir.mkdir("copilot-product-feature")
    _init_repo_with_commit(root, "Fix the copilot insight ranking")
    assert _run_hygiene_checker(root, "commits") == 0


def test_public_repo_hygiene_commit_message_still_catches_a_real_assistant_disclosure(tmpdir):
    """Dropping "Copilot" narrows the list; it does not weaken the check
    for the names that stay."""
    root = tmpdir.mkdir("copilot-dropped-others-stay")
    _init_repo_with_commit(root, "Written with Claude Code")  # hygiene-ok: probe data for a tmpdir git repo this checker never scans, not a real hit
    assert _run_hygiene_checker(root, "commits") > 0


def test_public_repo_hygiene_commit_message_governance_file_name_is_not_an_assistant_mention(tmpdir):
    """This repository's own governance-file name shares its first word
    with one of the assistant product names, and is referenced constantly
    in ordinary commit messages that have nothing to do with the tool --
    excluded by name so it does not collide."""
    root = tmpdir.mkdir("governance-file-name")
    _init_repo_with_commit(root, "Correct three stale gate counts in CLAUDE.md")
    assert _run_hygiene_checker(root, "commits") == 0


def test_public_repo_hygiene_commit_message_governance_file_name_does_not_mask_a_real_mention(tmpdir):
    """A genuine disclosure next to the governance-file name on the same
    line is still caught -- excluding the file name does not excuse the
    rest of the line."""
    root = tmpdir.mkdir("governance-file-name-plus-real-mention")
    _init_repo_with_commit(root, "Reworded CLAUDE.md; Written with Claude Code")  # hygiene-ok: probe data for a tmpdir git repo this checker never scans, not a real hit
    assert _run_hygiene_checker(root, "commits") > 0


def test_public_repo_hygiene_commit_message_ordinary_trailer_line_is_clean(tmpdir):
    """An ordinary Key: value trailer, naming neither an attribution
    convention nor an assistant product, is not a hit on its own."""
    root = tmpdir.mkdir("ordinary-trailer")
    _init_repo_with_commit(root, "Fix the timeout on the retry path\n\nFixes: #482\n")
    assert _run_hygiene_checker(root, "commits") == 0


def test_public_repo_hygiene_commit_message_generated_with_footer_cannot_be_escaped(tmpdir):
    """A generated-with footer line is never excused, marker or not -- the
    same rule the attribution trailer already follows."""
    root = tmpdir.mkdir("footer-with-marker")
    _init_repo_with_commit(
        root,
        "Fix the timeout on the retry path\n\n"
        "Generated with an assistant  # hygiene-ok: disclosure is required here\n",
    )
    assert _run_hygiene_checker(root, "commits") > 0


def test_public_repo_hygiene_commit_message_hygiene_ok_excuses_its_own_line(tmpdir):
    """The hygiene-ok: escape hatch excuses the physical line it sits on."""
    root = tmpdir.mkdir("escaped")
    _init_repo_with_commit(
        root,
        "Fix the timeout\n\n"
        "Fix the timeout (round-1 refuter finding D4)  # hygiene-ok: quoting the original finding for the changelog\n",  # hygiene-ok: deliberate probe content, escaped by the marker on the same line at runtime
    )
    assert _run_hygiene_checker(root, "commits") == 0


def test_public_repo_hygiene_commit_message_hygiene_ok_does_not_reach_other_lines(tmpdir):
    """A marker on one line does not excuse a role word on another."""
    root = tmpdir.mkdir("marker-elsewhere")
    _init_repo_with_commit(
        root,
        "Fix the timeout\n\n"
        "hygiene-ok: unrelated note\n"
        "Fix the timeout (round-1 refuter finding D4)\n",  # hygiene-ok: deliberate probe content, the marker above is on a different line and must not reach this one
    )
    assert _run_hygiene_checker(root, "commits") > 0


def test_public_repo_hygiene_commit_message_trailer_cannot_be_escaped(tmpdir):
    """An attribution-trailer line is never excused, marker or not -- the
    trailer check runs before the escape check."""
    root = tmpdir.mkdir("trailer-with-marker")
    _init_repo_with_commit(
        root,
        "Fix the timeout on the retry path\n\n"
        "Co-Authored-By: Example <e@example.com>  # hygiene-ok: attribution is required here\n",
    )
    assert _run_hygiene_checker(root, "commits") > 0


def test_public_repo_hygiene_commit_message_gate_fails_not_passes_when_git_unavailable(tmpdir):
    """A directory that is not a git repository at all (git log fails) must
    report a failure, never a silent zero-count pass."""
    root = tmpdir.mkdir("not-a-repo")
    proc = _run_hygiene_checker_raw(root, "commits")
    assert proc.returncode != 0, (
        "expected a non-zero exit when git history could not be read, got 0\n"
        "stdout=%r\nstderr=%r" % (proc.stdout, proc.stderr)
    )
    trailing = (proc.stdout or "").strip().splitlines()
    parsed_as_zero = bool(trailing) and trailing[-1].strip() == "0"
    assert not parsed_as_zero, (
        "the checker printed a count of 0 for an unreadable history; a caller "
        "parsing stdout would treat this as a clean pass instead of a failure"
    )


# The module docstring, the PR title and two commits all claimed the
# commit-message half already ran the record-id patterns; it only ever ran
# `_role_word_hits`. One probe per shape the process actually emits in a
# commit subject -- every one of these five must be a hit (count > 0) once
# `_scan_commit_messages` also runs `_record_id_matches`/
# `_bare_record_id_matches`, not just role words.
_COMMIT_MESSAGE_RECORD_ID_CASES = [
    ("T-004", "T-004: add impact API"),  # hygiene-ok: probe data for the commit-message record-id test, not a real hit
    ("D-ALL-1", "Fix D-ALL-1"),  # hygiene-ok: probe data for the commit-message record-id test, not a real hit
    ("T-RR-14 with a PR number nearby", "T-RR-14 built (PR 128): rework the tenant fence"),  # hygiene-ok: probe data for the commit-message record-id test, not a real hit
    ("F-05..F-08 list", "fix: close F-05, F-06, F-07 and F-08"),  # hygiene-ok: probe data for the commit-message record-id test, not a real hit
    ("T-003 conventional-commit style", "feat(intelligence): T-003 derived-fact store rework"),  # hygiene-ok: probe data for the commit-message record-id test, not a real hit
]


@pytest.mark.parametrize(
    "label, subject", _COMMIT_MESSAGE_RECORD_ID_CASES,
    ids=[c[0] for c in _COMMIT_MESSAGE_RECORD_ID_CASES],
)
def test_public_repo_hygiene_commit_message_catches_record_ids(tmpdir, label, subject):
    root = tmpdir.mkdir("commit-record-id")
    _init_repo_with_commit(root, subject)
    count = _run_hygiene_checker(root, "commits")
    assert count > 0, "row %r (%r) was not caught" % (label, subject)


def test_public_repo_hygiene_commit_message_catches_qa_lead_with_a_space(tmpdir):
    """"tech lead" (space) is already caught; "qa-lead" only matched its  # hygiene-ok: this docstring quotes the role words it tests, not a real hit
    own hyphenated spelling until now -- the same free-text spacing must
    be caught for both role words."""
    root = tmpdir.mkdir("qa-lead-space")  # hygiene-ok: probe directory name for the qa-lead space test, not a real hit
    _init_repo_with_commit(root, "Reviewed by QA lead")  # hygiene-ok: probe data for a tmpdir git repo this checker never scans, not a real hit
    assert _run_hygiene_checker(root, "commits") > 0


def test_public_repo_hygiene_commit_message_catches_round_with_two_digits(tmpdir):
    """A single-digit-only round pattern misses a double-digit round
    number once the pipeline runs that many rounds."""
    root = tmpdir.mkdir("round-two-digits")  # hygiene-ok: probe directory name for the round-two-digits test, not a real hit
    _init_repo_with_commit(root, "Fix the timeout (round 10 refuter finding)")  # hygiene-ok: probe data for a tmpdir git repo this checker never scans, not a real hit
    assert _run_hygiene_checker(root, "commits") > 0


def test_public_repo_hygiene_commit_message_catches_generic_ai_coding_assistant_disclosure(tmpdir):
    """A disclosure that names the KIND of tool rather than one of the
    specific listed products is still a disclosure."""
    root = tmpdir.mkdir("generic-assistant-disclosure")
    _init_repo_with_commit(root, "Generated by an AI coding assistant")  # hygiene-ok: probe data for a tmpdir git repo this checker never scans, not a real hit
    assert _run_hygiene_checker(root, "commits") > 0


def test_public_repo_hygiene_commit_message_catches_spaced_co_authored_by(tmpdir):
    """The attribution trailer's own free-text rendering, space-separated
    rather than hyphenated, carries the identical disclosure."""
    root = tmpdir.mkdir("spaced-trailer")
    _init_repo_with_commit(root, "Co authored by GitHub Copilot")  # hygiene-ok: probe data for a tmpdir git repo this checker never scans, not a real hit
    assert _run_hygiene_checker(root, "commits") > 0


def test_public_repo_hygiene_commit_message_catches_aiders_default_subject_prefix(tmpdir):
    """A fourth coding tool's own name, the same as the three already
    listed -- its default commit-subject prefix carries the bare word."""
    root = tmpdir.mkdir("aider-subject-prefix")
    _init_repo_with_commit(root, "aider: refactor the thing")
    assert _run_hygiene_checker(root, "commits") > 0


def test_public_repo_hygiene_commit_message_catches_assistant_name_in_author_name(tmpdir):
    """The message body (`%B`) is not the only place a coding tool
    discloses itself -- one tool's default author name is the ordinary git
    user name plus a literal `(aider)` suffix, invisible to a scan that
    reads only the message text."""
    root = tmpdir.mkdir("aider-author-name")
    _init_repo_with_commit_authored_by(
        root, "Fix the timeout on the retry path", "Jane Doe (aider) <jane@example.com>"
    )
    assert _run_hygiene_checker(root, "commits") > 0


def test_public_repo_hygiene_commit_message_catches_the_builder_pattern_in_a_commit_too(tmpdir):
    """The Gang-of-Four design-pattern exclusion applies identically in a
    commit message -- the role-word helpers are shared between both
    halves, so a commit describing the unrelated pattern must stay clean."""
    root = tmpdir.mkdir("commit-builder-pattern")
    _init_repo_with_commit(root, "Add the builder pattern for reports")
    assert _run_hygiene_checker(root, "commits") == 0


def test_public_repo_hygiene_content_scan_only_reads_tracked_files(tmpdir):
    """An untracked file in a real git working tree is not scanned -- only
    `git ls-files` output is, so a local scratch file never inflates the
    count (or hides a real hit some other run would catch)."""
    root = tmpdir.mkdir("tracked-only")
    _init_repo_with_commit(root, "Initial commit")
    _write(root, "app/tracked.py", "# clean\n")
    _git(root, "add", "app/tracked.py")
    _git(root, "commit", "-q", "-m", "Add tracked.py")
    _write(root, "app/untracked.py", "# orchestrator said so\n")  # hygiene-ok: deliberate probe content, deliberately left untracked and never committed
    assert _run_hygiene_checker(root, "content") == 0


def test_public_repo_hygiene_content_scan_falls_back_when_git_unavailable(tmpdir):
    """A tree that is not a git working tree still gets scanned, via a
    directory walk, and the fallback is noted rather than silent."""
    root = tmpdir.mkdir("no-git")
    _write(root, "app/probe.py", "# orchestrator said so\n")  # hygiene-ok: deliberate probe content, in a synthetic tmpdir repo this checker never scans
    proc = _run_hygiene_checker_raw(root, "content")
    assert proc.stdout.strip().splitlines()[-1] == "1"
    assert "falling back to a directory walk" in proc.stderr


def test_public_repo_hygiene_content_scan_fails_on_zero_tracked_files_under_an_existing_dir(tmpdir):
    """`git ls-files` succeeding with zero files under a scan directory
    that exists on disk is a broken read (wrong cwd, a detached or partial
    checkout), not an empty repository -- report a failure, never a silent
    0 a caller could mistake for a clean scan."""
    root = tmpdir.mkdir("zero-tracked")
    _init_repo_with_commit(root, "Initial commit")
    os.makedirs(str(root.join("app")))
    _write(root, "app/untracked.py", "# never committed\n")
    # app/ exists on disk, but nothing under it was ever `git add`ed, so
    # `git ls-files -- app scripts tests templates` reports zero files
    # while this real git repository's own ls-files call succeeds cleanly.
    proc = _run_hygiene_checker_raw(root, "content")
    assert proc.returncode == 2, (
        "expected a non-zero exit when git ls-files reported zero tracked files "
        "under an existing scan directory, got %d\nstdout=%r\nstderr=%r"
        % (proc.returncode, proc.stdout, proc.stderr)
    )
    trailing = (proc.stdout or "").strip().splitlines()
    parsed_as_zero = bool(trailing) and trailing[-1].strip() == "0"
    assert not parsed_as_zero, (
        "the checker printed a count of 0 for an untrusted zero-file read; a "
        "caller parsing stdout would treat this as a clean pass instead of a failure"
    )
    assert "zero tracked files" in proc.stderr


def test_every_registered_checker_carries_its_proof():
    """A checker in the registry must document the defect it was watched on.

    docs/TESTING_STANDARD.md rule 7. The `Proven-against:` line is how a
    reviewer, and this file's next author, learns what fixture to build.
    """
    missing = []
    for script, _ in CASES:
        path = os.path.join(SCRIPTS, script)
        assert os.path.exists(path), "%s is in CASES but not in scripts/" % script
        with open(path, encoding="utf-8") as fh:
            if "Proven-against:" not in fh.read():
                missing.append(script)
    assert not missing, (
        "these checkers carry no Proven-against: line, so nobody recorded "
        "watching them fail: %s" % ", ".join(missing)
    )


def test_canonical_route_detects_a_shadowed_endpoint():
    """Two endpoints on one (URL, method); the loser never runs.

    This gate cannot be driven by a synthetic tree the way every other checker
    here is: it reads a BOOTED url_map, because a static scan of @route
    decorators cannot see a blueprint's url_prefix, cannot see which side the
    USE_*_GUARDRAILS flags selected, and cannot see that init_blueprints logged
    an import failure and carried on. That is the whole reason the gate exists
    in this form.

    So the collision logic is deliberately separated from the booting, and this
    proof builds a two-blueprint Flask app instead of a fake package tree.
    Asserted in BOTH directions -- a checker that returns a positive count for
    everything would pass a red-only assertion.
    """
    import importlib.util
    from flask import Blueprint, Flask

    checker = os.path.join(REPO, "scripts", "check_canonical_route.py")
    spec = importlib.util.spec_from_file_location("_canonical_route", checker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    shadowed = Flask("shadowed")
    page = Blueprint("alpha", __name__)
    api = Blueprint("beta", __name__)
    page.add_url_rule("/thing", "page", lambda: "", methods=["GET"])
    api.add_url_rule("/thing", "api", lambda: "", methods=["GET"])
    shadowed.register_blueprint(page)
    shadowed.register_blueprint(api)
    found = module.collisions(list(shadowed.url_map.iter_rules()))
    assert len(found) == 1, (
        "two endpoints claim GET /thing and the gate did not notice: %s" % found
    )
    assert "alpha.page" in found[0] and "beta.api" in found[0], (
        "the finding must name BOTH endpoints, or nobody can tell which one is "
        "dead: %s" % found[0]
    )

    clean = Flask("clean")
    clean.add_url_rule("/thing", "only", lambda: "", methods=["GET"])
    assert module.collisions(list(clean.url_map.iter_rules())) == []


def test_canonical_route_ignores_the_methods_werkzeug_invents():
    """HEAD and OPTIONS are synthesised, never authored.

    Keying on the rule alone rather than on (rule, method) reported 287
    collisions against 24 real ones when this gate was written. A gate that
    cries wolf stops being read, and this repository has already carried two
    that ratcheted phantom findings.
    """
    import importlib.util
    from flask import Flask

    checker = os.path.join(REPO, "scripts", "check_canonical_route.py")
    spec = importlib.util.spec_from_file_location("_canonical_route2", checker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app = Flask("implicit")
    # One endpoint, several methods. Werkzeug adds HEAD and OPTIONS on top.
    app.add_url_rule("/thing", "only", lambda: "", methods=["GET", "POST"])
    assert module.collisions(list(app.url_map.iter_rules())) == []


# --------------------------------------------------------------------------
# check_smoke_coverage_on_change.resolve_base_ref: like check_canonical_route
# above, this reads real git state relative to a fixed REPO_ROOT rather than
# a `--root` argument, so a synthetic tree cannot drive it. Its own `_run`
# helper is the one seam it exposes, monkeypatched here to hand back a fixed
# answer for each git command it would otherwise run for real, matching the
# scheme this module's own docstring already describes for `_changed_files`.
# --------------------------------------------------------------------------


def _load_smoke_coverage_module():
    import importlib.util

    checker = os.path.join(SCRIPTS, "check_smoke_coverage_on_change.py")
    spec = importlib.util.spec_from_file_location("_smoke_coverage_resolver", checker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_base_ref_non_strict_prefers_the_merge_base(monkeypatch):
    """Non-strict mode (this script's own use): the merge-base with
    origin/main wins when it resolves, so a long-lived branch is not
    blamed for files main already changed elsewhere."""
    module = _load_smoke_coverage_module()
    monkeypatch.setattr(
        module, "_run",
        lambda cmd: "deadbeef" if cmd == ["git", "merge-base", "HEAD", "origin/main"] else "",
    )
    ref, reason = module.resolve_base_ref(strict=False)
    assert ref == "deadbeef"
    assert reason == ""


def test_resolve_base_ref_non_strict_falls_back_to_origin_main(monkeypatch):
    """No merge-base (e.g. an unrelated history) but origin/main itself
    resolves: falls back to it directly."""
    module = _load_smoke_coverage_module()

    def fake_run(cmd):
        if cmd == ["git", "merge-base", "HEAD", "origin/main"]:
            return ""
        if cmd == ["git", "rev-parse", "--verify", "origin/main"]:
            return "cafefeed"
        return ""

    monkeypatch.setattr(module, "_run", fake_run)
    ref, reason = module.resolve_base_ref(strict=False)
    assert ref == "origin/main"
    assert reason == ""


def test_resolve_base_ref_non_strict_falls_back_to_head_minus_one(monkeypatch):
    """Neither a merge-base nor origin/main resolves (a fresh clone, or an
    offline sandbox with no fetched origin at all): non-strict mode still
    resolves to something, HEAD~1, rather than reporting nothing."""
    module = _load_smoke_coverage_module()
    monkeypatch.setattr(module, "_run", lambda cmd: "")
    ref, reason = module.resolve_base_ref(strict=False)
    assert ref == "HEAD~1"
    assert reason == ""


def test_resolve_base_ref_strict_prefers_github_base_ref(monkeypatch):
    """Strict mode inside a pull request: the PR's own base branch, not
    origin/main, is the range under review."""
    module = _load_smoke_coverage_module()
    monkeypatch.setenv("GITHUB_BASE_REF", "release/9.2")
    monkeypatch.setattr(
        module, "_run",
        lambda cmd: "abc123" if cmd == ["git", "rev-parse", "--verify", "--quiet", "origin/release/9.2"] else "",
    )
    ref, reason = module.resolve_base_ref(strict=True)
    assert ref == "origin/release/9.2"
    assert reason == ""


def test_resolve_base_ref_strict_falls_back_to_origin_main_outside_a_pull_request(monkeypatch):
    """Strict mode with no GITHUB_BASE_REF set (a push, or a local run):
    origin/main is the range under review."""
    module = _load_smoke_coverage_module()
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    monkeypatch.setattr(
        module, "_run",
        lambda cmd: "abc123" if cmd == ["git", "rev-parse", "--verify", "--quiet", "origin/main"] else "",
    )
    ref, reason = module.resolve_base_ref(strict=True)
    assert ref == "origin/main"
    assert reason == ""


def test_resolve_base_ref_strict_never_falls_back_to_head_minus_one(monkeypatch):
    """Strict mode's whole point: when nothing resolves (a shallow clone,
    or no matching remote-tracking branch) it reports None and a reason --
    never HEAD~1, which would silently narrow "the commits under review"
    to one commit instead of skipping."""
    module = _load_smoke_coverage_module()
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    monkeypatch.setattr(module, "_run", lambda cmd: "")
    ref, reason = module.resolve_base_ref(strict=True)
    assert ref is None
    assert "origin/main" in reason


# --------------------------------------------------------------------------
# hygiene_text equivalence: three checkers each replaced an inline pattern
# with a call into the shared module. Each reference implementation below is
# a frozen copy of the inline code exactly as it read before that move --
# not imported from hygiene_text or from the checker -- so a later change to
# the shared module that silently changed behaviour diverges from this copy
# and fails here, rather than only being caught by chance elsewhere.
# --------------------------------------------------------------------------


def test_check_duplicate_breadcrumb_mask_comments_matches_the_original_inline_pattern():
    sys.path.insert(0, SCRIPTS)
    import check_duplicate_breadcrumb as mod

    import re as _re
    _old_comment_re = _re.compile(r"\{#.*?#\}|<!--.*?-->", _re.S)

    def _old_mask_comments(text):
        def _blank(m):
            s = m.group(0)
            return "".join(c if c == "\n" else " " for c in s)
        return _old_comment_re.sub(_blank, text)

    fixture = (
        "{# a second breadcrumb_nav() call used to live in a "
        "{% block breadcrumb %} above #}\n"
        '<nav aria-label="breadcrumb">x</nav>\n'
        '<!-- <nav aria-label="breadcrumb">y</nav> -->\n'
        "plain text with no comment at all"
    )
    assert mod._mask_comments(fixture) == _old_mask_comments(fixture)


def test_check_placeholder_copy_visible_text_matches_the_original_inline_pattern():
    sys.path.insert(0, SCRIPTS)
    import check_placeholder_copy as mod

    import re as _re
    _old_tags = _re.compile(r"<[^>]+>")
    _old_jinja = _re.compile(r"\{\{.*?\}\}|\{%.*?%\}", _re.S)

    def _old_visible_text(fragment):
        without_jinja = _old_jinja.sub("", fragment)
        return " ".join(_old_tags.sub(" ", without_jinja).split()).strip()

    fixture = '<b>{% if x %}bold{% endif %}</b> {{ value }} <i>text</i>'
    assert mod._visible_text(fixture) == _old_visible_text(fixture)


def test_check_broken_surfaces_blank_comments_matches_the_original_inline_pattern():
    sys.path.insert(0, SCRIPTS)
    import check_broken_surfaces as mod

    def _old_blank_comments(text, jinja):
        out = list(text)

        def blank(a, b):
            for k in range(a, min(b, len(out))):
                if out[k] != "\n":
                    out[k] = " "

        spans = []
        i, n = 0, len(text)
        while i < n:
            if jinja and text.startswith("{#", i):
                j = text.find("#}", i + 2)
                j = n if j == -1 else j + 2
                spans.append((i, j))
                i = j
            elif text.startswith("/*", i):
                j = text.find("*/", i + 2)
                j = n if j == -1 else j + 2
                spans.append((i, j))
                i = j
            elif text.startswith("//", i):
                j = text.find("\n", i)
                j = n if j == -1 else j
                spans.append((i, j))
                i = j
            else:
                i += 1
        for a, b in spans:
            blank(a, b)
        return "".join(out)

    fixture_js = (
        "// a defensive guard against an unresolved value\n"
        "code();\n/* block\ncomment */more();\n/* unterminated"
    )
    assert mod._blank_comments(fixture_js, jinja=False) == _old_blank_comments(fixture_js, jinja=False)

    fixture_jinja = '{# example markup #} <div>x</div> code // trailing comment'
    assert mod._blank_comments(fixture_jinja, jinja=True) == _old_blank_comments(fixture_jinja, jinja=True)


# --------------------------------------------------------------------------
# check_attr_quoting.py, check_macro_kwargs.py and check_alpine_await.py each
# carried their own byte-identical (or, for check_alpine_await.py, a
# near-identical union) copy of a hygiene_text comment/Jinja pattern, with no
# recorded reason a sibling checker's own frozen-reference tests above
# already established as the standard for this kind of duplication. `is`,
# not `==`: two independently-compiled patterns with equal source text are
# still two separate objects, so equality alone would pass whether or not
# the import ever happened -- only identity proves the constant was actually
# reused, not re-typed to read the same.
# --------------------------------------------------------------------------


def test_check_attr_quoting_imports_the_shared_jinja_patterns():
    sys.path.insert(0, SCRIPTS)
    import check_attr_quoting as mod

    import hygiene_text
    assert mod.JINJA_COMMENT_RE is hygiene_text.JINJA_COMMENT_RE
    assert mod.JINJA_RE is hygiene_text.JINJA_EXPR_OR_STMT_RE


def test_check_macro_kwargs_imports_the_shared_jinja_comment_pattern():
    sys.path.insert(0, SCRIPTS)
    import check_macro_kwargs as mod

    import hygiene_text
    assert mod.JINJA_COMMENT is hygiene_text.JINJA_COMMENT_RE


def test_check_alpine_await_imports_the_shared_jinja_any_pattern():
    sys.path.insert(0, SCRIPTS)
    import check_alpine_await as mod

    import hygiene_text
    assert mod.JINJA_RE is hygiene_text.JINJA_ANY_RE


def test_check_dynamic_link_prefixes_html_tag_shape_carries_a_recorded_reason():
    """Byte-identical to hygiene_text.HTML_TAG_RE by shape (a Flask route
    converter, not an HTML tag), not imported from it -- the file's own
    comment records why, rather than leaving the duplication unexplained."""
    path = os.path.join(SCRIPTS, "check_dynamic_link_prefixes.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    assert "not imported from it" in source
    assert "unrelated grammar" in source
