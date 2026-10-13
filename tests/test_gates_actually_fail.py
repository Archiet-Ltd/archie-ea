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

check_reuse.py DOES take --root, but it also takes a required --rule (RG-1,
RG-1b or RG-2), which the shared CASES/_run_checker convention above has no
room for -- _run_checker always calls a script with exactly --count --root.
Rather than widen that helper for one checker, its rules are pinned
red-and-green directly, below the CASES-driven test.
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


def _run_reuse_checker(rule, root):
    """check_reuse.py's --count output, for a given --rule, against a synthetic tree."""
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "check_reuse.py"),
         "--rule", rule, "--count", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO,
    )
    trailing = (proc.stdout or "").strip().splitlines()
    assert trailing, (
        "check_reuse.py --rule %s produced no count for root=%s\nstdout=%r\nstderr=%r"
        % (rule, root, proc.stdout, proc.stderr[:400])
    )
    try:
        return int(trailing[-1])
    except ValueError:
        raise AssertionError(
            "check_reuse.py --rule %s did not end with a count: %r (stderr=%r)"
            % (rule, trailing[-1], proc.stderr[:400])
        )


def test_reuse_macro_names_detects_a_second_definition(tmpdir):
    """RG-1: the same macro name defined in a second template file.

    Proven against the register's own named example -- two templates each
    defining ``empty_state`` -- rather than a bare probe name.
    """
    bad = tmpdir.mkdir("bad")
    _write(bad, "app/templates/macros/a.html", "{% macro empty_state(x) %}A{% endmacro %}\n")
    _write(bad, "app/templates/macros/b.html", "{% macro empty_state(y) %}B{% endmacro %}\n")

    good = tmpdir.mkdir("good")
    _write(good, "app/templates/macros/a.html", "{% macro empty_state(x) %}A{% endmacro %}\n")
    _write(good, "app/templates/macros/b.html", "{% macro different_name(y) %}B{% endmacro %}\n")

    bad_count = _run_reuse_checker("RG-1", bad)
    good_count = _run_reuse_checker("RG-1", good)

    assert bad_count == 1, (
        "two templates defining the same macro name reported %d, not 1 -- "
        "the gate is decoration if it cannot see its own named example" % bad_count
    )
    assert good_count == 0, (
        "two templates defining DIFFERENT macro names reported %d against the "
        "clean tree" % good_count
    )


def test_reuse_macro_names_escape_hatch_excludes_the_marked_line(tmpdir):
    """A well-formed 'reuse-ok: <concept-id> <reason>' marker on the DUPLICATE
    (not the canonical) definition suppresses the finding."""
    base = tmpdir.mkdir("marked")
    _write(base, "app/templates/macros/a.html", "{% macro probe_thing(x) %}A{% endmacro %}\n")
    _write(base, "app/templates/macros/b.html",
           "{% macro probe_thing(y) %}{# reuse-ok: probe deliberate local copy #}{% endmacro %}\n")

    count = _run_reuse_checker("RG-1", base)
    assert count == 0, (
        "a marked 'reuse-ok:' definition still counted toward the distinct-file "
        "total: %d" % count
    )


def test_reuse_macro_names_ps_exact_name_exemption(tmpdir):
    """Only the two exact page-shell protocol names, _ps_actions and
    _ps_sub, are exempt -- ANY other name, even one sharing the _ps_
    prefix, is counted by both RG-1 and RG-1b. A prefix-shaped exemption is
    a blanket bypass one rename away from hiding a real duplicate; the
    exact names are the width actually measured on the real tree."""
    prefix_dodge = tmpdir.mkdir("prefix_dodge")
    for i, letter in enumerate("abcd"):
        _write(prefix_dodge, "app/templates/%s/page.html" % letter,
               "{%% macro _ps_empty_state(x) %%}%d{%% endmacro %%}\n" % i)

    protocol = tmpdir.mkdir("protocol")
    _write(protocol, "app/templates/a/x.html", "{% macro _ps_actions(x) %}A{% endmacro %}\n")
    _write(protocol, "app/templates/b/y.html", "{% macro _ps_actions(y) %}B{% endmacro %}\n")
    _write(protocol, "app/templates/c/z.html", "{% macro _ps_sub(x) %}A{% endmacro %}\n")
    _write(protocol, "app/templates/d/w.html", "{% macro _ps_sub(y) %}B{% endmacro %}\n")

    assert _run_reuse_checker("RG-1", prefix_dodge) == 1, (
        "_ps_empty_state in four files, sharing the _ps_ prefix but not one of "
        "the two exact exempt names, must be counted as one duplicated name"
    )
    assert _run_reuse_checker("RG-1b", prefix_dodge) == 4, (
        "_ps_empty_state in four files must be counted as four definitions by RG-1b"
    )
    assert _run_reuse_checker("RG-1", protocol) == 0, (
        "the two exact page-shell protocol names must stay exempt on RG-1"
    )
    assert _run_reuse_checker("RG-1b", protocol) == 0, (
        "the two exact page-shell protocol names must stay exempt on RG-1b too"
    )


