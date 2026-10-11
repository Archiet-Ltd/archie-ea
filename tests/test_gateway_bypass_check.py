"""Gateway bypass enumeration -- AST-based scanning.

Scans the codebase with the `ast` module for direct provider calls that do not
go through the shared provider-register guard helper. It covers:

1. SDK method calls such as `.chat.completions.create`, `.messages.create`,
   `.embeddings.create`, `.generate_content`, and `openai.Model.list()`.
2. Raw `requests`/`http_requests` calls to provider hosts such as OpenAI,
   Anthropic, OpenRouter, Gemini, and DeepSeek.

Every discovered direct provider call must sit in a function that invokes
`LLMService._guard_provider_call(...)` before the outbound call.
"""

from __future__ import annotations

import ast
import importlib.util
import shutil
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Gateway / exempt files
# ---------------------------------------------------------------------------

GATEWAY_FILES = {
    "app/modules/ai_chat/services/llm_service_impl.py",
}

# Directories that are not real application code
SKIP_DIR_PREFIXES = (
    "code_templates/",
    "migrations/",
)

GUARD_HELPER = "_guard_provider_call"

REQUEST_TARGETS = {
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.delete",
    "http_requests.get",
    "http_requests.post",
    "http_requests.put",
    "http_requests.delete",
}

RAW_PROVIDER_HOST_MARKERS = (
    "api.openai.com",
    "api.anthropic.com",
    "openrouter.ai",
    "generativelanguage.googleapis.com",
    "api.deepseek.com",
    "GENERATIVE_BASE_URL",
    "UPLOAD_BASE_URL",
)

NON_PROMPT_SURFACES = {
    "app/api/v1/llm.py",
    "app/services/infrastructure_polling_service.py",
}

REQUIRED_GUARDED_CALLS = [
    ("app/modules/ai_chat/services/agent_runner.py", "client.messages.create", 1, "assistant agent anthropic call"),
    ("app/modules/ai_chat/services/agent_runner.py", "client.chat.completions.create", 2, "assistant agent openai-compatible call"),
    ("app/modules/ai_chat/services/ai_chat_multi_model.py", "self.openai_client.chat.completions.create", 1, "multi-model openai call"),
    ("app/modules/ai_chat/services/ai_chat_multi_model.py", "self.anthropic_client.messages.create", 1, "multi-model anthropic call"),
    ("app/modules/ai_chat/services/ai_semantic_discovery_service.py", "openai.Model.list", 1, "semantic discovery availability probe"),
    ("app/modules/ai_chat/services/multi_domain_chat_service.py", "client.chat.completions.create", 1, "vision openai call"),
    ("app/modules/ai_chat/services/multi_domain_chat_service.py", "client.messages.create", 1, "vision anthropic call"),
    ("app/modules/ai_chat/services/multi_domain_chat_service.py", "gen_model.generate_content", 1, "vision gemini call"),
    ("app/modules/architecture/services/gemini_file_search_service.py", "raw-provider-http", 4, "gemini file search HTTP calls"),
    ("app/modules/architecture/services/inference_providers.py", "client.messages.create", 1, "inference anthropic call"),
    ("app/modules/architecture/services/inference_providers.py", "client.chat.completions.create", 1, "inference openai call"),
    ("app/modules/architecture/services/multi_modal_llm_service.py", "raw-provider-http", 5, "multi-modal gemini HTTP calls"),
    ("app/services/vector_embedding_service.py", "client.embeddings.create", 1, "embedding openai call"),
]

# ---------------------------------------------------------------------------
# Expected bypass site list
#
# Each entry: (file_path, pattern, expected_count, description)
#   file_path       -- relative path from repo root
#   pattern         -- the call expression label (e.g. "OpenAI(", ".messages.create")
#   expected_count  -- how many times this pattern appears in the file
#   description     -- plain-text explanation of the work that will remove the call
#
# Line numbers are NOT used. The check matches on (file_path, pattern) only,
# so adding blank lines or reordering code within a file does not break it.
# ---------------------------------------------------------------------------

EXPECTED_BYPASSES = []

# ---- helpers ------------------------------------------------------------

def _should_skip_path(rel):
    if rel.startswith("tests/") or rel.startswith("."):
        return True
    if "/tests/" in rel:
        return True
    for prefix in SKIP_DIR_PREFIXES:
        if rel.startswith(prefix):
            return True
    parts = Path(rel).parts
    if any(p in ("__pycache__", "node_modules", "venv", ".venv") for p in parts):
        return True
    return False