def test_reuse_macro_names_marker_on_canonical_does_not_suppress(tmpdir):
    """A marker on the CANONICAL definition changes nothing -- the canonical
    was never the file this rule flags, so the unmarked duplicate still
    counts. Moving the same marker onto the duplicate is what suppresses it."""
    marker_on_canonical = tmpdir.mkdir("marker_on_canonical")
    _write(marker_on_canonical, "app/templates/a/canon.html",
           "{# reuse-ok: probe deliberate reason #}{% macro probe_marker(x) %}A{% endmacro %}\n")
    _write(marker_on_canonical, "app/templates/b/dup.html", "{% macro probe_marker(y) %}B{% endmacro %}\n")

    marker_on_duplicate = tmpdir.mkdir("marker_on_duplicate")
    _write(marker_on_duplicate, "app/templates/a/canon.html", "{% macro probe_marker(x) %}A{% endmacro %}\n")
    _write(marker_on_duplicate, "app/templates/b/dup.html",
           "{% macro probe_marker(y) %}{# reuse-ok: probe deliberate reason #}{% endmacro %}\n")

    canonical_marked_count = _run_reuse_checker("RG-1", marker_on_canonical)
    duplicate_marked_count = _run_reuse_checker("RG-1", marker_on_duplicate)

    assert canonical_marked_count == 1, (
        "a marker on the canonical definition alone suppressed the finding "
        "(%d) -- the duplicate it was meant to excuse carries no marker at all"
        % canonical_marked_count
    )
    assert duplicate_marked_count == 0, (
        "the same marker, moved onto the actual duplicate, should suppress "
        "the finding: got %d" % duplicate_marked_count
    )


def test_reuse_macro_names_malformed_marker_does_not_suppress(tmpdir):
    """'reuse-ok: x' (no reason), 'reuse-ok:!' (no identifier-shaped id) and a
    bare 'reuse-ok:' must not suppress -- only a marker with a real concept
    id AND a multi-word reason does."""
    malformed = {
        "no_reason": "{# reuse-ok: x #}",
        "bad_identifier": "{# reuse-ok:! #}",
        "bare": "{# reuse-ok: #}",
    }
    for label, marker in malformed.items():
        root = tmpdir.mkdir(label)
        _write(root, "app/templates/a/canon.html", "{% macro probe_shape(x) %}A{% endmacro %}\n")
        _write(root, "app/templates/b/dup.html", "{%% macro probe_shape(y) %%}%s{%% endmacro %%}\n" % marker)
        count = _run_reuse_checker("RG-1", root)
        assert count == 1, (
            "malformed marker %r (%s) suppressed a finding that should stand: "
            "got %d" % (marker, label, count)
        )

    well_formed = tmpdir.mkdir("well_formed")
    _write(well_formed, "app/templates/a/canon.html", "{% macro probe_shape(x) %}A{% endmacro %}\n")
    _write(well_formed, "app/templates/b/dup.html",
           "{% macro probe_shape(y) %}{# reuse-ok: probe deliberately accepted #}{% endmacro %}\n")
    assert _run_reuse_checker("RG-1", well_formed) == 0, (
        "a marker with an identifier-shaped id and a real reason should suppress"
    )


def test_reuse_macro_names_marker_reason_needs_two_letters_per_token(tmpdir):
    """'reuse-ok: q 1 2' and 'reuse-ok: zzz ... ,,,' must not suppress --
    no token in the reason carries two or more letters. A genuine reason
    of real words still does."""
    single_char_tokens = tmpdir.mkdir("single_char_tokens")
    _write(single_char_tokens, "app/templates/a/canon.html", "{% macro probe_n5(x) %}A{% endmacro %}\n")
    _write(single_char_tokens, "app/templates/b/dup.html",
           "{% macro probe_n5(y) %}{# reuse-ok: q 1 2 #}{% endmacro %}\n")
    assert _run_reuse_checker("RG-1", single_char_tokens) == 1, (
        "'reuse-ok: q 1 2' has no reason token with two or more letters and "
        "must not suppress"
    )

    punctuation_tokens = tmpdir.mkdir("punctuation_tokens")
    _write(punctuation_tokens, "app/templates/a/canon.html", "{% macro probe_n5b(x) %}A{% endmacro %}\n")
    _write(punctuation_tokens, "app/templates/b/dup.html",
           "{% macro probe_n5b(y) %}{# reuse-ok: zzz ... ,,, #}{% endmacro %}\n")
    assert _run_reuse_checker("RG-1", punctuation_tokens) == 1, (
        "'reuse-ok: zzz ... ,,,' has 'zzz' as the concept id and no reason "
        "token with two or more letters, and must not suppress"
    )

    real_reason = tmpdir.mkdir("real_reason")
    _write(real_reason, "app/templates/a/canon.html", "{% macro probe_n5c(x) %}A{% endmacro %}\n")
    _write(real_reason, "app/templates/b/dup.html",
           "{% macro probe_n5c(y) %}{# reuse-ok: probe deliberately accepted #}{% endmacro %}\n")
    assert _run_reuse_checker("RG-1", real_reason) == 0, (
        "a genuine reason, every token carrying two or more letters, should still suppress"
    )


def test_reuse_macro_definitions_rises_on_a_third_copy_while_names_stays_put(tmpdir):
    """RG-1b: a further copy of an ALREADY-duplicated name is free under
    RG-1's name-level count, so RG-1b -- the definition-level count over the
    same map -- must be the one that rises."""
    root = tmpdir.mkdir("growing")
    _write(root, "app/templates/a/x.html", "{% macro probe_growing(x) %}A{% endmacro %}\n")
    _write(root, "app/templates/b/y.html", "{% macro probe_growing(y) %}B{% endmacro %}\n")

    names_before = _run_reuse_checker("RG-1", root)
    defs_before = _run_reuse_checker("RG-1b", root)

    _write(root, "app/templates/c/z.html", "{% macro probe_growing(z) %}C{% endmacro %}\n")

    names_after = _run_reuse_checker("RG-1", root)
    defs_after = _run_reuse_checker("RG-1b", root)

    assert names_before == names_after == 1, (
        "RG-1 counts NAMES; a third copy of an already-duplicated name must not "
        "move it: before=%d after=%d" % (names_before, names_after)
    )
    assert defs_after == defs_before + 1, (
        "RG-1b counts DEFINITIONS behind a duplicated name; a third copy must "
        "raise it by exactly one: before=%d after=%d" % (defs_before, defs_after)
    )


def test_reuse_macro_definitions_marker_excludes_only_the_marked_definition(tmpdir):
    """RG-1b's escape hatch applies PER DEFINITION, unlike RG-1's whole-name
    suppression, for a REGISTERED name: a valid marker on one non-canonical
    definition removes only that one from the count. The canonical
    definition, and any other unmarked sibling, still count. (A marker on
    an UNREGISTERED name's definition is never honoured -- see
    test_reuse_macro_definitions_marker_on_unregistered_name_never_suppresses.)"""
    root = tmpdir.mkdir("two_files")
    _write(root, "docs/reuse-register.yml",
           "concepts:\n"
           "  - id: probe-concept\n"
           "    rules: [RG-1, RG-1b]\n"
           "    canonical:\n"
           "      paths: [app/templates/a/canon.html]\n"
           "      use: \"probe_n1(...) does the probe thing\"\n")
    _write(root, "app/templates/a/canon.html", "{% macro probe_n1(x) %}A{% endmacro %}\n")
    _write(root, "app/templates/b/dup.html",
           "{% macro probe_n1(y) %}{# reuse-ok: probe-concept deliberately accepted #}{% endmacro %}\n")

    assert _run_reuse_checker("RG-1", root) == 0, (
        "the only non-canonical definition carries a valid marker, so RG-1's "
        "whole-name suppression should fire"
    )
    assert _run_reuse_checker("RG-1b", root) == 1, (
        "RG-1b must still count the canonical definition even though the "
        "duplicate is marked: expected 1 (the canonical), not 0 or 2"
    )

    _write(root, "app/templates/c/third.html", "{% macro probe_n1(z) %}C{% endmacro %}\n")

    assert _run_reuse_checker("RG-1", root) == 1, (
        "the new, unmarked third definition means not every non-canonical "
        "definition is marked any more, so RG-1 must flag the name again"
    )
    assert _run_reuse_checker("RG-1b", root) == 2, (
        "the marked duplicate still does not count, but the new unmarked "
        "third definition raises RG-1b by exactly one: expected 2 (canonical "
        "+ third), not 3"
    )


def _run_rg1b_verbose(root):
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "check_reuse.py"), "--rule", "RG-1b", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO,
    )
    return proc.stdout