def _resolve_call_target(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _resolve_call_target(node.value)
        if base is None:
            return None
        return base + "." + node.attr
    return None


def _is_openai_constructor(name):
    return name == "OpenAI" or name == "openai.OpenAI"


def _is_anthropic_constructor(name):
    return name == "Anthropic" or name == "anthropic.Anthropic"


def _is_genai_generative_model(name):
    return name == "genai.GenerativeModel"


def _is_sentence_transformer(name):
    if name == "SentenceTransformer":
        return True
    if name == "_SentenceTransformer":
        return True
    return name.endswith(".SentenceTransformer")


SDK_CALL_PATTERNS = [
    ".chat.completions.create",
    ".messages.create",
    ".embeddings.create",
    ".generate_content",
    "Model.list",
]


def _is_provider_sdk_call(target):
    return any(target.endswith(pat) for pat in SDK_CALL_PATTERNS)


def _contains_provider_host(segment: str | None) -> bool:
    if not segment:
        return False
    return any(marker in segment for marker in RAW_PROVIDER_HOST_MARKERS)


def _is_raw_provider_http_call(node, target, source):
    if target not in REQUEST_TARGETS:
        return None
    segment = ast.get_source_segment(source, node)
    if _contains_provider_host(segment):
        return "raw-provider-http"
    return None


def _scan_callable(node, rel, source):
    has_guard = False
    findings = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        target = _resolve_call_target(sub.func)
        if target is None:
            continue
        if target.endswith(GUARD_HELPER):
            has_guard = True
            continue
        if _is_provider_sdk_call(target):
            findings.append((rel, sub.lineno, target))
            continue
        raw_label = _is_raw_provider_http_call(sub, target, source)
        if raw_label is not None:
            findings.append((rel, sub.lineno, raw_label))

    if findings and not has_guard:
        return findings
    return []


def _scan_callable_all(node, rel, source):
    findings = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        target = _resolve_call_target(sub.func)
        if target is None:
            continue
        if _is_provider_sdk_call(target):
            findings.append((rel, sub.lineno, target))
            continue
        raw_label = _is_raw_provider_http_call(sub, target, source)
        if raw_label is not None:
            findings.append((rel, sub.lineno, raw_label))
    return findings


def _iter_callable_nodes(tree):
    def _walk_body(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield node
            elif isinstance(node, ast.ClassDef):
                yield from _walk_body(node.body)

    yield from _walk_body(tree.body)


def scan_file(file_path, rel):
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []

    results = []
    for node in _iter_callable_nodes(tree):
        results.extend(_scan_callable(node, rel, source))
    return results


def scan_file_all(file_path, rel):
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []

    results = []
    for node in _iter_callable_nodes(tree):
        results.extend(_scan_callable_all(node, rel, source))
    return results


def discover_bypass_sites():
    all_results = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if _should_skip_path(rel):
            continue
        if rel in GATEWAY_FILES:
            continue
        if rel in NON_PROMPT_SURFACES:
            continue
        sites = scan_file(path, rel)
        all_results.extend(sites)
    return all_results


def discover_provider_call_sites():
    all_results = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if _should_skip_path(rel):
            continue
        if rel in GATEWAY_FILES:
            continue
        if rel in NON_PROMPT_SURFACES:
            continue
        sites = scan_file_all(path, rel)
        all_results.extend(sites)
    return all_results


def _build_expected_lookup():
    """Build {(file, pattern): expected_count} from EXPECTED_BYPASSES."""
    lookup = {}
    for entry in EXPECTED_BYPASSES:
        key = (entry[0], entry[1])
        lookup[key] = entry[2]
    return lookup


def _group_discovered(discovered):
    """Group discovered sites by (file, pattern) and return {(file, pattern): count}."""
    grouped = {}
    for rel, lineno, pattern in discovered:
        key = (rel, pattern)
        grouped[key] = grouped.get(key, 0) + 1
    return grouped


# ---- tests ----------------------------------------------------------


def test_no_unaccounted_bypasses():
    discovered = discover_bypass_sites()
    discovered_grouped = _group_discovered(discovered)
    expected_lookup = _build_expected_lookup()

    unaccounted = []
    for key, count in discovered_grouped.items():
        if key not in expected_lookup:
            unaccounted.append((key[0], key[1], count))
    assert unaccounted == [], (
        "Unaccounted bypass site(s) not in EXPECTED_BYPASSES:\n"
        + "\n".join(f"  {f}:{p} (count={c})" for f, p, c in unaccounted)
    )


def test_known_bypasses_still_present():
    discovered = discover_provider_call_sites()
    discovered_grouped = _group_discovered(discovered)

    mismatches = []
    for entry in REQUIRED_GUARDED_CALLS:
        key = (entry[0], entry[1])
        expected_count = entry[2]
        actual_count = discovered_grouped.get(key, 0)
        if actual_count != expected_count:
            mismatches.append((entry[0], entry[1], expected_count, actual_count, entry[3]))
    assert mismatches == [], (
        "Expected guarded provider call count mismatch:\n"
        + "\n".join(
            f"  {f}:{p} expected={e} actual={a} ({d})"
            for f, p, e, a, d in mismatches
        )
    )


def test_seeded_direct_provider_call_detected():
    """A function that calls a provider SDK without the guard helper is detected."""
    tmp_dir = REPO_ROOT / "tmp_bypass_check"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / "_seed_bypass.py"

    seed_code = textwrap.dedent("""\
        \"\"\"Seeded bypass file -- should be detected.\"\"\"
        from openai import OpenAI


        def do_thing(prompt):
            client = OpenAI(api_key="sk-test-1234")
            return client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
            )
    """)

    try:
        tmp_file.write_text(seed_code, encoding="utf-8")
        rel = tmp_file.relative_to(REPO_ROOT).as_posix()
        sites = scan_file(tmp_file, rel)
        assert len(sites) >= 1, (
            f"Seeded provider SDK call in {rel} was NOT detected. "
            f"AST scan returned: {sites}"
        )
        provider_sites = [s for s in sites if s[2].endswith(".chat.completions.create")]
        assert provider_sites, (
            f"Expected direct provider call detection in seeded file but got: {sites}"
        )
    finally:
        if tmp_file.exists():
            tmp_file.unlink()
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


def test_bypass_check_line_number_independent():
    """Adding a blank line above an unguarded provider call does not break detection,
    while a new direct raw-HTTP call is still reported.

    This proves the check matches on (file_path, pattern) not on line numbers.
    """
    tmp_dir = REPO_ROOT / "tmp_bypass_check_ln"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # -- Part 1: file with an unguarded provider call pattern --
        tmp_file = tmp_dir / "_test_ln_indep.py"
        code_v1 = textwrap.dedent("""\
            \"\"\"Test line-number independence.\"\"\"
            from openai import OpenAI


            def make_client():
                client = OpenAI(api_key="sk-test")
                return client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "hi"}],
                )
        """)
        tmp_file.write_text(code_v1, encoding="utf-8")
        rel = tmp_file.relative_to(REPO_ROOT).as_posix()

        sites_v1 = scan_file(tmp_file, rel)
        openai_v1 = [s for s in sites_v1 if s[2].endswith(".chat.completions.create")]
        assert len(openai_v1) == 1, (
            f"V1: expected 1 provider call, got {len(openai_v1)}: {sites_v1}"
        )

        # Add a blank line above the call (line number changes)
        code_v2 = textwrap.dedent("""\
            \"\"\"Test line-number independence.\"\"\"
            from openai import OpenAI


            def make_client():

                client = OpenAI(api_key="sk-test")
                return client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "hi"}],
                )
        """)
        tmp_file.write_text(code_v2, encoding="utf-8")

        sites_v2 = scan_file(tmp_file, rel)
        openai_v2 = [s for s in sites_v2 if s[2].endswith(".chat.completions.create")]
        assert len(openai_v2) == 1, (
            f"V2 (blank line added): expected 1 provider call, "
            f"got {len(openai_v2)}: {sites_v2}"
        )

        # -- Part 2: new file with a direct raw HTTP call not in EXPECTED_BYPASSES --
        new_file = tmp_dir / "_test_new_direct_call.py"
        new_code = textwrap.dedent("""\
            \"\"\"New direct call file.\"\"\"
            import requests


            def do_thing():
                return requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
                    json={"contents": []},
                )
        """)
        new_file.write_text(new_code, encoding="utf-8")
        new_rel = new_file.relative_to(REPO_ROOT).as_posix()

        new_sites = scan_file(new_file, new_rel)
        raw_sites = [s for s in new_sites if s[2] == "raw-provider-http"]
        assert len(raw_sites) == 1, (
            f"New direct call not detected: {new_sites}"
        )

        # Verify the new call would be unaccounted
        expected_lookup = _build_expected_lookup()
        for rel_found, lineno, pattern in new_sites:
            key = (new_rel, pattern)
            assert key not in expected_lookup, (
                f"New call ({new_rel}, {pattern}) should not be in EXPECTED_BYPASSES"
            )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_llm_boundary_gate_is_clean():
    """The llm-boundary gate still passes on the emitter tree."""
    checker_path = REPO_ROOT / "scripts" / "check_llm_boundary.py"
    spec = importlib.util.spec_from_file_location("check_llm_boundary", checker_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    violations = mod.find_violations()
    assert violations == [], (
        "llm-boundary violations in emitter tree:\n"
        + "\n".join(f"  {f}:{ln}: {txt}" for f, ln, txt in violations)
    )