def test_reuse_macro_definitions_canonical_finding_has_its_own_wording(tmpdir):
    """The canonical definition's own RG-1b finding must not read 'Already
    exists' against itself -- it gets distinct wording saying it is the
    canonical of a name with N definitions, that it counts because the
    copies exist, and that marking THIS line has no effect. The duplicate's
    own finding is unaffected: it still points 'Already exists' at the
    canonical, correctly, since the canonical is a different file there."""
    root = tmpdir.mkdir("canonical_wording")
    _write(root, "docs/reuse-register.yml",
           "concepts:\n"
           "  - id: probe-concept\n"
           "    rules: [RG-1, RG-1b]\n"
           "    canonical:\n"
           "      paths: [app/templates/components/probe_thing.html]\n"
           "      use: \"probe_thing(...) does the probe thing\"\n")
    _write(root, "app/templates/components/probe_thing.html", "{% macro probe_thing(x) %}A{% endmacro %}\n")
    _write(root, "app/templates/macros/other.html", "{% macro probe_thing(y) %}B{% endmacro %}\n")

    output = _run_rg1b_verbose(root)
    blocks = [b for b in output.split("\n\n") if b.strip().startswith("app/")]
    canonical_block = next(b for b in blocks if b.startswith("app/templates/components/probe_thing.html:1"))
    duplicate_block = next(b for b in blocks if b.startswith("app/templates/macros/other.html:1"))

    assert "is the canonical definition of macro 'probe_thing'" in canonical_block, (
        "the canonical's finding must carry its own wording, not 'Already exists': %r" % canonical_block
    )
    assert "Already exists:" not in canonical_block, (
        "the canonical's own finding must never read 'Already exists' against "
        "itself: %r" % canonical_block
    )
    assert "Already exists: app/templates/components/probe_thing.html -- probe_thing(...)" in duplicate_block, (
        "the DUPLICATE's finding must still point 'Already exists' at the canonical: %r" % duplicate_block
    )


def test_reuse_macro_definitions_dead_marker_is_reported(tmpdir):
    """A valid marker placed where it cannot take effect -- on the
    canonical of a registered name, or on any definition of an
    unregistered name -- is reported inside that definition's own finding
    as having no effect, not silently ignored, and it still counts."""
    registered = tmpdir.mkdir("registered_dead_marker")
    _write(registered, "docs/reuse-register.yml",
           "concepts:\n"
           "  - id: probe-concept\n"
           "    rules: [RG-1, RG-1b]\n"
           "    canonical:\n"
           "      paths: [app/templates/components/probe_thing.html]\n"
           "      use: \"probe_thing(...) does the probe thing\"\n")
    _write(registered, "app/templates/components/probe_thing.html",
           "{% macro probe_thing(x) %}{# reuse-ok: probe-concept deliberately accepted #}{% endmacro %}\n")
    _write(registered, "app/templates/macros/other.html", "{% macro probe_thing(y) %}B{% endmacro %}\n")

    registered_output = _run_rg1b_verbose(registered)
    assert "This line carries a reuse-ok marker, but it has no effect here" in registered_output, (
        "a marker on the canonical must be reported as having no effect: %r" % registered_output
    )
    assert "it is the canonical definition" in registered_output, (
        "the dead-marker note must say why: it is the canonical: %r" % registered_output
    )

    unregistered = tmpdir.mkdir("unregistered_dead_marker")
    _write(unregistered, "app/templates/a/x.html",
           "{% macro probe_dead(x) %}{# reuse-ok: probe deliberately accepted #}{% endmacro %}\n")
    _write(unregistered, "app/templates/b/y.html", "{% macro probe_dead(y) %}B{% endmacro %}\n")

    unregistered_output = _run_rg1b_verbose(unregistered)
    assert "This line carries a reuse-ok marker, but it has no effect here" in unregistered_output, (
        "a marker on a definition of an unregistered name must be reported as "
        "having no effect: %r" % unregistered_output
    )
    assert "no matching entry in docs/reuse-register.yml, so a marker is not honoured" in unregistered_output, (
        "the dead-marker note must say why: no matching concept: %r" % unregistered_output
    )
    assert _run_reuse_checker("RG-1b", unregistered) == 2, (
        "the marked definition of an unregistered name must still count"
    )


def test_reuse_macro_definitions_unregistered_findings_are_distinct_and_located_at_their_own_definition(tmpdir):
    """Every RG-1b finding for an unregistered name must report the
    definition it is actually about, not always the same (alphabetically
    first) file -- so N definitions produce N distinct finding texts, and a
    removed definition is visible in the output."""
    root = tmpdir.mkdir("distinct")
    for letter in "abc":
        _write(root, "app/templates/%s/page.html" % letter,
               "{% macro probe_distinct(x) %}A{% endmacro %}\n")

    output = _run_rg1b_verbose(root)
    blocks = [b for b in output.split("\n\n") if b.strip().startswith("app/")]

    assert len(blocks) == 3, "three definitions must produce three findings: %r" % output
    assert len(set(blocks)) == 3, "the three findings must all be distinct text: %r" % blocks
    for letter in "abc":
        assert any(b.startswith("app/templates/%s/page.html:1" % letter) for b in blocks), (
            "no finding located at app/templates/%s/page.html: %r" % (letter, blocks)
        )


def test_reuse_macro_definitions_marker_on_unregistered_name_never_suppresses(tmpdir):
    """A marker on one definition of an unregistered name never removes it
    from the count -- every definition of such a name counts, always, so
    adding a further, unmarked copy raises the count by exactly one."""
    root = tmpdir.mkdir("unregistered_marker")
    _write(root, "app/templates/macros/x.html",
           "{% macro probe_always(x) %}{# reuse-ok: probe deliberately accepted #}{% endmacro %}\n")
    _write(root, "app/templates/z/y.html", "{% macro probe_always(y) %}B{% endmacro %}\n")

    assert _run_reuse_checker("RG-1", root) == 1, "an unregistered name is always counted by RG-1"
    assert _run_reuse_checker("RG-1b", root) == 2, (
        "a marker on one definition of an unregistered name must not remove it "
        "from RG-1b's count: both definitions still count"
    )

    _write(root, "app/templates/a/new.html", "{% macro probe_always(z) %}C{% endmacro %}\n")
    assert _run_reuse_checker("RG-1b", root) == 3, (
        "a further, unmarked copy of an unregistered name must raise RG-1b by "
        "exactly one, even though an earlier copy is marked"
    )


def test_reuse_diagram_libraries_detects_a_vendor_load_outside_the_composer(tmpdir):
    """RG-2: a page referencing a diagram vendor library with no real renderer
    load. The green half carries a vendor library too (joint, on a page that
    DOES load the renderer) -- proving the checker looked and correctly
    found nothing to flag, not merely that an empty page passes."""
    bad = tmpdir.mkdir("bad")
    _write(bad, "app/templates/probe/map.html", '<script src="/static/vendor/d3.min.js"></script>\n')

    good = tmpdir.mkdir("good")
    _write(good, "app/templates/probe/map.html",
           '<script src="{{ url_for(\'static\', filename=\'js/archimate/composer_renderer.js\') }}"></script>\n'
           '<script src="/static/vendor/joint.min.js"></script>\n')

    bad_count = _run_reuse_checker("RG-2", bad)
    good_count = _run_reuse_checker("RG-2", good)

    assert bad_count == 1, (
        "a page referencing vendor/d3.min.js with no composer_renderer.js load "
        "reported %d, not 1" % bad_count
    )
    assert good_count == 0, (
        "a page that loads the canonical renderer AND references joint (an "
        "allowed library there) reported %d -- the allowance should apply" % good_count
    )


def test_reuse_diagram_libraries_allows_joint_and_dagre_on_a_composer_page(tmpdir):
    """The one allowed place: joint/dagre alongside a REAL composer_renderer.js load."""
    root = tmpdir.mkdir("composer_page")
    _write(root, "app/templates/probe/composer_like.html",
           '<script src="/static/vendor/joint.min.js"></script>\n'
           '<script src="/static/vendor/dagre.min.js"></script>\n'
           '<script src="/static/js/archimate/composer_renderer.js"></script>\n')

    count = _run_reuse_checker("RG-2", root)
    assert count == 0, (
        "joint and dagre alongside a real composer_renderer.js reference should "
        "be an allowed place, not a finding: %d" % count
    )


def test_reuse_diagram_libraries_d3_still_flagged_on_a_composer_page(tmpdir):
    """The composer allowance is specific to joint/dagre: d3 is not excused
    just because the same page also loads the canonical renderer."""
    root = tmpdir.mkdir("composer_page_d3")
    _write(root, "app/templates/probe/composer_like.html",
           '<script src="/static/js/archimate/composer_renderer.js"></script>\n'
           '<script src="/static/vendor/d3.min.js"></script>\n')

    count = _run_reuse_checker("RG-2", root)
    assert count == 1, (
        "d3 on a page that also loads the canonical renderer should still be "
        "flagged (the allowance covers only joint/dagre, the libraries the "
        "renderer is built on): got %d" % count
    )


def test_reuse_diagram_libraries_comment_only_composer_mention_does_not_excuse(tmpdir):
    """A comment that merely mentions archimate/composer_renderer.js is not a
    real load, so it must not excuse joint or dagre on that page."""
    root = tmpdir.mkdir("comment_only")
    _write(root, "app/templates/probe/page.html",
           "<!-- see archimate/composer_renderer.js for how this works -->\n"
           '<script src="/static/vendor/joint.min.js"></script>\n')

    count = _run_reuse_checker("RG-2", root)
    assert count == 1, (
        "a comment merely mentioning the renderer file should not excuse "
        "joint on the same page: got %d" % count
    )


def test_reuse_diagram_libraries_comment_with_a_real_looking_src_does_not_grant_allowance(tmpdir):
    """An HTML or Jinja comment that CONTAINS an attribute-shaped
    src="...composer_renderer.js" reference -- not just a bare mention --
    must not grant the allowance either: the whole comment body is blanked
    before this test runs."""
    html_comment = tmpdir.mkdir("html_comment")
    _write(html_comment, "app/templates/probe/page.html",
           '<!-- disabled: <script src="archimate/composer_renderer.js"></script> -->\n'
           '<script src="/static/vendor/joint.min.js"></script>\n')
    assert _run_reuse_checker("RG-2", html_comment) == 1, (
        "an HTML comment containing an attribute-shaped composer_renderer.js "
        "reference must not excuse joint on the same page"
    )

    jinja_comment = tmpdir.mkdir("jinja_comment")
    _write(jinja_comment, "app/templates/probe/page.html",
           '{# disabled: src="archimate/composer_renderer.js" #}\n'
           '<script src="/static/vendor/joint.min.js"></script>\n')
    assert _run_reuse_checker("RG-2", jinja_comment) == 1, (
        "a Jinja comment containing an attribute-shaped composer_renderer.js "
        "reference must not excuse joint on the same page"
    )


def test_reuse_diagram_libraries_data_filename_does_not_grant_allowance(tmpdir):
    """data-filename= (or data-src=) on an inert element must not grant the
    allowance -- the attribute name must have no word or hyphen character
    before it."""
    root = tmpdir.mkdir("data_filename")
    _write(root, "app/templates/probe/page.html",
           '<div data-filename="archimate/composer_renderer.js"></div>\n'
           '<script src="/static/vendor/joint.min.js"></script>\n')
    assert _run_reuse_checker("RG-2", root) == 1, (
        "data-filename= on an inert element must not excuse joint on the same page"
    )


def test_reuse_diagram_libraries_es_module_import_grants_allowance(tmpdir):
    """A genuine ES-module import of the renderer -- static, bare or
    dynamic -- must grant the allowance; the old, attribute-only pattern
    saw none of these forms."""
    forms = {
        "static": "import ComposerRenderer from '../archimate/composer_renderer.js';",
        "bare": "import '../archimate/composer_renderer.js';",
        "dynamic": "const mod = await import('../archimate/composer_renderer.js');",
    }
    for label, statement in forms.items():
        root = tmpdir.mkdir("import_%s" % label)
        _write(root, "app/static/js/probe/page.js",
               statement + '\nconst s = "vendor/joint.min.js";\n')
        count = _run_reuse_checker("RG-2", root)
        assert count == 0, (
            "a %s ES-module import of the renderer should excuse joint in the "
            "same file: got %d" % (label, count)
        )


def test_reuse_diagram_libraries_multiline_import_grants_allowance(tmpdir):
    """A normal multi-line named ES-module import of the renderer must
    grant the allowance; a single-line import of the same form still does."""
    multiline = tmpdir.mkdir("multiline_import")
    _write(multiline, "app/static/js/probe/page.js",
           "import {\n"
           "  ComposerRenderer,\n"
           "} from '../archimate/composer_renderer.js';\n"
           'const s = "vendor/joint.min.js";\n')
    assert _run_reuse_checker("RG-2", multiline) == 0, (
        "a multi-line named import of the renderer should excuse joint in the same file"
    )

    single_line = tmpdir.mkdir("single_line_import")
    _write(single_line, "app/static/js/probe/page.js",
           "import ComposerRenderer from '../archimate/composer_renderer.js';\n"
           'const s = "vendor/joint.min.js";\n')
    assert _run_reuse_checker("RG-2", single_line) == 0, (
        "a single-line named import of the renderer must still excuse joint"
    )


def test_reuse_diagram_libraries_js_comments_do_not_grant_allowance(tmpdir):
    """A '//' or '/* */' comment whose text imports the renderer must not
    grant the allowance in a .js file, matching the existing rule that a
    template comment cannot forge it either; a real load on a line that
    also contains 'https://' still grants it, because the guard against
    blanking a real 'https://' string does not disable the comment check,
    it only stops the WRONG span from being blanked."""
    line_comment = tmpdir.mkdir("line_comment")
    _write(line_comment, "app/static/js/probe/page.js",
           '// TODO: import "../archimate/composer_renderer.js"\n'
           'const s = "vendor/joint.min.js";\n')
    assert _run_reuse_checker("RG-2", line_comment) == 1, (
        "a // comment importing the renderer must not excuse joint in a .js file"
    )

    block_comment = tmpdir.mkdir("block_comment")
    _write(block_comment, "app/static/js/probe/page.js",
           '/* import "../archimate/composer_renderer.js" */\n'
           'const s = "vendor/joint.min.js";\n')
    assert _run_reuse_checker("RG-2", block_comment) == 1, (
        "a /* */ comment importing the renderer must not excuse joint in a .js file"
    )

    https_guard = tmpdir.mkdir("https_guard")
    _write(https_guard, "app/static/js/probe/page.js",
           "const base = \"https://example.com\"; "
           "import ComposerRenderer from \"../archimate/composer_renderer.js\";\n"
           'const s = "vendor/joint.min.js";\n')
    assert _run_reuse_checker("RG-2", https_guard) == 0, (
        "a https:// string earlier on the line must not blank a real import "
        "later on the same line"
    )


def test_reuse_diagram_libraries_suffix_boundary_rejects_lookalikes(tmpdir):
    """The library pattern requires a real separator ('-' or '.') after the
    base name when anything follows it -- jointly_shared.js and
    mermaid_docs_page.js must not match, while a genuinely suffixed build
    (d3-sankey) still does."""
    joint_lookalike = tmpdir.mkdir("joint_lookalike")
    _write(joint_lookalike, "app/templates/probe/page.html",
           '<script src="/static/js/jointly_shared.js"></script>\n')
    assert _run_reuse_checker("RG-2", joint_lookalike) == 0, (
        "jointly_shared.js must not match the joint library pattern"
    )

    mermaid_lookalike = tmpdir.mkdir("mermaid_lookalike")
    _write(mermaid_lookalike, "app/templates/probe/page.html",
           '<script src="/static/js/mermaid_docs_page.js"></script>\n')
    assert _run_reuse_checker("RG-2", mermaid_lookalike) == 0, (
        "mermaid_docs_page.js must not match the mermaid library pattern"
    )

    suffixed = tmpdir.mkdir("suffixed")
    _write(suffixed, "app/templates/probe/page.html",
           '<script src="/static/vendor/d3-sankey.min.js"></script>\n')
    assert _run_reuse_checker("RG-2", suffixed) == 1, (
        "a genuinely suffixed build, d3-sankey.min.js, must still match"
    )


def test_reuse_diagram_libraries_widened_pattern_catches_suffixed_and_nonvendor(tmpdir):
    """A suffixed build (d3-sankey, dagre-d3), a copy outside vendor/, a .j2
    page and a file under app/modules/*/static/ must all produce a finding --
    each paired against a lookalike that must NOT, to prove the widened
    pattern still has a boundary rather than matching everywhere."""
    sankey = tmpdir.mkdir("sankey")
    _write(sankey, "app/templates/probe/page.html", '<script src="/static/vendor/d3-sankey.min.js"></script>\n')
    assert _run_reuse_checker("RG-2", sankey) == 1, "vendor/d3-sankey.min.js alone must be caught"

    sankey_lookalike = tmpdir.mkdir("sankey_lookalike")
    _write(sankey_lookalike, "app/templates/probe/page.html",
           '<script src="/static/vendor/sankey_helper.min.js"></script>\n')
    assert _run_reuse_checker("RG-2", sankey_lookalike) == 0, (
        "a filename that merely mentions 'sankey' with no library name prefix "
        "must not match"
    )

    dagre_d3 = tmpdir.mkdir("dagre_d3")
    _write(dagre_d3, "app/templates/probe/page.html", '<script src="/static/vendor/dagre-d3.min.js"></script>\n')
    assert _run_reuse_checker("RG-2", dagre_d3) == 1, "vendor/dagre-d3.min.js must be caught"

    dagre_midword = tmpdir.mkdir("dagre_midword")
    _write(dagre_midword, "app/templates/probe/page.html",
           '<script src="/static/vendor/not-dagre-thing.min.js"></script>\n')
    assert _run_reuse_checker("RG-2", dagre_midword) == 0, (
        "'dagre' with no path/quote boundary immediately before it must not match"
    )

    outside_vendor = tmpdir.mkdir("outside_vendor")
    _write(outside_vendor, "app/templates/probe/page.html", '<script src="/static/js/mystuff/d3.min.js"></script>\n')
    assert _run_reuse_checker("RG-2", outside_vendor) == 1, "a d3 copy outside vendor/ must be caught"

    excluded_dir = tmpdir.mkdir("excluded_dir")
    _write(excluded_dir, "app/modules/solutions_product/templates/page.html",
           '<script src="/static/js/mystuff/d3.min.js"></script>\n')
    assert _run_reuse_checker("RG-2", excluded_dir) == 0, (
        "the same reference under an excluded (solutions_product) path must stay excluded"
    )

    j2_page = tmpdir.mkdir("j2_page")
    _write(j2_page, "app/templates/probe/page.j2", '<script src="/static/vendor/d3.min.js"></script>\n')
    assert _run_reuse_checker("RG-2", j2_page) == 1, ".j2 templates must be scanned, matching RG-1's scope"

    unscanned_ext = tmpdir.mkdir("unscanned_ext")
    _write(unscanned_ext, "app/templates/probe/page.txt", '<script src="/static/vendor/d3.min.js"></script>\n')
    assert _run_reuse_checker("RG-2", unscanned_ext) == 0, (
        "a non-template, non-JS extension must stay out of scope"
    )

    modules_static = tmpdir.mkdir("modules_static")
    _write(modules_static, "app/modules/foo/static/foo/chart.js", 'const s = "vendor/d3.min.js";\n')
    assert _run_reuse_checker("RG-2", modules_static) == 1, "app/modules/*/static/*.js must be scanned"

    modules_nonstatic = tmpdir.mkdir("modules_nonstatic")
    _write(modules_nonstatic, "app/modules/foo/routes/chart.js", 'const s = "vendor/d3.min.js";\n')
    assert _run_reuse_checker("RG-2", modules_nonstatic) == 0, (
        "a .js file under app/modules/ but NOT under a static/ folder must stay out of scope"
    )


def test_update_baseline_preserves_unrelated_top_level_keys(tmpdir, monkeypatch):
    """save_baseline() must keep every existing top-level key that is not
    'ratchets' -- previously it wrote a fixed {_comment, _note, ratchets}
    object, silently deleting a dated, hand-written reason (like a real
    '_note5') the very next time --update-baseline ran."""
    sys.path.insert(0, SCRIPTS)
    import verify  # noqa: E402

    baseline_path = tmpdir.join("verification_baseline.json")
    baseline_path.write_text(
        json.dumps({"_comment": "house comment", "_note5": "a dated, hand-written reason",
                    "ratchets": {"reuse_macro_names": 19}}),
        encoding="utf-8",
    )

    original_path = verify.BASELINE_PATH
    monkeypatch.setattr(verify, "BASELINE_PATH", type(original_path)(str(baseline_path)))
    try:
        verify.save_baseline({"reuse_macro_names": 20}, "updated for this test")
        written = json.loads(baseline_path.read_text(encoding="utf-8"))
    finally:
        monkeypatch.setattr(verify, "BASELINE_PATH", original_path)

    assert written.get("_note5") == "a dated, hand-written reason", (
        "an unrelated top-level key was lost across save_baseline(): %r" % written
    )
    assert written["ratchets"] == {"reuse_macro_names": 20}, (
        "the ratchets that were actually updated did not round-trip: %r" % written
    )


def test_update_baseline_preserves_the_bare_note_key(tmpdir, monkeypatch):
    """save_baseline() must never overwrite an existing bare '_note' key --
    that key holds real, hand-written, dated content in the live baseline
    file today, and every previous version of this function unconditionally
    replaced it with a generic 'updated <date>' stub. The routine stamp now
    goes under its own key instead."""
    sys.path.insert(0, SCRIPTS)
    import verify  # noqa: E402

    baseline_path = tmpdir.join("verification_baseline.json")
    baseline_path.write_text(
        json.dumps({"_note": "an existing hand-written, dated reason",
                    "ratchets": {"reuse_macro_names": 19}}),
        encoding="utf-8",
    )

    original_path = verify.BASELINE_PATH
    monkeypatch.setattr(verify, "BASELINE_PATH", type(original_path)(str(baseline_path)))
    try:
        verify.save_baseline({"reuse_macro_names": 20}, "updated for this test")
        written = json.loads(baseline_path.read_text(encoding="utf-8"))
    finally:
        monkeypatch.setattr(verify, "BASELINE_PATH", original_path)

    assert written.get("_note") == "an existing hand-written, dated reason", (
        "an existing bare _note key must survive save_baseline(): %r" % written
    )
    assert written.get("_last_baseline_update") == "updated for this test", (
        "the routine dated stamp must be written under its own key, not '_note': %r" % written
    )


def test_update_baseline_reports_a_rise_as_loudly_as_a_fall():
    """_baseline_diff must surface a RISE, not only a fall -- the CLI branch
    that used to print falls and stay silent about rises."""
    sys.path.insert(0, SCRIPTS)
    import verify  # noqa: E402

    lowered, raised = verify._baseline_diff({"x": 10, "y": 5}, {"x": 9, "y": 6})
    assert lowered == {"x": (10, 9)}, "a real fall must be reported: %r" % lowered
    assert raised == {"y": (5, 6)}, "a real rise must be reported, not silently accepted: %r" % raised


def test_update_baseline_prints_a_rise_when_driven_through_main(tmpdir, monkeypatch, capsys):
    """The rise print must be pinned by driving verify.main() itself, not by
    calling _baseline_diff() directly -- a test that only exercises the diff
    function leaves the print loop inside main() completely unexercised, so
    deleting that loop would not turn any test red. BASELINE_PATH points at
    a tmpdir file seeded with a baseline (1) far below the real measurement,
    and only the fast reuse-macro-names gate is run, so this stays quick."""
    sys.path.insert(0, SCRIPTS)
    import verify  # noqa: E402

    baseline_path = tmpdir.join("verification_baseline.json")
    baseline_path.write_text(
        json.dumps({"_note": "existing", "ratchets": {"reuse_macro_names": 1}}),
        encoding="utf-8",
    )

    original_path = verify.BASELINE_PATH
    monkeypatch.setattr(verify, "BASELINE_PATH", type(original_path)(str(baseline_path)))
    try:
        exit_code = verify.main(["--update-baseline", "--gate", "reuse-macro-names"])
        captured = capsys.readouterr()
        written = json.loads(baseline_path.read_text(encoding="utf-8"))
    finally:
        monkeypatch.setattr(verify, "BASELINE_PATH", original_path)

    assert exit_code == 0, "the update-baseline path itself must exit cleanly"
    assert "RAISED reuse_macro_names" in captured.out, (
        "the real measurement is above the seeded baseline (1); main() must "
        "print the rise, not stay silent about it: %r" % captured.out
    )
    assert written["ratchets"]["reuse_macro_names"] > 1, (
        "the real measurement should have been written: %r" % written
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
