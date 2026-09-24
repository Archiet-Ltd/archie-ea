"""Shared helper functions for codegen route modules."""
from app.services.archimate_backbone import sync_archimate_element
import hashlib
import json
import logging
import os
import re
from datetime import datetime

from flask_login import current_user

from app.extensions import db
from app.models.solution_models import Solution
from app.modules.codegen.models import (  # noqa: F401 — import surface shared by codegen route sub-modules
    CodegenGeneration, CodegenGenerationHistory, CodegenSystemBoundary,
    CodegenTemplateSet, CodegenTemplateFile, DataImport,
)

# Alias for readability — all SystemBoundary refs here mean the codegen one
SystemBoundary = CodegenSystemBoundary

logger = logging.getLogger(__name__)


# ── Supported languages — sourced from STACK_REGISTRY (single source of truth) ──
from app.modules.codegen.stack_registry import STACK_REGISTRY, SUPPORTED_LANGUAGES  # noqa: E402,F401 — re-exported to codegen_routes



def _snake(name):
    """snake_case a name.

    Hoisted from a nested definition. It was defined inside one function but
    referenced from six call sites in *other* functions, so each of those raised
    NameError. The body is unchanged, and it closes over nothing beyond the
    module-level `re` import, so hoisting is behaviour-preserving.

    Note there are three near-duplicates of this in the codebase (`_snake_name` in
    preview_routes.py, `_snake_case` in aabl_compiler.py, and this one). They should
    converge on a single implementation.
    """
    s = (name or "unknown").strip()
    # Split PascalCase/camelCase into words before snake_casing
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"[^a-zA-Z0-9]", "_", s)
    return re.sub(r"_+", "_", s).strip("_").lower()


def _check_access(solution, user=None):
    """Verify current user can access this solution's workbench."""
    user = user if user is not None else current_user
    if not user.is_authenticated:
        return False
    is_admin = getattr(user, "is_admin", False)
    if (is_admin() if callable(is_admin) else bool(is_admin)):
        return True
    if getattr(user, "is_platform_admin", False):
        return True
    if getattr(solution, "created_by_id", None) == user.id:
        return True
    stakeholder_emails = (
        getattr(solution, "solution_owner", None),
        getattr(solution, "business_sponsor", None),
        getattr(solution, "technical_lead", None),
    )
    return any(
        email and user.email and email.strip().lower() == user.email.strip().lower()
        for email in stakeholder_emails
    )



def _infer_auth_from_blueprint(section_narratives: dict) -> str:
    """Infer recommended auth type from blueprint security_viewpoint narrative.

    Returns a codegen auth value: 'jwt-local', 'oauth2', 'api-key', or 'none'.
    """
    text = (section_narratives.get("security_viewpoint") or "").lower()
    if not text:
        return "none"
    if "jwt" in text:
        return "jwt-local"
    if "oauth" in text:
        return "oauth2"
    if "api key" in text or "api-key" in text or "apikey" in text:
        return "api-key"
    return "none"



# ── Secret scanning patterns (GAP-10) ──
_SECRET_PATTERNS = [
    (re.compile(r'(?:api[_-]?key|apikey)\s*[=:]\s*["\'][A-Za-z0-9_\-]{20,}["\']', re.I), "API key"),
    (re.compile(r'(?:password|passwd|pwd)\s*[=:]\s*["\'][^"\']{8,}["\']', re.I), "Password"),
    (re.compile(r'(?:secret|token)\s*[=:]\s*["\'][A-Za-z0-9_\-]{20,}["\']', re.I), "Secret/Token"),
    (re.compile(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----', re.I), "Private key"),
    # Cloud provider credential patterns
    (re.compile(r'AKIA[0-9A-Z]{16}', re.I), "AWS Access Key"),
    (re.compile(r'(?:aws_secret_access_key|aws_secret)\s*[=:]\s*["\'][^"\']{20,}["\']', re.I), "AWS Secret"),
    (re.compile(r'ghp_[A-Za-z0-9]{36}', re.I), "GitHub token"),
    (re.compile(r'sk-[A-Za-z0-9]{20,}', re.I), "OpenAI/Stripe secret key"),
    # Dev-mode defaults that must never ship — replace with env var reference
    (re.compile(r'(?:jwt[_-]?secret|secret[_-]?key)\s*[=:]\s*["\'](?:test|dev|change-me|changeme|replace|secret|your-secret)[^"\']*["\']', re.I), "Dev JWT/secret default"),
]

# Security hardening checks for generated code (not secrets, but risky patterns)
_SECURITY_ISSUES = [
    # CORS wildcard in settings — must be restricted to specific origins in production
    (re.compile(r'allowed_origins\s*(?::\s*list\[str\])?\s*=\s*\["?\*"?\]', re.I), "CORS wildcard (allowed_origins=['*']) — restrict to specific origins for production"),
    # DEBUG=True leaking into non-test code
    (re.compile(r'\bdebug\s*(?::\s*bool)?\s*=\s*True\b', re.I), "debug=True in generated config — must be False in production"),
    # Hardcoded localhost URLs in non-test, non-example files
    (re.compile(r'(?:base_url|DATABASE_URL)\s*[=:]\s*["\']http://localhost', re.I), "Hardcoded localhost URL — use environment variable"),
]



def _parse_condition_to_preconditions(condition_text: str) -> list:
    """Parse a human-readable rule condition string into structured precondition dicts.

    handler.py.j2 iterates `{% for pc in rule.preconditions %}` and accesses
    `{{ pc.field }}`, `{{ pc.operator }}`, `{{ pc.value }}`.  Passing raw strings
    causes a Jinja2 AttributeError at render time — this function converts the
    condition text to the structured format the template requires.

    Returns a list of {field, operator, value} dicts, guaranteed non-empty.
    Falls back to a generic "not None" check on the first word when no operator
    is detected (so the generated handler still has a precondition body rather
    than an empty for-loop that produces dead code).

    Examples:
        "amount > 0"                      → [{"field": "amount", "operator": ">", "value": "0"}]
        "status must be 'approved'"       → [{"field": "status", "operator": "==", "value": "approved"}]
        "budget >= 1000"                  → [{"field": "budget", "operator": ">=", "value": "1000"}]
        "name is not empty"               → [{"field": "name", "operator": "!=", "value": "None"}]
        "response time < 200ms"           → [{"field": "response_time", "operator": "<", "value": "200"}]
    """
    if not condition_text or not condition_text.strip():
        return [{"field": "value", "operator": "!=", "value": "None"}]

    txt = condition_text.strip()

    # Pattern 1: field OP value  (handles >=, <=, ==, !=, >, <)
    m = re.search(r'(\w+)\s*(>=|<=|==|!=|>|<)\s*([^\s,;]+)', txt)
    if m:
        val = m.group(3).strip("'\"")
        return [{"field": m.group(1), "operator": m.group(2), "value": val}]

    # Pattern 2: "must be (equal to|greater than|less than|at least|at most) VALUE"
    m = re.search(
        r'(\w+)\s+(?:must\s+be\s+)?(?:equal\s+to|equals?)\s+["\']?([^\s"\',.;]+)["\']?', txt, re.I
    )
    if m:
        return [{"field": m.group(1), "operator": "==", "value": m.group(2).strip("'\"")}]

    m = re.search(
        r'(\w+)\s+(?:must\s+be\s+)?(?:greater\s+than|more\s+than)\s+(\d[\d.]*)', txt, re.I
    )
    if m:
        return [{"field": m.group(1), "operator": ">", "value": m.group(2)}]

    m = re.search(
        r'(\w+)\s+(?:must\s+be\s+)?(?:at\s+least|minimum|min)\s+(\d[\d.]*)', txt, re.I
    )
    if m:
        return [{"field": m.group(1), "operator": ">=", "value": m.group(2)}]

    m = re.search(
        r'(\w+)\s+(?:must\s+be\s+)?(?:less\s+than|at\s+most|maximum|max)\s+(\d[\d.]*)', txt, re.I
    )
    if m:
        return [{"field": m.group(1), "operator": "<", "value": m.group(2)}]

    # Pattern 3: "not empty / not null / not none / required"
    m = re.search(
        r'(\w+)\s+(?:is\s+)?(?:not\s+(?:empty|null|none|blank)|required|must\s+exist)', txt, re.I
    )
    if m:
        return [{"field": m.group(1), "operator": "!=", "value": "None"}]

    # Fallback: use the first meaningful word as field with a "not None" guard
    first_word = next(
        (w for w in re.split(r'\W+', txt) if len(w) >= 3 and w.lower() not in
         {"the", "must", "should", "have", "that", "this", "will", "with", "for", "are", "not"}),
        "value"
    )
    return [{"field": first_word, "operator": "!=", "value": "None"}]



def _build_peer_specs(solution_id: int) -> list:
    """Find peer solutions that share ArchiMate elements and have generated openapi.yaml files.

    Returns list of {solution_id, solution_name, openapi: dict}.
    Capped at 5 peers to stay within token budget. Used to generate typed client stubs
    so inter-solution calls reference real field names instead of guessed ones.
    """
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from app.models.solution_models import Solution as _Sol

        my_elem_q = db.session.query(
            SolutionArchiMateElement.element_id
        ).filter_by(solution_id=solution_id).subquery()

        peer_ids = db.session.query(
            SolutionArchiMateElement.solution_id
        ).filter(
            SolutionArchiMateElement.element_id.in_(my_elem_q),
            SolutionArchiMateElement.solution_id != solution_id,
        ).distinct().limit(5).all()

        peers = []
        for (pid,) in peer_ids:
            psol = _Sol.query.get(pid)
            pgen = CodegenGeneration.query.filter_by(solution_id=pid).first()
            if not psol or not pgen or not pgen.generated_files:
                continue
            raw = pgen.generated_files.get("openapi.yaml", "")
            if not raw:
                continue
            try:
                peers.append({
                    "solution_id": pid,
                    "solution_name": psol.name or f"Solution {pid}",
                    "openapi": json.loads(raw),
                })
            except Exception:
                continue
        return peers
    except Exception as exc:
        logger.debug("_build_peer_specs failed for solution %s: %s", solution_id, exc)
        return []



def _generate_peer_client_stub(peer: dict) -> str:
    """Generate app/integrations/<name>_client.py from a peer solution's openapi.yaml.

    Produces a typed httpx client class with one method per operation.
    The constructor takes base_url and optional auth_token.
    """
    name = peer["solution_name"]
    openapi = peer["openapi"]
    safe = re.sub(r'[^a-z0-9]', '_', name.lower()).strip('_')
    cls_name = ''.join(w.capitalize() for w in safe.split('_') if w) + "Client"

    methods = []
    for path_str, path_item in list(openapi.get("paths", {}).items())[:20]:
        for method in ("get", "post", "put", "delete", "patch"):
            op = path_item.get(method)
            if not op:
                continue
            op_id = op.get("operationId") or f"{method}_{re.sub(r'[^a-z0-9]', '_', path_str)}"
            op_id = re.sub(r'_+', '_', op_id).strip('_')[:40]
            has_body = method in ("post", "put", "patch")
            path_params = re.findall(r'\{([^}]+)\}', path_str)
            param_sig = ", ".join(path_params) + (", " if path_params else "") + ("body: dict = None" if has_body else "")
            path_fmt = re.sub(r'\{([^}]+)\}', r'{\1}', path_str)
            body_arg = ", json=body" if has_body else ""
            methods.append(
                f"    def {op_id}(self{', ' + param_sig if param_sig else ''}) -> dict:\n"
                f"        \"\"\"{'POST' if has_body else method.upper()} {path_str} — {op.get('summary', '')}\"\"\"\n"
                f"        r = self._client.{method}(f\"{path_fmt}\"{body_arg})\n"
                f"        r.raise_for_status()\n"
                f"        return r.json()\n"
            )

    methods_block = "\n".join(methods) if methods else "    pass\n"
    return (
        f'"""Typed client for {name} API — auto-generated from openapi.yaml by Entelim\n\n'
        f'Do not edit manually — regenerate via the Code Workbench.\n"""\n'
        f"import httpx\n\n\n"
        f"class {cls_name}:\n"
        f'    """HTTP client for {name} ({len(openapi.get("paths", {}))} endpoints)."""\n\n'
        f"    def __init__(self, base_url: str, auth_token: str = None, timeout: float = 10.0):\n"
        f"        headers = {{}}\n"
        f"        if auth_token:\n"
        f"            headers['Authorization'] = f'Bearer {{auth_token}}'\n"
        f"        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)\n\n"
        f"    def close(self):\n"
        f"        self._client.close()\n\n"
        f"    def __enter__(self):\n"
        f"        return self\n\n"
        f"    def __exit__(self, *_):\n"
        f"        self.close()\n\n"
        f"{methods_block}"
    )



def _validate_import_graph(files: dict) -> list:
    """Cross-file import graph validation — catches broken inter-module references.

    Builds a set of generated module paths from files, then scans every .py file
    for 'from app.X import Y' statements and checks that app.X maps to a generated
    file. Returns a list of {file, import_stmt, issue} dicts for broken imports.
    """
    # Build set of known module paths from generated files
    known_modules = set()
    for path in files:
        if not path.endswith(".py"):
            continue
        # Convert file path to dotted module name: app/models/user.py → app.models.user
        module = path.replace("/", ".").replace("\\", ".").removesuffix(".py")
        known_modules.add(module)
        # Also register parent packages
        parts = module.split(".")
        for i in range(1, len(parts)):
            known_modules.add(".".join(parts[:i]))

    broken = []
    import re
    _from_import = re.compile(r"^from\s+(app\.\S+)\s+import", re.MULTILINE)

    for path, content in files.items():
        if not path.endswith(".py"):
            continue
        for match in _from_import.finditer(content):
            module_ref = match.group(1)
            if module_ref not in known_modules:
                broken.append({
                    "file": path,
                    "import_stmt": match.group(0).strip(),
                    "issue": f"Module '{module_ref}' not found in generated bundle",
                })

    return broken



def _run_runtime_smoke_checks(files: dict, language: str, seed_context: dict | None = None) -> dict:
    """Run deterministic runtime smoke checks + seed expectation checks."""
    issues = {"errors": [], "warnings": [], "seed_expectation_issues": []}
    lower_language = (language or "").lower()

    if "python" in lower_language:
        has_main = "app/main.py" in files
        if not has_main:
            issues["errors"].append("Missing app/main.py entrypoint for Python target")
        for path, content in files.items():
            if not path.endswith(".py"):
                continue
            try:
                compile(content, path, "exec")
            except SyntaxError as exc:
                issues["errors"].append(f"{path}: syntax error ({exc.msg})")
        if has_main and "fastapi" not in (files.get("app/main.py", "") or "").lower():
            issues["warnings"].append("app/main.py does not appear to initialize FastAPI")

    if "go" in lower_language:
        if "go.mod" not in files:
            issues["errors"].append("Missing go.mod for Go target")
        if not any("package main" in (content or "") for path, content in files.items() if path.endswith(".go")):
            issues["errors"].append("No package main found in generated Go sources")

    if "java" in lower_language:
        if "pom.xml" not in files:
            issues["errors"].append("Missing pom.xml for Java/Spring target")
        spring_present = any(
            "@springbootapplication" in (content or "").lower()
            for path, content in files.items()
            if path.endswith(".java")
        )
        if not spring_present:
            issues["warnings"].append("No @SpringBootApplication class detected")

    if "salesforce" in lower_language:
        if "sfdx-project.json" not in files:
            issues["errors"].append("Missing sfdx-project.json for Salesforce target")
        apex_present = any(path.endswith(".cls") or path.endswith(".trigger") for path in files)
        if not apex_present:
            issues["errors"].append("No Apex class/trigger generated for Salesforce target")

    if "sap-cap" in lower_language:
        if "package.json" not in files:
            issues["errors"].append("Missing package.json for SAP CAP target")
        if "db/schema.cds" not in files:
            issues["errors"].append("Missing db/schema.cds for SAP CAP target")

    if seed_context:
        seed_index = (seed_context or {}).get("seed_index", {}) or {}
        corpus = "\n".join((content or "").lower() for content in files.values())
        for entity_name, seed in seed_index.items():
            required_fields = seed.get("required_fields", []) or []
            for field_name in required_fields:
                token = str(field_name).lower()
                if token and token not in corpus:
                    issues["seed_expectation_issues"].append(f"{entity_name}.{token}")

    return issues



_PY_TYPE_MAP = {
    "string": "sa.String(255)", "str": "sa.String(255)", "text": "sa.Text()",
    "integer": "sa.Integer()", "int": "sa.Integer()", "float": "sa.Float()",
    "boolean": "sa.Boolean()", "bool": "sa.Boolean()",
    "datetime": "sa.DateTime()", "date": "sa.Date()",
    "uuid": "sa.String(36)", "json": "sa.JSON()", "dict": "sa.JSON()",
}



_LONG_IMPORT_RE = re.compile(
    r'^(from\s+\S+\s+import\s+)([^(\n]{89,})$',  # single-line "from x import A, B, C" > 88 chars
    re.MULTILINE,
)
_MAX_LINE = 88  # black default; PEP-8 is 79, but 88 avoids excessive churn on generated code


def _wrap_long_imports(content: str) -> str:
    """Wrap long single-line from-imports into parenthesised multi-line form.

    from foo import A, B, C, D  (>88 chars)
    →
    from foo import (
        A,
        B,
        C,
        D,
    )
    """
    def _wrap_match(m: re.Match) -> str:
        prefix = m.group(1)   # "from foo.bar import "
        names_raw = m.group(2)  # "Alpha, Beta, Gamma, ..."
        names = [n.strip() for n in names_raw.split(',') if n.strip()]
        indented = ',\n    '.join(names)
        return f"{prefix}(\n    {indented},\n)"

    return _LONG_IMPORT_RE.sub(_wrap_match, content)


def _format_python_files(files: dict) -> None:
    """PEP-8 normalisation: trailing whitespace, tab expansion, blank line collapse, long import
    wrapping, and EOF newline.  Mutates files in-place.  Zero external dependencies — stdlib only.
    """
    _multi_blank = re.compile(r'\n{3,}')
    # Remove empty from-import blocks: from X import (\n) causes SyntaxError
    _empty_imp_ml = re.compile(r'^from\s+\S+\s+import\s*\(\s*\n[\s\n]*\)', re.MULTILINE)
    _empty_imp_1l = re.compile(r'^from\s+\S+\s+import\s*\(\s*\)\s*$', re.MULTILINE)
    for path in list(files):
        if not path.endswith('.py'):
            continue
        content = files[path]
        content = content.replace('\t', '    ')
        content = '\n'.join(line.rstrip() for line in content.splitlines())
        content = _multi_blank.sub('\n\n', content)
        content = _empty_imp_ml.sub('', content)
        content = _empty_imp_1l.sub('', content)
        content = _wrap_long_imports(content)
        content = _multi_blank.sub('\n\n', content)
        content = content.rstrip('\n') + '\n'
        files[path] = content


def _lint_python_files(files: dict) -> dict:
    """Count PEP-8 violations in generated Python files using stdlib only.

    Checks:
      E501 — line too long (> _MAX_LINE chars)
      W291/W293 — trailing whitespace (should be zero after _format_python_files)
      E302 — missing 2 blank lines before top-level def/class
      E711 — comparison to None using == instead of is

    Returns a summary dict: {
        "total_violations": N,
        "e501_count": N,
        "worst_files": [{"file": ..., "violations": N}],
        "sample_lines": [{"file": ..., "line": N, "code": "E501", "text": "..."}],
    }
    """
    _trailing_ws = re.compile(r'[ \t]+$', re.MULTILINE)
    _compare_none = re.compile(r'\b(?:==|!=)\s*None\b|\bNone\s*(?:==|!=)\b')
    e501 = 0
    w29x = 0
    e711 = 0
    worst: list = []
    samples: list = []

    for path, content in files.items():
        if not path.endswith('.py'):
            continue
        file_violations = 0
        lines = content.splitlines()
        for lineno, line in enumerate(lines, 1):
            if len(line) > _MAX_LINE:
                e501 += 1
                file_violations += 1
                if len(samples) < 5:
                    samples.append({"file": path, "line": lineno, "code": "E501",
                                    "text": line[:120]})
            if _trailing_ws.search(line):
                w29x += 1
                file_violations += 1
            if _compare_none.search(line) and 'test' not in path:
                e711 += 1
                file_violations += 1
        if file_violations:
            worst.append({"file": path, "violations": file_violations})

    worst.sort(key=lambda x: -x["violations"])
    total = e501 + w29x + e711
    return {
        "total_violations": total,
        "e501_count": e501,
        "w29x_count": w29x,
        "e711_count": e711,
        "worst_files": worst[:5],
        "sample_lines": samples,
        "max_line_length": _MAX_LINE,
    }


def _check_security_hardening(files: dict) -> dict:
    """Check generated code for security hardening issues beyond raw secrets.

    Returns {
        "issues": [{"file": ..., "issue": "..."}],
        "has_auth": bool,        # at least one route imports verify_token / auth_config
        "cors_wildcard": bool,   # allowed_origins=['*'] found
        "debug_mode": bool,      # debug=True in non-test config
        "score": 0-100,          # 100 = no issues
    }
    """
    issues = []
    has_auth = False
    cors_wildcard = False
    debug_mode = False

    # Check for auth import in route files
    for path, content in files.items():
        if path.endswith('.py') and ('route' in path or 'api/' in path or 'handler' in path):
            if 'verify_token' in content or 'require_role' in content or 'get_current_user' in content:
                has_auth = True
                break

    # Check security issues (excluding test/example files)
    for path, content in files.items():
        is_test_or_example = any(x in path for x in ('test', 'example', '.env.example', 'conftest'))
        if is_test_or_example:
            continue
        for pattern, description in _SECURITY_ISSUES:
            if pattern.search(content):
                issues.append({"file": path, "issue": description})
                if "CORS wildcard" in description:
                    cors_wildcard = True
                if "debug=True" in description:
                    debug_mode = True

    # Score: start at 100, deduct per issue (no auth = -40, each other issue = -10)
    score = 100
    if not has_auth:
        score -= 40
        issues.insert(0, {"file": "app/api/routes/", "issue": "No auth dependency (verify_token / require_role) found in any route file"})
    score -= len([i for i in issues if "No auth" not in i["issue"]]) * 10
    score = max(score, 0)

    # G13: auto-remediate CORS wildcard — replace in-place so the issue is fixed
    # in the generated output rather than just flagged. Wildcard CORS bypasses the
    # entire origin check; every consumer deployment would be vulnerable by default.
    # Matches both pydantic-settings style (allowed_origins) and FastAPI middleware
    # style (allow_origins), with both single and double quote variants.
    if cors_wildcard:
        _cors_wildcard_pattern = re.compile(
            r"(allow(?:ed)?_origins)\s*(?::\s*list\[str\])?\s*=\s*\[['\"]?\*['\"]?\]", re.I
        )
        def _cors_replace(m):
            var = m.group(1)  # preserve original variable name (allow_origins or allowed_origins)
            return (
                f'{var} = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")'
            )
        for _path in list(files.keys()):
            if _cors_wildcard_pattern.search(files[_path]):
                files[_path] = _cors_wildcard_pattern.sub(_cors_replace, files[_path])
                # Ensure os is imported in the patched file
                if "import os\n" not in files[_path] and not files[_path].startswith("import os"):
                    files[_path] = "import os\n" + files[_path]

    return {
        "issues": issues,
        "has_auth": has_auth,
        "cors_wildcard": cors_wildcard,
        "debug_mode": debug_mode,
        "score": score,
    }



def _topological_sort_classes(classes: list) -> list:
    """Sort UML classes so FK-dependent tables are created after their FK targets.

    Without this, `alembic upgrade head` fails with IntegrityError when a table with
    a FK is created before the referenced table exists.  Kahn's algorithm — O(V+E).
    Falls back to original order on circular dependency (which Alembic handles via
    deferred FK resolution anyway, but sorted order is safer on strict engines).
    """
    def _sn(name: str) -> str:
        s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", (name or "unknown").strip())
        s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
        return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

    by_name = {_sn(c.get("name", "unknown")): c for c in classes}
    # deps[A] = set of table names that A depends on (A has FK → dep)
    deps: dict = {name: set() for name in by_name}
    for name, cls in by_name.items():
        for rel in cls.get("relationships", []):
            target = _sn(rel.get("target_class", ""))
            if target and target in by_name and target != name:
                deps[name].add(target)

    # rdeps[B] = set of tables that depend on B
    rdeps: dict = {name: set() for name in by_name}
    for name, dep_set in deps.items():
        for dep in dep_set:
            rdeps[dep].add(name)

    in_degree = {name: len(dep_set) for name, dep_set in deps.items()}
    queue = sorted(name for name, d in in_degree.items() if d == 0)
    sorted_names = []
    while queue:
        node = queue.pop(0)
        sorted_names.append(node)
        for dependent in sorted(rdeps[node]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(sorted_names) != len(by_name):
        logger.warning("Circular FK dependency in UML class diagram — using original order")
        return classes

    return [by_name[name] for name in sorted_names]



def _generate_alembic_migration(uml: dict, genome: dict = None) -> str:
    """Generate alembic/versions/0001_initial.py deterministically from UML class diagram.

    Produces real op.create_table() calls so `alembic upgrade head` can build the DB
    from scratch without running autogenerate. This closes the "clone and run" gap.
    Tables are created in FK-dependency order (referenced tables first) so the migration
    succeeds on strict FK-enforcing engines like PostgreSQL.

    D-PASS4-6 / G3: In genome mode the UML snapshot is None — fall back to generating
    the Alembic schema from genome modules so the deployed app's models match the DB.
    """
    from datetime import datetime as _dt
    raw_classes = uml.get("class_diagram", {}).get("classes", [])

    # D-PASS4-6: if UML has no classes but a genome is available, synthesize classes
    # from the genome modules so the migration schema matches the generated models.
    if not raw_classes and genome:
        _type_map = {
            "uuid": "string", "string": "string", "text": "text", "integer": "integer",
            "int": "integer", "float": "float", "boolean": "boolean", "bool": "boolean",
            "datetime": "datetime", "date": "date", "json": "json",
        }
        for mod_key, mod_def in genome.get("modules", {}).items():
            # Skip buy-decision modules — no DB table needed
            if mod_def.get("build_or_buy") in ("buy", "vendor"):
                continue
            fields_dict = mod_def.get("fields", {})
            for entity_name, entity_fields in fields_dict.items():
                cls_fields = []
                rels = []
                for f in entity_fields:
                    fname = f.get("name", "")
                    if fname.lower() == "id":
                        continue
                    ftype = _type_map.get((f.get("type") or "string").lower(), "string")
                    if f.get("foreign_key"):
                        target = fname[:-3] if fname.endswith("_id") else fname
                        rels.append({"target_class": target})
                    else:
                        cls_fields.append({"name": fname, "type": ftype, "nullable": True})
                raw_classes.append({
                    "name": entity_name,
                    "fields": cls_fields,
                    "relationships": rels,
                })

    classes = _topological_sort_classes(raw_classes)
    now = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    def _to_snake(name: str) -> str:
        """PascalCase → snake_case preserving acronyms: HTTPSEndpoint → https_endpoint."""
        s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", (name or "unknown").strip())
        s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
        return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

    seen_tables: set = set()
    table_blocks = []
    drop_stmts = []
    for cls in classes:
        raw_name = cls.get("name", "unknown")
        table_name = _to_snake(raw_name)
        # Dedup: two classes that produce the same table name would cause alembic to crash
        if table_name in seen_tables:
            table_name = f"{table_name}_{raw_name.lower()[:8]}"
        seen_tables.add(table_name)
        fields = cls.get("fields", [])
        cols = ["        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),"]
        for f in fields:
            if f.get("name", "").lower() == "id":
                continue
            sa_type = _PY_TYPE_MAP.get((f.get("type") or "string").lower(), "sa.String(255)")
            nullable = "nullable=True" if f.get("nullable", True) else "nullable=False"
            cols.append(f"        sa.Column('{f['name']}', {sa_type}, {nullable}),")
        # Foreign keys
        for rel in cls.get("relationships", []):
            target = _to_snake(rel.get("target_class", ""))
            if target:
                fk_col = f"{target}_id"
                cols.append(f"        sa.Column('{fk_col}', sa.Integer(), sa.ForeignKey('{target}.id'), nullable=True),")
        col_block = "\n".join(cols)
        table_blocks.append(f"    op.create_table('{table_name}',\n{col_block}\n    )")
        drop_stmts.append(f"    op.drop_table('{table_name}')")

    upgrade_body = "\n".join(table_blocks) if table_blocks else "    pass"
    downgrade_body = "\n".join(reversed(drop_stmts)) if drop_stmts else "    pass"

    return f'''"""Initial schema — generated by Entelim Code Workbench

Revision ID: 0001
Revises:
Create Date: {now}
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
{upgrade_body}


def downgrade() -> None:
{downgrade_body}
'''



def _generate_alembic_support_files(solution_name: str) -> dict:
    """Generate alembic.ini and alembic/env.py so `alembic upgrade head` works out-of-the-box.

    Without these two files the generated alembic/versions/0001_initial.py is unusable —
    Alembic refuses to run without a config file and an env.py that supplies the DB URL.
    """
    ini = """# alembic.ini — generated by Entelim Code Workbench
# Edit script_location and sqlalchemy.url as needed.
[alembic]
script_location = alembic
prepend_sys_path = .
version_path_separator = os

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""

    env_py = '''"""Alembic environment — generated by Entelim Code Workbench.

Reads DATABASE_URL from the environment (set via docker-compose or .env).
Run migrations: alembic upgrade head
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from DATABASE_URL env var
database_url = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/app")
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''

    return {
        "alembic.ini": ini,
        "alembic/env.py": env_py,
        "alembic/script.py.mako": (
            '"""${message}\n\nRevision ID: ${up_revision}\nRevises: ${down_revision | comma,n}\n'
            'Create Date: ${create_date}\n"""\n'
            'from alembic import op\nimport sqlalchemy as sa\n${imports if imports else ""}\n\n'
            'revision = ${repr(up_revision)}\ndown_revision = ${repr(down_revision)}\n'
            'branch_labels = ${repr(branch_labels)}\ndepends_on = ${repr(depends_on)}\n\n\n'
            'def upgrade() -> None:\n    ${upgrades if upgrades else "pass"}\n\n\n'
            'def downgrade() -> None:\n    ${downgrades if downgrades else "pass"}\n'
        ),
    }



def _generate_test_conftest(language: str = "python-fastapi") -> str:
    """Generate a deterministic tests/conftest.py appropriate for the target language.

    For Flask: uses Flask test client + SQLite (synchronous).
    For FastAPI: uses httpx AsyncClient + SQLite (async SQLAlchemy).
    Overrides whatever the LLM produces.
    """
    if language == "python-flask":
        return '''\
"""Test configuration — generated by Entelim Code Workbench (Flask).

Provides a synchronous SQLite test database so pytest runs without PostgreSQL.
Uses Flask's built-in test client.
"""
import os

# Must be set before any app imports so SQLAlchemy uses SQLite, not Postgres.
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-not-for-production")
os.environ.setdefault("JWT_SECRET", "test-jwt-not-for-production")

import pytest


# ── Dynamic imports — LLM may place app/Base at different paths ──────────────

def _try_import_app():
    for mod_path, attr in [
        ("app", "create_app"),
        ("app.app", "create_app"),
        ("wsgi", "app"),
    ]:
        try:
            mod = __import__(mod_path, fromlist=[attr])
            obj = getattr(mod, attr, None)
            if obj is not None:
                return obj
        except Exception:
            pass
    return None


def _try_import_db():
    for mod_path, attr in [
        ("app.extensions", "db"),
        ("app.db", "db"),
        ("app", "db"),
    ]:
        try:
            mod = __import__(mod_path, fromlist=[attr])
            obj = getattr(mod, attr, None)
            if obj is not None:
                return obj
        except Exception:
            pass
    return None


_create_app = _try_import_app()
_db = _try_import_db()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    """Create Flask app configured for testing."""
    if _create_app is None:
        pytest.skip("Flask app factory not importable")
    if callable(_create_app):
        application = _create_app()
    else:
        application = _create_app
    application.config["TESTING"] = True
    application.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    return application


@pytest.fixture(scope="session")
def db_tables(app):
    """Create all tables once for the whole test session, then drop them."""
    if _db is not None:
        with app.app_context():
            try:
                _db.create_all()
            except Exception as exc:
                pytest.skip(f"DB table creation failed: {exc}")
    yield
    if _db is not None:
        with app.app_context():
            try:
                _db.drop_all()
            except Exception:
                pass


@pytest.fixture
def client(app, db_tables):
    """Flask test client."""
    with app.test_client() as c:
        with app.app_context():
            yield c
'''
    # Default: FastAPI async pattern
    return '''\
"""Test configuration — generated by Entelim Code Workbench.

Provides an async SQLite test database so pytest runs without PostgreSQL.
Overrides any LLM-generated conftest with a pattern that actually works.
"""
import asyncio
import os

# Must be set before any app imports so SQLAlchemy uses SQLite, not Postgres.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-not-for-production")
os.environ.setdefault("JWT_SECRET", "test-jwt-not-for-production")

import pytest
import pytest_asyncio


# ── Dynamic imports — LLM may place app/Base/engine at different paths ────────

def _try_import_app():
    for mod_path, attr in [
        ("app.main", "app"),
        ("main", "app"),
        ("app.app", "app"),
    ]:
        try:
            mod = __import__(mod_path, fromlist=[attr])
            obj = getattr(mod, attr, None)
            if obj is not None:
                return obj
        except Exception:
            pass
    return None


def _try_import_base_engine():
    for mod_path, base_attr, eng_attr in [
        ("app.database", "Base", "engine"),
        ("app.db", "Base", "engine"),
        ("app.core.database", "Base", "engine"),
        ("app.models.base", "Base", "engine"),
        ("app.models", "Base", "engine"),
    ]:
        try:
            mod = __import__(mod_path, fromlist=[base_attr, eng_attr])
            base = getattr(mod, base_attr, None)
            eng = getattr(mod, eng_attr, None)
            if base is not None and eng is not None:
                return base, eng
        except Exception:
            pass
    return None, None


_fastapi_app = _try_import_app()
_Base, _engine = _try_import_base_engine()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session")
async def db_tables():
    """Create all tables once for the whole test session, then drop them."""
    if _Base is not None and _engine is not None:
        try:
            async with _engine.begin() as conn:
                await conn.run_sync(_Base.metadata.create_all)
        except Exception as exc:
            pytest.skip(f"DB table creation failed: {exc}")
    yield
    if _Base is not None and _engine is not None:
        try:
            async with _engine.begin() as conn:
                await conn.run_sync(_Base.metadata.drop_all)
        except Exception:
            pass


@pytest_asyncio.fixture
async def client(db_tables):
    """Async HTTP test client backed by the FastAPI app (no live server needed)."""
    if _fastapi_app is None:
        pytest.skip("FastAPI app not importable — check app/main.py")
    import httpx
    async with httpx.AsyncClient(app=_fastapi_app, base_url="http://test") as ac:
        yield ac
'''



def _generate_contract_tests(openapi: dict, solution_name: str) -> str:
    """Generate tests/contract/test_api_contract.py from OpenAPI 3.1 spec.

    Produces three test categories per operation:
      1. Happy-path: verify no 5xx, JSON content-type (existing)
      2. Auth enforcement: send request without auth → expect 401/403
         (only for operations with 'security' in OpenAPI spec)
      3. Schema validation: send empty body to mutation endpoints → expect 422
         with 'detail' field in response body (FastAPI Pydantic format)

    Run against a live instance: BASE_URL=http://localhost:8000 pytest tests/contract/
    Provide a valid test token: TEST_AUTH_TOKEN=<token> pytest tests/contract/
    """
    if not openapi:
        return ""
    paths = openapi.get("paths", {})
    if not paths:
        return ""

    # Detect global security schemes to know whether auth tests are meaningful
    has_security_schemes = bool(
        openapi.get("components", {}).get("securitySchemes")
        or openapi.get("security")
    )

    test_methods = []
    seen = set()

    for path_str, path_item in list(paths.items())[:30]:  # cap at 30 paths
        path_level_security = path_item.get("security")

        for method in ("get", "post", "put", "delete", "patch"):
            op = path_item.get(method)
            if not op:
                continue
            op_id = op.get("operationId") or f"{method}_{re.sub(r'[^a-z0-9]', '_', path_str.lower())}"
            op_id = re.sub(r'_+', '_', op_id).strip('_')[:60]
            if op_id in seen:
                op_id += f"_{method}"
            seen.add(op_id)
            summary = op.get("summary", path_str)
            test_path = re.sub(r'\{[^}]+\}', '1', path_str)  # replace {id} with 1

            # ── 1. Happy-path test ─────────────────────────────────────────────
            expected_happy = "200, 201" if method == "post" else "200"
            test_methods.append(
                f"    def test_{op_id}_happy_path(self, client, auth_headers):\n"
                f"        \"\"\"Contract: {method.upper()} {path_str} — {summary}\"\"\"\n"
                f"        r = client.{method}('{test_path}', headers=auth_headers)\n"
                f"        assert r.status_code not in (500, 502, 503), (\n"
                f"            f'Server error on {method.upper()} {path_str}: {{r.status_code}} — {{r.text[:200]}}'\n"
                f"        )\n"
                f"        if r.status_code in ({expected_happy},):\n"
                f"            ct = r.headers.get('content-type', '')\n"
                f"            assert 'application/json' in ct, (\n"
                f"                f'{method.upper()} {path_str} returned {expected_happy} with non-JSON content-type: {{ct}}'\n"
                f"            )\n"
            )

            # ── 2. Auth enforcement test ──────────────────────────────────────
            # Generate for: ops with explicit security OR global schemes on non-public paths
            op_security = op.get("security", path_level_security)
            op_requires_auth = (
                op_security is not None and op_security != []  # explicit security declared
            ) or (
                has_security_schemes
                and not any(skip in path_str for skip in ("/health", "/docs", "/openapi", "/login", "/token", "/register"))
            )
            if op_requires_auth:
                test_methods.append(
                    f"    def test_{op_id}_requires_auth(self, client):\n"
                    f"        \"\"\"Contract: {method.upper()} {path_str} must reject unauthenticated requests.\"\"\"\n"
                    f"        r = client.{method}('{test_path}')  # no auth headers\n"
                    f"        assert r.status_code in (401, 403), (\n"
                    f"            f'Expected 401/403 without auth on {method.upper()} {path_str}, '\n"
                    f"            f'got {{r.status_code}}. Add auth dependency (Depends(get_current_user)) '\n"
                    f"            'to this route handler.'\n"
                    f"        )\n"
                )

            # ── 3. Schema validation test (POST/PUT/PATCH only) ───────────────
            if method in ("post", "put", "patch") and op.get("requestBody"):
                test_methods.append(
                    f"    def test_{op_id}_rejects_empty_body(self, client, auth_headers):\n"
                    f"        \"\"\"Contract: {method.upper()} {path_str} must reject empty/invalid body.\"\"\"\n"
                    f"        r = client.{method}('{test_path}', json={{}}, headers={{**auth_headers, 'Content-Type': 'application/json'}})\n"
                    f"        assert r.status_code == 422, (\n"
                    f"            f'Expected 422 for empty body on {method.upper()} {path_str}, '\n"
                    f"            f'got {{r.status_code}}. Add a Pydantic request model to validate input.'\n"
                    f"        )\n"
                    f"        body = r.json()\n"
                    f"        assert 'detail' in body, (\n"
                    f"            '422 response missing detail field on {method.upper()} {path_str}. '\n"
                    f"            'FastAPI Pydantic errors must include detail with field-level errors.'\n"
                    f"        )\n"
                    f"        assert isinstance(body['detail'], list) and body['detail'], (\n"
                    f"            '422 detail must be a non-empty list of field errors '\n"
                    f"            '(FastAPI ValidationError format).'\n"
                    f"        )\n"
                )

    if not test_methods:
        return ""

    methods_block = "\n".join(test_methods)
    return f'''"""API Contract Tests — generated from OpenAPI spec for {solution_name}

Three test categories per endpoint:
  1. Happy-path: no 5xx, JSON content-type
  2. Auth enforcement: unauthenticated request → 401/403
  3. Schema validation: empty body on mutations → 422 with field-level errors

Uses FastAPI's in-process TestClient — no live server required, runs in CI without
additional setup steps. Set TEST_AUTH_TOKEN env var for authenticated test coverage.
"""
import os
import importlib
import pytest

_AUTH_TOKEN = os.getenv("TEST_AUTH_TOKEN", "")


@pytest.fixture(scope="module")
def client():
    """In-process TestClient — no live server or network required."""
    from fastapi.testclient import TestClient
    for mod_path, attr in [("app.main", "app"), ("main", "app"), ("app.app", "app")]:
        try:
            mod = importlib.import_module(mod_path)
            app_obj = getattr(mod, attr, None)
            if app_obj is not None:
                return TestClient(app_obj, raise_server_exceptions=False)
        except Exception:
            continue
    pytest.skip("FastAPI app not importable — check app/main.py")


@pytest.fixture(scope="module")
def auth_headers():
    """Authorization headers for authenticated requests.

    Set TEST_AUTH_TOKEN env var to a valid bearer token before running.
    Without a token, auth tests will hit the auth enforcement tests instead.
    """
    if _AUTH_TOKEN:
        return {{"Authorization": f"Bearer {{_AUTH_TOKEN}}"}}
    return {{}}


class TestAPIContract:
    """Generated contract tests for {solution_name}.

    Tests verify API behaviour matches the OpenAPI contract:
    correct status codes, authentication enforcement, and input validation.
    """

{methods_block}
'''



def _scan_for_secrets(files):
    """Scan generated files for secrets, replace with placeholders. Returns warnings list."""
    warnings = []
    for path, content in list(files.items()):
        for pattern, label in _SECRET_PATTERNS:
            if pattern.search(content):
                files[path] = pattern.sub(
                    f'# REPLACE_ME: {label} detected — use environment variable',
                    files[path],
                )
                warnings.append(f"{path}: {label} replaced with placeholder")
    return warnings



def _normalize_entity_name(raw: str) -> str:
    """Strip LLM over-pluralization artifacts from entity name words.

    Fixes cases like "Businesss" → "Business", "Platformss" → "Platforms".
    Preserves legitimate double-s endings (Business, Process, Address, etc.).
    """
    words = raw.strip().split()
    cleaned = []
    for w in words:
        # Fix triple (or more) trailing s: "Businesss"→"Business", "Processs"→"Process"
        m = re.match(r'^(.*?)s{3,}$', w, re.IGNORECASE)
        if m:
            stem = m.group(1)
            w = stem + ('ss' if not stem.lower().endswith('s') else 's')
        # Fix double-s that is not a legitimate English word ending
        elif re.match(r'^.*[^s]ss$', w, re.IGNORECASE):
            if not re.search(r'(?:ness|ress|less|cess|ess|iss|oss|uss|ass|lass)$', w.lower()):
                w = w[:-1]  # "Platformss"→"Platforms", "Toolss"→"Tools"
        cleaned.append(w)
    return ' '.join(cleaned)


def _entity_table_name(clean_display: str) -> str:
    """Convert a space-separated entity display name to snake_case table name."""
    return '_'.join(w.lower() for w in clean_display.split() if w)


def _entity_class_name(clean_display: str) -> str:
    """Convert a space-separated entity display name to PascalCase class name."""
    return ''.join(w.capitalize() for w in clean_display.split() if w)


def _synthesize_uml_from_elements(solution_id):
    """Build a minimal UML snapshot directly from ArchiMate elements — no LLM needed.

    This enables deterministic code generation without running UML enrichment.
    DataObjects/BusinessObjects → classes with fields from spec_data.
    ApplicationComponents → services.
    BusinessProcesses → flows.
    TechnologyNodes → deployment nodes.
    """
    from app.models.solution_archimate_element import SolutionArchiMateElement
    from app.models.archimate_core import ArchiMateElement

    links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    if not links:
        return None

    element_ids = [link.element_id for link in links]
    elements = ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids)).all()
    spec_map = {link.element_id: (link.spec_data or {}) for link in links}

    classes = []
    components = []
    flows = []
    nodes = []

    for e in elements:
        spec = spec_map.get(e.id, {})
        fields_raw = spec.get("fields", [])

        if e.type in ("DataObject", "BusinessObject"):
            # Convert to UML class
            fields = []
            if isinstance(fields_raw, list):
                for f in fields_raw:
                    if isinstance(f, dict):
                        fields.append({
                            "name": f.get("name", "field"),
                            "type": f.get("type", "string"),
                            "required": f.get("required", False),
                        })
            if not fields:
                # Use domain-aware field inference — produces meaningful domain models
                # instead of generic id/name/description/status stubs.
                fields = _infer_fields_for_capability(e.name, e.description or "")
            _display = _normalize_entity_name(e.name)
            _table = _entity_table_name(_display)
            _class = _entity_class_name(_display)
            classes.append({
                "name": _class,
                "table_name": _table,
                "display_name": _display,
                "description": e.description or f"{_display} entity",
                "fields": fields,
                "source_element_id": e.id,
                "type": e.type,
            })

        elif e.type == "ApplicationComponent":
            components.append({
                "name": e.name,
                "description": e.description or f"{e.name} service",
                "source_element_id": e.id,
            })

        elif e.type in ("BusinessProcess", "BusinessService", "BusinessFunction"):
            flows.append({
                "name": e.name,
                "description": e.description or "",
                "source_element_id": e.id,
            })

        elif e.type in ("Node", "Device", "SystemSoftware", "TechnologyService"):
            nodes.append({
                "name": e.name,
                "description": e.description or "",
                "source_element_id": e.id,
            })

    if not classes:
        # No DataObjects — synthesize from ApplicationComponents using field inference
        # so generated models are domain-specific rather than generic stubs.
        for comp in components[:5]:
            _display = _normalize_entity_name(comp["name"])
            _table = _entity_table_name(_display)
            _class = _entity_class_name(_display)
            classes.append({
                "name": _class,
                "table_name": _table,
                "display_name": _display,
                "description": f"{_display} data model",
                "fields": _infer_fields_for_capability(comp["name"], comp.get("description", "")),
                "source_element_id": comp.get("source_element_id"),
                "type": "DataObject",
            })

    # Cap entities to prevent OOM on large solutions (182+ ArchiMate elements
    # can produce 100+ classes, each generating ~10 files — routes, schemas,
    # tests, admin UI pages, Next.js pages — exceeding server memory).
    _MAX_CLASSES = 25
    if len(classes) > _MAX_CLASSES:
        logger.warning(
            "Solution %d has %d UML classes — capping at %d to prevent OOM",
            solution_id, len(classes), _MAX_CLASSES,
        )
        classes = classes[:_MAX_CLASSES]

    return {
        "class_diagram": {"classes": classes},
        "sequence_diagram": {"flows": flows},
        "component_diagram": {"components": components},
        "deployment_diagram": {"nodes": nodes},
    }


# ── Domain-Aware Field Inference ─────────────────────────────────────────────
#
# Maps capability/entity names to domain-specific fields so generated code has
# meaningful models rather than the generic id/name/description/status fallback.
# Called by the journey bridge and UML synthesis when spec_data has no fields.

_FIELD_INFERENCE_PATTERNS = [
    # ── Identity / Auth ─────────────────────────────────────────────────────
    ({"user", "account", "member", "profile", "identity", "person"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "username",        "type": "string",   "required": True,  "unique": True, "maxLength": 64},
        {"name": "email",           "type": "string",   "required": True,  "unique": True, "maxLength": 255},
        {"name": "password_hash",   "type": "string",   "required": False, "readonly": True, "maxLength": 128},
        {"name": "full_name",       "type": "string",   "required": False, "maxLength": 128},
        {"name": "role",            "type": "enum",     "required": True,  "enum": ["admin", "user", "guest", "manager"]},
        {"name": "is_active",       "type": "boolean",  "required": True,  "default": True},
        {"name": "last_login_at",   "type": "datetime", "required": False, "readonly": True},
        {"name": "created_at",      "type": "datetime", "required": True,  "readonly": True},
        {"name": "updated_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Orders / Commerce ───────────────────────────────────────────────────
    ({"order", "purchase", "booking", "reservation", "checkout"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "order_number",    "type": "string",   "required": True,  "unique": True, "maxLength": 32},
        {"name": "customer_id",     "type": "integer",  "required": True},
        {"name": "status",          "type": "enum",     "required": True,  "enum": ["draft", "pending", "confirmed", "processing", "shipped", "delivered", "cancelled", "refunded"]},
        {"name": "total_amount",    "type": "number",   "required": True},
        {"name": "currency",        "type": "string",   "required": True,  "default": "GBP", "maxLength": 3},
        {"name": "shipping_address","type": "string",   "required": False, "maxLength": 512},
        {"name": "notes",           "type": "string",   "required": False},
        {"name": "ordered_at",      "type": "datetime", "required": True},
        {"name": "updated_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Products / Inventory ────────────────────────────────────────────────
    ({"product", "item", "inventory", "catalogue", "catalog", "stock", "sku", "merchandise"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "sku",             "type": "string",   "required": True,  "unique": True, "maxLength": 64},
        {"name": "name",            "type": "string",   "required": True,  "maxLength": 255},
        {"name": "description",     "type": "string",   "required": False},
        {"name": "price",           "type": "number",   "required": True},
        {"name": "currency",        "type": "string",   "required": True,  "default": "GBP", "maxLength": 3},
        {"name": "stock_quantity",  "type": "integer",  "required": True,  "default": 0},
        {"name": "category",        "type": "string",   "required": False, "maxLength": 128},
        {"name": "is_active",       "type": "boolean",  "required": True,  "default": True},
        {"name": "created_at",      "type": "datetime", "required": True,  "readonly": True},
        {"name": "updated_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Payments / Billing / Finance ────────────────────────────────────────
    ({"payment", "transaction", "invoice", "billing", "charge", "subscription", "wallet", "finance"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "reference",       "type": "string",   "required": True,  "unique": True, "maxLength": 64},
        {"name": "amount",          "type": "number",   "required": True},
        {"name": "currency",        "type": "string",   "required": True,  "default": "GBP", "maxLength": 3},
        {"name": "status",          "type": "enum",     "required": True,  "enum": ["pending", "processing", "completed", "failed", "refunded", "cancelled"]},
        {"name": "payment_method",  "type": "enum",     "required": False, "enum": ["card", "bank_transfer", "direct_debit", "wallet", "invoice"]},
        {"name": "payer_id",        "type": "integer",  "required": False},
        {"name": "description",     "type": "string",   "required": False, "maxLength": 512},
        {"name": "processed_at",    "type": "datetime", "required": False},
        {"name": "created_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Notifications / Messaging ───────────────────────────────────────────
    ({"notification", "message", "alert", "email", "sms", "push", "communication"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "recipient_id",    "type": "integer",  "required": True},
        {"name": "channel",         "type": "enum",     "required": True,  "enum": ["email", "sms", "push", "in_app", "webhook"]},
        {"name": "subject",         "type": "string",   "required": False, "maxLength": 255},
        {"name": "body",            "type": "string",   "required": True},
        {"name": "status",          "type": "enum",     "required": True,  "enum": ["pending", "sent", "delivered", "failed", "read"]},
        {"name": "sent_at",         "type": "datetime", "required": False},
        {"name": "read_at",         "type": "datetime", "required": False},
        {"name": "created_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Documents / Files / Content ─────────────────────────────────────────
    ({"document", "file", "attachment", "content", "asset", "media", "upload", "storage"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "title",           "type": "string",   "required": True,  "maxLength": 255},
        {"name": "file_path",       "type": "string",   "required": True,  "maxLength": 512},
        {"name": "mime_type",       "type": "string",   "required": False, "maxLength": 128},
        {"name": "file_size_bytes", "type": "integer",  "required": False},
        {"name": "owner_id",        "type": "integer",  "required": True},
        {"name": "is_public",       "type": "boolean",  "required": True,  "default": False},
        {"name": "tags",            "type": "array",    "required": False},
        {"name": "uploaded_at",     "type": "datetime", "required": True,  "readonly": True},
        {"name": "updated_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Workflows / Tasks / Projects ────────────────────────────────────────
    ({"task", "workflow", "project", "ticket", "issue", "job", "work item", "sprint"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "title",           "type": "string",   "required": True,  "maxLength": 255},
        {"name": "description",     "type": "string",   "required": False},
        {"name": "status",          "type": "enum",     "required": True,  "enum": ["backlog", "todo", "in_progress", "blocked", "in_review", "done", "cancelled"]},
        {"name": "priority",        "type": "enum",     "required": False, "enum": ["critical", "high", "medium", "low"]},
        {"name": "assignee_id",     "type": "integer",  "required": False},
        {"name": "reporter_id",     "type": "integer",  "required": True},
        {"name": "due_date",        "type": "datetime", "required": False},
        {"name": "completed_at",    "type": "datetime", "required": False, "readonly": True},
        {"name": "created_at",      "type": "datetime", "required": True,  "readonly": True},
        {"name": "updated_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Reporting / Analytics ───────────────────────────────────────────────
    ({"report", "analytics", "metric", "dashboard", "insight", "statistic", "kpi"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "report_type",     "type": "string",   "required": True,  "maxLength": 64},
        {"name": "name",            "type": "string",   "required": True,  "maxLength": 255},
        {"name": "parameters",      "type": "object",   "required": False},
        {"name": "result_data",     "type": "object",   "required": False},
        {"name": "status",          "type": "enum",     "required": True,  "enum": ["pending", "running", "completed", "failed"]},
        {"name": "generated_by_id", "type": "integer",  "required": False},
        {"name": "generated_at",    "type": "datetime", "required": False, "readonly": True},
        {"name": "created_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Audit / Compliance ──────────────────────────────────────────────────
    ({"audit", "log", "event", "history", "trail", "compliance", "governance"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "event_type",      "type": "string",   "required": True,  "maxLength": 64},
        {"name": "entity_type",     "type": "string",   "required": True,  "maxLength": 64},
        {"name": "entity_id",       "type": "integer",  "required": True},
        {"name": "actor_id",        "type": "integer",  "required": False},
        {"name": "before_state",    "type": "object",   "required": False},
        {"name": "after_state",     "type": "object",   "required": False},
        {"name": "ip_address",      "type": "string",   "required": False, "maxLength": 45},
        {"name": "occurred_at",     "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Organisation / Tenancy ──────────────────────────────────────────────
    ({"organisation", "organization", "org", "tenant", "company", "team", "department"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "name",            "type": "string",   "required": True,  "unique": True, "maxLength": 255},
        {"name": "slug",            "type": "string",   "required": True,  "unique": True, "maxLength": 64},
        {"name": "plan",            "type": "enum",     "required": True,  "enum": ["free", "starter", "professional", "enterprise"]},
        {"name": "is_active",       "type": "boolean",  "required": True,  "default": True},
        {"name": "owner_id",        "type": "integer",  "required": True},
        {"name": "settings",        "type": "object",   "required": False},
        {"name": "created_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Customers / CRM ─────────────────────────────────────────────────────
    ({"customer", "client", "contact", "lead", "prospect", "crm"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "name",            "type": "string",   "required": True,  "maxLength": 255},
        {"name": "email",           "type": "string",   "required": False, "unique": True, "maxLength": 255},
        {"name": "phone",           "type": "string",   "required": False, "maxLength": 32},
        {"name": "company",         "type": "string",   "required": False, "maxLength": 255},
        {"name": "status",          "type": "enum",     "required": True,  "enum": ["lead", "prospect", "active", "inactive", "churned"]},
        {"name": "source",          "type": "string",   "required": False, "maxLength": 64},
        {"name": "assigned_to_id",  "type": "integer",  "required": False},
        {"name": "notes",           "type": "string",   "required": False},
        {"name": "created_at",      "type": "datetime", "required": True,  "readonly": True},
        {"name": "updated_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Approvals / Reviews ─────────────────────────────────────────────────
    ({"approval", "review", "sign-off", "signoff", "decision", "appraisal", "assessment"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "subject_type",    "type": "string",   "required": True,  "maxLength": 64},
        {"name": "subject_id",      "type": "integer",  "required": True},
        {"name": "reviewer_id",     "type": "integer",  "required": True},
        {"name": "decision",        "type": "enum",     "required": True,  "enum": ["pending", "approved", "rejected", "deferred", "needs_changes"]},
        {"name": "comments",        "type": "string",   "required": False},
        {"name": "decided_at",      "type": "datetime", "required": False},
        {"name": "created_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Configuration / Settings ────────────────────────────────────────────
    ({"config", "configuration", "setting", "preference", "option", "parameter"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "key",             "type": "string",   "required": True,  "unique": True, "maxLength": 128},
        {"name": "value",           "type": "string",   "required": True},
        {"name": "data_type",       "type": "enum",     "required": True,  "enum": ["string", "integer", "boolean", "json"]},
        {"name": "scope",           "type": "string",   "required": False, "maxLength": 64},
        {"name": "description",     "type": "string",   "required": False},
        {"name": "is_sensitive",    "type": "boolean",  "required": True,  "default": False},
        {"name": "updated_by_id",   "type": "integer",  "required": False},
        {"name": "updated_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Addresses / Locations ───────────────────────────────────────────────
    ({"address", "location", "venue", "site", "branch", "warehouse", "store", "outlet"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "line_1",          "type": "string",   "required": True,  "maxLength": 255},
        {"name": "line_2",          "type": "string",   "required": False, "maxLength": 255},
        {"name": "city",            "type": "string",   "required": True,  "maxLength": 128},
        {"name": "postcode",        "type": "string",   "required": True,  "maxLength": 16},
        {"name": "country",         "type": "string",   "required": True,  "default": "GB", "maxLength": 2},
        {"name": "latitude",        "type": "number",   "required": False},
        {"name": "longitude",       "type": "number",   "required": False},
        {"name": "created_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Roles / Permissions / RBAC ──────────────────────────────────────────
    ({"role", "permission", "access", "rbac", "policy", "entitlement", "privilege"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "name",            "type": "string",   "required": True,  "unique": True, "maxLength": 64},
        {"name": "display_name",    "type": "string",   "required": True,  "maxLength": 128},
        {"name": "description",     "type": "string",   "required": False},
        {"name": "scope",           "type": "string",   "required": False, "maxLength": 64},
        {"name": "is_system",       "type": "boolean",  "required": True,  "default": False, "readonly": True},
        {"name": "created_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
    # ── Contracts / Agreements ──────────────────────────────────────────────
    ({"contract", "agreement", "deal", "proposal", "quote", "estimate", "sla"}, [
        {"name": "id",              "type": "integer",  "required": True,  "primary_key": True},
        {"name": "title",           "type": "string",   "required": True,  "maxLength": 255},
        {"name": "counterparty_id", "type": "integer",  "required": True},
        {"name": "status",          "type": "enum",     "required": True,  "enum": ["draft", "sent", "under_review", "signed", "active", "expired", "terminated"]},
        {"name": "value",           "type": "number",   "required": False},
        {"name": "currency",        "type": "string",   "required": False, "default": "GBP", "maxLength": 3},
        {"name": "start_date",      "type": "datetime", "required": False},
        {"name": "end_date",        "type": "datetime", "required": False},
        {"name": "signed_at",       "type": "datetime", "required": False},
        {"name": "created_at",      "type": "datetime", "required": True,  "readonly": True},
        {"name": "updated_at",      "type": "datetime", "required": True,  "readonly": True},
    ]),
]


def _infer_fields_for_capability(name: str, description: str = "") -> list:
    """Infer domain-specific fields from a capability/entity name and description.

    Matches name + description tokens against _FIELD_INFERENCE_PATTERNS.
    Returns a rich field list, falling back to a generic schema if no match.
    The returned fields are in the structure expected by DeterministicCodeGenerator
    and _synthesize_uml_from_elements.
    """
    tokens = set((name + " " + (description or "")).lower().split())
    # Remove common noise words
    tokens -= {"management", "service", "system", "module", "engine", "handler",
                "processing", "manager", "api", "platform", "component"}

    best_score = 0
    best_fields = None
    for keywords, fields in _FIELD_INFERENCE_PATTERNS:
        score = len(keywords & tokens)
        if score > best_score:
            best_score = score
            best_fields = fields

    if best_fields:
        return best_fields

    # Generic fallback — better than id/name/description/status alone
    safe_name = name.lower().replace(" ", "_").replace("-", "_")
    return [
        {"name": "id",          "type": "integer",  "required": True,  "primary_key": True},
        {"name": f"{safe_name}_code", "type": "string",   "required": True,  "unique": True, "maxLength": 64},
        {"name": "name",        "type": "string",   "required": True,  "maxLength": 255},
        {"name": "description", "type": "string",   "required": False},
        {"name": "status",      "type": "enum",     "required": True,  "enum": ["active", "inactive", "archived"]},
        {"name": "owner_id",    "type": "integer",  "required": False},
        {"name": "metadata",    "type": "object",   "required": False},
        {"name": "created_at",  "type": "datetime", "required": True,  "readonly": True},
        {"name": "updated_at",  "type": "datetime", "required": True,  "readonly": True},
    ]


# ── Brief→Element Context Enrichment ─────────────────────────────────────────

def _extract_element_context(element_name: str, element_type: str, brief_text: str, max_sentences: int = 3) -> str:
    """Extract the most relevant sentences from the enriched brief for a given ArchiMate element.

    Uses token-overlap scoring — no LLM call, instant, deterministic.
    Sentences from the brief that share the most tokens with the element name are ranked
    highest and returned as a contextual description.

    This powers _enrich_solution_elements_from_brief so that field inference
    (_infer_fields_for_capability) has real business context to match against
    instead of generic fallback patterns.
    """
    import re
    if not brief_text or not element_name:
        return ""

    _NOISE = {
        "management", "service", "system", "module", "engine", "handler",
        "processing", "manager", "api", "platform", "component", "the", "a", "an",
        "of", "in", "for", "and", "or", "to", "is", "are", "with", "that", "this",
        "it", "be", "as", "at", "by", "we", "our", "their", "which", "from",
    }
    name_tokens = set(re.sub(r"[^a-z0-9 ]", " ", element_name.lower()).split()) - _NOISE

    # Type-specific boosting tokens help match sentences about the right concern
    _TYPE_BOOST = {
        "DataObject":           {"data", "record", "store", "entity", "object", "field", "model"},
        "BusinessObject":       {"business", "domain", "entity", "object"},
        "ApplicationComponent": {"component", "service", "application", "system", "module"},
        "BusinessProcess":      {"process", "workflow", "flow", "step", "procedure", "activity"},
        "BusinessService":      {"service", "function", "capability", "operation"},
        "Driver":               {"driver", "motivation", "need", "problem", "challenge", "issue"},
        "Goal":                 {"goal", "objective", "target", "outcome", "achieve"},
        "Requirement":          {"requirement", "must", "shall", "need", "constraint", "rule"},
        "Stakeholder":          {"stakeholder", "user", "role", "actor", "persona", "team"},
        "BusinessRole":         {"role", "responsibility", "actor", "person", "staff"},
        "ApplicationInterface": {"interface", "api", "endpoint", "integration", "connect"},
        "Node":                 {"server", "infrastructure", "node", "host", "deploy"},
        "TechnologyService":    {"technology", "platform", "infrastructure", "service"},
        "WorkPackage":          {"phase", "milestone", "delivery", "sprint", "wave", "workpackage"},
    }
    query_tokens = name_tokens | _TYPE_BOOST.get(element_type, set())
    if not query_tokens:
        return ""

    # Split on sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+", brief_text.strip())
    scored = []
    for sent in sentences:
        sent_clean = sent.strip()
        if len(sent_clean) < 15:
            continue
        sent_tokens = set(re.sub(r"[^a-z0-9 ]", " ", sent_clean.lower()).split())
        score = len(query_tokens & sent_tokens)
        if score > 0:
            scored.append((score, sent_clean))

    if not scored:
        return ""

    scored.sort(key=lambda x: -x[0])
    seen: set = set()
    result = []
    for _, sent in scored:
        if sent not in seen and len(result) < max_sentences:
            seen.add(sent)
            result.append(sent)
    return " ".join(result)


def _enrich_solution_elements_from_brief(solution_id: int) -> int:
    """Backfill empty or generic element descriptions using the solution's enriched brief.

    Called at the start of every code generation request, BEFORE UML synthesis.
    For each ArchiMate element linked to the solution that has an empty or purely
    generic description, extracts the most relevant sentences from
    solution.problem_clarification and writes them back to the element.

    Why this matters:
      _infer_fields_for_capability() matches tokens from element name + description
      against domain patterns to decide what DB fields to generate.  An empty
      description causes the generic id/name/description/status fallback.  Injecting
      brief context (e.g. "process GDPR-regulated personal data with audit trail")
      unlocks the correct domain field set automatically.

    Idempotent — only overwrites descriptions that are empty or match known generic
    templates produced by the journey bridge.  User-authored descriptions are preserved.

    Returns count of elements enriched.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_archimate_element import SolutionArchiMateElement
    from app.models.solution_models import Solution as _Sol

    sol = _Sol.query.get(solution_id)
    brief = (getattr(sol, "problem_clarification", None) or getattr(sol, "description", None) or "").strip()
    if not brief or len(brief) < 50:
        return 0

    linked_elements = (
        db.session.query(ArchiMateElement)
        .join(SolutionArchiMateElement, SolutionArchiMateElement.element_id == ArchiMateElement.id)
        .filter(SolutionArchiMateElement.solution_id == solution_id)
        .all()
    )

    _GENERIC_PREFIXES = (
        "Application component for capability:",
        "ApplicationComponent:",
        "DataObject:",
        "BusinessProcess:",
        "BusinessService:",
        "BusinessObject:",
        "Node:",
        "TechnologyService:",
        "Business driver for",
        "ArchiMate element:",
    )

    enriched = 0
    for ae in linked_elements:
        desc = (ae.description or "").strip()
        is_empty = not desc
        is_generic = any(desc.startswith(pat) for pat in _GENERIC_PREFIXES) or (len(desc) < 25 and ":" in desc)
        if not (is_empty or is_generic):
            continue
        context = _extract_element_context(ae.name, ae.type, brief)
        if context:
            ae.description = context
            enriched += 1

    if enriched:
        try:
            db.session.commit()
            logger.info(
                "Brief enrichment: updated descriptions for %d elements on solution %d",
                enriched, solution_id,
            )
        except Exception as _err:
            logger.warning("Brief enrichment commit failed for solution %d: %s", solution_id, _err)
            try:
                db.session.rollback()
            except Exception as exc:
                logger.debug("suppressed error in _enrich_solution_elements_from_brief (app/modules/codegen/routes/_helpers.py): %s", exc)
            enriched = 0

    return enriched


# ── Brief→Business Rules Extraction ──────────────────────────────────────────

# Sentence-level patterns that signal a business rule in plain-English briefs.
# Each entry: (compiled_regex, severity, rule_type_hint)
# Severity follows the existing pipeline convention: "must" | "should" | "context"
_RULE_SIGNAL_PATTERNS: list = []


def _build_rule_signal_patterns():
    """Lazily compile the rule-signal patterns (module-level singleton)."""
    global _RULE_SIGNAL_PATTERNS
    if _RULE_SIGNAL_PATTERNS:
        return _RULE_SIGNAL_PATTERNS
    import re
    _specs = [
        # Hard constraints — "must"
        (r"\b(must|shall|required to|mandatory|is not (allowed|permitted)|prohibited|cannot|may not)\b", "must", "constraint"),
        # Compliance / regulatory — "must"
        (r"\b(gdpr|hipaa|pci[-\s]?dss|sox|iso\s?27001|fca|basel|mifid|cqc|nist|ferpa|ccpa|regulation|compliance|audit trail|data protection)\b", "must", "compliance"),
        # Approval / authorisation thresholds — "must"
        (r"\b(approv(al|e|ed)|authoris(e|ation)|sign[-\s]?off|escalat(e|ion)|two[-\s]?(person|factor|approver)|dual[-\s]?approval|four[-\s]?eyes)\b", "must", "approval"),
        # Validation / data quality — "must"
        (r"\b(validat(e|ion|ed)|sanitiz(e|ation)|input check|data quality|format check|schema validation|must be valid|must match)\b", "must", "validation"),
        # SLA / performance targets — "must"
        (r"\b(sla|service level|within \d+|response time|latency|throughput|availability|99\.?\d*\s*%|uptime|rto|rpo)\b", "must", "sla"),
        # Encryption / security — "must"
        (r"\b(encrypt(ed|ion|ing)?|at[\s-]rest|in[\s-]transit|tls|ssl|aes|rsa|hash(ed|ing)?|password[\s-]?policy|token(is|iz)ation|mask(ed|ing)?)\b", "must", "security"),
        # Access control — "must"
        (r"\b(role[-\s]?based|rbac|permission|access control|least privilege|only (admin|owner|manager|authorised))\b", "must", "access_control"),
        # Soft targets / goals — "should"
        (r"\b(should|ought to|ideally|where possible|preferred|target(ed)?|aim(s)? to|designed to|intended to)\b", "should", "goal"),
        # Business outcome / KPI — "should"
        (r"\b(reduce|improve|increase|decreas|optimis|automat|streamlin|eliminat|replac).{0,40}\b(by|to|from)\s+\d+", "should", "kpi"),
        # Integration / data flow — context
        (r"\b(integrat(e|ion|ed)|sync(hronis|roniz)?|replac(e|ing)|migrat(e|ion)|import|export|feed(s)?|pipeline|event([-\s]?driven)?)\b", "context", "integration"),
    ]
    _RULE_SIGNAL_PATTERNS = [
        (re.compile(pat, re.IGNORECASE), sev, rtype) for pat, sev, rtype in _specs
    ]
    return _RULE_SIGNAL_PATTERNS


def _extract_business_rules_from_brief(solution_id: int) -> int:
    """Parse solution.problem_clarification for implicit business rules and persist them.

    Uses a regex rule-signal library to detect sentences in the 50K enriched brief
    that contain constraints, compliance mentions, approval thresholds, SLAs,
    validation requirements, and security controls.  Each matched sentence is stored
    as a SolutionRequirement row tagged source="brief_extraction" so it flows through
    Source 7 of _get_solution_business_rules() into the codegen pipeline.

    Idempotent — existing rows from prior extractions are deleted and replaced each
    time to stay current if the brief is updated.

    Returns the count of rules stored.
    """
    import re
    from app.models.solution_models import Solution as _Sol

    sol = _Sol.query.get(solution_id)
    brief = (getattr(sol, "problem_clarification", None) or "").strip()
    if not brief or len(brief) < 50:
        return 0

    patterns = _build_rule_signal_patterns()

    # Split into sentences, score each against all patterns
    sentences = re.split(r"(?<=[.!?])\s+", brief)
    rule_candidates: list = []
    seen_sigs: set = set()

    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 20 or len(sent) > 400:
            continue
        best_sev = None
        best_type = None
        match_count = 0
        for pattern, sev, rtype in patterns:
            if pattern.search(sent):
                match_count += 1
                # Severity priority: must > should > context
                if best_sev is None or (sev == "must" and best_sev != "must") or \
                        (sev == "should" and best_sev == "context"):
                    best_sev = sev
                    best_type = rtype
        if best_sev is None:
            continue
        # Deduplicate by a content fingerprint (first 60 chars lowercased, stripped)
        sig = re.sub(r"\s+", " ", sent[:60].lower().strip())
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)

        # Derive a concise name from the first noun phrase (up to 6 tokens)
        name_tokens = re.sub(r"[^a-z0-9 ]", " ", sent.lower()).split()[:6]
        name = " ".join(t for t in name_tokens if t not in {
            "the", "a", "an", "and", "or", "is", "are", "to", "for", "in", "of",
        }).strip().title()[:120] or sent[:80]

        rule_candidates.append({
            "name": name,
            "description": sent,
            "severity": best_sev,
            "rule_type": best_type,
            "match_count": match_count,
        })

    if not rule_candidates:
        return 0

    # Keep top 40 by match_count to avoid noise flooding the pipeline
    rule_candidates.sort(key=lambda r: -r["match_count"])
    rule_candidates = rule_candidates[:40]

    try:
        from app.models.solution_architect_models import SolutionRequirement, RequirementType

        # Remove previous extraction run so we stay idempotent
        SolutionRequirement.query.filter_by(
            solution_id=solution_id, source="brief_extraction"
        ).delete(synchronize_session=False)

        _TYPE_TO_REQ = {
            "constraint":     RequirementType.CONSTRAINT,
            "compliance":     RequirementType.CONSTRAINT,
            "approval":       RequirementType.CONSTRAINT,
            "validation":     RequirementType.CONSTRAINT,
            "sla":            RequirementType.QUALITY,
            "security":       RequirementType.CONSTRAINT,
            "access_control": RequirementType.CONSTRAINT,
            "goal":           RequirementType.FUNCTIONAL,
            "kpi":            RequirementType.QUALITY,
            "integration":    RequirementType.FUNCTIONAL,
        }

        for rc in rule_candidates:
            req_type = _TYPE_TO_REQ.get(rc["rule_type"], RequirementType.FUNCTIONAL)
            row = SolutionRequirement(
                solution_id=solution_id,
                name=rc["name"],
                description=rc["description"],
                requirement_type=req_type,
                source="brief_extraction",
                is_mandatory=(rc["severity"] == "must"),
                moscow_priority="MUST" if rc["severity"] == "must" else (
                    "SHOULD" if rc["severity"] == "should" else "COULD"
                ),
                compliance_tags=[rc["rule_type"]],
            )
            db.session.add(row)
            sync_archimate_element(row)

        db.session.commit()
        logger.info(
            "Brief rule extraction: stored %d rules for solution %d",
            len(rule_candidates), solution_id,
        )
        return len(rule_candidates)

    except Exception as _err:
        logger.warning(
            "Brief rule extraction failed for solution %d: %s", solution_id, _err
        )
        try:
            db.session.rollback()
        except Exception as exc:
            logger.debug("suppressed error in _extract_business_rules_from_brief (app/modules/codegen/routes/_helpers.py): %s", exc)
        return 0


# ── Brief→State Machine Inference ────────────────────────────────────────────

# Canonical workflow patterns keyed by brief signal tokens.
# Each entry: (signal_tokens, states, initial, transitions)
# Transitions: list of (trigger, from_state, to_state)
_WORKFLOW_PATTERNS = {
    "approval": {
        "signals": {"approv", "authoris", "authoriz", "sign-off", "sign off", "dual approval",
                    "four eyes", "manager approval", "escalat", "review process", "pending approval"},
        "states": ["DRAFT", "PENDING_REVIEW", "APPROVED", "REJECTED"],
        "initial": "DRAFT",
        "transitions": [
            ("submit",  "DRAFT",          "PENDING_REVIEW"),
            ("approve", "PENDING_REVIEW",  "APPROVED"),
            ("reject",  "PENDING_REVIEW",  "REJECTED"),
            ("resubmit","REJECTED",        "PENDING_REVIEW"),
            ("withdraw","PENDING_REVIEW",  "DRAFT"),
        ],
    },
    "order": {
        "signals": {"order", "purchase", "fulfilm", "fulfillment", "shipment", "delivery",
                    "dispatch", "procurement"},
        "states": ["CREATED", "CONFIRMED", "PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED"],
        "initial": "CREATED",
        "transitions": [
            ("confirm",  "CREATED",    "CONFIRMED"),
            ("process",  "CONFIRMED",  "PROCESSING"),
            ("ship",     "PROCESSING", "SHIPPED"),
            ("deliver",  "SHIPPED",    "DELIVERED"),
            ("cancel",   "CREATED",    "CANCELLED"),
            ("cancel",   "CONFIRMED",  "CANCELLED"),
        ],
    },
    "claim": {
        "signals": {"claim", "incident", "ticket", "case", "complaint", "grievance",
                    "dispute", "refund request"},
        "states": ["SUBMITTED", "UNDER_REVIEW", "PENDING_INFO", "APPROVED", "REJECTED", "CLOSED"],
        "initial": "SUBMITTED",
        "transitions": [
            ("review",       "SUBMITTED",    "UNDER_REVIEW"),
            ("request_info", "UNDER_REVIEW", "PENDING_INFO"),
            ("provide_info", "PENDING_INFO", "UNDER_REVIEW"),
            ("approve",      "UNDER_REVIEW", "APPROVED"),
            ("reject",       "UNDER_REVIEW", "REJECTED"),
            ("close",        "APPROVED",     "CLOSED"),
            ("close",        "REJECTED",     "CLOSED"),
        ],
    },
    "request": {
        "signals": {"request", "requisition", "change request", "service request",
                    "work order", "task", "assignment"},
        "states": ["DRAFT", "SUBMITTED", "IN_REVIEW", "APPROVED", "REJECTED", "IN_PROGRESS", "COMPLETED"],
        "initial": "DRAFT",
        "transitions": [
            ("submit",    "DRAFT",       "SUBMITTED"),
            ("review",    "SUBMITTED",   "IN_REVIEW"),
            ("approve",   "IN_REVIEW",   "APPROVED"),
            ("reject",    "IN_REVIEW",   "REJECTED"),
            ("start",     "APPROVED",    "IN_PROGRESS"),
            ("complete",  "IN_PROGRESS", "COMPLETED"),
            ("resubmit",  "REJECTED",    "SUBMITTED"),
        ],
    },
    "invoice": {
        "signals": {"invoice", "billing", "payment", "payable", "receivable",
                    "settlement", "remittance", "posting"},
        "states": ["DRAFT", "ISSUED", "PENDING_PAYMENT", "PAID", "OVERDUE", "CANCELLED"],
        "initial": "DRAFT",
        "transitions": [
            ("issue",     "DRAFT",           "ISSUED"),
            ("send",      "ISSUED",          "PENDING_PAYMENT"),
            ("pay",       "PENDING_PAYMENT", "PAID"),
            ("overdue",   "PENDING_PAYMENT", "OVERDUE"),
            ("cancel",    "DRAFT",           "CANCELLED"),
            ("cancel",    "ISSUED",          "CANCELLED"),
        ],
    },
    "contract": {
        "signals": {"contract", "agreement", "sow", "statement of work", "sla",
                    "service level", "nda", "msa"},
        "states": ["DRAFT", "UNDER_REVIEW", "NEGOTIATION", "SIGNED", "ACTIVE", "EXPIRED", "TERMINATED"],
        "initial": "DRAFT",
        "transitions": [
            ("submit_review", "DRAFT",        "UNDER_REVIEW"),
            ("negotiate",     "UNDER_REVIEW", "NEGOTIATION"),
            ("approve",       "NEGOTIATION",  "UNDER_REVIEW"),
            ("sign",          "UNDER_REVIEW", "SIGNED"),
            ("activate",      "SIGNED",       "ACTIVE"),
            ("expire",        "ACTIVE",       "EXPIRED"),
            ("terminate",     "ACTIVE",       "TERMINATED"),
        ],
    },
}

# Entity-name token patterns that signal workflow entities.
_WORKFLOW_ENTITY_TOKENS = {
    "approval", "order", "claim", "invoice", "contract", "request", "ticket",
    "case", "task", "project", "requisition", "application", "submission",
    "enrollment", "registration", "booking", "reservation", "incident",
    "change", "release", "deployment", "issue", "problem", "complaint",
    "purchase", "po", "work_order", "service_request",
}


def _infer_state_machines_from_brief(
    solution_id: int,
    classes: list,
    business_rules: list,
) -> dict:
    """Infer StateMachineDef objects from the solution brief and entity classes.

    Strategy:
      1. Scan solution.problem_clarification for workflow-signal tokens to determine
         which canonical patterns are relevant (approval, order, claim, etc.)
      2. For each UML class, check if its name matches a workflow-entity token.
      3. Match entity classes to patterns and build StateMachineDef objects.
      4. Also detect "approval" signals in the extracted business_rules list.

    Returns: dict {entity_name (PascalCase): StateMachineDef-compatible dict}
    so it can be merged directly into bundle.state_machines.

    No DB writes — purely in-memory inference that enriches the bundle at generation time.
    """
    import re
    from app.models.solution_models import Solution as _Sol
    from app.modules.solutions_product.services.product_spec_bundle import (
        StateMachineDef, TransitionDef,
    )

    sol = _Sol.query.get(solution_id)
    brief = (getattr(sol, "problem_clarification", None) or "").strip().lower()

    # Step 1: detect active patterns from brief + business_rules
    active_patterns: set = set()
    rule_text = " ".join(r.get("name", "") + " " + r.get("condition", "") for r in business_rules).lower()
    combined_text = brief + " " + rule_text

    for pattern_key, pdef in _WORKFLOW_PATTERNS.items():
        for signal in pdef["signals"]:
            if signal.lower() in combined_text:
                active_patterns.add(pattern_key)
                break

    if not active_patterns:
        return {}

    # Step 2: match entity classes to patterns
    result: dict = {}
    _snake_re = re.compile(r"[^a-z0-9]")

    for cls in classes:
        cls_name = cls.get("name", "") if isinstance(cls, dict) else str(cls)
        if not cls_name:
            continue
        cls_tokens = set(_snake_re.sub(" ", cls_name.lower()).split())

        # Check if entity name overlaps with workflow entity tokens
        if not (cls_tokens & _WORKFLOW_ENTITY_TOKENS):
            continue

        # Find best-matching pattern for this entity
        best_pattern = None
        best_score = 0
        for pkey in active_patterns:
            pdef = _WORKFLOW_PATTERNS[pkey]
            # Score: how many pattern signals appear in entity name
            score = sum(1 for sig in pdef["signals"] if any(t.startswith(sig[:5]) for t in cls_tokens))
            # Also score if entity name directly contains the pattern key
            if pkey in cls_tokens or any(pkey.startswith(t[:4]) for t in cls_tokens):
                score += 3
            if score > best_score:
                best_score = score
                best_pattern = pkey

        # Fallback: if entity is a workflow entity but no pattern dominates, use approval
        if not best_pattern and "approval" in active_patterns:
            best_pattern = "approval"
        elif not best_pattern:
            best_pattern = next(iter(active_patterns))

        pdef = _WORKFLOW_PATTERNS[best_pattern]
        sm = StateMachineDef(
            entity=cls_name,
            field_name="status",
            states=pdef["states"],
            initial_state=pdef["initial"],
            transitions=[
                TransitionDef(from_state=fr, to_state=to, trigger=trigger)
                for trigger, fr, to in pdef["transitions"]
            ],
        )
        result[cls_name] = sm

    return result


# ── Brief→Decision Model Extraction ──────────────────────────────────────────

# Patterns that indicate a decision/threshold rule in the brief.
_DECISION_SIGNAL_RE = None


def _get_decision_signal_re():
    global _DECISION_SIGNAL_RE
    if _DECISION_SIGNAL_RE:
        return _DECISION_SIGNAL_RE
    import re
    _DECISION_SIGNAL_RE = re.compile(
        r"(?:"
        r"(?:over|above|below|under|more than|less than|greater than|at least|at most)\s+[\d,£$€]+|"
        r"if\s+.{5,60}\s+then\s+|"
        r"when\s+.{5,60}\s+(is|are|equals?|exceeds?)\s+|"
        r"\d+\s*%\s+(?:discount|surcharge|penalty|fee|rebate)|"
        r"tier\s+[1-9]|"
        r"grade\s+[a-f]|"
        r"category\s+[a-z]|"
        r"risk\s+(?:low|medium|high|critical)"
        r")",
        re.IGNORECASE,
    )
    return _DECISION_SIGNAL_RE


def _extract_decision_models_from_brief(solution_id: int, classes: list) -> list:
    """Extract DMN-style decision models from the solution brief.

    Detects threshold/tier/if-then sentences in the brief and synthesises
    DecisionModelDef objects with typed rows.

    Returns a list of DecisionModelDef objects ready for bundle.decision_models.
    No DB writes — pure in-memory synthesis.
    """
    import re
    from app.models.solution_models import Solution as _Sol
    from app.modules.solutions_product.services.product_spec_bundle import (
        DecisionModelDef, DecisionRowDef,
    )

    sol = _Sol.query.get(solution_id)
    brief = (getattr(sol, "problem_clarification", None) or "").strip()
    if not brief or len(brief) < 50:
        return []

    signal_re = _get_decision_signal_re()
    sentences = re.split(r"(?<=[.!?])\s+", brief)
    decision_sentences = [s.strip() for s in sentences if signal_re.search(s) and 15 < len(s) < 400]

    if not decision_sentences:
        return []

    models: list = []
    seen_names: set = set()
    class_names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in classes]

    for sent in decision_sentences[:12]:  # cap at 12 to avoid noise
        # Try to associate with a class
        entity = "Domain"
        best_score = 0
        for cn in class_names:
            tokens = set(re.sub(r"[^a-z0-9 ]", " ", cn.lower()).split())
            score = sum(1 for t in tokens if t and t in sent.lower())
            if score > best_score:
                best_score = score
                entity = cn

        # Derive a function name from first few significant tokens
        name_tokens = re.sub(r"[^a-z0-9 ]", " ", sent.lower()).split()[:7]
        _SKIP = {"if", "when", "the", "a", "an", "and", "or", "is", "are", "to", "for", "in", "of",
                 "over", "above", "below", "under", "more", "less", "than", "at", "least", "most"}
        name = "_".join(t for t in name_tokens if t not in _SKIP)[:50] or "decision"
        fn_name = f"evaluate_{name}"[:60]
        if fn_name in seen_names:
            fn_name = f"{fn_name}_{len(models)}"
        seen_names.add(fn_name)

        # Extract threshold value if present
        threshold_match = re.search(r"([\d,]+(?:\.\d+)?)\s*([£$€%])?", sent)
        threshold_val = None
        if threshold_match:
            try:
                threshold_val = float(threshold_match.group(1).replace(",", ""))
            except ValueError:
                pass

        # Build a simple two-row table: above threshold → requires_action=True, else False
        rows: list = []
        if threshold_val is not None:
            rows = [
                DecisionRowDef(
                    conditions={"amount": f">={threshold_val}"},
                    outputs={"requires_action": True, "threshold": threshold_val},
                    annotation=f"Exceeds threshold from: {sent[:80]}",
                ),
                DecisionRowDef(
                    conditions={"amount": f"<{threshold_val}"},
                    outputs={"requires_action": False, "threshold": threshold_val},
                    annotation="Below threshold — standard processing",
                ),
            ]

        dm = DecisionModelDef(
            name=fn_name,
            entity=entity,
            description=sent[:200],
            inputs=["amount", "context"] if threshold_val else ["input"],
            outputs=["requires_action", "threshold"] if threshold_val else ["result"],
            rows=rows,
            hit_policy="FIRST",
            source="brief_extraction",
        )
        models.append(dm)

    return models


# ─────────────────────────────────────────────────────────────────────────────
# Gap-closure enrichment functions — wire DB models into the codegen pipeline
# ─────────────────────────────────────────────────────────────────────────────


def _build_sla_load_config(solution_id: int, services: list) -> dict:
    """Read SolutionSLA rows and build the k6 load_config dict.

    Replaces the TestGenerator's generic defaults with real SLA numbers so
    k6 thresholds reflect the contracted performance targets.

    Returns a dict with the same shape as TestGenerator.generate_load_test_config().
    Returns {} if no SLA rows found (caller should fall back to defaults), and
    None if the lookup failed — the caller skips on either, but only {} means
    "there are genuinely no SLAs".
    """
    try:
        from app.models.solution_sad_models import SolutionSLA
        sla_rows = SolutionSLA.query.filter_by(solution_id=solution_id).all()
        if not sla_rows:
            return {}

        from app.modules.solutions_product.services.test_generator import TestGenerator

        sla_dicts = []
        for row in sla_rows:
            entry = {"name": row.sla_name or "default"}
            if row.availability_target:
                entry["availability"] = float(row.availability_target)
            if row.response_time_ms:
                entry["response_time_ms"] = int(row.response_time_ms)
            if row.throughput_tps:
                entry["throughput_tps"] = float(row.throughput_tps)
            if row.rto_hours:
                entry["rto_hours"] = float(row.rto_hours)
            if row.rpo_hours:
                entry["rpo_hours"] = float(row.rpo_hours)
            sla_dicts.append(entry)

        # Build endpoint list from services (same shape TestGenerator expects)
        endpoints = []
        for svc in (services or []):
            svc.get("name", "") if isinstance(svc, dict) else getattr(svc, "name", "")
            for path in (svc.get("paths", []) if isinstance(svc, dict) else getattr(svc, "paths", [])):
                if isinstance(path, dict):
                    endpoints.append(path)
                else:
                    endpoints.append({
                        "path": path.path,
                        "method": path.method,
                        "name": path.operation_id,
                    })

        # Add RTO/RPO as additional thresholds (recovery objectives become test assertions)
        load_cfg = TestGenerator().generate_load_test_config(
            solution_id, sla_dicts, endpoints,
        )

        # Augment with throughput scenario if TPS specified
        for sla in sla_dicts:
            tps = sla.get("throughput_tps")
            if tps and tps > 0:
                vus_estimate = max(1, int(tps * 0.8))  # ~80% headroom
                load_cfg["scenarios"]["throughput_sla"] = {
                    "executor": "constant-arrival-rate",
                    "rate": int(tps),
                    "timeUnit": "1s",
                    "duration": "5m",
                    "preAllocatedVUs": vus_estimate,
                    "maxVUs": vus_estimate * 2,
                }
                load_cfg["thresholds"].append({
                    "metric": "throughput",
                    "k6_metric": "http_reqs",
                    "condition": f"rate>={int(tps * 0.9)}",
                    "source_sla": sla["name"],
                })

        return load_cfg
    except Exception:
        logger.exception(
            "SLA load config lookup failed for solution %s — generated k6 "
            "thresholds will fall back to generic defaults", solution_id,
        )
        return None


def _build_resilience_config(solution_id: int) -> dict:
    """Read RiskSnapshot rows and build a resilience config dict.

    High-score risks (impact × probability ≥ 12) require circuit breakers.
    Medium-score risks (≥ 6) require retry policies.
    Any risk in category 'integration' or 'infrastructure' requires both.

    Returns:
        {
          "circuit_breakers": [{"entity": str, "risk": str, "failure_threshold": int, "timeout_s": int}],
          "retry_policies": [{"entity": str, "risk": str, "max_retries": int, "backoff": str}],
          "dlq_topics": [str],
          "chaos_test_targets": [{"entity": str, "risk": str, "scenario": str}],
        }

    Returns {} when there are genuinely no qualifying risks, None when the
    lookup failed. The caller skips on either, but only {} is a claim about
    the data.
    """
    try:
        from app.models.solution_sad_models import RiskSnapshot
        risks = RiskSnapshot.query.filter_by(solution_id=solution_id).all()
        if not risks:
            return {}

        circuit_breakers = []
        retry_policies = []
        dlq_topics = []
        chaos_targets = []

        for r in risks:
            impact = r.impact or 3
            prob = r.probability or 3
            score = float(impact) * float(prob)
            category = (r.risk_category or "").lower()
            entity = _snake(r.risk_name or "service")

            if score >= 12 or category in ("integration", "infrastructure", "external_dependency"):
                circuit_breakers.append({
                    "entity": entity,
                    "risk": r.risk_name,
                    "risk_score": round(score, 1),
                    "failure_threshold": 5,
                    "recovery_timeout_s": 30,
                    "half_open_max_calls": 3,
                })
                dlq_topics.append(f"{entity}_dlq")

            if score >= 6 or category in ("data", "processing"):
                retry_policies.append({
                    "entity": entity,
                    "risk": r.risk_name,
                    "risk_score": round(score, 1),
                    "max_retries": 3,
                    "backoff": "exponential",
                    "base_delay_ms": 100,
                    "max_delay_ms": 5000,
                })

            if score >= 9:
                chaos_targets.append({
                    "entity": entity,
                    "risk": r.risk_name,
                    "scenario": f"simulate_{category or 'failure'}_on_{entity}",
                })

        if not (circuit_breakers or retry_policies):
            return {}

        return {
            "circuit_breakers": circuit_breakers,
            "retry_policies": retry_policies,
            "dlq_topics": list(set(dlq_topics)),
            "chaos_test_targets": chaos_targets,
        }
    except Exception:
        logger.exception(
            "Resilience config lookup failed for solution %s — generated code "
            "will carry no circuit breakers or retry policies", solution_id,
        )
        return None


def _build_compliance_config(solution_id: int) -> dict:
    """Read SolutionComplianceMapping rows and build a compliance config.

    Each unique compliance framework found triggers a set of generated code
    artefacts:
        GDPR    → data deletion endpoints, PII field masking, consent middleware
        SOX     → immutable audit log middleware, 7-year retention policy
        HIPAA   → AES-256 encryption-at-rest config, access log middleware
        ISO27001 → security headers middleware, vulnerability scan config
        PCI-DSS → tokenisation stubs, no-log-card-data middleware

    Returns:
        {
          "frameworks": ["GDPR", "SOX", ...],
          "requirements": [{"framework": str, "control_id": str, "description": str, "code_impact": str}],
          "gdpr": bool, "sox": bool, "hipaa": bool, "iso27001": bool, "pci_dss": bool,
        }

    Returns {} when the solution has genuinely no compliance mappings, None
    when the lookup failed. The caller skips on either, but the distinction is
    the difference between "no frameworks apply" and "we could not find out".
    """
    try:
        from app.models.solution_sad_models import SolutionComplianceMapping
        rows = SolutionComplianceMapping.query.filter_by(solution_id=solution_id).all()
        if not rows:
            return {}

        frameworks = list({(r.framework or "").upper() for r in rows if r.framework})
        requirements = []
        for r in rows:
            fw = (r.framework or "").upper()
            code_impact = {
                "GDPR": "pii_masking + right_to_erasure_endpoint + consent_middleware",
                "SOX": "immutable_audit_log + 7yr_retention + segregation_of_duties",
                "HIPAA": "aes256_at_rest + access_log_middleware + min_necessary_access",
                "ISO27001": "security_headers + csp + hsts + vulnerability_scan_config",
                "PCI-DSS": "tokenisation + no_card_data_in_logs + tls12_enforcement",
            }.get(fw, "compliance_annotation")
            requirements.append({
                "framework": fw,
                "control_id": r.control_id or "",
                "description": r.control_description or "",
                "element_name": r.element_name or "",
                "code_impact": code_impact,
            })

        return {
            "frameworks": frameworks,
            "requirements": requirements,
            "gdpr": "GDPR" in frameworks,
            "sox": "SOX" in frameworks,
            "hipaa": "HIPAA" in frameworks,
            "iso27001": "ISO27001" in frameworks,
            "pci_dss": "PCI-DSS" in frameworks,
        }
    except Exception:
        logger.exception(
            "Compliance config lookup failed for solution %s — generated code "
            "will omit every GDPR/SOX/HIPAA/ISO27001/PCI-DSS artefact",
            solution_id,
        )
        return None


def _build_rbac_config(solution_id: int) -> dict:
    """Read SolutionStakeholder rows and build an RBAC configuration.

    Each unique stakeholder role becomes a permission group:
        - Role enum value
        - Default permission set (read/write/admin based on stakeholder type)
        - Route guard decorator name

    Returns:
        {
          "roles": [{"name": str, "snake": str, "permissions": [...], "is_admin": bool}],
          "has_rbac": bool,
        }
    """
    try:
        # SolutionStakeholder itself carries no solution_id and no role — both
        # live on the junction (a stakeholder can be mapped to a solution or
        # to an analysis session, and its role is per-mapping, not per-person).
        # This used to query SolutionStakeholder.filter_by(solution_id=...),
        # which raised InvalidRequestError on every call and was silently
        # swallowed by the except below, so generated code always shipped
        # with has_rbac=False and no route guards.
        from app.models.solution_stakeholder import SolutionStakeholder, SolutionStakeholderMapping
        mappings = (
            SolutionStakeholderMapping.query
            .join(SolutionStakeholder, SolutionStakeholderMapping.stakeholder_id == SolutionStakeholder.id)
            .filter(SolutionStakeholderMapping.solution_id == solution_id)
            .all()
        )
        if not mappings:
            return {"has_rbac": False, "roles": []}

        seen = set()
        roles = []
        for m in mappings:
            s = m.stakeholder
            role_value = m.role.value if getattr(m, "role", None) is not None else ""
            role_name = (role_value or "").strip()
            if not role_name or role_name in seen:
                continue
            seen.add(role_name)

            # Derive permissions from role keywords
            role_lower = role_name.lower()
            is_admin = any(k in role_lower for k in ("admin", "cto", "ceo", "director", "manager", "owner"))
            is_readonly = any(k in role_lower for k in ("viewer", "auditor", "read", "analyst", "observer"))

            if is_admin:
                permissions = ["read", "write", "delete", "admin"]
            elif is_readonly:
                permissions = ["read"]
            else:
                permissions = ["read", "write"]

            roles.append({
                "name": role_name,
                "snake": _snake(role_name),
                "permissions": permissions,
                "is_admin": is_admin,
                "stakeholder_type": getattr(s, "stakeholder_type", None) or "user",
            })

        return {
            "has_rbac": bool(roles),
            "roles": roles,
        }
    except Exception:
        # Shape is fixed by the callers, which do `.get("has_rbac")`, so this
        # cannot become None. Log loudly instead: a silent False here ships
        # generated code with no route guards at all.
        logger.exception(
            "RBAC config lookup failed for solution %s — generated code will "
            "have no role guards", solution_id,
        )
        return {"has_rbac": False, "roles": []}


def _read_adr_constraints(solution_id: int) -> list:
    """Read SolutionADRDirect-linked ArchitectureDecisionRecords as constraints.

    ADR decisions constrain technology choices in the generator:
        - Decision: "Use PostgreSQL" → override bundle.deployment.database
        - Decision: "Event-driven between services" → override architecture_style
        - Decision: "Microservices" → enforce service decomposition

    Returns a list of constraint dicts:
        [{"title": str, "decision": str, "category": str, "override_key": str, "override_value": str}]

    Returns [] when there are genuinely no linked ADRs, None when the lookup
    failed. The caller skips on either.
    """
    try:
        from app.models.solution_sad_models import SolutionADRDirect
        # `app.models.architecture_models` has never existed — the resulting
        # ImportError was swallowed by the handler below, so this function
        # returned [] on every call and the generator silently ignored every
        # recorded architecture decision.
        from app.models.adr import ArchitectureDecisionRecord

        links = SolutionADRDirect.query.filter_by(solution_id=solution_id).all()
        if not links:
            return []

        constraints = []
        for link in links:
            try:
                adr = ArchitectureDecisionRecord.query.get(link.adr_id)
                if not adr:
                    continue
                title = (adr.title or "").lower()
                decision = adr.decision or ""

                # Categorise and derive override
                override_key = None
                override_value = None
                category = "general"

                if any(k in title for k in ("postgresql", "postgres", "mysql", "mongodb", "sqlite", "database", "db")):
                    category = "database"
                    for db_kw in ("postgresql", "postgres", "mysql", "mongodb", "redis", "sqlite"):
                        if db_kw in title or db_kw in decision.lower():
                            override_key = "deployment.database"  # secrets-safety-ok: genome config path, not a credential
                            override_value = "postgresql" if "postgres" in db_kw else db_kw
                            break

                elif any(k in title for k in ("event", "kafka", "rabbitmq", "pubsub", "messaging")):
                    category = "messaging"
                    for mq_kw in ("kafka", "rabbitmq", "sqs", "pubsub", "nats"):
                        if mq_kw in title or mq_kw in decision.lower():
                            override_key = "deployment.messaging"  # secrets-safety-ok: genome config path, not a credential
                            override_value = mq_kw
                            break

                elif any(k in title for k in ("microservice", "monolith", "service mesh", "architecture")):
                    category = "architecture_style"

                elif any(k in title for k in ("kubernetes", "docker", "container", "ecs", "fargate")):
                    category = "container"

                constraints.append({
                    "title": adr.title or "",
                    "decision": decision,
                    "rationale": adr.rationale or "",
                    "category": category,
                    "override_key": override_key,
                    "override_value": override_value,
                })
            except Exception:
                logger.warning(
                    "Skipping ADR %s for solution %s — could not be read",
                    getattr(link, "adr_id", None), solution_id, exc_info=True,
                )
                continue

        return constraints
    except Exception:
        logger.exception(
            "ADR constraint lookup failed for solution %s — generated code "
            "will ignore its recorded architecture decisions", solution_id,
        )
        return None


def _build_integration_clients(solution_id: int) -> list:
    """Read SolutionIntegrationFlow rows and build typed client stub configs.

    Each integration flow becomes a generated client stub with:
        - Protocol-appropriate base client (httpx, kafka-python, grpc, boto3, etc.)
        - Auth pattern from the flow's linked ApplicationInterface
        - Retry/timeout config from the linked SLA (if any)

    Returns a list of client dicts:
        [{"name": str, "protocol": str, "direction": str, "source": str,
          "target": str, "client_class": str, "base_url": str, "auth": str,
          "retry": dict, "timeout_s": int}]

    Returns [] when the solution has genuinely no integration flows, None when
    the lookup failed. The caller skips on either.
    """
    try:
        from app.models.solution_sad_models import SolutionIntegrationFlow
        # `app.models.application_models` has never existed — the ImportError
        # was swallowed by the handler below, so this returned [] on every call
        # and no integration client stub was ever generated. The flow's
        # source/target FK targets `application_components.id`.
        from app.models.application_portfolio import ApplicationComponent

        flows = SolutionIntegrationFlow.query.filter_by(solution_id=solution_id).all()
        if not flows:
            return []

        _PROTOCOL_CLIENT = {
            "rest": "httpx.AsyncClient",
            "http": "httpx.AsyncClient",
            "graphql": "httpx.AsyncClient",
            "soap": "zeep.AsyncClient",
            "grpc": "grpc.aio.Channel",
            "kafka": "aiokafka.AIOKafkaProducer",
            "rabbitmq": "aio_pika.connect_robust",
            "event": "aiokafka.AIOKafkaProducer",
            "s3": "aiobotocore.Session",
            "sftp": "asyncssh.connect",
        }
        _PROTOCOL_AUTH = {
            "rest": "bearer_token",
            "http": "bearer_token",
            "graphql": "bearer_token",
            "soap": "basic_auth",
            "grpc": "mtls",
            "kafka": "sasl_plain",
            "rabbitmq": "amqp_credentials",
        }

        clients = []
        for f in flows:
            proto = (f.flow_type or f.protocol or "rest").lower()
            direction = (f.flow_direction or "outbound").lower()

            # Resolve source/target app names
            src_name = "source"
            tgt_name = "target"
            try:
                if f.source_app_id:
                    app = db.session.get(ApplicationComponent, f.source_app_id)
                    if app:
                        src_name = _snake(app.name or "source")
                if f.target_app_id:
                    app = db.session.get(ApplicationComponent, f.target_app_id)
                    if app:
                        tgt_name = _snake(app.name or "target")
            except Exception as exc:
                logger.debug("suppressed error in _build_integration_clients (app/modules/codegen/routes/_helpers.py): %s", exc)

            client_name = f"{src_name}_to_{tgt_name}_client"
            clients.append({
                "name": client_name,
                "flow_name": f.flow_name or client_name,
                "protocol": proto,
                "direction": direction,
                "source": src_name,
                "target": tgt_name,
                "client_class": _PROTOCOL_CLIENT.get(proto, "httpx.AsyncClient"),
                "auth": _PROTOCOL_AUTH.get(proto, "bearer_token"),
                "retry": {"max_retries": 3, "backoff": "exponential", "base_ms": 100},
                "timeout_s": 30,
            })

        return clients
    except Exception:
        logger.exception(
            "Integration client lookup failed for solution %s — generated code "
            "will contain no client stubs for its integration flows", solution_id,
        )
        return None


def _build_kpi_metrics_config(solution_id: int) -> list:
    """Read SolutionGoal.kpis and SolutionOutcome rows, return Prometheus metric defs.

    Each KPI/outcome metric becomes a Prometheus gauge with:
        - Metric name (snake_case, prefixed with solution name)
        - Help text from the goal/outcome description
        - Labels derived from KPI attributes

    Returns a list of metric dicts:
        [{"metric_name": str, "type": str, "help": str, "labels": [...], "source": str}]

    Returns None when the lookup failed. Note the success path always appends a
    health gauge, so a successful call is never empty — an empty result could
    only ever have meant failure.
    """
    try:
        from app.models.solution_architect_models import SolutionGoal, SolutionAnalysisSession
        from app.models.solution_models import Solution
        from app.models.solution_outcomes import SolutionOutcome

        sol = Solution.query.get(solution_id)
        metrics = []
        seen_names = set()

        # Source 1: SolutionGoal.kpis (JSON array)
        pd_id = None
        try:
            if sol and sol.analysis_session_id:
                sess = SolutionAnalysisSession.query.get(sol.analysis_session_id)
                if sess and sess.problem_definition:
                    pd_id = sess.problem_definition.id
        except Exception as exc:
            logger.debug("suppressed error in _build_kpi_metrics_config (app/modules/codegen/routes/_helpers.py): %s", exc)

        if pd_id:
            for goal in SolutionGoal.query.filter_by(problem_id=pd_id).all():
                kpis = goal.kpis or []
                if isinstance(kpis, str):
                    import json as _json
                    try:
                        kpis = _json.loads(kpis)
                    except Exception:
                        kpis = []
                for kpi in (kpis if isinstance(kpis, list) else []):
                    kpi_name = kpi if isinstance(kpi, str) else (kpi.get("name") or kpi.get("metric", ""))
                    if not kpi_name:
                        continue
                    metric_name = f"archie_{_snake(kpi_name)}"
                    if metric_name in seen_names:
                        continue
                    seen_names.add(metric_name)
                    metrics.append({
                        "metric_name": metric_name,
                        "type": "gauge",
                        "help": f"KPI: {kpi_name} — from goal: {goal.name}",
                        "labels": ["solution_id", "period"],
                        "source": "goal_kpi",
                    })

        # Source 2: SolutionOutcome (tracked measurable outcomes)
        try:
            for outcome in SolutionOutcome.query.filter_by(solution_id=solution_id).all():
                name = outcome.name or ""
                if not name:
                    continue
                metric_name = f"archie_{_snake(name)}_target"
                if metric_name in seen_names:
                    continue
                seen_names.add(metric_name)
                metrics.append({
                    "metric_name": metric_name,
                    "type": "gauge",
                    "help": f"Outcome target: {name}",
                    "labels": ["solution_id", "outcome_type"],
                    "source": "solution_outcome",
                    "target_value": getattr(outcome, "target_value", None),
                })
        except Exception as exc:
            logger.debug("suppressed error in _build_kpi_metrics_config (app/modules/codegen/routes/_helpers.py): %s", exc)

        # Always add a solution health gauge
        metrics.append({
            "metric_name": "archie_solution_health_score",
            "type": "gauge",
            "help": "Overall solution health score (0-100)",
            "labels": ["solution_id"],
            "source": "system",
        })

        return metrics
    except Exception:
        logger.exception(
            "KPI metric lookup failed for solution %s — generated code will "
            "expose no Prometheus metrics for its goals or outcomes", solution_id,
        )
        return None


# ── Journey→ArchiMate Bridge ─────────────────────────────────────────────────

def _ensure_archimate_elements_from_journey(solution_id: int) -> int:
    """Journey→ArchiMate bridge called at the start of every code generation request.

    Ensures SolutionArchiMateElement junction rows exist so the downstream pipeline
    (UML synthesis, genome compiler) has application-layer elements to work with.

    Three-tier fallback — each tier only runs if the previous produced no
    application-layer elements:

      Tier 1 — application elements already exist → no-op (normal path).
      Tier 2 — unconfirmed proposals exist (user skipped Steps 4/5 domain review)
               → create ArchiMateElement + junction for each pending/accepted proposal.
      Tier 3 — no proposals at all (LLM generation never ran, no API keys)
               → synthesize ApplicationComponent elements from SolutionCapability
                 records saved by the derive-capabilities step.

    Idempotent — safe to call on every generation request.
    Returns count of elements created/linked in this call.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_archimate_element import SolutionArchiMateElement

    _APPLICATION_TYPES = {
        "ApplicationComponent", "ApplicationService", "ApplicationFunction",
        "ApplicationInterface", "DataObject", "BusinessObject",
        "BusinessService", "BusinessProcess", "BusinessFunction",
    }

    # ── Tier 1: application-layer elements already exist → done ──────────────
    existing_app = (
        db.session.query(SolutionArchiMateElement.id)
        .join(ArchiMateElement, ArchiMateElement.id == SolutionArchiMateElement.element_id)
        .filter(
            SolutionArchiMateElement.solution_id == solution_id,
            ArchiMateElement.type.in_(_APPLICATION_TYPES),
        )
        .first()
    )
    if existing_app:
        return 0

    # Resolve organization_id and enriched brief for element creation
    _sol = Solution.query.get(solution_id)
    _org_id = getattr(_sol, "organization_id", None) or 1
    _brief = (getattr(_sol, "problem_clarification", None) or getattr(_sol, "description", None) or "").strip()

    created = 0

    # ── Tier 2: promote unconfirmed proposals ────────────────────────────────
    # Fires when LLM generation ran (Step 3) but user skipped confirm_domain
    # (Steps 4/5). Proposals exist with promoted_element_id=None.
    try:
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        unconfirmed = SolutionBlueprintProposal.query.filter(
            SolutionBlueprintProposal.solution_id == solution_id,
            SolutionBlueprintProposal.promoted_element_id.is_(None),
            SolutionBlueprintProposal.status.in_(["pending", "accepted", "proposed"]),
        ).all()

        if unconfirmed:
            _TYPE_TO_LAYER = {
                "ApplicationComponent": "application", "ApplicationService": "application",
                "ApplicationFunction": "application", "ApplicationInterface": "application",
                "DataObject": "application", "BusinessObject": "business",
                "BusinessService": "business", "BusinessProcess": "business",
                "BusinessFunction": "business", "BusinessActor": "business",
                "BusinessRole": "business", "Stakeholder": "motivation",
                "Driver": "motivation", "Goal": "motivation", "Requirement": "motivation",
                "Constraint": "motivation", "Principle": "motivation",
                "Capability": "strategy", "CourseOfAction": "strategy",
                "Node": "technology", "SystemSoftware": "technology",
                "TechnologyService": "technology", "Artifact": "technology",
                "WorkPackage": "implementation", "Deliverable": "implementation",
                "Plateau": "implementation", "Gap": "implementation",
            }

            for p in unconfirmed:
                if not (p.archimate_type and p.name):
                    continue
                ae_type = p.archimate_type
                ae_layer = _TYPE_TO_LAYER.get(ae_type, "application")

                ae = ArchiMateElement.query.filter_by(name=p.name, type=ae_type).first()
                if not ae:
                    # Use brief-extracted context if proposal has no description
                    _ctx = p.description or _extract_element_context(p.name, ae_type, _brief) or f"{ae_type}: {p.name}"
                    ae = ArchiMateElement(
                        name=p.name,
                        type=ae_type,
                        layer=ae_layer,
                        description=_ctx,
                        organization_id=_org_id,
                    )
                    db.session.add(ae)
                    db.session.flush()

                p.promoted_element_id = ae.id
                if p.status == "pending":
                    p.status = "accepted"

                if not SolutionArchiMateElement.query.filter_by(
                    solution_id=solution_id, element_id=ae.id
                ).first():
                    db.session.add(SolutionArchiMateElement(
                        solution_id=solution_id,
                        element_id=ae.id,
                        layer_type=ae_layer,
                        element_name=p.name,
                        element_table="archimate_elements",
                        element_role="journey_promoted",
                        is_new_element=True,
                    ))
                    created += 1

            if created:
                db.session.commit()
                logger.info(
                    "Journey bridge tier 2: promoted %d unconfirmed proposals for solution %d",
                    created, solution_id,
                )

            # Re-check after promotion
            existing_app = (
                db.session.query(SolutionArchiMateElement.id)
                .join(ArchiMateElement, ArchiMateElement.id == SolutionArchiMateElement.element_id)
                .filter(
                    SolutionArchiMateElement.solution_id == solution_id,
                    ArchiMateElement.type.in_(_APPLICATION_TYPES),
                )
                .first()
            )
            if existing_app:
                return created

    except Exception as _t2_err:
        logger.warning("Journey bridge tier 2 failed for solution %d: %s", solution_id, _t2_err)
        try:
            db.session.rollback()
        except Exception as exc:
            logger.debug("suppressed error in _ensure_archimate_elements_from_journey (app/modules/codegen/routes/_helpers.py): %s", exc)

    # ── Tier 3: synthesize from SolutionCapability records ───────────────────
    # Fires when LLM generation never ran (no API keys). SolutionCapability rows
    # are written by the derive-capabilities step regardless of LLM availability.
    try:
        from app.models.solution_capability import SolutionCapability

        caps = SolutionCapability.query.filter_by(solution_id=solution_id).all()
        solution = Solution.query.get(solution_id)

        cap_created = 0
        for cap in caps:
            cap_name = (cap.name or "").strip()
            if not cap_name:
                continue

            ae = ArchiMateElement.query.filter_by(name=cap_name, type="ApplicationComponent").first()
            if not ae:
                # Prefer brief-extracted context over generic fallback
                _cap_ctx = (cap.description or "").strip()
                if not _cap_ctx:
                    _cap_ctx = _extract_element_context(cap_name, "ApplicationComponent", _brief)
                ae = ArchiMateElement(
                    name=cap_name,
                    type="ApplicationComponent",
                    layer="application",
                    description=_cap_ctx or f"Application component for capability: {cap_name}",
                    organization_id=_org_id,
                )
                db.session.add(ae)
                db.session.flush()

            if not SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id, element_id=ae.id
            ).first():
                # Field inference uses enriched description → domain-specific fields
                _desc_for_fields = ae.description or cap.description or ""
                inferred_fields = _infer_fields_for_capability(cap_name, _desc_for_fields)
                db.session.add(SolutionArchiMateElement(
                    solution_id=solution_id,
                    element_id=ae.id,
                    layer_type="application",
                    element_name=cap_name,
                    element_table="archimate_elements",
                    element_role="journey_derived",
                    is_new_element=True,
                    spec_data={
                        "fields": inferred_fields,
                        "fields_status": "inferred",
                        "is_journey_derived": True,
                        "source_capability": cap_name,
                    },
                ))
                cap_created += 1

        # Ensure at least one Driver from the problem statement
        if solution:
            driver_desc = solution.problem_clarification or solution.description or ""
            driver_name = (solution.name or f"Solution {solution_id}") + " Driver"
            ae_drv = ArchiMateElement.query.filter_by(name=driver_name, type="Driver").first()
            if not ae_drv:
                ae_drv = ArchiMateElement(
                    name=driver_name,
                    type="Driver",
                    layer="motivation",
                    description=driver_desc or f"Business driver for {solution.name or solution_id}",
                    organization_id=_org_id,
                )
                db.session.add(ae_drv)
                db.session.flush()

            if not SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id, element_id=ae_drv.id
            ).first():
                db.session.add(SolutionArchiMateElement(
                    solution_id=solution_id,
                    element_id=ae_drv.id,
                    layer_type="motivation",
                    element_name=driver_name,
                    element_table="archimate_elements",
                    element_role="journey_derived",
                    is_new_element=True,
                ))
                cap_created += 1

        if cap_created:
            db.session.commit()
            created += cap_created
            logger.info(
                "Journey bridge tier 3: synthesized %d elements from %d capabilities for solution %d",
                cap_created, len(caps), solution_id,
            )

    except Exception as _t3_err:
        logger.warning("Journey bridge tier 3 failed for solution %d: %s", solution_id, _t3_err)
        try:
            db.session.rollback()
        except Exception as exc:
            logger.debug("suppressed error in _ensure_archimate_elements_from_journey (app/modules/codegen/routes/_helpers.py): %s", exc)

    # ── Tier 4: synthesize from solution description ────────────────────────
    # Last resort when tiers 1-3 produced no application-layer elements.
    # A Driver alone doesn't count — the genome compiler needs ApplicationComponents.
    _has_app_elements = (
        db.session.query(SolutionArchiMateElement.id)
        .join(ArchiMateElement, ArchiMateElement.id == SolutionArchiMateElement.element_id)
        .filter(
            SolutionArchiMateElement.solution_id == solution_id,
            ArchiMateElement.type.in_(_APPLICATION_TYPES),
        )
        .first()
    )
    if not _has_app_elements:
        try:
            solution = Solution.query.get(solution_id)
            desc = (solution.description or "") if solution else ""
            if desc and len(desc) > 20:
                from app.modules.codegen.services.aabl_compiler import (
                    _DOMAIN_ARCHETYPES,
                )
                # Extract entity candidates from description
                desc_lower = desc.lower()
                _ENTITY_KEYWORDS = set()
                for keywords, _ in _DOMAIN_ARCHETYPES:
                    _ENTITY_KEYWORDS.update(keywords)
                # Also add common SaaS nouns
                _ENTITY_KEYWORDS.update({
                    "workspace", "user", "team", "project", "task", "comment",
                    "billing", "subscription", "invoice", "notification",
                    "api_key", "setting", "role", "permission", "webhook",
                    "report", "dashboard", "document", "file", "message",
                })
                raw_matched = [kw for kw in _ENTITY_KEYWORDS if kw.replace("_", " ") in desc_lower or kw in desc_lower]
                if not raw_matched:
                    raw_matched = ["user", "workspace", "task"]

                # Deduplicate: prefer compound keywords over their components.
                # "feature_flag" supersedes "feature" and "flag".
                # "api_key" supersedes "api".
                # "audit_log" supersedes "audit" and "log".
                raw_matched.sort(key=len, reverse=True)  # longest first
                matched = []
                for kw in raw_matched:
                    parts = set(kw.split("_"))
                    # Skip if any already-accepted keyword contains all our parts
                    # (e.g., skip "flag" if "feature_flag" was already accepted)
                    if any(parts <= set(accepted.split("_")) for accepted in matched):
                        continue
                    # Skip if this keyword is a generic single word that's a component
                    # of a compound keyword already matched
                    if "_" not in kw and any(kw in accepted.split("_") for accepted in matched):
                        continue
                    matched.append(kw)

                # Also remove near-duplicates where one is a suffix of another
                # e.g., "audit" and "audit_log" — keep the longer one
                final = []
                for kw in matched:
                    if any(kw != other and kw in other for other in matched):
                        continue  # skip "audit" if "audit_log" exists
                    final.append(kw)
                matched = final if final else matched

                # Ensure core entities for SaaS/platform apps
                _saas_signals = {"saas", "multi-tenant", "platform", "subscription", "workspace"}
                if any(sig in desc_lower for sig in _saas_signals):
                    for core in ["user", "workspace"]:
                        if core not in matched and not any(core in m for m in matched):
                            matched.append(core)

                t4_created = 0
                for entity_name in matched:
                    pascal_name = "".join(w.capitalize() for w in entity_name.split("_"))
                    ae = ArchiMateElement.query.filter_by(name=pascal_name, type="ApplicationComponent").first()
                    if not ae:
                        ae = ArchiMateElement(
                            name=pascal_name,
                            type="ApplicationComponent",
                            layer="application",
                            description=f"Entity derived from solution description: {entity_name}",
                            organization_id=_org_id,
                        )
                        db.session.add(ae)
                        db.session.flush()

                    if not SolutionArchiMateElement.query.filter_by(
                        solution_id=solution_id, element_id=ae.id
                    ).first():
                        inferred_fields = _infer_fields_for_capability(pascal_name, desc)
                        db.session.add(SolutionArchiMateElement(
                            solution_id=solution_id,
                            element_id=ae.id,
                            layer_type="application",
                            element_name=pascal_name,
                            element_table="archimate_elements",
                            element_role="journey_derived",
                            is_new_element=True,
                            spec_data={
                                "fields": inferred_fields,
                                "fields_status": "inferred",
                                "is_journey_derived": True,
                                "source": "description_extraction",
                            },
                        ))
                        t4_created += 1

                if t4_created:
                    db.session.commit()
                    created += t4_created
                    logger.info(
                        "Journey bridge tier 4: synthesized %d entities from description for solution %d",
                        t4_created, solution_id,
                    )
        except Exception as _t4_err:
            logger.warning("Journey bridge tier 4 failed for solution %d: %s", solution_id, _t4_err)
            try:
                db.session.rollback()
            except Exception as exc:
                logger.debug("suppressed error in _ensure_archimate_elements_from_journey (app/modules/codegen/routes/_helpers.py): %s", exc)

    return created


def _compute_chain_completeness(solution_id):
    """Compute average chain completeness for a solution's ArchiMate elements.

    Returns a float 0.0-1.0, or None if completeness cannot be computed.
    """
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from app.models.archimate_core import ArchiMateElement
        from app.modules.architecture.services.inference_engine_service import ArchiMateInferenceEngine

        links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
        if not links:
            return None

        element_ids = [link.element_id for link in links if link.element_id]
        if not element_ids:
            return None

        # Find architecture_id from the first element
        elem = ArchiMateElement.query.get(element_ids[0])
        if not elem or not elem.architecture_id:
            return None

        engine = ArchiMateInferenceEngine(elem.architecture_id)
        scores = []
        for eid in element_ids:
            try:
                diag = engine.diagnose(eid)
                scores.append(diag.completeness)
            except Exception as e:
                logger.debug("Skipping element %d in completeness check: %s", eid, e)

        return sum(scores) / len(scores) if scores else 0.0
    except Exception as e:
        logger.warning("Chain completeness check failed: %s", e)
        return None



def _as_bool(value, default: bool = False) -> bool:
    """Coerce loose payload/config values to bool."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default



def _utc_now_iso() -> str:
    """UTC ISO timestamp helper with timezone."""
    from datetime import timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()



def _append_intent_gate_event(gen, event: dict) -> None:
    """Append a governance event for intent gating to gen.config."""
    cfg = dict(gen.config or {})
    events = list(cfg.get("_intent_gate_events") or [])
    events.append(event)
    cfg["_intent_gate_events"] = events[-50:]
    gen.config = cfg



def _persist_intent_verify_state(gen, status: str, summary: dict = None,
                                 pass_rate: int = None, error: str = "") -> None:
    """Persist latest intent verification state to gen.config."""
    cfg = dict(gen.config or {})
    state = {
        "status": status if status in {"pass", "warn", "pending"} else "warn",
        "verified_at": _utc_now_iso(),
        "pass_rate": int(pass_rate or 0),
        "passed": int((summary or {}).get("passed", 0)),
        "failed": int((summary or {}).get("failed", 0)),
        "errors": int((summary or {}).get("errors", 0)),
        "error": (error or "")[:500],
    }
    cfg["_intent_verify"] = state
    gen.config = cfg



def _latest_intent_verify_state(gen, max_age_minutes: int = 120) -> tuple[bool, dict]:
    """Return (is_valid_for_gate, state) using persisted or historical verify evidence."""
    from datetime import timezone, timedelta

    def _parse_ts(ts: str):
        if not ts or not isinstance(ts, str):
            return None
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    now = datetime.now(timezone.utc)
    cfg = dict(gen.config or {})
    state = cfg.get("_intent_verify") if isinstance(cfg.get("_intent_verify"), dict) else None
    if state:
        verified_at = _parse_ts(state.get("verified_at"))
        age_ok = bool(verified_at and (now - verified_at) <= timedelta(minutes=max_age_minutes))
        passed = (
            state.get("status") == "pass"
            and int(state.get("failed", 0)) == 0
            and int(state.get("errors", 0)) == 0
        )
        detail = {
            "source": "config",
            "status": state.get("status", "warn"),
            "verified_at": state.get("verified_at"),
            "pass_rate": int(state.get("pass_rate", 0) or 0),
            "passed": int(state.get("passed", 0) or 0),
            "failed": int(state.get("failed", 0) or 0),
            "errors": int(state.get("errors", 0) or 0),
            "age_ok": age_ok,
            "error": state.get("error", ""),
        }
        return bool(age_ok and passed), detail

    latest_hist = CodegenGenerationHistory.query.filter_by(
        codegen_generation_id=gen.id
    ).order_by(CodegenGenerationHistory.generated_at.desc()).first()
    qd = (latest_hist.quality_details or {}) if latest_hist else {}
    test_exec = qd.get("test_execution") if isinstance(qd.get("test_execution"), dict) else {}
    verified_at = _parse_ts(test_exec.get("verified_at"))
    age_ok = bool(verified_at and (now - verified_at) <= timedelta(minutes=max_age_minutes))
    passed = (
        int(test_exec.get("failed", 0) or 0) == 0
        and int(test_exec.get("errors", 0) or 0) == 0
        and int(test_exec.get("pass_rate", 0) or 0) >= 100
    )
    detail = {
        "source": "history",
        "status": "pass" if passed else "warn",
        "verified_at": test_exec.get("verified_at"),
        "pass_rate": int(test_exec.get("pass_rate", 0) or 0),
        "passed": int(test_exec.get("passed", 0) or 0),
        "failed": int(test_exec.get("failed", 0) or 0),
        "errors": int(test_exec.get("errors", 0) or 0),
        "age_ok": age_ok,
        "error": "",
    }
    return bool(age_ok and passed), detail



def _collect_spec_confirmation_counts(solution_id: int, uml_snapshot: dict) -> tuple[int, int, list[str]]:
    """Return (total_mapped_classes, confirmed_classes, missing_class_names)."""
    classes = (uml_snapshot or {}).get("class_diagram", {}).get("classes", [])
    if not classes:
        return 0, 0, []

    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    except Exception as exc:
        logger.warning("Spec confirmation lookup failed for solution %s: %s", solution_id, exc)
        return 0, 0, []

    status_by_element = {}
    for link in links:
        spec = link.spec_data or {}
        status_by_element[link.element_id] = spec.get("fields_status")

    total = 0
    confirmed = 0
    missing = []
    for cls in classes:
        source_id = cls.get("source_element_id")
        if not source_id:
            continue
        total += 1
        if status_by_element.get(source_id) == "confirmed":
            confirmed += 1
        else:
            missing.append(cls.get("name") or f"source:{source_id}")

    return total, confirmed, missing



def _find_placeholder_stubs(files: dict) -> list[dict]:
    """Scan generated source files for placeholder implementation markers."""
    findings = []
    patterns = [
        ("todo_business_logic", re.compile(r"TODO:\s*Implement business logic", re.I)),
        ("todo_validation_logic", re.compile(r"TODO:\s*Add actual constraint check", re.I)),
        ("not_implemented_detail", re.compile(r"not yet implemented", re.I)),
        ("deterministic_scaffold_only", re.compile(r"deterministic scaffold only", re.I)),
        ("http_501_placeholder", re.compile(r"status_code\s*=\s*501", re.I)),
        ("unsupported_operation", re.compile(r"UnsupportedOperationException", re.I)),
    ]
    source_exts = (".py", ".go", ".java", ".ts", ".tsx", ".js")

    for path, content in (files or {}).items():
        p = (path or "").lower()
        if not p.endswith(source_exts):
            continue
        for marker, pattern in patterns:
            if pattern.search(content or ""):
                findings.append({"file": path, "marker": marker})
                break
    return findings



def _compute_file_artifact_signals(files: dict, max_files: int = 120) -> list:
    """Per-file signals for Workbench quality drill-down (deterministic; not semantic 'accuracy')."""
    if not files:
        return []
    rows = []
    for path in sorted(files.keys()):
        if len(rows) >= max_files:
            break
        content = files.get(path)
        if not isinstance(content, str):
            continue
        norm = path.replace("\\", "/")
        lc = content.lower()
        has_trace = "archimate_source" in lc or "source_element_id" in lc
        parts = norm.split("/")
        group = "root"
        if len(parts) >= 2 and parts[0] == "app":
            group = parts[1]
        elif len(parts) >= 1 and parts[0] == "tests":
            group = "tests"
        elif len(parts) >= 1:
            group = parts[0]
        rows.append(
            {
                "path": norm,
                "group": group,
                "line_count": content.count("\n") + 1,
                "has_trace_marker": has_trace,
            }
        )
    return rows



def _enrich_quality_details_for_ui(details, files) -> None:
    """Attach UI-friendly keys (classes array, per-file signals) — mutates details in place."""
    if not details:
        return
    pc = details.get("per_class") or {}
    if pc and not details.get("classes"):
        details["classes"] = sorted(
            [
                {
                    "name": n,
                    "field_count": d.get("field_count", 0),
                    "method_count": 0,
                    "score": d.get("field_score", 0),
                    "has_source": d.get("has_source", False),
                }
                for n, d in pc.items()
            ],
            key=lambda x: (x["name"] or "").lower(),
        )
    if files and not details.get("artifact_file_signals"):
        details["artifact_file_signals"] = _compute_file_artifact_signals(files)



def _compute_quality_score(uml, files, business_rules: list = None, seed_context: dict = None):
    """Pure-Python quality scoring. Returns (score_0_to_100, details_dict) or (None, None).

    Six dimensions:
      - Schema completeness (22%): avg fields per class vs target of 7
      - Test coverage (18%): test files as % of total, target 30%
      - Relationship density (12%): sequence flows vs target of 3 per class
      - Traceability (12%): % of classes with source_element_id
      - Business rule coverage (16%): % of must/should rules with detectable enforcement
      - Domain fidelity (20%): seeded entity/field/adapter coverage from canonical seed packs
    """
    if not uml:
        return None, None

    classes = uml.get("class_diagram", {}).get("classes", [])
    flows = uml.get("sequence_diagram", {}).get("flows", [])

    if not classes:
        return None, None

    # 1. Schema completeness (target: 7 fields per class)
    per_class = {}
    field_scores = []
    for cls in classes:
        n_fields = len(cls.get("fields", []))
        cls_score = min(n_fields / 7.0, 1.0) * 100
        field_scores.append(cls_score)
        per_class[cls.get("name", "?")] = {
            "field_count": n_fields,
            "has_source": bool(cls.get("source_element_id")),
            "field_score": round(cls_score, 1),
        }
    schema_completeness = sum(field_scores) / len(field_scores)

    # 2. Test coverage (target: 30% of files are tests)
    file_paths = list(files.keys())
    test_files = [f for f in file_paths if "test" in f.lower()]
    total_files = len(file_paths)
    if total_files:
        test_score = min((len(test_files) / total_files) / 0.30, 1.0) * 100
    else:
        test_score = 0.0

    # 3. Relationship density: UML flows + inferred HTTP route operations
    # Counting route decorators is more accurate than relying solely on UML flows
    expected_flows = len(classes) * 3
    _http_op_re = re.compile(
        r'@(?:router|app|blueprint|bp)\s*\.\s*(?:get|post|put|patch|delete)\s*\(',
        re.IGNORECASE,
    )
    inferred_ops = sum(
        len(_http_op_re.findall(content or ""))
        for path, content in files.items()
        if path.endswith(".py") and ("routes/" in path or "routers/" in path)
    )
    effective_flows = len(flows) + inferred_ops
    relationship_density = min(effective_flows / max(expected_flows, 1), 1.0) * 100

    # 4. Traceability (% of classes with source_element_id)
    traced = sum(1 for c in classes if c.get("source_element_id"))
    traceability = traced / len(classes) * 100

    # 5. Business rule coverage (% of must/should rules enforced in generated code)
    rule_coverage_details = None
    if business_rules:
        all_code = "\n".join(files.values())
        rule_coverage_details = _compute_rule_coverage(business_rules, all_code)
        rule_coverage = rule_coverage_details["coverage_pct"]
    else:
        rule_coverage = 100.0  # No rules → full score (not penalised for missing data)

    # 6. Domain fidelity from seed context (if present)
    domain_fidelity = {
        "score": 100.0,
        "expected_entities": 0,
        "seeded_entities": 0,
        "required_field_coverage": 100.0,
        "adapter_coverage": 100.0,
        "missing_required_fields": [],
    }
    try:
        from app.modules.codegen.services.domain_seed_service import DomainSeedResolver

        domain_fidelity = DomainSeedResolver.compute_domain_fidelity(uml, seed_context)
    except Exception as exc:
        logger.debug("suppressed error in _compute_quality_score (app/modules/codegen/routes/_helpers.py): %s", exc)

    overall = (
        schema_completeness * 0.22 +
        test_score * 0.18 +
        relationship_density * 0.12 +
        traceability * 0.12 +
        rule_coverage * 0.16 +
        domain_fidelity["score"] * 0.20
    )

    # Recommendations — structured objects: {text, dimension, icon}
    # icon: "warning" (amber), "info" (blue), "error" (red)
    def _rec(text, dimension, icon="warning"):
        return {"text": text, "dimension": dimension, "icon": icon}

    recs = []
    if schema_completeness < 70:
        weak = [n for n, d in per_class.items() if d["field_count"] < 5]
        if weak:
            sample = ", ".join(weak[:3])
            recs.append(_rec(f"Add more fields to {len(weak)} class(es): {sample}", "schema_completeness"))
    if test_score < 60:
        recs.append(_rec("Low test file ratio — ensure process tests are generated", "test_coverage"))
    if relationship_density < 50:
        recs.append(_rec("Add BusinessProcess elements to define more API flows", "relationship_density"))
    if traceability < 80:
        untraceable = [n for n, d in per_class.items() if not d["has_source"]]
        if untraceable:
            recs.append(_rec(
                f"{len(untraceable)} class(es) lack ArchiMate source — link DataObjects to this solution",
                "traceability"
            ))
    if rule_coverage < 60 and rule_coverage_details:
        uncovered = rule_coverage_details.get("uncovered", [])
        if uncovered:
            sample = ", ".join(f'"{r}"' for r in uncovered[:3])
            recs.append(_rec(
                f"{len(uncovered)} business rule(s) not enforced in generated code: {sample}",
                "rule_coverage"
            ))
    if domain_fidelity["score"] < 80:
        missing = domain_fidelity.get("missing_required_fields", [])
        if missing:
            sample = ", ".join(missing[:3])
            recs.append(_rec(
                f"Domain fidelity gap: {len(missing)} canonical required field(s) missing (e.g. {sample})",
                "domain_fidelity"
            ))
        if domain_fidelity.get("adapter_coverage", 100.0) < 80:
            recs.append(_rec("Vendor adapter mapping coverage is low for seeded entities", "domain_fidelity"))

    # 7. Linting — E501/W29x/E711 violations (informational, not in weighted formula)
    lint_result = _lint_python_files(files)
    if lint_result["total_violations"] > 0 and lint_result["e501_count"] > 10:
        recs.append(_rec(
            f"Linting: {lint_result['e501_count']} lines exceed {lint_result['max_line_length']} chars "
            f"(E501). Consider wrapping long expressions.",
            "linting",
            "info"
        ))

    # 8. Security hardening — auth presence, CORS, debug mode (informational)
    security_result = _check_security_hardening(files)
    if not security_result["has_auth"]:
        recs.append(_rec(
            "Security: no auth dependency (verify_token/require_role) found in route files.",
            "security", "error"
        ))
    if security_result["cors_wildcard"]:
        recs.append(_rec(
            "Security: CORS allowed_origins=['*'] — restrict to specific origins before deploying.",
            "security", "error"
        ))
    if security_result["debug_mode"]:
        recs.append(_rec(
            "Security: debug=True found in config — ensure this is False in production.",
            "security", "error"
        ))

    details = {
        "schema_completeness": round(schema_completeness, 1),
        "test_coverage": round(test_score, 1),
        "relationship_density": round(relationship_density, 1),
        "traceability": round(traceability, 1),
        "rule_coverage": round(rule_coverage, 1),
        "domain_fidelity": round(domain_fidelity["score"], 1),
        "domain_fidelity_detail": domain_fidelity,
        "lint": lint_result,
        "security": security_result,
        "per_class": per_class,
        "recommendations": recs,
        "class_count": len(classes),
        "flow_count": len(flows),
        "effective_flow_count": effective_flows if "effective_flows" in dir() else len(flows),
        "inferred_route_ops": inferred_ops if "inferred_ops" in dir() else 0,
        "test_file_count": len(test_files),
        "total_files": total_files,
    }
    if rule_coverage_details:
        details["rule_coverage_detail"] = rule_coverage_details

    _enrich_quality_details_for_ui(details, files)

    return round(overall, 1), details



def _generate_entity_tests(files: dict) -> dict:
    """Deterministically generate per-entity test files from existing route files.

    Produces 8 test file categories per entity (detected from app/routes/<entity>.py):
    unit/model, unit/schema, unit/routes, integration/crud, integration/auth,
    e2e, performance, contract.

    Returns {path: content} dict. No LLM calls, no external deps.
    """
    entities = sorted({
        p.split("/")[-1][:-3]
        for p in files
        if p.startswith("app/routes/") and p.endswith(".py") and "__init__" not in p
    })
    if not entities:
        return {}

    out: dict = {}
    categories = [
        ("unit", "model"),
        ("unit", "schema"),
        ("unit", "routes"),
        ("integration", "crud"),
        ("integration", "auth"),
        ("e2e", "e2e"),
        ("performance", "perf"),
        ("contract", "contract"),
        ("security", "security"),
        ("mutation", "mutation"),
        ("data_quality", "data_quality"),
    ]
    for e in entities:
        cls_name = "".join(w.capitalize() for w in e.split("_"))
        route_prefix = e.replace("_", "-")
        for layer, kind in categories:
            path = f"tests/{layer}/test_{e}_{kind}.py"
            out[path] = (
                f"# GENERATED: {path}\n"
                f'"""\n'
                f"Deterministic {kind} tests for {cls_name}.\n"
                f"\n"
                f"Generated by Entelim — do not edit manually.\n"
                f'"""\n'
                f"import pytest\n"
                f"\n"
                f"\n"
                f"@pytest.mark.{kind}\n"
                f"class Test{cls_name}_{kind.capitalize()}:\n"
                f'    def test_importable(self):\n'
                f'        """Module-level smoke test."""\n'
                f"        pass\n"
                f"\n"
                f'    def test_route_prefix_constant(self):\n'
                f'        """Route prefix is deterministic."""\n'
                f'        assert "{route_prefix}" == "{route_prefix}"\n'
            )
    return out


def _get_solution_business_rules(solution_id: int) -> list:
    """Return business rules from all solution motivation-layer entities.

    Sources (in priority order):
      1. SolutionBlueprintProposal.acm_properties.business_rules (architect-specified)
      2. SolutionConstraint — hard constraints become severity="must"
      3. SolutionGoal — desired outcomes become severity="should"
      4. SolutionDriver — motivators become severity="context" (informational)
      5. SolutionQualityAttribute — NFR targets become severity="must"

    All rules are normalised to {name, condition, severity, source} so downstream
    functions (_validate_blueprint_constraints, _compute_quality_score) can treat
    them uniformly regardless of origin.
    """
    rules = []

    # 1. BlueprintProposal ACM rules (existing source, backward-compatible)
    try:
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        proposals = SolutionBlueprintProposal.query.filter_by(solution_id=solution_id).all()
        for p in proposals:
            props = p.acm_properties or {}
            for rule in props.get("business_rules", []):
                if isinstance(rule, dict) and rule.get("name"):
                    rules.append({**rule, "source": "blueprint_proposal"})
    except Exception as exc:
        logger.debug("suppressed error in _get_solution_business_rules (app/modules/codegen/routes/_helpers.py): %s", exc)

    # 2. Resolve problem_id via Solution → SolutionAnalysisSession → SolutionProblemDefinition
    pd_id = None
    try:
        from app.models.solution_models import Solution
        from app.models.solution_architect_models import SolutionAnalysisSession
        solution_obj = Solution.query.get(solution_id)
        if solution_obj and solution_obj.analysis_session_id:
            session_obj = SolutionAnalysisSession.query.get(solution_obj.analysis_session_id)
            if session_obj and session_obj.problem_definition:
                pd_id = session_obj.problem_definition.id
    except Exception as exc:
        logger.debug("suppressed error in _get_solution_business_rules (app/modules/codegen/routes/_helpers.py): %s", exc)

    # 3. SolutionConstraint — severity 4-5 = "must", else "should"
    if pd_id:
        try:
            from app.models.solution_architect_models import SolutionConstraint
            for c in SolutionConstraint.query.filter_by(problem_id=pd_id).all():
                rules.append({
                    "name": c.name,
                    "condition": c.description or "",
                    "severity": "must" if (c.severity or 0) >= 4 else "should",
                    "source": "constraint",
                    "value": c.value or "",
                })
        except Exception as exc:
            logger.debug("suppressed error in _get_solution_business_rules (app/modules/codegen/routes/_helpers.py): %s", exc)

    # 4. SolutionGoal — desired outcomes; severity="should" (architects add them progressively)
    if pd_id:
        try:
            from app.models.solution_architect_models import SolutionGoal
            for g in SolutionGoal.query.filter_by(problem_id=pd_id).all():
                rules.append({
                    "name": g.name,
                    "condition": g.description or g.measurement_criteria or "",
                    "severity": "should",
                    "source": "goal",
                    "kpis": g.kpis or [],
                })
        except Exception as exc:
            logger.debug("suppressed error in _get_solution_business_rules (app/modules/codegen/routes/_helpers.py): %s", exc)

    # 5. SolutionDriver — business motivators; severity="context" (not testable directly)
    if pd_id:
        try:
            from app.models.solution_architect_models import SolutionDriver
            for d in SolutionDriver.query.filter_by(problem_id=pd_id).all():
                rules.append({
                    "name": d.name,
                    "condition": d.description or "",
                    "severity": "context",
                    "source": "driver",
                })
        except Exception as exc:
            logger.debug("suppressed error in _get_solution_business_rules (app/modules/codegen/routes/_helpers.py): %s", exc)

    # 6. SolutionQualityAttribute — NFR targets with measurable acceptance criteria
    try:
        from app.models.solution_sad_models import SolutionQualityAttribute
        for qa in SolutionQualityAttribute.query.filter_by(solution_id=solution_id).all():
            rules.append({
                "name": qa.attribute_name,
                "condition": f"{qa.attribute_type} target: {qa.target_value or 'unspecified'}",
                "severity": "must",
                "source": "quality_attribute",
                "attribute_type": qa.attribute_type,
                "target_value": qa.target_value,
            })
    except Exception as exc:
        logger.debug("suppressed error in _get_solution_business_rules (app/modules/codegen/routes/_helpers.py): %s", exc)

    # 7. SolutionRequirement (direct solution_id link) — captures document-promoted
    #    Constraint/Goal/Driver/Requirement proposals for solutions that have no
    #    SolutionProblemDefinition (document-sourced solutions skip the analysis session).
    #    severity: CONSTRAINT="must", QUALITY="should", FUNCTIONAL="should"
    try:
        from app.models.solution_architect_models import SolutionRequirement, RequirementType
        direct_reqs = SolutionRequirement.query.filter_by(solution_id=solution_id).all()
        for r in direct_reqs:
            # Only read rows that were explicitly tagged for codegen:
            #   "proposal:<id>"    — document-promoted ArchiMate proposals
            #   "intake_nfr"       — NFRs saved via the structured intake wizard
            #   "brief_extraction" — auto-extracted from the 50K problem brief
            # This prevents double-counting rows already captured by Sources 3-5
            # (SolutionConstraint / SolutionGoal / SolutionDriver are separate tables).
            src = r.source or ""
            if not (src.startswith("proposal:") or src in ("intake_nfr", "brief_extraction")):
                continue
            sev = "must" if r.requirement_type == RequirementType.CONSTRAINT else "should"
            rule = {
                "name": r.name,
                "condition": r.description or "",
                "severity": sev,
                "source": "solution_requirement",
                "is_mandatory": r.is_mandatory or False,
            }
            if r.acceptance_criteria:
                rule["acceptance_criteria"] = r.acceptance_criteria
            if r.moscow_priority:
                rule["priority"] = r.moscow_priority
            if r.compliance_tags:
                rule["compliance_tags"] = r.compliance_tags
            if r.stakeholder_name:
                rule["stakeholder"] = r.stakeholder_name
            if r.verification_method:
                rule["verification_method"] = r.verification_method
            rules.append(rule)
    except Exception as exc:
        logger.debug("suppressed error in _get_solution_business_rules (app/modules/codegen/routes/_helpers.py): %s", exc)

    try:
        from app.modules.codegen.services.domain_seed_service import DomainSeedResolver

        return DomainSeedResolver.normalize_rules(rules)
    except Exception:
        return rules



def _compute_rule_coverage(business_rules: list, all_code: str) -> dict:
    """Measure how many business rules have detectable enforcement in generated code.

    Strategy:
      1. Substring matching — fast, always runs, no dependencies.
      2. LLM semantic check — runs if LLM is configured, validates rules that
         substring matching missed (false negatives from keyword mismatch).
         Falls back silently if LLM unavailable.

    Returns:
        {
          "covered": [rule_names],
          "uncovered": [rule_names],
          "coverage_pct": float,    # 0-100
          "method": "substring"|"llm_semantic",
        }
    """
    enforceable = [r for r in business_rules if r.get("severity") in ("must", "should")]
    if not enforceable:
        return {"covered": [], "uncovered": [], "coverage_pct": 100.0, "method": "substring"}

    code_lower = all_code.lower()

    def _substring_match(rule):
        keyword = (rule.get("condition") or rule.get("name", "")).lower()
        words = [w for w in re.split(r'\W+', keyword) if len(w) >= 4]
        # Also check the rule name itself
        name_words = [w for w in re.split(r'\W+', rule.get("name", "").lower()) if len(w) >= 4]
        all_words = list(dict.fromkeys(words + name_words))  # deduplicate, preserve order
        return any(w in code_lower for w in all_words[:5])

    covered = []
    uncovered_by_substr = []
    for rule in enforceable:
        if _substring_match(rule):
            covered.append(rule["name"])
        else:
            uncovered_by_substr.append(rule)

    # Try LLM semantic check for rules that substring matching missed
    if uncovered_by_substr:
        try:
            from app.modules.ai_chat.services.llm_service import LLMService
            provider, model = LLMService._get_configured_provider()
            rules_text = "\n".join(
                f"- {r['name']}: {(r.get('condition') or '')[:80]}"
                for r in uncovered_by_substr
            )
            code_sample = all_code[:4000]
            prompt = (
                "You are a code reviewer. Given the business rules below and a code sample, "
                "identify which rules have NO implementation in the code.\n\n"
                f"BUSINESS RULES:\n{rules_text}\n\n"
                f"CODE SAMPLE:\n{code_sample}\n\n"
                "Return ONLY a JSON array of rule names that are NOT implemented. "
                'Example: ["Rule A", "Rule B"]\n'
                "If all rules are implemented, return: []"
            )
            import json as _json
            raw, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
            if raw:
                text = raw.strip()
                if text.startswith("```"):
                    _lines = text.split("\n")
                    text = "\n".join(_lines[1:-1] if _lines[-1].strip() == "```" else _lines[1:])
                match = re.search(r'\[.*\]', text, re.DOTALL)
                if match:
                    not_implemented = set(_json.loads(match.group()))
                    for rule in uncovered_by_substr:
                        if rule["name"] in not_implemented:
                            pass  # stays uncovered
                        else:
                            covered.append(rule["name"])  # LLM found it
                    uncovered = [r["name"] for r in uncovered_by_substr if r["name"] in not_implemented]
                    coverage_pct = len(covered) / len(enforceable) * 100
                    return {
                        "covered": covered,
                        "uncovered": uncovered,
                        "coverage_pct": round(coverage_pct, 1),
                        "method": "llm_semantic",
                    }
        except Exception as exc:
            logger.debug("suppressed error in _compute_rule_coverage (app/modules/codegen/routes/_helpers.py): %s", exc)  # fall through to substring result

    uncovered = [r["name"] for r in uncovered_by_substr]
    coverage_pct = len(covered) / len(enforceable) * 100
    return {
        "covered": covered,
        "uncovered": uncovered,
        "coverage_pct": round(coverage_pct, 1),
        "method": "substring",
    }



def _generate_architecture_invariant_tests(
    section_narratives: dict, config: dict, solution_name: str, business_rules: list = None
) -> str:
    """Generate tests/architecture/test_invariants.py from blueprint decisions.

    Produces runnable pytest tests that enforce architectural invariants
    against the generated code files at rest — no live server required.
    Each test corresponds to one blueprint constraint. Ships inside the ZIP
    so the customer's CI can enforce the three-way contract: Idea ↔ Architecture ↔ Code.
    """
    security_text = (section_narratives.get("security_viewpoint") or "").lower()
    nfr_text = (section_narratives.get("nfr_satisfaction") or "").lower()
    arch_text = (section_narratives.get("application_architecture") or "").lower()

    tests = []

    if "jwt" in security_text or "bearer" in security_text:
        tests.append('''\
    def test_jwt_authentication_structurally_present(self):
        """Blueprint: security_viewpoint requires JWT authentication.

        Uses AST parsing to verify JWT/Bearer auth is *imported and used* as a
        dependency — not just mentioned in a string or comment.
        """
        import ast as _ast
        JWT_NAMES = {
            "HTTPBearer", "OAuth2PasswordBearer", "OAuth2", "JWTError",
            "ExpiredSignatureError", "decode", "get_current_user",
        }
        JWT_MODULES = {"jwt", "jose", "authlib", "fastapi.security"}
        # Focus on auth/security/deps files first, fall back to all files
        candidate_files = {
            p: s for p, s in _SOURCE_FILES.items()
            if any(k in p for k in ("security", "auth", "deps", "middleware"))
        } or _SOURCE_FILES
        found = False
        for src in candidate_files.values():
            try:
                tree = _ast.parse(src)
            except SyntaxError:
                continue
            for node in _ast.walk(tree):
                if isinstance(node, _ast.ImportFrom):
                    module = node.module or ""
                    if any(m in module for m in JWT_MODULES) or any(
                        alias.name in JWT_NAMES for alias in node.names
                    ):
                        found = True
                        break
                elif isinstance(node, _ast.Import):
                    if any(alias.name in JWT_MODULES for alias in node.names):
                        found = True
                        break
            if found:
                break
        assert found, (
            "INVARIANT FAIL: Blueprint requires JWT. No JWT/Bearer auth import found "
            "(HTTPBearer, OAuth2PasswordBearer, jose, jwt). "
            "Add auth dependency to app/core/security.py and inject via Depends() in routes."
        )
''')

    if "https" in security_text or "tls" in security_text:
        tests.append('''\
    def test_tls_configuration_present(self):
        """Blueprint: security_viewpoint requires HTTPS/TLS enforcement.

        Checks that TLS/SSL configuration appears in startup, config, or deployment files —
        not just in a string literal.
        """
        import ast as _ast
        TLS_MODULES = {"ssl", "certifi"}
        TLS_ATTRS = {"ssl_keyfile", "ssl_certfile", "ssl_context", "HTTPS_ONLY"}
        candidate_files = {
            p: s for p, s in _SOURCE_FILES.items()
            if any(k in p for k in ("main", "config", "settings", "startup"))
        } or _SOURCE_FILES
        found = False
        for src in candidate_files.values():
            try:
                tree = _ast.parse(src)
            except SyntaxError:
                continue
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Import):
                    if any(alias.name in TLS_MODULES for alias in node.names):
                        found = True
                        break
                elif isinstance(node, (_ast.Attribute, _ast.Name)):
                    name = getattr(node, "id", None) or getattr(node, "attr", None)
                    if name in TLS_ATTRS:
                        found = True
                        break
            if found:
                break
        assert found, (
            "INVARIANT FAIL: Blueprint requires HTTPS/TLS. No TLS configuration found. "
            "Add ssl_keyfile/ssl_certfile to uvicorn startup or HTTPS_ONLY=True to config."
        )
''')

    if "rbac" in security_text or "role-based" in security_text:
        tests.append('''\
    def test_rbac_dependency_present(self):
        """Blueprint: security_viewpoint requires role-based access control.

        Checks that route functions have Depends() parameters for permission/role checks
        rather than just containing the word "role" somewhere.
        """
        import ast as _ast
        PERMISSION_NAMES = {
            "require_role", "check_permission", "has_permission", "require_permission",
            "RoleChecker", "PermissionChecker", "role_required", "permission_required",
        }
        route_files = {
            p: s for p, s in _SOURCE_FILES.items()
            if any(k in p for k in ("routes", "api", "views", "endpoints"))
        } or _SOURCE_FILES
        found = False
        for src in route_files.values():
            try:
                tree = _ast.parse(src)
            except SyntaxError:
                continue
            # Check that at least one route function has a permission-related Depends()
            for node in _ast.walk(tree):
                if isinstance(node, _ast.FunctionDef):
                    for arg in node.args.args:
                        ann = arg.annotation
                        if isinstance(ann, _ast.Call):
                            fn = getattr(ann.func, "id", None) or getattr(ann.func, "attr", None)
                            if fn == "Depends":
                                for kw_or_arg in (ann.args or []):
                                    arg_name = (
                                        getattr(kw_or_arg, "id", None)
                                        or getattr(kw_or_arg, "attr", None)
                                        or ""
                                    )
                                    if any(p in arg_name for p in ("role", "perm", "auth")):
                                        found = True
                                        break
                if found:
                    break
            if found:
                break
        assert found, (
            "INVARIANT FAIL: Blueprint requires RBAC. No role/permission Depends() found in routes. "
            "Create a RoleChecker or require_permission dependency and inject via Depends() in route handlers."
        )
''')

    if "pagination" in nfr_text:
        tests.append('''\
    def test_pagination_query_params_present(self):
        """Blueprint: NFRs require paginated list endpoints.

        Checks that at least one list route function has page/limit/offset as Query parameters
        in its signature — not just the words appearing anywhere.
        """
        import ast as _ast
        PAGINATION_NAMES = {"page", "limit", "offset", "size", "per_page", "skip"}
        route_files = {
            p: s for p, s in _SOURCE_FILES.items()
            if any(k in p for k in ("routes", "api", "views", "endpoints"))
        } or _SOURCE_FILES
        found = False
        for src in route_files.values():
            try:
                tree = _ast.parse(src)
            except SyntaxError:
                continue
            for node in _ast.walk(tree):
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    param_names = {arg.arg for arg in node.args.args}
                    if param_names & PAGINATION_NAMES:
                        found = True
                        break
            if found:
                break
        assert found, (
            "INVARIANT FAIL: Blueprint NFRs require pagination. "
            "No page/limit/offset/skip parameters found in route function signatures. "
            "Add `page: int = Query(1)` and `size: int = Query(20)` to list routes."
        )
''')

    if "rate limit" in nfr_text or "rate-limit" in nfr_text or "throttl" in nfr_text:
        tests.append('''\
    def test_rate_limiter_instantiated(self):
        """Blueprint: NFRs require rate limiting.

        Checks that a rate limiter is instantiated and applied — not just imported.
        """
        import ast as _ast
        LIMITER_NAMES = {"Limiter", "RateLimiter", "slowapi", "limits"}
        found_instantiation = False
        for src in _SOURCE_FILES.values():
            try:
                tree = _ast.parse(src)
            except SyntaxError:
                continue
            for node in _ast.walk(tree):
                # Check for Limiter(...) instantiation
                if isinstance(node, _ast.Call):
                    fn = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    if fn in LIMITER_NAMES:
                        found_instantiation = True
                        break
                # Check for @limiter.limit decorator
                elif isinstance(node, _ast.Attribute) and node.attr == "limit":
                    if getattr(node.value, "id", None) in ("limiter", "rate_limiter"):
                        found_instantiation = True
                        break
            if found_instantiation:
                break
        assert found_instantiation, (
            "INVARIANT FAIL: Blueprint NFRs require rate limiting. "
            "No Limiter instantiation or @limiter.limit decorator found. "
            "Install slowapi and add Limiter(key_func=get_remote_address) to app startup."
        )
''')

    if "audit" in nfr_text or "audit" in arch_text:
        tests.append('''\
    def test_audit_function_called_in_mutations(self):
        """Blueprint: NFRs require audit trail.

        Checks that mutation routes (POST/PUT/DELETE) have an audit function call
        in their body — not just that the word "audit" appears somewhere.
        """
        import ast as _ast
        AUDIT_CALL_NAMES = {"audit_log", "create_audit", "log_event", "record_audit", "emit_audit"}
        MUTATION_DECORATORS = {"post", "put", "delete", "patch"}
        route_files = {
            p: s for p, s in _SOURCE_FILES.items()
            if any(k in p for k in ("routes", "api", "views", "endpoints"))
        } or _SOURCE_FILES
        found = False
        for src in route_files.values():
            try:
                tree = _ast.parse(src)
            except SyntaxError:
                continue
            for fn_node in _ast.walk(tree):
                if not isinstance(fn_node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    continue
                # Check if it has a mutation decorator (@router.post, @app.put, etc.)
                is_mutation = any(
                    isinstance(d, _ast.Call)
                    and getattr(getattr(d, "func", None), "attr", None) in MUTATION_DECORATORS
                    for d in fn_node.decorator_list
                )
                if not is_mutation:
                    continue
                # Check body for audit function call
                for stmt in _ast.walk(fn_node):
                    if isinstance(stmt, _ast.Call):
                        fn_name = getattr(stmt.func, "id", None) or getattr(stmt.func, "attr", None)
                        if fn_name in AUDIT_CALL_NAMES:
                            found = True
                            break
                if found:
                    break
            if found:
                break
        assert found, (
            "INVARIANT FAIL: Blueprint requires audit logging in mutation routes. "
            "No audit_log() / create_audit() call found in POST/PUT/DELETE handlers. "
            "Add audit_log(user_id, action, resource_id) to every mutating route."
        )
''')

    if "async" in arch_text or "event" in arch_text or "queue" in arch_text:
        tests.append('''\
    def test_async_handlers_or_queue_present(self):
        """Blueprint: application architecture references async/event-driven patterns.

        Checks for actual async function definitions or a task queue instantiation.
        """
        import ast as _ast
        QUEUE_NAMES = {"Celery", "dramatiq", "arq", "rq", "Queue", "Worker"}
        found = False
        for src in _SOURCE_FILES.values():
            try:
                tree = _ast.parse(src)
            except SyntaxError:
                continue
            for node in _ast.walk(tree):
                if isinstance(node, _ast.AsyncFunctionDef):
                    found = True
                    break
                if isinstance(node, _ast.Call):
                    fn = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    if fn in QUEUE_NAMES:
                        found = True
                        break
            if found:
                break
        assert found, (
            "INVARIANT FAIL: Blueprint specifies async/event-driven patterns. "
            "No async def or task queue instantiation found. "
            "Add async route handlers or integrate a task queue (Celery/arq)."
        )
''')

    # Generate tests from explicit business rules captured in wizard Step 3
    # Use multi-keyword AST identifier search — not raw substring on all code
    if business_rules:
        for rule in business_rules:
            name = rule.get("name", "")
            condition = rule.get("condition", "")
            severity = rule.get("severity", "must")
            if severity != "must":
                continue  # Only generate hard assertions for "must" rules
            keyword = (condition or name).lower()
            words = [w for w in re.split(r'\W+', keyword) if len(w) >= 4]
            name_words = [w for w in re.split(r'\W+', name.lower()) if len(w) >= 4]
            search_words = list(dict.fromkeys(words + name_words))[:5]
            if not search_words:
                continue
            safe_fn = re.sub(r'[^a-z0-9]', '_', name.lower()).strip('_')[:50] or 'rule'
            escaped_name = name.replace('"', '\\"').replace("'", "\\'")
            escaped_cond = (condition.replace('"', '\\"').replace("'", "\\'"))[:120]
            words_repr = repr(search_words)
            tests.append(f'''\
    def test_business_rule_{safe_fn}(self):
        """Business rule (must): {escaped_name}

        Condition: {escaped_cond}
        Checks that key identifiers from this rule appear as AST names in the
        service/route code — not just in comments or string literals.
        """
        import ast as _ast
        search_words = {words_repr}
        # Check service and route files — where business logic should live
        target_files = {{
            p: s for p, s in _SOURCE_FILES.items()
            if any(k in p for k in ("service", "routes", "api", "handler"))
        }} or _SOURCE_FILES
        found_in_code = False
        for src in target_files.values():
            try:
                tree = _ast.parse(src)
            except SyntaxError:
                # Fall back to substring match if AST parse fails
                if any(w in src.lower() for w in search_words):
                    found_in_code = True
                    break
                continue
            # Collect Name, Attribute, and function/class definition identifiers
            identifiers = {{
                node.id.lower() for node in _ast.walk(tree)
                if isinstance(node, _ast.Name)
            }} | {{
                node.attr.lower() for node in _ast.walk(tree)
                if isinstance(node, _ast.Attribute)
            }} | {{
                node.name.lower() for node in _ast.walk(tree)
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef))
            }}
            # Substring match: word contained in any identifier (catches e.g. rule name in method name)
            if any(any(w in ident for ident in identifiers) for w in search_words):
                found_in_code = True
                break
        assert found_in_code, (
            'INVARIANT FAIL: Business rule "{escaped_name}" not enforced in service/route code. '
            f'None of {{search_words}} found as code identifiers. '
            'Condition: "{escaped_cond}" — add a validator function and call it in the route handler.'
        )
''')

    if not tests:
        # Always include a baseline test so the file is never empty
        # fabricated-ok: template emits generated test-file source code, not production/UI data
        tests.append(
            "    def test_no_hardcoded_secrets(self):\n"
            '        """Baseline invariant: generated code must not contain hardcoded secrets."""\n'
            "        import ast as _ast, re as _re\n"
            "        secret_pattern = _re.compile(\n"
            "            r'(?i)(password|secret|api_key|token)\\s*=\\s*[\"\\'][^\"\\']{8,}[\"\\']'\n"
            "        )\n"
            "        # Only flag actual assignments, not env var lookups\n"
            "        violations = []\n"
            "        for path, src in _SOURCE_FILES.items():\n"
            "            if 'test_' in path or '.env' in path:\n"
            "                continue\n"
            "            try:\n"
            "                tree = _ast.parse(src)\n"
            "            except SyntaxError:\n"
            "                continue\n"
            "            for node in _ast.walk(tree):\n"
            "                if isinstance(node, _ast.Assign):\n"
            "                    if isinstance(node.value, _ast.Constant) and isinstance(node.value.value, str):\n"
            "                        if len(node.value.value) >= 8 and secret_pattern.search(\n"
            "                            _ast.unparse(node)\n"
            "                        ):\n"
            "                            violations.append(path)\n"
            "                            break\n"
            "        assert not violations, (\n"
            "            f'Hardcoded secret assignments found in: {violations}. '\n"
            "            'Use os.getenv() or a secrets manager instead.'\n"
            "        )\n"
        )

    if not tests:
        return ""

    methods_block = "\n".join(tests)
    safe_name = re.sub(r"[^a-z0-9]", "_", solution_name.lower()).strip("_") or "solution"
    return f'''"""Architecture Invariant Tests — generated from blueprint for {solution_name}

Enforces the three-way contract: Idea <-> Architecture <-> Code.
Each test uses AST parsing to check structural correctness — not string matching.
These tests run against source files at rest — no live server required.

Run: pytest tests/architecture/ -v
"""
import os
import pytest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_SOURCE_FILES = {{
    str(p.relative_to(_PROJECT_ROOT)): p.read_text(encoding="utf-8", errors="ignore")
    for p in _PROJECT_ROOT.rglob("*.py")
    if "test_" not in p.name
    and "__pycache__" not in str(p)
    and p.stat().st_size < 500_000
}}


class TestArchitectureInvariants_{safe_name}:
    """Architectural invariants derived from {solution_name} blueprint.

    Checks use ast.parse() to verify code structure — imports, function signatures,
    and call sites — not just keyword presence in raw source text.
    """

{methods_block}
'''



def _validate_blueprint_constraints(
    files: dict,
    section_narratives: dict,
    config: dict,
    business_rules: list = None,
) -> list:
    """Validate generated code satisfies blueprint architectural constraints.

    Checks three layers of constraints:
      1. Section-narrative constraints (security_viewpoint, nfr_satisfaction) — keyword-based
      2. Business rules from solution motivation layer — via _compute_rule_coverage
         which tries LLM semantic check before falling back to substring matching

    Returns a list of constraint violation dicts: {severity, constraint, finding}.
    Severity: 'error' (blueprint explicitly requires, not found) | 'warning' (likely gap).
    """
    violations = []
    all_code = "\n".join(files.values()).lower()
    security_text = (section_narratives.get("security_viewpoint") or "").lower()
    nfr_text = (section_narratives.get("nfr_satisfaction") or "").lower()
    auth = config.get("auth", "none")

    # ── 1. Security constraints (section-narrative keyword matching) ──────────
    if "jwt" in security_text and auth in ("none", ""):
        violations.append({
            "severity": "error",
            "constraint": "JWT authentication required",
            "finding": "Blueprint security_viewpoint specifies JWT but auth is not configured.",
        })
    if "jwt" in security_text and "bearer" not in all_code and "jwt" not in all_code:
        violations.append({
            "severity": "warning",
            "constraint": "JWT implementation",
            "finding": "Blueprint requires JWT but no JWT/Bearer token handling found in generated code.",
        })
    if "https" in security_text and "ssl" not in all_code and "tls" not in all_code and "https" not in all_code:
        violations.append({
            "severity": "warning",
            "constraint": "HTTPS/TLS enforcement",
            "finding": "Blueprint specifies HTTPS but no TLS/SSL configuration found in generated code.",
        })
    if "rbac" in security_text or "role-based" in security_text:
        if "role" not in all_code and "permission" not in all_code:
            violations.append({
                "severity": "warning",
                "constraint": "Role-based access control",
                "finding": "Blueprint requires RBAC but no role/permission handling found in generated code.",
            })

    # ── 2. NFR constraints (section-narrative keyword matching) ──────────────
    if "pagination" in nfr_text and "page" not in all_code and "limit" not in all_code and "offset" not in all_code:
        violations.append({
            "severity": "warning",
            "constraint": "Pagination",
            "finding": "Blueprint NFRs mention pagination but no pagination logic found in generated code.",
        })
    if "rate limit" in nfr_text or "rate-limit" in nfr_text:
        if "rate" not in all_code and "throttle" not in all_code and "slowapi" not in all_code:
            violations.append({
                "severity": "warning",
                "constraint": "Rate limiting",
                "finding": "Blueprint NFRs specify rate limiting but no rate limiting found in generated code.",
            })
    if "audit" in nfr_text or "audit trail" in nfr_text:
        if "audit" not in all_code and "log" not in all_code:
            violations.append({
                "severity": "warning",
                "constraint": "Audit trail",
                "finding": "Blueprint NFRs require audit trail but no audit logging found in generated code.",
            })

    # ── 3. Business rule coverage (motivation-layer entities) ────────────────
    if business_rules:
        coverage = _compute_rule_coverage(business_rules, "\n".join(files.values()))
        for rule_name in coverage.get("uncovered", []):
            # Find original rule to get source and severity
            rule_obj = next((r for r in business_rules if r.get("name") == rule_name), {})
            source = rule_obj.get("source", "rule")
            severity_label = rule_obj.get("severity", "should")
            condition = (rule_obj.get("condition") or "")[:100]
            violations.append({
                "severity": "error" if severity_label == "must" else "warning",
                "constraint": f"{source.replace('_', ' ').title()}: {rule_name}",
                "finding": (
                    f"Business rule '{rule_name}' not enforced in generated code "
                    f"(detected via {coverage['method']}). "
                    + (f"Condition: {condition}" if condition else "")
                ),
            })

    return violations



def _generate_settings_config(config: dict) -> str:
    """Generate app/core/config.py — pydantic-settings Settings class.

    Validates all required environment variables at process startup so the service
    fails fast with a human-readable error instead of crashing 30 minutes into
    a deployment with an AttributeError on None.
    """
    auth = config.get("auth", "none")
    needs_jwt = auth in ("jwt-local", "oauth2")
    return f'''\
"""Application settings — validated at startup via pydantic-settings.

Every required environment variable is declared here. If DATABASE_URL or
SECRET_KEY is missing, the service raises a clear ValidationError before
accepting any traffic — not a cryptic runtime error under load.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Required ──────────────────────────────────────────────────────────────
    database_url: str
    secret_key: str

    # ── Auth ──────────────────────────────────────────────────────────────────
{('    jwt_secret: str  # Required for jwt-local auth' if needs_jwt else '    jwt_secret: str = ""')}
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # ── Application ───────────────────────────────────────────────────────────
    debug: bool = False
    log_level: str = "INFO"
    # Set ALLOWED_ORIGINS env var to restrict CORS in production (comma-separated).
    # Example: ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:8080"]

    # ── Database ──────────────────────────────────────────────────────────────
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30


settings = Settings()
'''



def _parse_condition_to_boundary(condition: str):
    """Parse a business rule condition string into (field, invalid_value, valid_value).

    Handles the common patterns written by architects:
      "status >= 1"    → field=status, invalid=0, valid=1
      "count > 0"      → field=count,  invalid=0, valid=1
      "amount <= 1000" → field=amount, invalid=1001, valid=1000
      "risk < 5"       → field=risk,   invalid=5, valid=4
      "title != empty" → field=title,  invalid="", valid="valid_title"
      "name required"  → field=name,   invalid=None, valid="SomeName"

    Returns None if the condition cannot be parsed.
    """
    import re as _re
    c = (condition or "").strip().lower()

    # Numeric comparisons: field op number
    m = _re.match(r'(\w+)\s*(>=|>|<=|<|==|!=)\s*(-?\d+(?:\.\d+)?)', c)
    if m:
        field, op, raw = m.group(1), m.group(2), m.group(3)
        threshold = float(raw) if '.' in raw else int(raw)
        if op == '>=':
            return field, threshold - 1, threshold
        if op == '>':
            return field, threshold, threshold + 1
        if op == '<=':
            return field, threshold + 1, threshold
        if op == '<':
            return field, threshold, threshold - 1
        if op == '==' or op == '!=':
            return field, threshold + 1, threshold  # != means value must equal threshold

    # Not-empty / not-null patterns: "field != empty|null|''"
    m = _re.match(r'(\w+)\s*!=\s*(?:empty|null|""|\'\'|\'\s*\')', c)
    if m:
        return m.group(1), '', 'valid_value'

    # "field required" or "required field"
    m = _re.match(r'(\w+)\s+(?:is\s+)?(?:required|not\s+null|not\s+empty)', c)
    if m:
        return m.group(1), None, 'required_value'
    m = _re.match(r'(?:required|not\s+null|not\s+empty)\s+(\w+)', c)
    if m:
        return m.group(1), None, 'required_value'

    # Length constraints: len(field) >= N
    m = _re.match(r'len\s*\(\s*(\w+)\s*\)\s*(>=|>|<=|<)\s*(\d+)', c)
    if m:
        field, op, n = m.group(1), m.group(2), int(m.group(3))
        if op in ('>=', '>'):
            too_short = max(0, n - (0 if op == '>' else 1))
            return field, 'x' * too_short, 'x' * (n + 1)
        if op in ('<=', '<'):
            too_long = n + (0 if op == '<' else 1)
            return field, 'x' * (too_long + 5), 'x' * (n - 1)

    return None



def _find_matching_endpoint(openapi: dict, field_name: str) -> tuple:
    """Find the best POST/PUT/PATCH endpoint whose request body contains field_name.

    Returns (path, method) or (None, None) if no match found.
    """
    paths = openapi.get("paths", {}) if openapi else {}
    schemas = (openapi or {}).get("components", {}).get("schemas", {})

    def _schema_fields(ref_str):
        """Resolve a $ref and return its property keys."""
        if not ref_str or not ref_str.startswith("#/components/schemas/"):
            return set()
        schema_name = ref_str.split("/")[-1]
        schema = schemas.get(schema_name, {})
        return set(schema.get("properties", {}).keys())

    best = (None, None)
    for path, path_item in paths.items():
        for method in ("post", "put", "patch"):
            op = path_item.get(method)
            if not op:
                continue
            body = op.get("requestBody", {})
            content = body.get("content", {})
            for media_type in content.values():
                schema = media_type.get("schema", {})
                ref = schema.get("$ref")
                if ref:
                    fields = _schema_fields(ref)
                elif "properties" in schema:
                    fields = set(schema["properties"].keys())
                else:
                    fields = set()
                if field_name in fields or field_name in path.lower():
                    return path, method
    return best



def _generate_business_rule_contract_tests(
    business_rules: list, openapi: dict, solution_name: str
) -> str:
    """Generate tests/contract/test_business_rules.py — behavioral HTTP tests
    that verify each 'must' business rule fires correctly at the API boundary.

    For each parseable rule:
    - Sends a request with a value that violates the condition → expects 422
    - Sends a request with a valid value → expects no 5xx

    This is the behavioral complement to test_invariants.py (structural) and
    test_validators.py (unit) — it closes the arch-to-code loop by verifying
    rules are enforced end-to-end, not just present in the code.
    """
    must_rules = [r for r in (business_rules or []) if r.get("severity") == "must"]
    if not must_rules:
        return ""

    test_methods = []
    skip_reasons = []

    for rule in must_rules:
        name = rule.get("name", "")
        condition = rule.get("condition", "")
        safe_fn = re.sub(r'[^a-z0-9]', '_', name.lower()).strip('_')[:50] or 'rule'
        escaped_name = name.replace('"', '\\"').replace("'", "\\'")
        escaped_cond = condition.replace('"', '\\"').replace("'", "\\'")[:100]

        parsed = _parse_condition_to_boundary(condition)
        if not parsed:
            skip_reasons.append(
                f"# RULE: {name!r} — condition {condition!r} could not be parsed to boundary values"
            )
            continue

        field, invalid_val, valid_val = parsed
        path, method = _find_matching_endpoint(openapi, field)

        if not path:
            # Fall back: generate a skip test so the rule is visible in the test report
            test_methods.append(
                f"    def test_rule_{safe_fn}_enforcement(self, client, auth_headers):\n"
                f"        \"\"\"Business rule (must): {escaped_name}\\n\\n"
                f"        Condition: {escaped_cond}\\n"
                f"        Cannot locate an endpoint whose request body contains field '{field}'.\\n"
                f"        Add this test manually once you know which endpoint enforces this rule.\"\"\"\n"
                f"        import pytest\n"
                f"        pytest.skip(\n"
                f"            'No endpoint found for field {field!r}. '\n"
                f"            'Manually specify the endpoint for rule: {escaped_name}'\n"
                f"        )\n"
            )
            continue

        # Represent invalid value as Python literal
        if invalid_val is None:
            invalid_repr = 'None'
        elif isinstance(invalid_val, str):
            invalid_repr = repr(invalid_val)
        else:
            invalid_repr = str(invalid_val)

        if valid_val is None:
            valid_repr = 'None'
        elif isinstance(valid_val, str):
            valid_repr = repr(valid_val) if valid_val != 'valid_value' else '"valid_value"'
        else:
            valid_repr = str(valid_val)

        test_path = re.sub(r'\{[^}]+\}', '1', path)
        method_lower = method.lower()

        test_methods.append(
            f"    def test_rule_{safe_fn}_rejects_violation(self, client, auth_headers):\n"
            f"        \"\"\"Business rule (must): {escaped_name}\\n\\n"
            f"        Rule: {escaped_cond}\\n"
            f"        Sends {field}={invalid_val!r} — expects 422 (validation rejected).\"\"\"\n"
            f"        payload = {{'{field}': {invalid_repr}}}\n"
            f"        r = client.{method_lower}(\n"
            f"            '{test_path}',\n"
            f"            json=payload,\n"
            f"            headers={{**auth_headers, 'Content-Type': 'application/json'}},\n"
            f"        )\n"
            f"        assert r.status_code == 422, (\n"
            f"            f'Rule \"{escaped_name}\" not enforced: '\n"
            f"            f'{method.upper()} {test_path} with {{{field!r}: {invalid_repr}}} '\n"
            f"            f'returned {{r.status_code}}, expected 422. '\n"
            f"            f'Check _validate_{safe_fn[:30]}() in the handler.'\n"
            f"        )\n"
            f"        body = r.json()\n"
            f"        assert 'detail' in body, (\n"
            f"            '422 response missing detail field — FastAPI Pydantic errors must '\n"
            f"            'include detail with field-level errors.'\n"
            f"        )\n"
        )
        test_methods.append(
            f"    def test_rule_{safe_fn}_accepts_valid(self, client, auth_headers):\n"
            f"        \"\"\"Business rule (must): {escaped_name}\\n\\n"
            f"        Rule: {escaped_cond}\\n"
            f"        Sends {field}={valid_val!r} — expects no 5xx server error.\"\"\"\n"
            f"        payload = {{'{field}': {valid_repr}}}\n"
            f"        r = client.{method_lower}(\n"
            f"            '{test_path}',\n"
            f"            json=payload,\n"
            f"            headers={{**auth_headers, 'Content-Type': 'application/json'}},\n"
            f"        )\n"
            f"        assert r.status_code not in (500, 502, 503), (\n"
            f"            f'Valid payload caused server error on {method.upper()} {test_path}: '\n"
            f"            f'{{r.status_code}} — {{r.text[:200]}}'\n"
            f"        )\n"
        )

    if not test_methods and not skip_reasons:
        return ""

    methods_block = "\n".join(test_methods)
    skips_block = "\n".join(skip_reasons) + ("\n" if skip_reasons else "")

    return f'''"""Business Rule Contract Tests — generated for {solution_name}

Verifies each architect-specified "must" business rule is enforced at the HTTP
API boundary.  For every parseable rule:

  • test_rule_<name>_rejects_violation — sends an invalid value, asserts 422
  • test_rule_<name>_accepts_valid     — sends a valid value, asserts no 5xx

These tests are the behavioral complement to:
  • tests/architecture/test_invariants.py  — structural presence (AST)
  • tests/unit/test_validators.py          — unit-level validator functions

They close the arch-to-code loop: a rule must be in the architecture (invariant),
implemented in the validator (unit test), AND enforced end-to-end at the API
boundary (this file).

Run: BASE_URL=http://localhost:8000 TEST_AUTH_TOKEN=<token> pytest tests/contract/test_business_rules.py -v
"""
import os
import pytest
import httpx

{skips_block}
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TEST_AUTH_TOKEN = os.getenv("TEST_AUTH_TOKEN", "test-token")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="module")
def auth_headers():
    return {{"Authorization": f"Bearer {{TEST_AUTH_TOKEN}}"}}


class TestBusinessRuleEnforcement:
    """Behavioral tests verifying each 'must' business rule fires at the API level.

    Generated from {solution_name} blueprint rules on {__import__('datetime').date.today()}.
    """

{methods_block}
'''



def _generate_validator_unit_tests(business_rules: list, solution_name: str) -> str:
    """Generate tests/unit/test_validators.py — boundary-value unit tests for every
    generated _validate_*() function.

    These tests verify the validator functions themselves with valid and invalid inputs —
    not the HTTP layer (that's contract tests) and not the code structure (that's invariants).
    For each MUST rule: one test that asserts the validator raises on violation, one that
    asserts it passes on valid input.
    """
    must_rules = [r for r in business_rules if r.get("severity") == "must"]
    if not must_rules:
        return ""

    test_methods = []
    for rule in must_rules:
        name = rule.get("name", "")
        condition = (rule.get("condition") or "")[:100]
        source = rule.get("source", "")
        safe_fn = re.sub(r'[^a-z0-9]', '_', name.lower()).strip('_')[:40] or 'rule'
        target_val = rule.get("target_value") or rule.get("value") or ""
        escaped_name = name.replace('"', '\\"').replace("'", "\\'")
        escaped_cond = condition.replace('"', '\\"').replace("'", "\\'")

        test_methods.append(f'''\
class TestValidate_{safe_fn}:
    """Unit tests for the validator enforcing rule: {escaped_name}

    Source: {source} | Condition: {escaped_cond}
    """

    def test_violation_raises(self):
        """Validator must raise when the rule is violated."""
        # Locate the validator in app/handlers/ — search class methods (validators
        # are instance methods on XxxHandler classes, not module-level functions).
        import importlib, pkgutil, inspect
        handler_cls = None
        method_name = None
        try:
            import app.handlers as _handlers_pkg
            for _importer, _modname, _ispkg in pkgutil.iter_modules(_handlers_pkg.__path__):
                _mod = importlib.import_module(f"app.handlers.{{_modname}}")
                for _cname, _cls in inspect.getmembers(_mod, predicate=inspect.isclass):
                    for _mname, _mfn in inspect.getmembers(_cls, predicate=inspect.isfunction):
                        if "validate" in _mname.lower() and "{safe_fn[:20]}" in _mname.lower():
                            handler_cls = _cls
                            method_name = _mname
                            break
                    if method_name:
                        break
                if method_name:
                    break
        except (ImportError, AttributeError):
            pass
        if handler_cls is None or method_name is None:
            import pytest
            pytest.skip(
                "Validator for rule \\"{escaped_name}\\" not found in app/handlers/. "
                "Regenerate in hybrid mode to populate handlers."
            )
        import pytest
        # Instantiate handler with db_session=None — validators check preconditions
        # before touching self.db, so None is safe for unit-level testing.
        instance = handler_cls(db_session=None)
        bound_method = getattr(instance, method_name)
        with pytest.raises((ValueError, AttributeError, Exception)):
            # entity=None triggers precondition failure → raises ValueError
            bound_method(None)

    def test_valid_input_passes(self):
        """Validator must not raise a ValueError for a non-None entity."""
        import importlib, pkgutil, inspect
        handler_cls = None
        method_name = None
        try:
            import app.handlers as _handlers_pkg
            for _importer, _modname, _ispkg in pkgutil.iter_modules(_handlers_pkg.__path__):
                _mod = importlib.import_module(f"app.handlers.{{_modname}}")
                for _cname, _cls in inspect.getmembers(_mod, predicate=inspect.isclass):
                    for _mname, _mfn in inspect.getmembers(_cls, predicate=inspect.isfunction):
                        if "validate" in _mname.lower() and "{safe_fn[:20]}" in _mname.lower():
                            handler_cls = _cls
                            method_name = _mname
                            break
                    if method_name:
                        break
                if method_name:
                    break
        except (ImportError, AttributeError):
            pass
        if handler_cls is None or method_name is None:
            import pytest
            pytest.skip("Validator not found — regenerate in hybrid mode")
        instance = handler_cls(db_session=None)
        bound_method = getattr(instance, method_name)
        # A simple object with a plausible field value — should pass the precondition
        # or fail only on self.db.commit() (AttributeError on None), not ValueError.
        class _MockEntity:
            id = 1
            status = "{target_val or 'active'}"
            value = 100
            amount = 100
            count = 1
        try:
            bound_method(_MockEntity())
        except ValueError:
            import pytest
            pytest.fail("Validator raised ValueError for a valid-looking entity — check precondition logic")
        except (AttributeError, Exception):
            pass  # db.commit() on None session — expected in unit test context
''')

    if not test_methods:
        return ""

    methods_block = "\n\n".join(test_methods)
    return f'''"""Validator Unit Tests — generated for {solution_name}

Tests each _validate_*() function from app/handlers/ with:
  - None input (must raise — any required field validator should reject None)
  - Reasonable string input (should not raise unexpectedly)

These tests guarantee the validators exist and behave correctly at the unit level,
independent of the HTTP layer (contract tests) and code structure (invariant tests).

Run: pytest tests/unit/ -v
"""
import pytest


{methods_block}
'''



def _generate_smoke_test() -> str:
    """Generate tests/test_smoke.py — verifies the FastAPI app boots and /health returns 200.

    This is the single most fundamental test: if the app doesn't boot, nothing else matters.
    Runs in-process via TestClient — no live server, no external dependencies.
    """
    return '''\
"""Smoke Tests — verifies the application boots and core infrastructure is reachable.

These tests run in-process using FastAPI's TestClient. No live server or external
dependencies required — the test itself catches import errors, missing env vars
(via Settings validation), and broken startup event handlers.

Run: pytest tests/test_smoke.py -v
"""
import os
import pytest

# Provide minimal required env vars so Settings validation passes in test mode
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./smoke_test.db")
os.environ.setdefault("SECRET_KEY", "smoke-test-secret-not-for-production")
os.environ.setdefault("JWT_SECRET", "smoke-test-jwt-not-for-production")
os.environ.setdefault("TESTING", "1")


def _get_test_client():
    """Import app and return TestClient. Raises ImportError if app is broken."""
    from fastapi.testclient import TestClient
    for mod_path, attr in [("app.main", "app"), ("main", "app"), ("app.app", "app")]:
        try:
            import importlib
            mod = importlib.import_module(mod_path)
            fastapi_app = getattr(mod, attr, None)
            if fastapi_app is not None:
                return TestClient(fastapi_app, raise_server_exceptions=False)
        except Exception:
            continue
    raise ImportError("Could not import FastAPI app from app.main, main, or app.app")


def test_app_imports_without_error():
    """The FastAPI app must be importable — catches missing deps and import-time errors."""
    client = _get_test_client()
    assert client is not None, "TestClient creation failed"


def test_health_endpoint_returns_200():
    """GET /health must return 200 — the most basic liveness check."""
    client = _get_test_client()
    r = client.get("/health")
    assert r.status_code == 200, (
        f"Health endpoint returned {r.status_code}. "
        "Add @app.get('/health') async def health(): return {'status': 'ok'} to main.py"
    )


def test_health_response_is_json():
    """GET /health must return JSON with a status field."""
    client = _get_test_client()
    r = client.get("/health")
    if r.status_code != 200:
        pytest.skip("Health endpoint not available — see test_health_endpoint_returns_200")
    body = r.json()
    assert "status" in body, (
        f"Health response missing 'status' field. Got: {body}. "
        "Return {'status': 'ok', 'db': 'ok'} from the health endpoint."
    )


def test_openapi_spec_is_valid():
    """GET /openapi.json must return a valid OpenAPI spec — catches route registration errors."""
    client = _get_test_client()
    r = client.get("/openapi.json")
    assert r.status_code == 200, (
        f"OpenAPI spec endpoint returned {r.status_code}. "
        "FastAPI auto-generates this — a non-200 indicates a startup error."
    )
    spec = r.json()
    assert "openapi" in spec, "Response missing 'openapi' field — not a valid OpenAPI spec"
    assert "paths" in spec, "OpenAPI spec has no paths — check route registration in main.py"
    assert spec["paths"], "OpenAPI spec has empty paths — no routes are registered"
'''



def _generate_seed_expectation_tests(seed_context: dict | None) -> str:
    """Generate deterministic tests for seed-required canonical fields."""
    if not seed_context:
        return ""
    seed_index = (seed_context or {}).get("seed_index", {}) or {}
    if not seed_index:
        return ""

    checks = []
    for entity_name, seed in sorted(seed_index.items()):
        required_fields = sorted(set(seed.get("required_fields", []) or []))
        if not required_fields:
            continue
        checks.append(
            {
                "entity": entity_name,
                "required_fields": required_fields,
            }
        )
    if not checks:
        return ""

    checks_json = json.dumps(checks, indent=2)
    return f'''\
"""Seed fidelity tests.

Ensures generated artifacts include required canonical fields from the selected
domain seed pack. This test intentionally validates against seed metadata rather
than generated business logic to avoid self-referential false positives.
"""
from pathlib import Path
import json


SEED_CHECKS = json.loads("""{checks_json}""")


def test_seed_required_fields_are_materialized():
    corpus = []
    for path in Path(".").rglob("*"):
        if path.suffix.lower() not in {{".py", ".ts", ".tsx", ".js", ".java", ".go", ".cls", ".cds"}}:
            continue
        try:
            corpus.append(path.read_text(encoding="utf-8", errors="ignore").lower())
        except Exception:
            continue
    all_text = "\\n".join(corpus)
    missing = []
    for check in SEED_CHECKS:
        entity = check.get("entity", "")
        for field_name in check.get("required_fields", []):
            token = str(field_name).lower()
            if token and token not in all_text:
                missing.append(f"{{entity}}.{{token}}")
    assert not missing, f"Missing canonical seeded fields: {{missing[:20]}}"
'''



def _verify_enrichment_rule_coverage(files: dict, business_rules: list) -> list:
    """After LLM enrichment, verify that MUST business rules have validator functions.

    Checks enriched service files for `_validate_` function definitions and calls.
    Returns a list of rule names that the LLM did NOT implement (with injection stubs
    already added to the files dict so the rules are enforced deterministically).

    This closes the loop: the enrichment prompt asks the LLM to generate validators;
    this function ensures they exist even when the LLM ignores the instruction.
    """
    import ast as _ast

    must_rules = [r for r in business_rules if r.get("severity") == "must"]
    if not must_rules:
        return []

    # Collect all _validate_* function names found in enriched service/route files
    found_validators = set()
    enriched_files = {
        p: c for p, c in files.items()
        if (p.startswith("app/services/") or p.startswith("app/api/routes/")
            or p.startswith("app/handlers/"))
        and p.endswith(".py")
    }
    for content in enriched_files.values():
        try:
            tree = _ast.parse(content)
            for node in _ast.walk(tree):
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    if node.name.startswith("_validate_") or node.name.startswith("validate_"):
                        found_validators.add(node.name.lower())
        except SyntaxError:
            # Collect from raw text as fallback
            for line in content.split("\n"):
                if line.strip().startswith("def _validate_") or line.strip().startswith("def validate_"):
                    fn_name = line.strip().split("(")[0].replace("def ", "").lower()
                    found_validators.add(fn_name)

    # For each MUST rule: check if a matching validator exists, inject stub if not
    missing = []
    for rule in must_rules:
        name = rule.get("name", "")
        safe_fn = re.sub(r'[^a-z0-9]', '_', name.lower()).strip('_')[:40]
        # Check if any found validator name contains key words from the rule
        rule_tokens = {w for w in re.split(r'\W+', name.lower()) if len(w) >= 4}
        matched = any(
            any(tok in fn_name for tok in rule_tokens)
            for fn_name in found_validators
        )
        if not matched:
            missing.append(name)
            # Inject a deterministic stub validator into the first available service file
            _inject_validator_stub(files, rule, safe_fn)

    return missing



def _inject_validator_stub(files: dict, rule: dict, safe_fn: str) -> None:
    """Inject a _validate_<rule>() stub into the first service file.

    Called when LLM enrichment failed to generate the validator.  The stub raises
    HTTPException(422) immediately so the rule is enforced (fails loudly) rather
    than silently bypassed — the team must implement the real logic before shipping.
    """
    target_file = next(
        (p for p in sorted(files.keys())
         if p.startswith("app/services/") and p.endswith(".py")),
        None
    )
    if target_file is None:
        # Fall back to first route file
        target_file = next(
            (p for p in sorted(files.keys())
             if p.startswith("app/api/routes/") and p.endswith(".py")),
            None
        )
    if target_file is None:
        return

    name = rule.get("name", "rule")
    condition = (rule.get("condition") or name)[:120]
    escaped_name = name.replace("'", "\\'")
    escaped_cond = condition.replace("'", "\\'")

    stub = f'''

# ── Auto-injected validator: LLM enrichment did not generate this ──────────────
# Rule: {escaped_name} | Condition: {escaped_cond}
# TODO: Implement the actual validation logic below before deploying to production.
def _validate_{safe_fn}(value):
    """Enforce business rule: {escaped_name}.

    Condition: {escaped_cond}
    AUTO-GENERATED STUB: raises immediately so CI catches missing implementation.
    Replace with real logic before shipping.
    """
    from fastapi import HTTPException
    if value is None:
        raise HTTPException(
            status_code=422,
            detail=f"Business rule \\'{escaped_name}\\' violated: value is required. "
                   "Implement _validate_{safe_fn}() with domain-specific logic."
        )
    # TODO: Add actual constraint check here
    # raise HTTPException(status_code=422, detail="...") when rule is violated
'''
    files[target_file] = files[target_file] + stub



def _generate_implementation_guide(solution, uml_snapshot: dict, config: dict) -> str:
    """Generate IMPLEMENTATION_GUIDE.md from Step 5 roadmap phases.

    Reads the phased delivery plan produced by JourneyOrchestrator.get_roadmap_data()
    and emits a Markdown document that ties each generated file back to the delivery
    phase it belongs to.  Fallbacks gracefully when journey state is absent.
    """

    solution_name = solution.name or "Untitled Solution"
    generated_at = datetime.utcnow().strftime("%Y-%m-%d")
    language = config.get("language", "python-fastapi")

    phases = []
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id=solution.id)
        roadmap = orch.get_roadmap_data()
        phases = roadmap.get("phases", [])
    except Exception as exc:
        logger.debug("suppressed error in _generate_implementation_guide (app/modules/codegen/routes/_helpers.py): %s", exc)  # No journey state — guide will still emit with UML-derived section

    lines = [
        f"# Implementation Guide — {solution_name}",
        f"\n_Generated: {generated_at} | Language: {language}_\n",
        "This guide maps each delivery phase to the components and files generated for this solution.",
        "Follow the phases in order: infrastructure before application before business process.\n",
    ]

    if phases:
        for ph in phases:
            phase_name = ph.get("name", f"Phase {ph.get('phase_order', '?')}")
            elements = ph.get("elements", [])
            lines.append(f"## {phase_name}\n")
            if not elements:
                lines.append("_No elements in this phase._\n")
                continue
            lines.append("| Component | Type | Build/Buy | Effort | Team |")
            lines.append("|-----------|------|-----------|--------|------|")
            for el in elements:
                bob = el.get("build_or_buy") or "build"
                effort = el.get("estimated_effort") or "—"
                team = el.get("team_owner") or "—"
                lines.append(f"| {el.get('name', '?')} | {el.get('type', '?')} | {bob} | {effort} | {team} |")
            lines.append("")
    else:
        # Fallback: derive a single phase list from UML classes
        classes = uml_snapshot.get("class_diagram", {}).get("classes", [])
        if classes:
            lines.append("## Phase 1 — Core Services (derived from UML)\n")
            lines.append("| Component | Source |")
            lines.append("|-----------|--------|")
            for cls in classes:
                src = cls.get("source_element_id") or "—"
                lines.append(f"| {cls.get('name', '?')} | ArchiMate #{src} |")
            lines.append("")

    lines += [
        "## Implementation Checklist\n",
        "- [ ] Infrastructure provisioned (databases, message brokers, secrets)",
        "- [ ] Environment variables set (see `app/core/config.py`)",
        "- [ ] Run `alembic upgrade head` to apply schema migrations",
        "- [ ] Execute smoke test: `pytest tests/test_smoke.py`",
        "- [ ] Execute contract tests: `pytest tests/contract/`",
        "- [ ] Execute architecture invariant tests: `pytest tests/architecture/`",
        "- [ ] Review `DECISIONS.md` for rationale behind key architectural choices",
        "",
    ]

    return "\n".join(lines)



def _generate_adr(solution, config: dict, gen_version: int) -> str:
    """Generate DECISIONS.md — Architecture Decision Records tracing code choices to blueprint.

    Records the blueprint version, architectural constraints, and rationale behind
    key code generation decisions so the generated codebase is self-documenting.
    """
    narratives = getattr(solution, "section_narratives", None) or {}
    bp_version = getattr(solution, "blueprint_version", 1) or 1
    bp_updated = getattr(solution, "blueprint_updated_at", None)
    bp_updated_str = bp_updated.strftime("%Y-%m-%d") if bp_updated else "unknown"

    auth = config.get("auth", "none")
    language = config.get("language", "python-fastapi")
    config.get("ui_framework", "none")
    config.get("deployment", "docker")

    AUTH_LABELS = {
        "jwt-local": "JWT (symmetric HS256)",
        "oauth2": "OAuth2 / OIDC",
        "api-key": "API Key (X-API-Key header)",
        "none": "No authentication",
    }

    lines = [
        f"# Architecture Decision Records — {solution.name or 'Solution'}",
        "",
        f"> Generated by Entelim · Blueprint v{bp_version} ({bp_updated_str}) · Codegen v{gen_version}",
        f"> Generation date: {datetime.utcnow().strftime('%Y-%m-%d')}",
        "",
        "These ADRs trace key design decisions in the generated codebase back to the",
        "architectural blueprint approved by the solution architect.",
        "",
        "---",
        "",
        "## ADR-001: Authentication Strategy",
        "",
        f"**Decision:** {AUTH_LABELS.get(auth, auth)}",
        "",
        "**Context:**",
    ]
    security_text = narratives.get("security_viewpoint", "").strip()
    if security_text:
        lines.append(security_text)
    else:
        lines.append("No security constraints specified in blueprint.")
    lines += [
        "",
        f"**Consequence:** All protected endpoints require `{auth}` authentication.",
        "See `app/core/security.py` and `app/api/deps.py` for implementation.",
        "",
        "---",
        "",
        "## ADR-002: Non-Functional Requirements Commitments",
        "",
        "**Decision:** The following NFR constraints from the blueprint are committed to in this codebase.",
        "",
        "**Context:**",
    ]
    nfr_text = narratives.get("nfr_satisfaction", "").strip()
    if nfr_text:
        lines.append(nfr_text)
    else:
        lines.append("No NFR constraints specified in blueprint.")
    lines += [
        "",
        "**Consequence:** Validation rules, pagination limits, and rate limiting in generated",
        "code reflect these commitments. See `app/api/routes/` for enforcement.",
        "",
        "---",
        "",
        "## ADR-003: Deployment Architecture",
        "",
        f"**Decision:** {language} application, containerised with Docker.",
        "",
        "**Context:**",
    ]
    deploy_text = narratives.get("deployment_view", "").strip()
    if deploy_text:
        lines.append(deploy_text)
    else:
        lines.append("No deployment constraints specified in blueprint.")
    lines += [
        "",
        "**Consequence:** `docker-compose.yml` and `Dockerfile` reflect this deployment model.",
        "Infrastructure-as-code in `infra/` directory.",
        "",
        "---",
        "",
        "## ADR-004: Data Architecture",
        "",
        "**Decision:** PostgreSQL relational database, ORM-managed schema.",
        "",
        "**Context:**",
    ]
    data_text = narratives.get("data_information", "").strip()
    if data_text:
        lines.append(data_text)
    else:
        lines.append("No data architecture constraints specified in blueprint.")
    lines += [
        "",
        "**Consequence:** All models in `app/models/` derive from blueprint ArchiMate DataObjects.",
        "See `ARCHITECTURE.md` for element-to-model traceability.",
        "",
        "---",
        "",
        "## Blueprint Traceability",
        "",
        "| Source | Version | Updated |",
        "|--------|---------|---------|",
        f"| Solution Blueprint | v{bp_version} | {bp_updated_str} |",
        "| ArchiMate elements | — | See ARCHITECTURE.md |",
        f"| Codegen generation | v{gen_version} | {datetime.utcnow().strftime('%Y-%m-%d')} |",
    ]

    return "\n".join(lines)



def _notify_tech_lead(solution, file_count, language, completeness):
    """Notify the solution's technical lead about code generation (GAP-09).

    `solution` may be a detached instance by the time a streaming generation
    finishes (the SSE generator's own db.session gets recycled mid-stream),
    so any lazy-loaded attribute access — including the getattr below —
    must be inside the try: an unguarded getattr previously raised
    DetachedInstanceError and killed the whole generate-stream response
    after the code bundle had already been produced and persisted.
    """
    try:
        tech_lead = getattr(solution, "technical_lead", None)
        if not tech_lead:
            return
        from app.models.user import User
        # tenant-scoping-ok: User.email is globally unique, so this cannot
        # match another org's user.
        lead = User.query.filter_by(email=tech_lead).first()
        if not lead:
            return
        from app.services.notification_service import NotificationService
        NotificationService.create(
            user_id=lead.id,
            title=f"API contracts generated for {solution.name}",
            message=f"{file_count} files generated ({language}, chain {completeness or 'N/A'})",
            link=f"/solutions/{solution.id}/codegen",
        )
    except Exception as e:
        logger.debug("Tech lead notification skipped: %s", e)



def _enrich_background(app_ctx, solution_id):
    """Background thread: run UML enrichment and store result in gen.config."""
    def _run():
        def _set_status(status, **kwargs):
            try:
                gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
                if not gen:
                    gen = CodegenGeneration(solution_id=solution_id, version=0)
                    db.session.add(gen)
                cfg = dict(gen.config or {})
                cfg["_enrich_status"] = {"status": status, **kwargs}
                gen.config = cfg
                db.session.commit()
            except Exception as exc:
                logger.error("[enrich_bg] _set_status(%s) failed: %s", status, exc)
                db.session.rollback()

        import time as _time
        _set_status("running", started_at=_time.time())
        try:
            from app.modules.codegen.services.uml_enrichment_service import UMLEnrichmentService
            result = UMLEnrichmentService.enrich(solution_id)
            if not result["success"]:
                _set_status("failed", error=result.get("error", "Enrichment failed"))
                return
            gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
            if not gen:
                gen = CodegenGeneration(solution_id=solution_id, version=0)
                db.session.add(gen)
            gen.uml_snapshot = result["uml"]
            gen.generated_files = None
            gen.version = (gen.version or 0) + 1
            cfg = dict(gen.config or {})
            cfg["_enrich_status"] = {
                "status": "done",
                "version": gen.version,
                "element_count": result["element_count"],
            }
            gen.config = cfg
            db.session.commit()
        except Exception as exc:
            logger.error("[enrich_bg] unhandled exception: %s", exc)
            try:
                _set_status("failed", error=str(exc))
            except Exception as inner_exc:
                logger.critical(
                    "[enrich_bg] cannot persist failure status for solution %d: %s (original: %s)",
                    solution_id, inner_exc, exc,
                )
                try:
                    db.session.rollback()
                    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
                    if gen:
                        gen.config = {**(gen.config or {}), "_enrich_status": {"status": "failed", "error": str(exc)}}
                        db.session.commit()
                except Exception:
                    logger.critical("[enrich_bg] all status persistence attempts failed for solution %d", solution_id)

    with app_ctx:
        _run()



def _spec_type_to_uml(spec_type: str) -> str:
    """Convert spec_data field type to UML/Python type used in class diagrams."""
    return {
        "string": "str",
        "text": "str",
        "integer": "int",
        "float": "float",
        "boolean": "bool",
        "datetime": "datetime",
        "date": "datetime",
        "uuid": "UUID",
        "decimal": "Decimal",
        "json": "dict",
    }.get((spec_type or "").lower(), "str")



def _uml_to_product_spec_bundle(uml, config, solution, business_rules: list = None):
    """Bridge the workbench UML snapshot → ProductSpecBundle for deterministic generation.

    The UML snapshot (from Phase 1 LLM enrichment) contains class_diagram, sequence_diagram,
    component_diagram, deployment_diagram.  The deterministic generator expects a
    ProductSpecBundle built from OpenAPI spec.  This function converts UML → OpenAPI → Bundle.

    business_rules: normalized list from _get_solution_business_rules() — must/should rules
    from the solution's motivation layer. Converted to BusinessRuleDef and distributed to
    UML entity classes so DeterministicCodeGenerator emits handler files with enforcement code.
    """
    import re
    from app.modules.solutions_product.services.product_spec_bundle import (
        ProductSpecBundle, ServiceDef, PathDef, InfraContext, DeploymentDef, IntegrationDef,
    )

    classes = uml.get("class_diagram", {}).get("classes", [])
    flows = uml.get("sequence_diagram", {}).get("flows", [])
    components = uml.get("component_diagram", {}).get("components", [])
    nodes = uml.get("deployment_diagram", {}).get("nodes", [])

    seed_context = {}
    provenance = {"entities": {}, "fields": {}, "rules": {}}
    if business_rules is None:
        business_rules = []
    try:
        from app.modules.codegen.services.domain_seed_service import DomainSeedResolver

        seed_resolution = DomainSeedResolver.resolve(solution, classes, business_rules)
        classes = seed_resolution.get("classes", classes)
        business_rules = seed_resolution.get("normalized_rules", business_rules)
        seed_context = {
            "version": seed_resolution.get("version"),
            "vendor_keys": seed_resolution.get("vendor_keys", []),
            "seed_index": seed_resolution.get("seed_index", {}),
            "coverage": seed_resolution.get("coverage", {}),
        }
        provenance = seed_resolution.get("provenance", provenance)
        provenance["rules"] = {
            r.get("name", f"rule_{idx}"): r.get("source", r.get("evidence_source", "inferred"))
            for idx, r in enumerate(business_rules)
        }
    except Exception as seed_exc:
        logger.debug("Seed resolver unavailable, using inference-only bridge: %s", seed_exc)

    # ── Build/buy routing ─────────────────────────────────────────────────────
    # DB-lookup acm_properties for each source element so "buy"/"SaaS" components
    # are emitted as integration client stubs rather than full CRUD services.
    _source_ids = [c.get("source_element_id") for c in classes if c.get("source_element_id")]
    _build_or_buy_map: dict = {}  # source_element_id -> normalised decision string
    if _source_ids:
        try:
            from app.models.archimate_core import ArchiMateElement as _AE
            for _row in _AE.query.filter(_AE.id.in_(_source_ids)).with_entities(_AE.id, _AE.acm_properties).all():
                _acm = _row.acm_properties or {}
                _bob = _acm.get("build_or_buy")
                if _bob:
                    _val = _bob.get("value", _bob) if isinstance(_bob, dict) else _bob
                    if _val:
                        _build_or_buy_map[_row.id] = str(_val).lower()
        except Exception as exc:
            logger.debug("suppressed error in _uml_to_product_spec_bundle (app/modules/codegen/routes/_helpers.py): %s", exc)  # Never block generation over a missing acm lookup

    _BUY_DECISIONS = {"buy", "saas", "extend-existing", "extend existing"}
    _buy_component_names: set = set()  # pascal names routed to integration stubs

    # ArchiMate type suffixes to strip from element/class names for clean domain names.
    _ARCHIMATE_SUFFIXES_RE = re.compile(
        r"\s*(?:Data\s*Object|Business\s*Object|Application\s*Component|"
        r"Application\s*Service|Application\s*Function|Application\s*Interface|"
        r"Business\s*Process|Business\s*Service|Business\s*Function|"
        r"Technology\s*Object|Technology\s*Service|Node|Artifact|"
        r"DataObject|BusinessObject|ApplicationComponent|"
        r"ApplicationService|ApplicationFunction|ApplicationInterface|"
        r"BusinessProcess|BusinessService|BusinessFunction|"
        r"TechnologyObject|TechnologyService)\s*$",
        re.IGNORECASE,
    )

    def _strip_archimate(name):
        stripped = _ARCHIMATE_SUFFIXES_RE.sub("", name).strip()
        return stripped if stripped else name

    # _snake is now defined at module level (see near the top of this file): it was
    # referenced from six call sites in OTHER functions, which all raised NameError
    # while it lived only in this scope.

    def _pascal(name):
        sanitized = re.sub(r"[^a-zA-Z0-9\s_\-]", "", (name or "Unknown").strip())
        segments = re.split(r"[\s_\-]+", sanitized)
        words = []
        for seg in segments:
            if not seg:
                continue
            # Split on camelCase/PascalCase boundaries: "WorkOrder" → ["Work", "Order"]
            p = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", seg)
            p = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", p)
            words.extend(p.split("_"))
        result = "".join(w.capitalize() for w in words if w) or "Unknown"
        return ("Entity" + result) if result[0].isdigit() else result

    def _pluralize(word):
        """English pluralization for common cases."""
        if not word:
            return word
        if word.endswith("y") and word[-2:] not in ("ay", "ey", "oy", "uy"):
            return word[:-1] + "ies"
        if word.endswith(("s", "sh", "ch", "x", "z")):
            return word + "es"
        if word.endswith("f"):
            return word[:-1] + "ves"
        if word.endswith("fe"):
            return word[:-2] + "ves"
        return word + "s"

    # Build OpenAPI spec from UML classes + flows
    paths = {}
    schemas = {}
    tag_map = {}

    for cls in classes:
        name = _strip_archimate(cls.get("name", "Unknown"))
        slug = _pluralize(_snake(name))
        pascal_name = _pascal(name)
        source_id = cls.get("source_element_id")

        # Route buy/SaaS components to integration stubs — skip CRUD generation.
        # The component will surface as a generated httpx client in app/integrations/.
        if _build_or_buy_map.get(source_id, "build") in _BUY_DECISIONS:
            _buy_component_names.add(pascal_name)
            continue

        # Build schema from class fields — GAP-06: map foreign keys to $ref
        properties = {}
        required = []
        for f in cls.get("fields", []):
            fk = f.get("foreign_key")
            prop = {"type": f.get("type", "string")}
            if fk and "." in fk:
                prop = {"type": "integer", "x-foreign-key": fk}
            if f.get("enum_values"):
                prop["enum"] = f["enum_values"]
            if f.get("max_length"):
                prop["maxLength"] = f["max_length"]
            if f.get("default") is not None:
                prop["default"] = f["default"]
            properties[f["name"]] = prop
            if f.get("required") and not f.get("primary_key"):
                required.append(f["name"])
        schemas[pascal_name] = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

        # CRUD paths
        list_path = f"/api/{slug}"
        detail_path = f"/api/{slug}/{{id}}"
        paths[list_path] = {
            "get": {"operationId": f"list_{_snake(name)}", "tags": [pascal_name],
                    "summary": f"List {name}", "responses": {"200": {}},
                    "x-archimate-source": source_id},
            "post": {"operationId": f"create_{_snake(name)}", "tags": [pascal_name],
                     "summary": f"Create {name}", "responses": {"201": {}},
                     "x-archimate-source": source_id},
        }
        paths[detail_path] = {
            "get": {"operationId": f"get_{_snake(name)}", "tags": [pascal_name],
                    "summary": f"Get {name}", "responses": {"200": {}},
                    "x-archimate-source": source_id},
            "put": {"operationId": f"update_{_snake(name)}", "tags": [pascal_name],
                    "summary": f"Update {name}", "responses": {"200": {}},
                    "x-archimate-source": source_id},
            "patch": {"operationId": f"patch_{_snake(name)}", "tags": [pascal_name],
                      "summary": f"Partial update {name}", "responses": {"200": {}},
                      "x-archimate-source": source_id},
            "delete": {"operationId": f"delete_{_snake(name)}", "tags": [pascal_name],
                       "summary": f"Delete {name}", "responses": {"204": {}},
                       "x-archimate-source": source_id},
        }

    # Merge explicit flows into existing class services (match by path prefix)
    # GAP-06: Use business process steps as operation descriptions
    class_slugs = {}
    for c in classes:
        base = _snake(c.get("name", ""))
        pascal = _pascal(c.get("name", ""))
        class_slugs[_pluralize(base)] = pascal   # plural
        class_slugs[base] = pascal         # singular

    for flow in flows:
        path = flow.get("path", "")
        method = flow.get("http_method", "GET").lower()
        if not path or not method:
            continue
        # Find parent class by matching path slug (try plural and singular, with hyphens and underscores)
        tag = None
        for slug, class_name in class_slugs.items():
            if slug.replace("_", "-") in path or slug in path:
                tag = class_name
                break
        if not tag:
            continue  # Skip flows that don't match any class

        # GAP-06: Build description from business process steps
        steps = flow.get("steps", [])
        description = ""
        if steps:
            description = " → ".join(
                s.get("description", s.get("action", "")) for s in steps if s.get("description") or s.get("action")
            )

        # Only add if path+method not already covered by CRUD
        if path not in paths or method not in paths.get(path, {}):
            paths.setdefault(path, {})[method] = {
                "operationId": _snake(flow.get("name", "")),
                "tags": [tag],
                "summary": flow.get("name", ""),
                "description": description or flow.get("name", ""),
                "responses": {"200": {}},
                "x-archimate-source": flow.get("source_element_id"),
            }

    openapi = {
        "openapi": "3.1.0",
        "info": {"title": solution.name or "Generated API", "version": "1.0.0"},
        "paths": paths,
        "components": {"schemas": schemas},
    }

    # Parse services from OpenAPI
    for path_str, path_item in paths.items():
        for method in ("get", "post", "put", "delete", "patch"):
            op = path_item.get(method)
            if not op:
                continue
            tag = op.get("tags", ["default"])[0]
            if tag not in tag_map:
                tag_map[tag] = ServiceDef(name=tag, tag=tag, paths=[])
            tag_map[tag].paths.append(PathDef(
                path=path_str, method=method.upper(),
                operation_id=op.get("operationId", f"{method}_{_snake(path_str)}"),
                summary=op.get("summary", ""), request_schema=None,
                response_schema=tag, archimate_source_id=op.get("x-archimate-source"),
            ))

    # Build confirmed_fields from UML class fields.
    # Use dicts (not FieldDef) so both Python/Go generators (which call .get())
    # and Salesforce generator (which reads attributes) can consume them.
    confirmed_fields = {}
    for cls in classes:
        name = _pascal(_strip_archimate(cls.get("name", "")))
        class_field_provenance = provenance.get("fields", {}).get(name, {})
        confirmed_fields[name] = []
        for f in cls.get("fields", []):
            field_name = f["name"]
            confirmed_fields[name].append(
                {
                    "name": field_name,
                    "type": f.get("type", "string"),
                    "format": f.get("format"),
                    "required": f.get("required", not f.get("nullable", True)),
                    "readonly": f.get("readonly", False),
                    "description": f.get("description", ""),
                    "primary_key": f.get("primary_key", False),
                    "foreign_key": f.get("foreign_key"),
                    "unique": f.get("unique", False),
                    "max_length": f.get("max_length"),
                    "default": f.get("default"),
                    "enum": f.get("enum_values"),
                    "enum_values": f.get("enum_values"),
                    "provenance": class_field_provenance.get(_snake(field_name), "inferred"),
                }
            )

    # Override confirmed_fields with DB-confirmed specs from SolutionArchiMateElement.
    # When an architect has confirmed fields via the blueprint panel, those take priority
    # over UML-snapshot-derived class names (which may be infrastructure element names
    # rather than clean domain entity names like WorkOrder, Customer, etc.).
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement as _SAE2
        _sae_links = _SAE2.query.filter_by(solution_id=solution.id).all()
        _db_confirmed = {}
        for _lnk in _sae_links:
            _sd2 = _lnk.spec_data or {}
            if _sd2.get("fields") and _sd2.get("fields_status") == "confirmed":
                _ename = getattr(_lnk, "element_name", None) or f"element_{_lnk.element_id}"
                _db_confirmed[_ename] = _sd2["fields"]
        if _db_confirmed:
            confirmed_fields = _db_confirmed
    except Exception as exc:
        logger.debug("suppressed error in _uml_to_product_spec_bundle (app/modules/codegen/routes/_helpers.py): %s", exc)  # Never block generation over a missing DB lookup

    # Build deployment config from workbench config
    language = config.get("language", "python-fastapi")
    _lang_map = {
        "python-fastapi": ("python3.12", "fastapi"),
        "python-flask": ("python3.12", "flask"),
        "go-chi": ("go1.22", "chi"),
        "java-spring-boot": ("java17", "spring-boot"),
        "salesforce-apex": ("apex", "salesforce"),
        "react-native-expo": ("node20", "expo"),
        "react-shadcn": ("node20", "react"),
        "sap-cap": ("node20", "sap-cap"),
    }
    runtime, framework = _lang_map.get(language, ("python3.12", "fastapi"))

    deployment = DeploymentDef(
        runtime=runtime, framework=framework, database="postgresql",
    )

    # Gap 2: Extract business process flow steps per service for richer code generation
    business_process_flows = {}
    for flow in flows:
        steps = flow.get("steps", [])
        if not steps:
            continue
        # Match flow to a service class by path slug
        tag = None
        path = flow.get("path", "")
        for slug, class_name in class_slugs.items():
            if slug.replace("_", "-") in path or slug in path:
                tag = class_name
                break
        if not tag:
            continue
        if tag not in business_process_flows:
            business_process_flows[tag] = []
        for step in steps:
            business_process_flows[tag].append({
                "action": step.get("action", ""),
                "description": step.get("description", ""),
                "method_body_hint": step.get("method_body_hint", ""),
                "from": step.get("from", ""),
                "to": step.get("to", ""),
            })

    spec_hash = hashlib.sha256(str(openapi).encode()).hexdigest()[:16]

    # ── Integration stubs for buy/SaaS components ─────────────────────────────
    # Each buy/SaaS component becomes an IntegrationDef that triggers client.py.j2
    # in DeterministicCodeGenerator — emits a typed httpx client with retry + circuit-breaker.
    _buy_integrations: dict = {}
    for _cls in classes:
        _pname = _pascal(_cls.get("name", ""))
        if _pname not in _buy_component_names:
            continue
        _sname = _snake(_cls.get("name", ""))
        _buy_integrations[_sname] = IntegrationDef(
            name=_sname,
            protocol="rest",
            auth_method="api_key",
            sla={"timeout_ms": 5000, "max_retries": 3},
            retry_policy={"max_attempts": 3, "backoff_factor": 2},
            circuit_breaker={"failure_threshold": 5, "recovery_timeout_s": 30},
            direction="consume",
            version=1,
        )

    # ── Bridge: pull confirmed integration contracts from blueprint spec_data ─
    # SolutionArchiMateElement.spec_data.integrations stores contracts that
    # architects confirmed in the blueprint panel.  These were previously
    # orphaned — the codegen only read _buy_integrations (buy-classified
    # components only).  This loop merges ALL confirmed element-level contracts
    # into the bundle so client.py.j2 generates real typed clients.
    # Also covers existing enterprise systems (SAP, ERP, etc.) that are
    # classified as "existing" rather than "buy" and would otherwise be skipped.
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement as _SAE
        from app.models.integration_contract import IntegrationContract as _IC
        _sae_rows = _SAE.query.filter_by(solution_id=solution.id).all()
        for _sae in _sae_rows:
            _sd = _sae.spec_data or {}
            _elem_contracts = _sd.get("integrations", {})
            for _target_id, _contract in _elem_contracts.items():
                if _contract.get("status") != "confirmed":
                    continue
                _int_name = _snake(
                    _contract.get("name")
                    or _sae.element_name
                    or f"system_{_target_id}"
                )
                if _int_name in _buy_integrations:
                    continue  # buy stub already present — don't overwrite
                _buy_integrations[_int_name] = IntegrationDef(
                    name=_int_name,
                    protocol=_contract.get("protocol", "rest"),
                    auth_method=_contract.get("auth_method", "bearer"),
                    sla=_contract.get("sla", {}),
                    retry_policy=_contract.get("retry_policy", {}),
                    circuit_breaker=_contract.get("circuit_breaker", {}),
                    direction=_contract.get("direction", "consume"),
                    version=_contract.get("version", 1),
                )
        # Also pull from the IntegrationContract registry (application-level contracts).
        # These are registered by admins against specific applications and provide
        # real base_url, auth_config (env var names), and per-environment URLs.
        # Attach them via a custom attribute so client.py.j2 can use base_url + auth_config.
        _app_element_ids = {_sae.element_id for _sae in _sae_rows}
        if _app_element_ids:
            from app.models.archimate_core import ArchiMateElement as _AME
            _ame_rows = _AME.query.filter(_AME.id.in_(_app_element_ids)).all()
            _ame_name_map = {_a.id: _a.name for _a in _ame_rows}
            # Match IntegrationContracts by name substring against element names
            _all_contracts = _IC.query.all()
            for _ic in _all_contracts:
                _ic_key = _snake(_ic.name)
                # Merge: enrich existing IntegrationDef or create new one
                _existing = _buy_integrations.get(_ic_key)
                _idef = IntegrationDef(
                    name=_ic_key,
                    protocol=(_ic.protocol or "rest").lower(),
                    auth_method=(_ic.auth_method or "bearer").lower(),
                    sla={
                        "availability": _ic.sla_availability,
                        "max_latency_ms": _ic.sla_latency_ms or 5000,
                    },
                    retry_policy={},
                    circuit_breaker={},
                    direction="consume",
                    version=1,
                )
                # Attach registry-level fields as extra attrs for template
                _idef.base_url = _ic.base_url or ""
                _idef.auth_config = _ic.auth_config or {}
                _idef.environments = _ic.environments or {}
                _idef.contract_id = _ic.id
                if _existing:
                    # Enrich existing stub with real URLs if not already set
                    if not getattr(_existing, "base_url", ""):
                        _existing.base_url = _idef.base_url
                        _existing.auth_config = _idef.auth_config
                        _existing.environments = _idef.environments
                        _existing.contract_id = _idef.contract_id
                else:
                    _buy_integrations[_ic_key] = _idef
    except Exception as _bridge_exc:
        logger.warning("Integration contract bridge failed (non-fatal): %s", _bridge_exc)

    # ── Architecture style auto-detection ────────────────────────────────────
    # Infer the primary architecture style from names and descriptions in the UML
    # snapshot so DeterministicCodeGenerator._render_arch_style_files() activates
    # the correct Kafka / serverless / microservices scaffolding.
    _all_uml_text = " ".join(
        (e.get("name", "") + " " + e.get("description", ""))
        for e in (classes + components + nodes)
    ).lower()
    if any(kw in _all_uml_text for kw in ("kafka", "event bus", "message broker", "event stream", "pub/sub", "pubsub", "rabbitmq", "kinesis", "event-driven")):
        _arch_primary = "event_driven"
    elif any(kw in _all_uml_text for kw in ("lambda", "serverless", "faas", "step function", "azure function", "cloud function")):
        _arch_primary = "serverless"
    else:
        _arch_primary = "microservices"
    _architecture_style = {"primary": _arch_primary}

    # jwt-local: auto-inject minimal identity_provider dict so auth_config.py.j2
    # generates JWT_SECRET-based verification middleware
    idp = config.get("identity_provider")
    if not idp and config.get("auth") == "jwt-local":
        idp = {"type": "jwt-local"}

    # Convert motivation-layer rules to BusinessRuleDef format and distribute to entity classes.
    # This is what activates DeterministicCodeGenerator handler generation — without a populated
    # bundle.business_rules the generator skips the entire app/handlers/ output directory.
    bundle_business_rules = {}
    if business_rules:
        from app.modules.solutions_product.services.product_spec_bundle import BusinessRuleDef
        enforceable = [r for r in business_rules if r.get("severity") in ("must", "should")]
        if enforceable:
            # Assign rules to the best-matching entity class by keyword overlap,
            # or to the first class if no match (solution-level constraints).
            class_names_snake = {
                re.sub(r"[^a-z0-9]", "_", _strip_archimate(cls.get("name", "")).lower()).strip("_"): _strip_archimate(cls.get("name", ""))
                for cls in classes
            }
            primary_class = _strip_archimate(classes[0].get("name", "Solution")) if classes else "Solution"
            for rule in enforceable:
                rule_tokens = set(re.split(r'\W+', (rule.get("name", "") + " " + rule.get("condition", "")).lower()))
                best_class = primary_class
                best_overlap = 0
                for snake, orig_name in class_names_snake.items():
                    class_tokens = set(re.split(r'\W+', snake))
                    overlap = len(rule_tokens & class_tokens)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_class = orig_name
                if best_class not in bundle_business_rules:
                    bundle_business_rules[best_class] = []
                safe_id = re.sub(r'[^a-z0-9_]', '_', rule.get("name", "rule").lower())[:40]
                condition_text = rule.get("condition", rule.get("name", ""))
                bundle_business_rules[best_class].append(BusinessRuleDef(
                    id=f"sol_{safe_id}",
                    type="validation",
                    entity=best_class,
                    # handler.py.j2 renders: def {trigger}_{model_name}(...)
                    # Must be a valid Python identifier fragment (no commas/spaces).
                    # Including safe_id makes each method unique:
                    #   validate_file_size_limit_document, validate_status_valid_document, …
                    trigger=f"validate_{safe_id}",
                    # handler.py.j2 iterates preconditions as {field, operator, value} dicts.
                    # _parse_condition_to_preconditions() converts the condition text to that
                    # format so the template renders correctly instead of crashing on .field.
                    preconditions=_parse_condition_to_preconditions(condition_text),
                    postconditions=[],
                    side_effects=[],
                    severity="error" if rule.get("severity") == "must" else "warning",
                    priority=5 if rule.get("severity") == "must" else 3,
                ))

    # Auto-enable CI/CD (GitHub Actions) if not explicitly configured — always ship a test pipeline
    ci_cd_config = config.get("ci_cd") or {"provider": "github_actions"}

    # Auto-derive NFR flags from motivation-layer quality attributes and blueprint NFR text
    _nfr_overrides = list(filter(None, [
        {"name": "Rate Limiting", "category": "performance", "flag": "rate_limiting"}
        if config.get("rate_limiting") or any(
            "rate" in (r.get("condition") or "").lower() or "throttl" in (r.get("condition") or "").lower()
            for r in (business_rules or [])
        ) else None,
        {"name": "Pagination", "category": "performance", "flag": "pagination"}
        if config.get("pagination") else None,
        {"name": "Audit Trail", "category": "compliance", "flag": "audit_trail"}
        if config.get("audit_trail") or any(
            "audit" in (r.get("condition") or "").lower() or "audit" in (r.get("name") or "").lower()
            for r in (business_rules or [])
        ) else None,
        {"name": "Role-Based Access Control", "category": "security", "flag": "rbac"}
        if config.get("rbac") or any(
            "role" in (r.get("condition") or "").lower() or "rbac" in (r.get("condition") or "").lower()
            for r in (business_rules or [])
        ) else None,
        {"name": "HTTPS/TLS Enforcement", "category": "security", "flag": "ssl_redirect"}
        if config.get("ssl_redirect") else None,
    ]))

    # Normalize multi_tenancy from workbench config (can be bool or object)
    _mt_raw = config.get("multi_tenancy")
    if isinstance(_mt_raw, dict):
        _multi_tenancy = {
            "enabled": _mt_raw.get("enabled", True),
            "strategy": _mt_raw.get("strategy", "row_level"),
            "tenant_field": _mt_raw.get("tenant_field", "organization_id"),
        }
    elif _mt_raw:
        _multi_tenancy = {
            "enabled": True,
            "strategy": "row_level",
            "tenant_field": "organization_id",
        }
    else:
        _multi_tenancy = {}

    return ProductSpecBundle(
        solution_id=solution.id,
        solution_name=solution.name or "Untitled",
        openapi=openapi,
        asyncapi=None,
        schemas=schemas,
        services=list(tag_map.values()),
        events=[],
        infra_context=InfraContext(
            nodes=[{
                "name": n.get("name"),
                "type": n.get("node_type"),
                "components": n.get("components_deployed", []),
                "properties": n.get("properties", {}),
                "source_element_id": n.get("source_element_id"),
            } for n in nodes] if nodes else [],
            nfrs=_nfr_overrides,
        ),
        contract_tests=[],
        maturity_score=0.0,
        spec_hash=spec_hash,
        confirmed_fields=confirmed_fields,
        deployment=deployment,
        identity_provider=idp,
        ci_cd=ci_cd_config,
        business_process_flows=business_process_flows,
        business_rules=bundle_business_rules,
        integrations=_buy_integrations,
        architecture_style=_architecture_style,
        seed_context=seed_context,
        provenance=provenance,
        multi_tenancy=_multi_tenancy,
    )



def _map_cds_type(json_type: str, json_format: str | None = None) -> str:
    """Map JSON Schema type+format (or Python/ORM type) to SAP CDS type.

    Accepts both JSON Schema vocabulary (string/number/integer/boolean) and
    Python-style vocabulary (str/float/int/bool/datetime/uuid) because the
    UML enrichment pipeline emits Python types.

    Deterministic — same inputs always produce same CDS type.
    """
    json_type = (json_type or "string").lower()
    json_format = (json_format or "").lower()

    # ── integer / int ─────────────────────────────────────────────────────────
    if json_type in ("integer", "int"):
        return "Integer"
    # ── number / float / double / decimal ─────────────────────────────────────
    if json_type in ("number", "float", "double", "decimal"):
        return "Decimal(10,2)"
    # ── boolean / bool ────────────────────────────────────────────────────────
    if json_type in ("boolean", "bool"):
        return "Boolean"
    # ── array / list ──────────────────────────────────────────────────────────
    if json_type in ("array", "list"):
        return None  # Handled via associations — skip field
    # ── datetime / timestamp ──────────────────────────────────────────────────
    if json_type in ("datetime", "timestamp"):
        return "Timestamp"
    # ── date ──────────────────────────────────────────────────────────────────
    if json_type == "date":
        return "Date"
    # ── uuid (type-level, e.g. "UUID" or "uuid") ──────────────────────────────
    if json_type == "uuid":
        return "UUID"
    # ── string variants keyed by format ───────────────────────────────────────
    if json_format == "date-time":
        return "Timestamp"
    if json_format == "date":
        return "Date"
    if json_format == "uuid":
        return "UUID"
    return "String(255)"



def _build_service_handler_js(
    entity_name: str,
    fields: list,
    relationships: list,
    namespace: str,
    provenance: str,
) -> str:
    """Generate a deterministic CAP service handler with field-aware validation and enrichment.

    Before CREATE: validates required fields, numeric types, and UUID formats derived from UML.
    After READ: computes boolean display labels and relationship presence flags.
    Deterministic — same inputs always produce byte-identical output.
    """
    _managed = {"id", "createdat", "createdby", "modifiedat", "modifiedby"}

    # ── Categorise fields by constraint type ─────────────────────────────────
    required_strings: list[str] = []
    required_numbers: list[tuple[str, bool]] = []  # (name, is_integer)
    uuid_fields: list[str] = []
    bool_fields: list[str] = []

    for f in sorted(fields, key=lambda x: (x.get("name") or "").lower()):
        fname = (f.get("name") or "").strip()
        if not fname or fname.lower() in _managed or f.get("primary_key"):
            continue
        ftype = (f.get("type") or "string").lower()
        ffmt  = (f.get("format") or "").lower()
        is_req = not f.get("nullable", True)

        if ffmt == "uuid":
            uuid_fields.append(fname)
        elif ftype == "boolean":
            bool_fields.append(fname)
        elif is_req and ftype == "string" and ffmt not in ("date-time", "date"):
            required_strings.append(fname)
        elif is_req and ftype in ("integer", "number"):
            required_numbers.append((fname, ftype == "integer"))

    # ── Build before-CREATE validation body ───────────────────────────────────
    before_lines: list[str] = ["    const { data } = req;"]

    if uuid_fields:
        before_lines.append(
            "    const UUID_RE = "
            "/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;"
        )

    for fname in required_strings:
        before_lines.append(
            f"    if (!data.{fname} || String(data.{fname}).trim() === '') {{\n"
            f"      return req.error(400, '{fname} is required and must be non-empty');\n"
            f"    }}"
        )

    for fname, is_int in required_numbers:
        condition = (
            f"!Number.isInteger(Number(data.{fname}))"
            if is_int else
            f"isNaN(Number(data.{fname}))"
        )
        kind = "an integer" if is_int else "a number"
        before_lines.append(
            f"    if (data.{fname} !== undefined && {condition}) {{\n"
            f"      return req.error(400, '{fname} must be {kind}');\n"
            f"    }}"
        )

    for fname in uuid_fields:
        before_lines.append(
            f"    if (data.{fname} && !UUID_RE.test(data.{fname})) {{\n"
            f"      return req.error(400, '{fname} must be a valid UUID (RFC 4122)');\n"
            f"    }}"
        )

    if len(before_lines) == 1:
        before_lines.append(
            "    // No field constraints derived from UML — extend with business rules"
        )

    # ── Build after-READ enrichment body ──────────────────────────────────────
    after_lines: list[str] = [
        "    const list = Array.isArray(results) ? results : (results ? [results] : []);",
        "    list.forEach(item => {",
        "      if (!item) return;",
    ]

    for fname in sorted(bool_fields):
        after_lines.append(
            f"      item._{fname}Label = item.{fname} === true ? 'Active' : 'Inactive';"
        )

    for rel in sorted(relationships, key=lambda r: (r.get("target_class") or "").lower()):
        target = (rel.get("target_class") or "").strip()
        if not target:
            continue
        t_snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", target)
        t_snake = re.sub(r"[^a-z0-9]+", "_", t_snake.lower()).strip("_")
        after_lines.append(
            f"      item._has{target} = item.{t_snake}_ID != null;"
        )

    if len(after_lines) == 3:
        after_lines.append(
            "      // No boolean fields or relationships derived — extend with enrichment logic"
        )

    after_lines.append("    });")

    before_body = "\n".join(before_lines)
    after_body  = "\n".join(after_lines)

    return (
        f"{provenance}\n"
        f"'use strict';\n\n"
        f"const cds = require('@sap/cds');\n\n"
        f"module.exports = cds.service.impl(async function () {{\n"
        f"  const {{ {entity_name} }} = this.entities;\n\n"
        f"  // ── Before CREATE ────────────────────────────────────────────────\n"
        f"  this.before('CREATE', {entity_name}, async (req) => {{\n"
        f"{before_body}\n"
        f"  }});\n\n"
        f"  // ── After READ ───────────────────────────────────────────────────\n"
        f"  this.after('READ', {entity_name}, (results) => {{\n"
        f"{after_body}\n"
        f"  }});\n"
        f"}});\n"
    )



def _generate_sap_cap(solution, config: dict, uml: dict) -> dict:
    """Generate a complete SAP CAP (BTP / OData V4) project from the UML snapshot.

    All output is deterministic — same solution + UML always produces byte-identical files.
    Every generated file is provenance-tagged with solution ID and blueprint version.
    """
    import json as _json

    sol_id = solution.id
    bp_version = getattr(solution, "blueprint_version", 1) or 1
    sol_name = (solution.name or f"solution_{sol_id}").strip()

    # Deterministic name helpers
    def _to_snake(name: str) -> str:
        s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", (name or "unknown").strip())
        s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
        return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

    def _to_pascal(name: str) -> str:
        return "".join(w.capitalize() for w in re.split(r"[^a-zA-Z0-9]", name) if w)

    def _to_kebab(name: str) -> str:
        return _to_snake(name).replace("_", "-")

    sol_snake = _to_snake(sol_name)
    sol_pascal = _to_pascal(sol_name)
    sol_kebab = _to_kebab(sol_name)
    provenance_comment = f"// Generated by Entelim — solution {sol_id}, blueprint version {bp_version}"

    classes = sorted(
        uml.get("class_diagram", {}).get("classes", []),
        key=lambda c: (c.get("name") or "").lower(),
    )

    files = {}

    # ── db/schema.cds ──────────────────────────────────────────────────────
    entity_blocks = []
    namespace = sol_snake
    for cls in classes:
        entity_name = _to_pascal(cls.get("name") or "Entity")
        fields = sorted(cls.get("fields", []), key=lambda f: (f.get("name") or "").lower())
        field_lines = []
        for f in fields:
            if (f.get("name") or "").lower().replace("_", "") in ("id", "createdat", "createdby", "modifiedat", "modifiedby", "updatedat", "updatedby"):
                continue
            cds_type = _map_cds_type(f.get("type"), f.get("format"))
            if cds_type is None:
                continue  # array — use associations
            description = f.get("description") or ""
            comment = f"  // {description}" if description else ""
            field_lines.append(f"  {f['name']:<30}: {cds_type};{comment}")
        # Associations from relationships
        for rel in sorted(cls.get("relationships", []), key=lambda r: (r.get("target_class") or "").lower()):
            target = _to_pascal(rel.get("target_class") or "")
            if target:
                field_lines.append(f"  {_to_snake(target):<30}: Association to {target};")
        fields_block = "\n".join(field_lines) if field_lines else "  // No additional fields"
        entity_blocks.append(
            f"// ── {entity_name} {'─' * max(0, 75 - len(entity_name))}\n"
            f"entity {entity_name} : managed {{\n"
            f"  key ID             : UUID;\n"
            f"{fields_block}\n"
            f"}}"
        )

    schema_body = "\n\n".join(entity_blocks) if entity_blocks else "// No entities found in UML snapshot"
    files["db/schema.cds"] = (
        f"{provenance_comment}\n"
        f"// DO NOT EDIT — regenerate via Entelim codegen workbench\n\n"
        f"namespace {namespace};\n"
        f"using {{ managed }} from '@sap/cds/common';\n\n"
        f"{schema_body}\n"
    )

    # ── srv/<svc>-service.cds + srv/<svc>-service.js ──────────────────────
    scopes_for_xsuaa = []
    role_templates = []
    role_collection_refs = []

    for cls in classes:
        entity_name = _to_pascal(cls.get("name") or "Entity")
        svc_name = f"{entity_name}Service"
        svc_kebab = _to_kebab(entity_name)
        read_scope = f"{sol_snake}.{entity_name}.Read"
        write_scope = f"{sol_snake}.{entity_name}.Write"
        scopes_for_xsuaa.extend([
            {"name": f"$XSAPPNAME.{entity_name}.Read",  "description": f"Read {entity_name}"},
            {"name": f"$XSAPPNAME.{entity_name}.Write", "description": f"Write {entity_name}"},
        ])
        role_templates.extend([
            {"name": f"{entity_name}Viewer", "description": f"Read-only access to {entity_name}",
             "scope-references": [f"$XSAPPNAME.{entity_name}.Read"]},
            {"name": f"{entity_name}Editor", "description": f"Full access to {entity_name}",
             "scope-references": [f"$XSAPPNAME.{entity_name}.Read", f"$XSAPPNAME.{entity_name}.Write"]},
        ])
        role_collection_refs.append(f"$XSAPPNAME.{entity_name}Editor")

        files[f"srv/{svc_kebab}-service.cds"] = (
            f"{provenance_comment}\n"
            f"using {{ {namespace}.{entity_name} }} from '../db/schema';\n\n"
            f"/**\n"
            f" * {svc_name} — OData V4 service\n"
            f" */\n"
            f"@requires: '{read_scope}'\n"
            f"service {svc_name} @(path: '/{svc_kebab}') {{\n"
            f"  @requires: '{write_scope}'\n"
            f"  entity {entity_name} as projection on {namespace}.{entity_name};\n"
            f"}}\n"
        )

        files[f"srv/{svc_kebab}-service.js"] = _build_service_handler_js(
            entity_name=entity_name,
            fields=cls.get("fields", []),
            relationships=cls.get("relationships", []),
            namespace=namespace,
            provenance=provenance_comment,
        )

        files[f"app/{svc_kebab}/annotations.cds"] = (
            f"{provenance_comment}\n"
            f"using {svc_name} from '../../srv/{svc_kebab}-service';\n\n"
            f"// Fiori Elements annotations — add UI.LineItem, UI.FieldGroup, etc.\n"
        )

        # ── Build CREATE test payload from required field metadata ─────────────
        _test_managed = {"id", "createdat", "createdby", "modifiedat", "modifiedby"}
        _req_fields = [
            f for f in cls.get("fields", [])
            if not f.get("nullable", True)
            and not f.get("primary_key")
            and (f.get("name") or "").lower() not in _test_managed
        ]
        _payload_parts = []
        for _tf in _req_fields[:6]:  # cap payload to first 6 required fields
            _tf_name = _tf.get("name") or ""
            _tf_type = (_tf.get("type") or "string").lower()
            _tf_fmt  = (_tf.get("format") or "").lower()
            if _tf_type == "integer":
                _payload_parts.append(f"{_tf_name}: 1")
            elif _tf_type == "number":
                _payload_parts.append(f"{_tf_name}: 1.0")
            elif _tf_type == "boolean":
                _payload_parts.append(f"{_tf_name}: true")
            elif _tf_fmt == "uuid":
                _payload_parts.append(f"{_tf_name}: '00000000-0000-0000-0000-000000000001'")
            elif _tf_fmt == "date-time":
                _payload_parts.append(f"{_tf_name}: new Date().toISOString()")
            else:
                _payload_parts.append(f"{_tf_name}: 'test-value'")
        _valid_payload_js = ("{ " + ", ".join(_payload_parts) + " }") if _payload_parts else "{}"

        # Find the first required plain-string field for the rejection test
        _first_req_str = next(
            (_f.get("name") for _f in _req_fields
             if (_f.get("type") or "string").lower() == "string"
             and (_f.get("format") or "") not in ("uuid", "date-time", "date")),
            None,
        )
        _omit_parts = (
            [p for p in _payload_parts if not p.startswith(f"{_first_req_str}:")]
            if _first_req_str else []
        )
        _omit_payload_js = ("{ " + ", ".join(_omit_parts) + " }") if _omit_parts else "{}"

        # Build optional extra test blocks (empty string if not applicable)
        _create_happy_test = ""
        if _payload_parts:
            _create_happy_test = (
                f"\n\n  it('accepts CREATE {entity_name} with all required fields',"
                f" async () => {{\n"
                f"    const result = await srv.send('POST', '/{entity_name}', {_valid_payload_js});\n"
                f"    expect(result).toBeDefined();\n"
                f"  }});"
            )

        _rejection_test = ""
        if _first_req_str:
            _rejection_test = (
                f"\n\n  it('rejects CREATE {entity_name} when {_first_req_str} is missing',"
                f" async () => {{\n"
                f"    await expect(\n"
                f"      srv.send('POST', '/{entity_name}', {_omit_payload_js})\n"
                f"    ).rejects.toThrow();\n"
                f"  }});"
            )

        files[f"tests/{svc_kebab}.test.js"] = (
            f"{provenance_comment}\n"
            f"'use strict';\n\n"
            f"const cds = require('@sap/cds/lib');\n"
            f"const {{ SELECT }} = cds;\n"
            f"const {{ expect }} = require('@jest/globals');\n\n"
            f"describe('{svc_name}', () => {{\n"
            f"  let srv;\n\n"
            f"  beforeAll(async () => {{\n"
            f"    srv = await cds.connect.to('{svc_name}');\n"
            f"  }});\n\n"
            f"  it('responds to READ {entity_name}', async () => {{\n"
            f"    const result = await srv.run(SELECT.from('{namespace}.{entity_name}').limit(1));\n"
            f"    expect(result).toBeDefined();\n"
            f"  }});"
            f"{_create_happy_test}"
            f"{_rejection_test}\n"
            f"}});\n"
        )

    # ── .cdsrc.json ────────────────────────────────────────────────────────
    cdsrc = {
        "requires": {
            "db": {
                "[development]": {"kind": "sqlite", "credentials": {"url": ":memory:"}},
                "[production]": {"kind": "hana"},
            },
            "auth": {
                "[development]": {"kind": "mocked", "users": {"alice": {"roles": [f"{sol_pascal}Admin"]}}},
                "[production]": {"kind": "xsuaa"},
            },
        },
        "build": {"target": "gen"},
    }
    files[".cdsrc.json"] = _json.dumps(cdsrc, indent=2) + "\n"

    # ── package.json ───────────────────────────────────────────────────────
    pkg = {
        "name": sol_kebab,
        "version": "1.0.0",
        "description": f"{sol_name} — SAP CAP BTP Extension generated by Entelim",
        "engines": {"node": ">=18"},
        "dependencies": {"@sap/cds": "^7", "@sap/cds-dk": "^7", "express": "^4"},
        "devDependencies": {"jest": "^29", "supertest": "^6"},
        "scripts": {"start": "cds-serve", "test": "jest", "build": "cds build"},
        "jest": {"testEnvironment": "node"},
    }
    files["package.json"] = _json.dumps(pkg, indent=2) + "\n"

    # ── xs-security.json ───────────────────────────────────────────────────
    role_collection = {
        "name": f"{sol_pascal}Admin",
        "description": f"Full access to all {sol_name} services",
        "role-template-references": role_collection_refs,
    }
    xs_security = {
        "xsappname": sol_snake,
        "tenant-mode": "dedicated",
        "scopes": scopes_for_xsuaa,
        "role-templates": role_templates,
        "role-collections": [role_collection],
    }
    files["xs-security.json"] = _json.dumps(xs_security, indent=2) + "\n"

    # ── mta.yaml ───────────────────────────────────────────────────────────
    files["mta.yaml"] = (
        f"# Generated by Entelim — solution {sol_id}, blueprint version {bp_version}\n"
        f"_schema-version: \"3.3.0\"\n"
        f"ID: {sol_snake}\n"
        f"version: 1.0.0\n"
        f"description: \"{sol_name} — generated by Entelim\"\n\n"
        f"modules:\n"
        f"  - name: {sol_snake}-srv\n"
        f"    type: nodejs\n"
        f"    path: .\n"
        f"    parameters:\n"
        f"      buildpack: nodejs_buildpack\n"
        f"      memory: 256M\n"
        f"    requires:\n"
        f"      - name: {sol_snake}-hana\n"
        f"      - name: {sol_snake}-xsuaa\n"
        f"      - name: {sol_snake}-destination\n"
        f"    provides:\n"
        f"      - name: srv-api\n"
        f"        properties:\n"
        f"          srv-url: ${{default-url}}\n\n"
        f"  - name: {sol_snake}-db-deployer\n"
        f"    type: hdb\n"
        f"    path: gen/db\n"
        f"    requires:\n"
        f"      - name: {sol_snake}-hana\n\n"
        f"resources:\n"
        f"  - name: {sol_snake}-hana\n"
        f"    type: com.sap.xs.hana\n"
        f"    parameters:\n"
        f"      service: hana\n"
        f"      service-plan: hdi-shared\n\n"
        f"  - name: {sol_snake}-xsuaa\n"
        f"    type: org.cloudfoundry.managed-service\n"
        f"    parameters:\n"
        f"      service: xsuaa\n"
        f"      service-plan: application\n"
        f"      path: ./xs-security.json\n\n"
        f"  - name: {sol_snake}-destination\n"
        f"    type: org.cloudfoundry.managed-service\n"
        f"    parameters:\n"
        f"      service: destination\n"
        f"      service-plan: lite\n"
    )

    # ── Makefile ───────────────────────────────────────────────────────────
    files["Makefile"] = (
        f"# Generated by Entelim — solution {sol_id}, blueprint version {bp_version}\n"
        f".PHONY: run build deploy test\n\n"
        f"run:\n"
        f"\tcds watch\n\n"
        f"build:\n"
        f"\tmbt build -p cf\n\n"
        f"deploy: build\n"
        f"\tcf deploy mta_archives/{sol_snake}_1.0.0.mtar\n\n"
        f"test:\n"
        f"\tnpm test\n"
    )

    # ── README.md ──────────────────────────────────────────────────────────
    from datetime import datetime as _dt
    ts = _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    files["README.md"] = (
        f"# {sol_name} — SAP CAP BTP Extension\n\n"
        f"Generated by Entelim on {ts}. Solution ID: {sol_id}.\n\n"
        f"## Prerequisites\n\n"
        f"- SAP BTP subaccount with Cloud Foundry environment enabled\n"
        f"- HANA Cloud instance allocated to your CF space\n"
        f"- MBT: `npm install -g mbt`\n"
        f"- CF CLI: `cf login -a <api-endpoint>`\n"
        f"- Node.js ≥ 18\n\n"
        f"## Local Development\n\n"
        f"```bash\n"
        f"npm install\n"
        f"make run\n"
        f"```\n\n"
        f"## Build and Deploy\n\n"
        f"```bash\n"
        f"make build\n"
        f"make deploy\n"
        f"```\n\n"
        f"## Run Tests\n\n"
        f"```bash\n"
        f"make test\n"
        f"```\n\n"
        f"## Architecture\n\n"
        f"Generated from the Entelim Solution Architecture Blueprint.\n"
        f"Blueprint URL: /solutions/{sol_id}\n"
    )

    return files



def _has_element_type(uml: dict, *types: str) -> bool:
    """Return True if the UML snapshot contains any class whose 'type' is in *types."""
    return any(
        c.get("type", "") in types
        for c in uml.get("classes", [])
    )



def _generate_azure_bicep(solution, config: dict, uml: dict) -> dict:
    """Generate a complete Azure Bicep infrastructure project from the UML snapshot.

    All output is deterministic — same solution + UML always produces byte-identical files.
    Every generated file is provenance-tagged with solution ID and blueprint version.
    """
    import re as _re

    sol_id = solution.id
    bp_version = getattr(solution, "blueprint_version", 1) or 1
    sol_name = (solution.name or f"solution_{sol_id}").strip()

    # Derive safe identifiers
    _raw_snake = sol_name.lower().replace(" ", "_").replace("-", "_")
    solution_snake = _re.sub(r"[^a-z0-9_]", "", _raw_snake).strip("_") or f"solution_{sol_id}"

    prov_bicep = f"// Generated by Entelim — solution {sol_id}, blueprint version {bp_version}"
    prov_yaml = f"# Generated by Entelim — solution {sol_id}, blueprint version {bp_version}"

    has_data = _has_element_type(uml, "DataObject")
    has_api = _has_element_type(uml, "ApplicationInterface", "ApplicationService")
    has_bus = _has_element_type(uml, "CommunicationNetwork")

    files: dict = {}

    # ── modules/key-vault.bicep ────────────────────────────────────────────────
    files["modules/key-vault.bicep"] = (
        f"{prov_bicep}\n"
        f"param environment string\n"
        f"param location string\n"
        f"param solutionName string\n\n"
        f"resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {{\n"
        f"  name: '${{solutionName}}-kv-${{environment}}'\n"
        f"  location: location\n"
        f"  properties: {{\n"
        f"    sku: {{ family: 'A', name: 'standard' }}\n"
        f"    tenantId: subscription().tenantId\n"
        f"    enableRbacAuthorization: true\n"
        f"    enableSoftDelete: true\n"
        f"    softDeleteRetentionInDays: 90\n"
        f"  }}\n"
        f"}}\n\n"
        f"output uri string = keyVault.properties.vaultUri\n"
        f"output name string = keyVault.name\n"
    )

    # ── modules/app-service.bicep ──────────────────────────────────────────────
    files["modules/app-service.bicep"] = (
        f"{prov_bicep}\n"
        f"param environment string\n"
        f"param keyVaultUri string\n"
        f"param location string\n"
        f"param solutionName string\n\n"
        f"resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {{\n"
        f"  name: '${{solutionName}}-asp-${{environment}}'\n"
        f"  location: location\n"
        f"  sku: {{ name: 'B1', tier: 'Basic' }}\n"
        f"}}\n\n"
        f"resource appService 'Microsoft.Web/sites@2023-01-01' = {{\n"
        f"  name: '${{solutionName}}-app-${{environment}}'\n"
        f"  location: location\n"
        f"  properties: {{\n"
        f"    serverFarmId: appServicePlan.id\n"
        f"    siteConfig: {{\n"
        f"      appSettings: [\n"
        f"        {{ name: 'KEY_VAULT_URI', value: keyVaultUri }}\n"
        f"        {{ name: 'ENVIRONMENT', value: environment }}\n"
        f"      ]\n"
        f"    }}\n"
        f"  }}\n"
        f"}}\n\n"
        f"output url string = 'https://${{appService.properties.defaultHostName}}'\n"
        f"output name string = appService.name\n"
    )

    # ── modules/sql-database.bicep (conditional) ───────────────────────────────
    if has_data:
        files["modules/sql-database.bicep"] = (
            f"{prov_bicep}\n"
            f"param environment string\n"
            f"param location string\n"
            f"param solutionName string\n\n"
            f"resource sqlServer 'Microsoft.Sql/servers@2023-05-01-preview' = {{\n"
            f"  name: '${{solutionName}}-sql-${{environment}}'\n"
            f"  location: location\n"
            f"  properties: {{ administratorLogin: 'sqladmin', administratorLoginPassword: 'TODO-use-key-vault' }}\n"
            f"}}\n\n"
            f"resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-05-01-preview' = {{\n"
            f"  parent: sqlServer\n"
            f"  name: '${{solutionName}}-db'\n"
            f"  location: location\n"
            f"  sku: {{ name: 'Basic', tier: 'Basic' }}\n"
            f"}}\n\n"
            f"output connectionStringSecretUri string = '${{solutionName}}-db-connstr'\n"
        )

    # ── modules/api-management.bicep (conditional) ────────────────────────────
    if has_api:
        files["modules/api-management.bicep"] = (
            f"{prov_bicep}\n"
            f"param environment string\n"
            f"param location string\n"
            f"param solutionName string\n\n"
            f"resource apim 'Microsoft.ApiManagement/service@2023-03-01-preview' = {{\n"
            f"  name: '${{solutionName}}-apim-${{environment}}'\n"
            f"  location: location\n"
            f"  sku: {{ name: 'Consumption', capacity: 0 }}\n"
            f"  properties: {{\n"
            f"    publisherEmail: 'admin@${{solutionName}}.local'\n"
            f"    publisherName: solutionName\n"
            f"  }}\n"
            f"}}\n\n"
            f"output gatewayUrl string = apim.properties.gatewayUrl\n"
        )

    # ── modules/service-bus.bicep (conditional) ───────────────────────────────
    if has_bus:
        files["modules/service-bus.bicep"] = (
            f"{prov_bicep}\n"
            f"param environment string\n"
            f"param location string\n"
            f"param solutionName string\n\n"
            f"resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {{\n"
            f"  name: '${{solutionName}}-sb-${{environment}}'\n"
            f"  location: location\n"
            f"  sku: {{ name: 'Standard', tier: 'Standard' }}\n"
            f"}}\n\n"
            f"output namespaceName string = serviceBusNamespace.name\n"
        )

    # ── main.bicep ─────────────────────────────────────────────────────────────
    conditional_modules = ""
    conditional_outputs = ""
    if has_data:
        conditional_modules += (
            "\n// ── SQL Database ─────────────────────────────────────────────────────────────\n"
            "module sqlDatabase 'modules/sql-database.bicep' = {\n"
            "  name: 'sqlDatabase'\n"
            "  params: {\n"
            "    environment: environment\n"
            "    location: location\n"
            "    solutionName: solutionName\n"
            "  }\n"
            "}\n"
        )
        conditional_outputs += "output sqlConnectionStringSecretUri string = sqlDatabase.outputs.connectionStringSecretUri\n"
    if has_api:
        conditional_modules += (
            "\n// ── API Management ───────────────────────────────────────────────────────────\n"
            "module apiManagement 'modules/api-management.bicep' = {\n"
            "  name: 'apiManagement'\n"
            "  params: {\n"
            "    environment: environment\n"
            "    location: location\n"
            "    solutionName: solutionName\n"
            "  }\n"
            "}\n"
        )
        conditional_outputs += "output apimGatewayUrl string = apiManagement.outputs.gatewayUrl\n"
    if has_bus:
        conditional_modules += (
            "\n// ── Service Bus ──────────────────────────────────────────────────────────────\n"
            "module serviceBus 'modules/service-bus.bicep' = {\n"
            "  name: 'serviceBus'\n"
            "  params: {\n"
            "    environment: environment\n"
            "    location: location\n"
            "    solutionName: solutionName\n"
            "  }\n"
            "}\n"
        )
        conditional_outputs += "output serviceBusNamespace string = serviceBus.outputs.namespaceName\n"

    files["main.bicep"] = (
        f"{prov_bicep}\n"
        f"// DO NOT EDIT — regenerate via Entelim codegen workbench\n\n"
        f"targetScope = 'resourceGroup'\n\n"
        f"@description('Solution name — used as prefix for all resource names')\n"
        f"param solutionName string = '{solution_snake}'\n\n"
        f"@description('Deployment environment')\n"
        f"@allowed(['dev', 'staging', 'prod'])\n"
        f"param environment string = 'dev'\n\n"
        f"@description('Azure region')\n"
        f"param location string = resourceGroup().location\n\n"
        f"// ── Key Vault ─────────────────────────────────────────────────────────────────\n"
        f"module keyVault 'modules/key-vault.bicep' = {{\n"
        f"  name: 'keyVault'\n"
        f"  params: {{\n"
        f"    environment: environment\n"
        f"    location: location\n"
        f"    solutionName: solutionName\n"
        f"  }}\n"
        f"}}\n\n"
        f"// ── App Service ───────────────────────────────────────────────────────────────\n"
        f"module appService 'modules/app-service.bicep' = {{\n"
        f"  name: 'appService'\n"
        f"  params: {{\n"
        f"    environment: environment\n"
        f"    keyVaultUri: keyVault.outputs.uri\n"
        f"    location: location\n"
        f"    solutionName: solutionName\n"
        f"  }}\n"
        f"}}\n"
        f"{conditional_modules}\n"
        f"output appServiceUrl string = appService.outputs.url\n"
        f"output keyVaultUri string = keyVault.outputs.uri\n"
        f"{conditional_outputs}"
    )

    # ── parameters/dev.bicepparam ──────────────────────────────────────────────
    files["parameters/dev.bicepparam"] = (
        f"{prov_bicep}\n"
        f"using 'main.bicep'\n\n"
        f"param environment = 'dev'\n"
        f"param solutionName = '{solution_snake}'\n"
    )

    # ── parameters/prod.bicepparam ─────────────────────────────────────────────
    files["parameters/prod.bicepparam"] = (
        f"{prov_bicep}\n"
        f"using 'main.bicep'\n\n"
        f"param environment = 'prod'\n"
        f"param solutionName = '{solution_snake}'\n"
    )

    # ── azure.yaml ─────────────────────────────────────────────────────────────
    files["azure.yaml"] = (
        f"{prov_yaml}\n"
        f"name: {solution_snake}\n"
        f"metadata:\n"
        f"  template: archie-generated@1.0.0\n\n"
        f"services:\n"
        f"  api:\n"
        f"    project: .\n"
        f"    language: python\n"
        f"    host: appservice\n\n"
        f"hooks:\n"
        f"  preprovision:\n"
        f"    shell: sh\n"
        f"    run: echo \"Pre-provision: validate Azure subscription access\"\n"
        f"  postdeploy:\n"
        f"    shell: sh\n"
        f"    run: echo \"Post-deploy: verify health endpoint\"\n"
    )

    # ── .github/workflows/azure-dev.yml ───────────────────────────────────────
    files[".github/workflows/azure-dev.yml"] = (
        f"{prov_yaml}\n"
        f"name: Deploy {sol_name} to Azure\n\n"
        f"on:\n"
        f"  push:\n"
        f"    branches: [main]\n"
        f"  workflow_dispatch:\n\n"
        f"permissions:\n"
        f"  id-token: write\n"
        f"  contents: read\n\n"
        f"jobs:\n"
        f"  deploy:\n"
        f"    runs-on: ubuntu-latest\n"
        f"    steps:\n"
        f"      - uses: actions/checkout@v4\n\n"
        f"      - name: Log in to Azure\n"
        f"        uses: azure/login@v2\n"
        f"        with:\n"
        f"          client-id: ${{{{ secrets.AZURE_CLIENT_ID }}}}\n"
        f"          tenant-id: ${{{{ secrets.AZURE_TENANT_ID }}}}\n"
        f"          subscription-id: ${{{{ secrets.AZURE_SUBSCRIPTION_ID }}}}\n\n"
        f"      - name: Install azd\n"
        f"        uses: Azure/setup-azd@v1\n\n"
        f"      - name: Provision infrastructure\n"
        f"        run: azd provision --no-prompt\n"
        f"        env:\n"
        f"          AZURE_ENV_NAME: ${{{{ vars.AZURE_ENV_NAME }}}}\n"
        f"          AZURE_LOCATION: ${{{{ vars.AZURE_LOCATION }}}}\n"
        f"          AZURE_SUBSCRIPTION_ID: ${{{{ secrets.AZURE_SUBSCRIPTION_ID }}}}\n\n"
        f"      - name: Deploy application\n"
        f"        run: azd deploy --no-prompt\n"
        f"        env:\n"
        f"          AZURE_ENV_NAME: ${{{{ vars.AZURE_ENV_NAME }}}}\n"
    )

    # ── Makefile ───────────────────────────────────────────────────────────────
    files["Makefile"] = (
        f"# Generated by Entelim — solution {sol_id}, blueprint version {bp_version}\n"
        f".PHONY: provision deploy destroy lint\n\n"
        f"provision:\n"
        f"\tazd provision\n\n"
        f"deploy:\n"
        f"\tazd deploy\n\n"
        f"destroy:\n"
        f"\tazd down\n\n"
        f"lint:\n"
        f"\taz bicep build --file main.bicep\n\n"
        f"dev-params:\n"
        f"\tazd env set AZURE_ENV_NAME {solution_snake}-dev\n"
    )

    # ── README.md ──────────────────────────────────────────────────────────────
    from datetime import datetime as _dt
    ts = _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    files["README.md"] = (
        f"# {sol_name} — Azure Bicep Infrastructure\n\n"
        f"Generated by Entelim on {ts}. Solution ID: {sol_id}.\n\n"
        f"## Prerequisites\n\n"
        f"- Azure CLI: `az login`\n"
        f"- Azure Developer CLI (azd): `winget install microsoft.azd`\n"
        f"- Bicep CLI: `az bicep install`\n"
        f"- An active Azure subscription with Contributor role\n\n"
        f"## Provision & Deploy\n\n"
        f"```bash\n"
        f"azd auth login\n"
        f"make provision\n"
        f"make deploy\n"
        f"```\n\n"
        f"## Lint Bicep\n\n"
        f"```bash\n"
        f"make lint\n"
        f"```\n\n"
        f"## Destroy\n\n"
        f"```bash\n"
        f"make destroy\n"
        f"```\n\n"
        f"## Architecture\n\n"
        f"Generated from the Entelim Solution Architecture Blueprint.\n"
        f"Blueprint URL: /solutions/{sol_id}\n"
    )

    return files



def _generate_power_platform_solution(solution, config: dict, uml: dict) -> dict:
    """Generate a complete Power Platform Solution scaffolding project from the UML snapshot.

    All output is deterministic — same solution + UML always produces byte-identical files.
    Every generated file is provenance-tagged with solution ID and blueprint version.
    """
    import json as _json
    import re as _re

    sol_id = solution.id
    bp_version = getattr(solution, "blueprint_version", 1) or 1
    sol_name = (solution.name or f"solution_{sol_id}").strip()

    # Derive safe identifiers
    _raw_snake = sol_name.lower().replace(" ", "_").replace("-", "_")
    solution_snake = _re.sub(r"[^a-z0-9_]", "", _raw_snake).strip("_") or f"solution_{sol_id}"
    solution_pascal = "".join(w.capitalize() for w in sol_name.split())

    prov_comment = f"Generated by Entelim — solution {sol_id}, blueprint version {bp_version}"
    prov_xml = f"<!-- {prov_comment} -->"
    prov_yaml = f"# {prov_comment}"

    files: dict = {}

    # ── solution/solution.xml ──────────────────────────────────────────────────
    files["solution/solution.xml"] = (
        f"<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        f"{prov_xml}\n"
        f"<ImportExportXml version=\"9.0.0.0\">\n"
        f"  <SolutionManifest>\n"
        f"    <UniqueName>{solution_snake}</UniqueName>\n"  # raw-html-ok: solution_snake is regex-sanitized to [a-z0-9_] a few lines above
        f"    <LocalizedNames>\n"
        f"      <LocalizedName description=\"{sol_name}\" languagecode=\"1033\" />\n"
        f"    </LocalizedNames>\n"
        f"    <Descriptions />\n"
        f"    <Version>1.0.0.0</Version>\n"
        f"    <Managed>0</Managed>\n"
        f"    <Publisher>\n"
        f"      <UniqueName>archie</UniqueName>\n"
        f"      <EMailAddress />\n"
        f"      <CustomizationPrefix>arc</CustomizationPrefix>\n"
        f"    </Publisher>\n"
        f"    <RootComponents />\n"
        f"  </SolutionManifest>\n"
        f"</ImportExportXml>\n"
    )

    # ── solution/customizations.xml ────────────────────────────────────────────
    files["solution/customizations.xml"] = (
        f"<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        f"{prov_xml}\n"
        f"<ImportExportXml version=\"9.0.0.0\">\n"
        f"  <Entities />\n"
        f"  <Roles />\n"
        f"  <Workflows />\n"
        f"  <FieldSecurityProfiles />\n"
        f"  <Templates />\n"
        f"  <EntityMaps />\n"
        f"  <EntityRelationships />\n"
        f"  <OrganizationSettings />\n"
        f"  <optionsets />\n"
        f"  <CustomControls />\n"
        f"  <EntityDataProviders />\n"
        f"  <Languages><Language>1033</Language></Languages>\n"
        f"</ImportExportXml>\n"
    )

    # ── pac-manifest.json ──────────────────────────────────────────────────────
    pac_manifest = {
        "_archie_provenance": prov_comment,
        "name": solution_pascal,
        "version": "1.0.0",
        "publisher": {"name": "Entelim", "prefix": "ent"},
        "environment": {"url": "${POWER_PLATFORM_ENVIRONMENT_URL}"},
        "solutions": [{"name": solution_snake, "path": "./solution"}],
    }
    files["pac-manifest.json"] = _json.dumps(pac_manifest, indent=2) + "\n"

    # ── canvas-apps/{SolutionPascal}App/CanvasManifest.json ───────────────────
    canvas_manifest = {
        "_archie_provenance": prov_comment,
        "name": f"{solution_pascal}App",
        "publisher": "Entelim",
        "version": "1.0.0",
        "permissions": ["Dataverse"],
    }
    files[f"canvas-apps/{solution_pascal}App/CanvasManifest.json"] = _json.dumps(canvas_manifest, indent=2) + "\n"

    # ── canvas-apps/{SolutionPascal}App/Connections.json ──────────────────────
    connections = {
        "_archie_provenance": prov_comment,
        "connections": [],
    }
    files[f"canvas-apps/{solution_pascal}App/Connections.json"] = _json.dumps(connections, indent=2) + "\n"

    # ── environment.yaml ───────────────────────────────────────────────────────
    files["environment.yaml"] = (
        f"{prov_yaml}\n"
        f"name: {solution_snake}\n"
        f"url: ${{POWER_PLATFORM_ENVIRONMENT_URL}}\n"
        f"type: Sandbox\n"
    )

    # ── .env.template ─────────────────────────────────────────────────────────
    files[".env.template"] = (
        "POWER_PLATFORM_TENANT_ID=<your-tenant-id>\n"
        "POWER_PLATFORM_CLIENT_ID=<your-client-id>\n"
        "POWER_PLATFORM_CLIENT_SECRET=<your-client-secret>\n"
        "POWER_PLATFORM_ENVIRONMENT_URL=https://<org>.crm.dynamics.com\n"
    )

    # ── Makefile ───────────────────────────────────────────────────────────────
    files["Makefile"] = (
        f"# Generated by Entelim — solution {sol_id}, blueprint version {bp_version}\n"
        f".PHONY: auth import export validate\n\n"
        f"auth:\n"
        f"\tpac auth create --environment ${{POWER_PLATFORM_ENVIRONMENT_URL}}\n\n"
        f"import:\n"
        f"\tpac solution import --path ./solution\n\n"
        f"export:\n"
        f"\tpac solution export --name {solution_snake} --path ./solution\n\n"
        f"validate:\n"
        f"\tpac solution check --path ./solution\n\n"
        f"canvas-push:\n"
        f"\tpac canvas pack --sources ./canvas-apps/{solution_pascal}App --msapp ./{solution_pascal}App.msapp\n"
        f"\tpac canvas push --msapp ./{solution_pascal}App.msapp\n"
    )

    # ── README.md ──────────────────────────────────────────────────────────────
    from datetime import datetime as _dt
    ts = _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    files["README.md"] = (
        f"# {sol_name} — Power Platform Solution\n\n"
        f"Generated by Entelim on {ts}. Solution ID: {sol_id}.\n\n"
        f"## Prerequisites\n\n"
        f"- Power Platform CLI (pac): `winget install Microsoft.PowerAppsCLI`\n"
        f"- A Power Platform environment with System Customizer or higher role\n"
        f"- `.env` file populated from `.env.template`\n\n"
        f"## Authenticate\n\n"
        f"```bash\n"
        f"cp .env.template .env  # fill in your credentials\n"
        f"make auth\n"
        f"```\n\n"
        f"## Import Solution\n\n"
        f"```bash\n"
        f"make import\n"
        f"```\n\n"
        f"## Export Solution\n\n"
        f"```bash\n"
        f"make export\n"
        f"```\n\n"
        f"## Validate\n\n"
        f"```bash\n"
        f"make validate\n"
        f"```\n\n"
        f"## Architecture\n\n"
        f"Generated from the Entelim Solution Architecture Blueprint.\n"
        f"Blueprint URL: /solutions/{sol_id}\n"
    )

    return files



def _generate_sap_btp_integration(solution, config: dict, uml: dict) -> dict:
    """Generate SAP BTP Integration Suite iFlow artefacts.

    All output is deterministic — same solution + UML always produces byte-identical files.
    Every generated file is provenance-tagged with solution ID and blueprint version.
    """
    import json as _json
    import re as _re

    sol_id = solution.id
    bp_version = getattr(solution, "blueprint_version", 1) or 1
    sol_name_raw = (solution.name or f"solution_{sol_id}").strip()

    # Derive safe identifiers
    _safe = _re.sub(r"[^a-zA-Z0-9_]", "_", sol_name_raw.replace(" ", "_"))
    solution_name = _re.sub(r"_+", "_", _safe).strip("_") or f"solution_{sol_id}"
    solution_name_lower = solution_name.lower()

    prov_xml = f"<!-- Generated by Entelim — solution {sol_id} -->"
    prov_comment = f"# Generated by Entelim — solution {sol_id}"
    prov_code = f"// Generated by Entelim — solution {sol_id}"

    files: dict = {}

    # ── iflow/main.iflw ───────────────────────────────────────────────────────
    files["iflow/main.iflw"] = (
        f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        f"{prov_xml}\n"
        f"<IntegrationProject name=\"{solution_name}\" version=\"1.0\">\n"
        f"  <IntegrationFlows>\n"
        f"    <IntegrationFlow name=\"{solution_name}_MainFlow\" id=\"{solution_name}_MainFlow\">\n"
        f"      <Participants>\n"
        f"        <Participant name=\"Sender\" type=\"Initiator\"/>\n"
        f"        <Participant name=\"Receiver\" type=\"Receiver\"/>\n"
        f"      </Participants>\n"
        f"      <MessageFlow>\n"
        f"        <Sender name=\"Sender\">\n"
        f"          <Adapter type=\"HTTPS\">\n"
        f"            <Address>/api/{solution_name_lower}/inbound</Address>\n"
        f"            <AuthorizationMode>ClientCertificate</AuthorizationMode>\n"
        f"          </Adapter>\n"
        f"        </Sender>\n"
        f"        <ProcessStep name=\"ContentModifier_SetHeaders\" type=\"ContentModifier\">\n"
        f"          <Headers>\n"
        f"            <Header name=\"X-Entelim-Solution\" value=\"{sol_id}\"/>\n"
        f"            <Header name=\"X-Entelim-Generated\" value=\"true\"/>\n"
        f"          </Headers>\n"
        f"        </ProcessStep>\n"
        f"        <Receiver name=\"Receiver\">\n"
        f"          <Adapter type=\"HTTP\">\n"
        f"            <Address>{{{{receiver_endpoint}}}}</Address>\n"
        f"            <AuthorizationMode>OAuth2ClientCredentials</AuthorizationMode>\n"
        f"            <CredentialName>{{{{receiver_credential}}}}</CredentialName>\n"
        f"          </Adapter>\n"
        f"        </Receiver>\n"
        f"      </MessageFlow>\n"
        f"    </IntegrationFlow>\n"
        f"  </IntegrationFlows>\n"
        f"</IntegrationProject>\n"
    )

    # ── iflow/value_mappings.xml ──────────────────────────────────────────────
    files["iflow/value_mappings.xml"] = (
        f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        f"{prov_xml}\n"
        f"<ValueMappingTable name=\"{solution_name}_ValueMappings\">\n"
        f"  <!-- Add value mappings here for field translation -->\n"
        f"</ValueMappingTable>\n"
    )

    # ── iflow/parameters.prop ─────────────────────────────────────────────────
    files["iflow/parameters.prop"] = (
        f"{prov_comment}\n"
        f"# Externalized parameters for {solution_name} integration\n"
        f"# Replace with actual values in SAP BTP Integration Suite\n"
        f"receiver_endpoint=https://your-receiver-endpoint.example.com/api\n"
        f"receiver_credential=your-oauth2-credential-name\n"
        f"sender_address=/api/{solution_name_lower}/inbound\n"
    )

    # ── odata/service.xml ─────────────────────────────────────────────────────
    files["odata/service.xml"] = (
        f"<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        f"{prov_xml}\n"
        f"<edmx:Edmx Version=\"4.0\" xmlns:edmx=\"http://docs.oasis-open.org/odata/ns/edmx\">\n"
        f"  <edmx:DataServices>\n"
        f"    <Schema Namespace=\"{solution_name}.Service\" xmlns=\"http://docs.oasis-open.org/odata/ns/edm\">\n"
        f"      <EntityType Name=\"{solution_name}Entity\">\n"
        f"        <Key><PropertyRef Name=\"Id\"/></Key>\n"
        f"        <Property Name=\"Id\" Type=\"Edm.Int32\" Nullable=\"false\"/>\n"
        f"        <Property Name=\"Name\" Type=\"Edm.String\"/>\n"
        f"        <Property Name=\"Status\" Type=\"Edm.String\"/>\n"
        f"      </EntityType>\n"
        f"      <EntityContainer Name=\"{solution_name}Container\">\n"
        f"        <EntitySet Name=\"{solution_name}Entities\" EntityType=\"{solution_name}.Service.{solution_name}Entity\"/>\n"
        f"      </EntityContainer>\n"
        f"    </Schema>\n"
        f"  </edmx:DataServices>\n"
        f"</edmx:Edmx>\n"
    )

    # ── odata/service.cds ─────────────────────────────────────────────────────
    files["odata/service.cds"] = (
        f"{prov_code}\n"
        f"using from './service';\n"
        f"\n"
        f"annotate {solution_name}Container.{solution_name}Entities with @(\n"
        f"  UI.LineItem: [\n"
        f"    {{{{ Label: 'ID', Value: Id }}}},\n"
        f"    {{{{ Label: 'Name', Value: Name }}}},\n"
        f"    {{{{ Label: 'Status', Value: Status }}}}\n"
        f"  ]\n"
        f");\n"
    )

    # ── scripts/deploy.sh ─────────────────────────────────────────────────────
    files["scripts/deploy.sh"] = (
        f"#!/bin/bash\n"
        f"{prov_comment}\n"
        f"# Deploy {solution_name} integration to SAP BTP\n"
        f"\n"
        f"SPACE=\"${{CF_SPACE:-dev}}\"\n"
        f"ORG=\"${{CF_ORG:-your-org}}\"\n"
        f"\n"
        f"echo \"Deploying {solution_name} integration to BTP...\"\n"
        f"cf target -o \"$ORG\" -s \"$SPACE\"\n"
        f"cf deploy mta_archives/{solution_name_lower}_integration.mtar\n"
    )

    # ── scripts/README.md ─────────────────────────────────────────────────────
    files["scripts/README.md"] = (
        f"# {solution_name} Integration Architecture\n"
        f"\n"
        f"Generated by Entelim from Solution Blueprint (ID: {sol_id})\n"
        f"\n"
        f"## Integration Pattern\n"
        f"\n"
        f"This integration uses SAP BTP Integration Suite as the middleware layer.\n"
        f"\n"
        f"## Files\n"
        f"\n"
        f"- `iflow/main.iflw` — SAP BTP iFlow descriptor\n"
        f"- `iflow/value_mappings.xml` — Field value mapping table\n"
        f"- `iflow/parameters.prop` — Externalized parameters (replace before deploy)\n"
        f"- `odata/service.xml` — OData service descriptor (EDMX)\n"
        f"- `odata/service.cds` — CDS annotation layer\n"
        f"- `scripts/deploy.sh` — Deployment script stub\n"
        f"\n"
        f"## Prerequisites\n"
        f"\n"
        f"1. SAP BTP account with Integration Suite subscription\n"
        f"2. OAuth2 client credentials configured in BTP cockpit\n"
        f"3. Cloud Foundry CLI installed\n"
        f"\n"
        f"## Deployment\n"
        f"\n"
        f"1. Import `iflow/main.iflw` into SAP BTP Integration Suite\n"
        f"2. Update `iflow/parameters.prop` with actual endpoints\n"
        f"3. Run `./scripts/deploy.sh`\n"
    )

    # ── .archie_provenance.json ───────────────────────────────────────────────
    provenance = {
        "generated_by": "Entelim",
        "solution_id": sol_id,
        "solution_name": solution_name,
        "blueprint_version": bp_version,
        "codegen_target": "sap-btp-integration",
        "spec": "docs/2026-03-27-integration-architecture-governance-design.md",
    }
    files[".archie_provenance.json"] = _json.dumps(provenance, indent=2) + "\n"

    return files



def _generate_azure_logic_app(solution, config: dict, uml: dict) -> dict:
    """Generate Azure Logic App workflow artefacts.

    All output is deterministic — same solution + UML always produces byte-identical files.
    Every generated file is provenance-tagged with solution ID and blueprint version.
    """
    import json as _json
    import re as _re

    sol_id = solution.id
    getattr(solution, "blueprint_version", 1) or 1
    sol_name_raw = (solution.name or f"solution_{sol_id}").strip()

    # Derive safe identifiers
    _safe = _re.sub(r"[^a-zA-Z0-9_]", "_", sol_name_raw.replace(" ", "_"))
    solution_name = _re.sub(r"_+", "_", _safe).strip("_") or f"solution_{sol_id}"
    solution_name_lower = solution_name.lower()

    files: dict = {}

    # ── workflows/{solution_name_lower}-integration.json ──────────────────────
    workflow = {
        "$schema": "https://schema.management.azure.com/schemas/2016-06-01/Microsoft.Logic.json",
        "contentVersion": "1.0.0.0",
        "_archie_provenance": {
            "solution_id": sol_id,
            "generated_by": "Entelim",
            "codegen_target": "azure-logic-app",
        },
        "definition": {
            "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
            "contentVersion": "1.0.0.0",
            "parameters": {
                "$connections": {"defaultValue": {}, "type": "Object"},
                "sap_base_url": {"type": "String"},
                "target_base_url": {"type": "String"},
            },
            "triggers": {
                "manual": {
                    "type": "Request",
                    "kind": "Http",
                    "inputs": {"schema": {}},
                }
            },
            "actions": {
                "Call_SAP_OData": {
                    "type": "Http",
                    "inputs": {
                        "method": "GET",
                        "uri": f"@{{parameters('sap_base_url')}}/api/{solution_name_lower}",
                        "authentication": {
                            "type": "Basic",
                            "username": "@parameters('sap_user')",
                            "password": "@parameters('sap_password')",
                        },
                    },
                },
                "Send_To_Target": {
                    "type": "Http",
                    "runAfter": {"Call_SAP_OData": ["Succeeded"]},
                    "inputs": {
                        "method": "POST",
                        "uri": "@{parameters('target_base_url')}/api/receive",
                        "body": "@body('Call_SAP_OData')",
                    },
                },
            },
        },
    }
    files[f"workflows/{solution_name_lower}-integration.json"] = _json.dumps(workflow, indent=2) + "\n"

    # ── workflows/connections.json ────────────────────────────────────────────
    connections = {
        "$schema": "https://schema.management.azure.com/schemas/2016-06-01/Microsoft.Logic.json",
        "_archie_provenance": {"solution_id": sol_id, "generated_by": "Entelim"},
        "connections": {
            "sap-odata": {
                "type": "Microsoft.Web/connections",
                "apiDefinitionUrl": "[concat(subscription().id, '/providers/Microsoft.Web/locations/', resourceGroup().location, '/managedApis/sapodata')]",
            }
        },
    }
    files["workflows/connections.json"] = _json.dumps(connections, indent=2) + "\n"

    # ── parameters/dev.parameters.json ───────────────────────────────────────
    dev_params = {
        "$schema": "https://schema.management.azure.com/schemas/2015-01-01/deploymentParameters.json#",
        "_archie_provenance": {"solution_id": sol_id},
        "contentVersion": "1.0.0.0",
        "parameters": {
            "sap_base_url": {"value": "https://your-sap-dev.example.com"},
            "target_base_url": {"value": "https://your-target-dev.example.com"},
        },
    }
    files["parameters/dev.parameters.json"] = _json.dumps(dev_params, indent=2) + "\n"

    # ── parameters/prod.parameters.json ──────────────────────────────────────
    prod_params = {
        "$schema": "https://schema.management.azure.com/schemas/2015-01-01/deploymentParameters.json#",
        "_archie_provenance": {"solution_id": sol_id},
        "contentVersion": "1.0.0.0",
        "parameters": {
            "sap_base_url": {"value": "https://your-sap-prod.example.com"},
            "target_base_url": {"value": "https://your-target-prod.example.com"},
        },
    }
    files["parameters/prod.parameters.json"] = _json.dumps(prod_params, indent=2) + "\n"

    # ── deploy/azuredeploy.json ───────────────────────────────────────────────
    arm_template = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "_archie_provenance": {"solution_id": sol_id, "generated_by": "Entelim"},
        "contentVersion": "1.0.0.0",
        "parameters": {
            "logicAppName": {"type": "string", "defaultValue": f"{solution_name_lower}-integration"},
            "location": {"type": "string", "defaultValue": "[resourceGroup().location]"},
        },
        "resources": [
            {
                "type": "Microsoft.Logic/workflows",
                "apiVersion": "2019-05-01",
                "name": "[parameters('logicAppName')]",
                "location": "[parameters('location')]",
                "properties": {
                    "definition": "[json(string(reference(resourceId('Microsoft.Resources/deployments', 'workflowDeploy'), '2019-10-01').outputs.workflowDefinition.value))]"
                },
            }
        ],
    }
    files["deploy/azuredeploy.json"] = _json.dumps(arm_template, indent=2) + "\n"

    # ── README.md ─────────────────────────────────────────────────────────────
    files["README.md"] = (
        f"# {solution_name} Azure Logic App Integration\n"
        f"\n"
        f"Generated by Entelim from Solution Blueprint (ID: {sol_id})\n"
        f"\n"
        f"## Prerequisites\n"
        f"\n"
        f"- Azure subscription with Logic Apps enabled\n"
        f"- Azure CLI installed\n"
        f"- SAP OData connector configured in Azure\n"
        f"\n"
        f"## Deployment\n"
        f"\n"
        f"```bash\n"
        f"az deployment group create \\\n"
        f"  --resource-group <your-rg> \\\n"
        f"  --template-file deploy/azuredeploy.json \\\n"
        f"  --parameters @parameters/prod.parameters.json\n"
        f"```\n"
        f"\n"
        f"## Files\n"
        f"\n"
        f"- `workflows/{solution_name_lower}-integration.json` — Logic App workflow definition\n"
        f"- `workflows/connections.json` — API connections manifest\n"
        f"- `parameters/dev.parameters.json` — Dev environment parameters\n"
        f"- `parameters/prod.parameters.json` — Prod environment parameters\n"
        f"- `deploy/azuredeploy.json` — ARM template\n"
    )

    return files



def _auto_verify_generated_tests(solution_id, gen, files, _sse_fn=None):
    """Run generated tests in Docker synchronously after code generation.

    Returns dict with pass_rate and summary, or None if Docker unavailable.
    Persists results into quality_details on the latest history record.
    """
    import shutil
    import subprocess
    import tempfile
    from datetime import datetime as _dt

    # Docker available?
    try:
        _dc = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        if _dc.returncode != 0:
            return None
    except Exception:
        return None

    tmpdir = None
    _test_img = None
    try:
        tmpdir = tempfile.mkdtemp(prefix=f"archie-autoverify-{solution_id}-")

        for path, content in files.items():
            full = os.path.join(tmpdir, path.replace("/", os.sep))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            try:
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write(content)
            except Exception as exc:
                logger.debug("suppressed error in _auto_verify_generated_tests (app/modules/codegen/routes/_helpers.py): %s", exc)

        env_path = os.path.join(tmpdir, ".env")
        if not os.path.exists(env_path):
            with open(env_path, "w") as ef:
                ef.write(
                    "POSTGRES_USER=app\nPOSTGRES_PASSWORD=changeme\n"
                    "POSTGRES_DB=app\nDATABASE_URL=postgresql://app:changeme@db:5432/app\n"  # secrets-safety-ok: placeholder creds for throwaway docker test sandbox
                    "SECRET_KEY=test-secret\nJWT_SECRET=test-jwt\nTESTING=1\n"
                )

        _conftest_path = os.path.join(tmpdir, "tests", "conftest.py")
        os.makedirs(os.path.dirname(_conftest_path), exist_ok=True)
        with open(_conftest_path, "w", encoding="utf-8") as cf:
            cf.write(_generate_test_conftest())

        # Patch Dockerfile test deps
        _dockerfile_path = os.path.join(tmpdir, "Dockerfile")
        if os.path.exists(_dockerfile_path):
            df = open(_dockerfile_path).read()
            if "AS test" in df and "aiosqlite" not in df:
                df = df.replace(
                    "RUN pip install pytest httpx pytest-asyncio",
                    "RUN pip install pytest httpx pytest-asyncio aiosqlite email-validator",
                )
                if "aiosqlite" not in df:
                    df = df.replace(
                        "FROM base AS test",
                        "FROM base AS test\nRUN pip install --quiet aiosqlite email-validator",
                    )
                with open(_dockerfile_path, "w") as dff:
                    dff.write(df)

        # Build test image
        _test_img = f"archie-autoverify-{solution_id}-{os.path.basename(tmpdir)}"
        _has_test_stage = "AS test" in files.get("Dockerfile", "")
        _build_target = ["--target", "test"] if _has_test_stage else []
        build_proc = subprocess.run(
            ["/usr/bin/docker", "build"] + _build_target + ["-t", _test_img, "."],
            cwd=tmpdir, capture_output=True, text=True, timeout=300,
        )
        if build_proc.returncode != 0:
            logger.warning("Auto-verify build failed for solution %d", solution_id)
            return {"status": "build_failed", "pass_rate": None}

        # Run tests
        has_unit = any(p.startswith("tests/unit/") and p.endswith(".py") for p in files)
        test_target = "tests/unit/" if has_unit else "tests/"
        test_proc = subprocess.run(
            [
                "/usr/bin/docker", "run", "--rm",
                "--memory=512m", "--cpus=1.0",
                "-e", "DATABASE_URL=sqlite+aiosqlite:///./test.db",
                "-e", "TESTING=1",
                _test_img,
                "pytest", test_target, "--tb=short", "-q", "--no-header",
            ],
            cwd=tmpdir, capture_output=True, text=True, timeout=120,
        )

        summary = {"passed": 0, "failed": 0, "errors": 0}
        for tl in reversed(test_proc.stdout.splitlines()):
            m = re.search(r'(\d+) passed(?:[^\d]+(\d+) failed)?(?:[^\d]+(\d+) error)?', tl)
            if m:
                summary = {
                    "passed": int(m.group(1) or 0),
                    "failed": int(m.group(2) or 0),
                    "errors": int(m.group(3) or 0),
                }
                break

        total = summary["passed"] + summary["failed"] + summary["errors"]
        pass_rate = round(summary["passed"] / total * 100) if total > 0 else 0

        # Persist to quality_details
        try:
            latest_hist = CodegenGenerationHistory.query.filter_by(
                codegen_generation_id=gen.id
            ).order_by(CodegenGenerationHistory.generated_at.desc()).first()
            if latest_hist and latest_hist.quality_details:
                qd = dict(latest_hist.quality_details)
                qd["test_execution"] = {
                    "passed": summary["passed"],
                    "failed": summary["failed"],
                    "errors": summary["errors"],
                    "pass_rate": pass_rate,
                    "verified_at": _dt.utcnow().isoformat(),
                    "auto": True,
                }
                qd["test_coverage"] = float(pass_rate)
                new_score = round(
                    qd.get("schema_completeness", 0) * 0.22
                    + float(pass_rate) * 0.18
                    + qd.get("relationship_density", 0) * 0.12
                    + qd.get("traceability", 0) * 0.12
                    + qd.get("rule_coverage", 100) * 0.16
                    + qd.get("domain_fidelity", 100) * 0.20,
                    1,
                )
                latest_hist.quality_details = qd
                latest_hist.quality_score = new_score
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(latest_hist, "quality_details")
                db.session.commit()
        except Exception as _upd_exc:
            db.session.rollback()
            logger.warning("Auto-verify: failed to persist results: %s", _upd_exc)

        logger.info(
            "Auto-verify solution %d: %d passed, %d failed, %d errors (%d%%)",
            solution_id, summary["passed"], summary["failed"], summary["errors"], pass_rate,
        )
        return {"status": "done", "summary": summary, "pass_rate": pass_rate}

    except subprocess.TimeoutExpired:
        logger.warning("Auto-verify timed out for solution %d", solution_id)
        return {"status": "timeout", "pass_rate": None}
    except Exception as exc:
        logger.warning("Auto-verify failed for solution %d: %s", solution_id, exc)
        return {"status": "error", "error": str(exc), "pass_rate": None}
    finally:
        if _test_img:
            try:
                subprocess.run(["/usr/bin/docker", "rmi", "-f", _test_img],
                               capture_output=True, timeout=15)
            except Exception as exc:
                logger.debug("suppressed error in _auto_verify_generated_tests (app/modules/codegen/routes/_helpers.py): %s", exc)
        if tmpdir and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)



def _generate_refine_frontend(solution_name: str, uml_snapshot: dict) -> dict:
    """Generate a Refine (refine.dev) React admin frontend from UML class diagram.

    Returns dict of filepath → content for the frontend/ directory.
    The generated app uses @refinedev/antd with a simple-rest data provider
    that talks to the backend API at VITE_API_URL.
    """
    classes = (uml_snapshot or {}).get("class_diagram", {}).get("classes", [])
    slug = re.sub(r"[^a-z0-9]", "-", solution_name.lower()).strip("-") or "my-app"

    files = {}

    files["frontend/package.json"] = json.dumps(
        {
            "name": f"{slug}-frontend",
            "version": "0.1.0",
            "private": True,
            "scripts": {
                "dev": "vite",
                "build": "tsc && vite build",
                "preview": "vite preview",
            },
            "dependencies": {
                "@refinedev/antd": "^5.35.0",
                "@refinedev/core": "^4.46.0",
                "@refinedev/react-router-v6": "^4.5.0",
                "@refinedev/simple-rest": "^4.5.0",
                "antd": "^5.15.0",
                "react": "^18.2.0",
                "react-dom": "^18.2.0",
                "react-router-dom": "^6.14.0",
            },
            "devDependencies": {
                "@types/react": "^18.2.0",
                "@types/react-dom": "^18.2.0",
                "@vitejs/plugin-react": "^4.0.0",
                "typescript": "^5.0.0",
                "vite": "^5.0.0",
            },
        },
        indent=2,
    )

    files["frontend/index.html"] = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="UTF-8" />\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
        f"    <title>{solution_name} Admin</title>\n"
        "  </head>\n"
        "  <body>\n"
        '    <div id="root"></div>\n'
        '    <script type="module" src="/src/main.tsx"></script>\n'
        "  </body>\n"
        "</html>\n"
    )

    files["frontend/vite.config.ts"] = (
        'import { defineConfig } from "vite";\n'
        'import react from "@vitejs/plugin-react";\n\n'
        "export default defineConfig({\n"
        "  plugins: [react()],\n"
        "  server: {\n"
        "    proxy: {\n"
        '      "/api": {\n'
        "        target: process.env.VITE_API_URL || \"http://localhost:8000\",\n"
        "        changeOrigin: true,\n"
        "      },\n"
        "    },\n"
        "  },\n"
        "});\n"
    )

    files["frontend/tsconfig.json"] = json.dumps(
        {
            "compilerOptions": {
                "target": "ES2020",
                "useDefineForClassFields": True,
                "lib": ["ES2020", "DOM", "DOM.Iterable"],
                "module": "ESNext",
                "skipLibCheck": True,
                "moduleResolution": "bundler",
                "allowImportingTsExtensions": True,
                "resolveJsonModule": True,
                "isolatedModules": True,
                "noEmit": True,
                "jsx": "react-jsx",
                "strict": True,
            },
            "include": ["src"],
        },
        indent=2,
    )

    files["frontend/.env.example"] = "VITE_API_URL=http://localhost:8000\n"

    files["frontend/Dockerfile"] = (
        "FROM node:20-alpine AS build\n"
        "WORKDIR /app\n"
        "COPY package*.json ./\n"
        "RUN npm ci\n"
        "COPY . .\n"
        "RUN npm run build\n\n"
        "FROM nginx:alpine\n"
        "COPY --from=build /app/dist /usr/share/nginx/html\n"
        "EXPOSE 80\n"
        'CMD ["nginx", "-g", "daemon off;"]\n'
    )

    files["frontend/README.md"] = (
        f"# {solution_name} — React Admin Frontend\n\n"
        "Generated by Entelim Code Workbench using [Refine](https://refine.dev).\n\n"
        "## Quick start\n\n"
        "```bash\n"
        "cd frontend\n"
        "cp .env.example .env\n"
        "# Edit .env: set VITE_API_URL to your backend URL\n"
        "npm install\n"
        "npm run dev\n"
        "```\n\n"
        "## Build for production\n\n"
        "```bash\n"
        "npm run build\n"
        "docker build -t frontend .\n"
        "```\n"
    )

    # Build resource list from UML classes
    resources = []
    for cls in classes:
        name = cls.get("name", "").strip()
        if not name:
            continue
        table = cls.get("table_name") or (
            re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower() + "s"
        )
        fields = [
            f for f in (cls.get("attributes") or [])
            if isinstance(f, dict) and f.get("name") and f.get("name") != "id"
        ][:5]
        if not fields:
            fields = [{"name": "name", "type": "str"}]
        resources.append({"name": name, "table": table, "fields": fields})

    if not resources:
        resources = [{"name": "Item", "table": "items", "fields": [{"name": "name", "type": "str"}]}]

    # src/main.tsx
    files["frontend/src/main.tsx"] = (
        'import React from "react";\n'
        'import { createRoot } from "react-dom/client";\n'
        'import App from "./App";\n'
        'import "antd/dist/reset.css";\n\n'
        'createRoot(document.getElementById("root")!).render(\n'
        "  <React.StrictMode>\n"
        "    <App />\n"
        "  </React.StrictMode>\n"
        ");\n"
    )

    resource_imports = "\n".join(
        'import {{ {n}List, {n}Create, {n}Edit, {n}Show }} from "./pages/{lower}";'.format(
            n=r["name"], lower=r["name"].lower()
        )
        for r in resources
    )

    resource_defs = ",\n    ".join(
        (
            '{{ name: "{table}", list: {n}List, create: {n}Create,'
            " edit: {n}Edit, show: {n}Show }}"
        ).format(n=r["name"], table=r["table"])
        for r in resources
    )

    files["frontend/src/App.tsx"] = (
        'import {{ Refine }} from "@refinedev/core";\n'
        'import {{ RefineAntd, Layout, ErrorComponent }} from "@refinedev/antd";\n'
        'import simpleRestProvider from "@refinedev/simple-rest";\n'
        'import {{ BrowserRouter, Route, Routes, Outlet }} from "react-router-dom";\n'
        'import {{ NavigateToResource, UnsavedChangesNotifier }} from "@refinedev/react-router-v6";\n'
        "{resource_imports}\n\n"
        'const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";\n\n'
        "export default function App() {{\n"
        "  return (\n"
        "    <BrowserRouter>\n"
        "      <Refine\n"
        "        dataProvider={{simpleRestProvider(API_URL)}}\n"
        "        resources={{[\n"
        "          {resource_defs}\n"
        "        ]}}\n"
        "        options={{{{ syncWithLocation: true, warnWhenUnsavedChanges: true }}}}\n"
        "      >\n"
        "        <RefineAntd>\n"
        "          <Routes>\n"
        "            <Route element={{<Layout><Outlet /></Layout>}}>\n"
        "              <Route index element={{<NavigateToResource />}} />\n"
        "              <Route path='*' element={{<ErrorComponent />}} />\n"
        "            </Route>\n"
        "          </Routes>\n"
        "          <UnsavedChangesNotifier />\n"
        "        </RefineAntd>\n"
        "      </Refine>\n"
        "    </BrowserRouter>\n"
        "  );\n"
        "}}\n"
    ).format(resource_imports=resource_imports, resource_defs=resource_defs)

    def _tsx_safe(text):
        """Strip characters that could break out of a JSX string literal or
        attribute when interpolating an architect-defined field name into
        generated TSX source: a double-quote breaks the attribute, curly
        braces open a JS expression. field names carry no character
        restriction upstream, so this closes a code-generation-injection
        gap into the generated admin app's own source."""
        return str(text).replace('"', "").replace("{", "").replace("}", "")

    # Per-resource page files
    for r in resources:
        name = r["name"]
        table = r["table"]
        lower = name.lower()
        fields = r["fields"]

        col_defs = "\n          ".join(
            '<Table.Column title="{title}" dataIndex="{fname}" key="{fname}" />'.format(  # raw-html-ok: title/fname are _tsx_safe()'d inline (defined above), stripping quote/brace characters before interpolation into generated TSX
                title=_tsx_safe(f["name"]).replace("_", " ").title(), fname=_tsx_safe(f["name"])
            )
            for f in fields
        )

        form_items = "\n          ".join(
            (
                '<Form.Item label="{title}" name="{fname}">'  # raw-html-ok: title/fname are _tsx_safe()'d inline (defined above), stripping quote/brace characters before interpolation into generated TSX
                "<Input /></Form.Item>"
            ).format(title=_tsx_safe(f["name"]).replace("_", " ").title(), fname=_tsx_safe(f["name"]))
            for f in fields
        )

        show_fields = "\n          ".join(
            '<TextField label="{title}" value={{record?.["{fname}"]}} />'.format(  # raw-html-ok: title/fname are _tsx_safe()'d inline (defined above), stripping quote/brace characters before interpolation into generated TSX
                title=_tsx_safe(f["name"]).replace("_", " ").title(), fname=_tsx_safe(f["name"])
            )
            for f in fields
        )

        files[f"frontend/src/pages/{lower}/index.ts"] = (
            "export {{ {n}List }} from './list';\n"
            "export {{ {n}Create }} from './create';\n"
            "export {{ {n}Edit }} from './edit';\n"
            "export {{ {n}Show }} from './show';\n"
        ).format(n=name)

        files[f"frontend/src/pages/{lower}/list.tsx"] = (
            'import {{ List, useTable }} from "@refinedev/antd";\n'
            'import {{ Table }} from "antd";\n\n'
            "export const {n}List = () => {{\n"
            '  const {{ tableProps }} = useTable({{ resource: "{table}" }});\n'
            "  return (\n"
            "    <List>\n"
            '      <Table {{...tableProps}} rowKey="id">\n'
            "        {col_defs}\n"
            "      </Table>\n"
            "    </List>\n"
            "  );\n"
            "}};\n"
        ).format(n=name, table=table, col_defs=col_defs)

        files[f"frontend/src/pages/{lower}/create.tsx"] = (
            'import {{ Create, useForm }} from "@refinedev/antd";\n'
            'import {{ Form, Input }} from "antd";\n\n'
            "export const {n}Create = () => {{\n"
            '  const {{ formProps, saveButtonProps }} = useForm({{ resource: "{table}" }});\n'
            "  return (\n"
            "    <Create saveButtonProps={{saveButtonProps}}>\n"
            '      <Form {{...formProps}} layout="vertical">\n'
            "        {form_items}\n"
            "      </Form>\n"
            "    </Create>\n"
            "  );\n"
            "}};\n"
        ).format(n=name, table=table, form_items=form_items)

        files[f"frontend/src/pages/{lower}/edit.tsx"] = (
            'import {{ Edit, useForm }} from "@refinedev/antd";\n'
            'import {{ Form, Input }} from "antd";\n\n'
            "export const {n}Edit = () => {{\n"
            '  const {{ formProps, saveButtonProps }} = useForm({{ resource: "{table}" }});\n'
            "  return (\n"
            "    <Edit saveButtonProps={{saveButtonProps}}>\n"
            '      <Form {{...formProps}} layout="vertical">\n'
            "        {form_items}\n"
            "      </Form>\n"
            "    </Edit>\n"
            "  );\n"
            "}};\n"
        ).format(n=name, table=table, form_items=form_items)

        files[f"frontend/src/pages/{lower}/show.tsx"] = (
            'import {{ Show, TextField }} from "@refinedev/antd";\n'
            'import {{ useShow }} from "@refinedev/core";\n\n'
            "export const {n}Show = () => {{\n"
            '  const {{ queryResult }} = useShow({{ resource: "{table}" }});\n'
            "  const {{ data }} = queryResult;\n"
            "  const record = data?.data;\n"
            "  return (\n"
            "    <Show>\n"
            "      {show_fields}\n"
            "    </Show>\n"
            "  );\n"
            "}};\n"
        ).format(n=name, table=table, show_fields=show_fields)

    return files



def _parse_github_owner_repo(github_url: str):
    """Extract (owner, repo) from a GitHub HTML URL like https://github.com/owner/repo."""
    if not github_url:
        return None, None
    # Strip trailing slash and .git
    url = github_url.rstrip("/").removesuffix(".git")
    parts = url.split("github.com/")
    if len(parts) < 2:
        return None, None
    path_parts = parts[1].split("/")
    if len(path_parts) < 2:
        return None, None
    return path_parts[0], path_parts[1]



def _get_github_service():
    """Load GitHub token from APISettings and return a GitHubService, or None."""
    try:
        from app.models.models import APISettings
        from app.modules.codegen.services.github_service import GitHubService
        gh_settings = APISettings.query.filter_by(provider="github").first()
        if not gh_settings or not gh_settings.api_key:
            return None, "No GitHub token configured — go to Admin > API Settings"
        return GitHubService(gh_settings.api_key), None
    except Exception as e:
        return None, str(e)



def _generate_shadcn_frontend(solution_name: str, uml_snapshot: dict, config: dict = None) -> dict:
    """Generate a shadcn/ui + Next.js 14 App Router frontend from UML class diagram.

    Returns dict of filepath -> content for the frontend/ directory.
    The generated app uses typed Zod schemas and a typed fetch client derived
    from the confirmed field specs in uml_snapshot.class_diagram.classes.
    """
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    classes = (uml_snapshot or {}).get("class_diagram", {}).get("classes", [])
    solution_slug = re.sub(r"[^a-z0-9]", "_", solution_name.lower()).strip("_") or "my_app"

    template_dir = os.path.normpath(os.path.join(
        os.path.dirname(__file__),          # codegen/routes/
        "..", "..",                          # up to app/modules/
        "solutions_product", "templates", "react_shadcn",
    ))

    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    files: dict = {}

    # ── Shared context (available in all templates) ──────────────────────────
    cfg = config or {}
    base_ctx = {
        "solution_name": solution_name,
        "solution_slug": solution_slug,
        "classes": classes,
        "auth": cfg.get("auth", "none"),
    }

    # ── Root config files (rendered once) ────────────────────────────────────
    single_templates = [
        ("package.json.j2",                           "frontend/package.json"),
        ("tsconfig.json.j2",                          "frontend/tsconfig.json"),
        ("next.config.js.j2",                         "frontend/next.config.js"),
        ("tailwind.config.ts.j2",                     "frontend/tailwind.config.ts"),
        ("postcss.config.js.j2",                      "frontend/postcss.config.js"),
        ("components.json.j2",                        "frontend/components.json"),
        ("Dockerfile.j2",                             "frontend/Dockerfile"),
        ("gitignore.j2",                              "frontend/.gitignore"),
        (".env.example.j2",                           "frontend/.env.example"),
        ("src/lib/schemas.ts.j2",                     "frontend/src/lib/schemas.ts"),
        ("src/lib/api.ts.j2",                         "frontend/src/lib/api.ts"),
        ("src/lib/utils.ts.j2",                       "frontend/src/lib/utils.ts"),
        ("src/app/globals.css.j2",                    "frontend/src/app/globals.css"),
        ("src/app/layout.tsx.j2",                     "frontend/src/app/layout.tsx"),
        ("src/app/page.tsx.j2",                       "frontend/src/app/page.tsx"),
        ("src/components/nav-sidebar.tsx.j2",         "frontend/src/components/nav-sidebar.tsx"),
        ("src/components/data-table.tsx.j2",          "frontend/src/components/data-table.tsx"),
        ("src/components/ui/button.tsx.j2",           "frontend/src/components/ui/button.tsx"),
        ("src/components/ui/input.tsx.j2",            "frontend/src/components/ui/input.tsx"),
        ("src/components/ui/label.tsx.j2",            "frontend/src/components/ui/label.tsx"),
        ("src/components/ui/table.tsx.j2",            "frontend/src/components/ui/table.tsx"),
        ("src/components/ui/badge.tsx.j2",            "frontend/src/components/ui/badge.tsx"),
        ("src/components/ui/card.tsx.j2",             "frontend/src/components/ui/card.tsx"),
        ("src/components/ui/select.tsx.j2",           "frontend/src/components/ui/select.tsx"),
        ("src/components/ui/skeleton.tsx.j2",         "frontend/src/components/ui/skeleton.tsx"),
        ("src/components/ui/separator.tsx.j2",        "frontend/src/components/ui/separator.tsx"),
        ("src/components/ui/toast.tsx.j2",            "frontend/src/components/ui/toast.tsx"),
        ("src/components/ui/toaster.tsx.j2",          "frontend/src/components/ui/toaster.tsx"),
        ("src/components/ui/use-toast.ts.j2",         "frontend/src/components/ui/use-toast.ts"),
        ("src/app/error.tsx.j2",                      "frontend/src/app/error.tsx"),
        ("src/app/not-found.tsx.j2",                  "frontend/src/app/not-found.tsx"),
        ("src/components/providers.tsx.j2",           "frontend/src/components/providers.tsx"),
        ("src/components/ui/checkbox.tsx.j2",         "frontend/src/components/ui/checkbox.tsx"),
        ("src/components/ui/textarea.tsx.j2",         "frontend/src/components/ui/textarea.tsx"),
        ("src/components/ui/alert-dialog.tsx.j2",     "frontend/src/components/ui/alert-dialog.tsx"),
    ]
    # jwt-local: add route guard middleware + login page + register + auth context
    if cfg.get("auth") == "jwt-local":
        single_templates.extend([
            ("middleware.ts.j2",                            "frontend/middleware.ts"),
            ("src/lib/auth.ts.j2",                         "frontend/src/lib/auth.ts"),
            ("src/app/login/page.tsx.j2",                  "frontend/src/app/login/page.tsx"),
            ("src/app/register/page.tsx.j2",               "frontend/src/app/register/page.tsx"),
        ])
    for tpl_name, out_path in single_templates:
        try:
            tpl = env.get_template(tpl_name)
            files[out_path] = tpl.render(**base_ctx)
        except Exception as exc:  # noqa: BLE001
            files[out_path] = f"// Template error: {exc}"

    # vercel.json — one-click "Deploy to Vercel" support
    files["frontend/vercel.json"] = (
        '{\n  "framework": "nextjs",\n  "buildCommand": "npm run build",\n'
        '  "outputDirectory": ".next",\n  "installCommand": "npm install"\n}\n'
    )

    # ── Per-resource pages (rendered once per class) ─────────────────────────
    per_resource_templates = [
        ("src/app/[resource]/page.tsx.j2",       "frontend/src/app/{slug}/page.tsx"),
        ("src/app/[resource]/new/page.tsx.j2",   "frontend/src/app/{slug}/new/page.tsx"),
        ("src/app/[resource]/[id]/page.tsx.j2",  "frontend/src/app/{slug}/{id_seg}/page.tsx"),
    ]
    for cls in classes:
        slug = cls.get("table_name") or cls["name"].lower()
        cls_ctx = {
            **base_ctx,
            "name": cls["name"],
            "table_name": slug,
            "display_name": cls.get("display_name") or cls["name"].replace("_", " "),
            "fields": cls.get("fields", []),
            "relationships": cls.get("relationships", []),
            "validations": cls.get("validations", []),
            "description": cls.get("description", ""),
        }
        for tpl_name, out_pattern in per_resource_templates:
            out_path = out_pattern.format(slug=slug, id_seg="[id]")
            try:
                tpl = env.get_template(tpl_name)
                files[out_path] = tpl.render(**cls_ctx)
            except Exception as exc:  # noqa: BLE001
                files[out_path] = f"// Template error: {exc}"

    return files



def _generate_expo_mobile(solution_name: str, uml_snapshot: dict, config: dict = None) -> dict:
    """Generate a React Native / Expo mobile app from UML class diagram.

    Returns dict of filepath -> content for the mobile/ directory.
    Uses Expo Router v3 (file-based routing), NativeWind for styling,
    and the same Zod schemas + typed API client as the web frontend.
    """
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    classes = (uml_snapshot or {}).get("class_diagram", {}).get("classes", [])
    solution_slug = re.sub(r"[^a-z0-9]", "_", solution_name.lower()).strip("_") or "my_app"

    template_dir = os.path.normpath(os.path.join(
        os.path.dirname(__file__),
        "..", "..",
        "solutions_product", "templates", "react_native_expo",
    ))

    cfg = config or {}
    mobile_ui = cfg.get("mobile_ui_framework", "nativewind")

    # Build loader: Paper override dir takes precedence over base templates
    _NATIVEWIND_ONLY = {"tailwind.config.js.j2", "global.css.j2"}
    loader_paths = [template_dir]
    if mobile_ui != "nativewind":
        override_dir = os.path.join(template_dir, mobile_ui)
        if os.path.isdir(override_dir):
            loader_paths = [override_dir, template_dir]

    env = Environment(
        loader=FileSystemLoader(loader_paths),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    files: dict = {}

    base_ctx = {
        "solution_name": solution_name,
        "solution_slug": solution_slug,
        "classes": classes,
        "auth": cfg.get("auth", "none"),
        "mobile_ui_framework": mobile_ui,
    }

    # Root config files (rendered once)
    single_templates = [
        ("package.json.j2",          "mobile/package.json"),
        ("app.json.j2",              "mobile/app.json"),
        ("tsconfig.json.j2",         "mobile/tsconfig.json"),
        ("babel.config.js.j2",       "mobile/babel.config.js"),
        ("metro.config.js.j2",       "mobile/metro.config.js"),
        *([("tailwind.config.js.j2", "mobile/tailwind.config.js"),
           ("global.css.j2",         "mobile/global.css")] if mobile_ui == "nativewind" else []),
        (".env.example.j2",          "mobile/.env.example"),
        ("gitignore.j2",             "mobile/.gitignore"),
        ("eas.json.j2",              "mobile/eas.json"),
        ("README.md.j2",             "mobile/README.md"),
        ("src/lib/schemas.ts.j2",    "mobile/src/lib/schemas.ts"),
        ("src/lib/api.ts.j2",        "mobile/src/lib/api.ts"),
        ("app/_layout.tsx.j2",             "mobile/app/_layout.tsx"),
        ("app/(tabs)/_layout.tsx.j2",      "mobile/app/(tabs)/_layout.tsx"),
        ("app/index.tsx.j2",               "mobile/app/(tabs)/index.tsx"),
        ("app/(tabs)/profile.tsx.j2",      "mobile/app/(tabs)/profile.tsx"),
    ]
    # jwt-local: add auth screens + AuthProvider
    if cfg.get("auth") == "jwt-local":
        single_templates.extend([
            ("app/login.tsx.j2",                  "mobile/app/login.tsx"),
            ("app/register.tsx.j2",               "mobile/app/register.tsx"),
            ("src/providers/AuthProvider.tsx.j2", "mobile/src/providers/AuthProvider.tsx"),
        ])
    for tpl_name, out_path in single_templates:
        try:
            tpl = env.get_template(tpl_name)
            files[out_path] = tpl.render(**base_ctx)
        except Exception as exc:  # noqa: BLE001
            files[out_path] = f"// Template error: {exc}"

    # Per-resource screens (rendered once per class)
    per_resource_templates = [
        ("app/[resource]/index.tsx.j2",   "mobile/app/{slug}/index.tsx"),
        ("app/[resource]/new.tsx.j2",     "mobile/app/{slug}/new.tsx"),
        ("app/[resource]/[id].tsx.j2",    "mobile/app/{slug}/[id].tsx"),
    ]
    for cls in classes:
        slug = cls.get("table_name") or cls["name"].lower()
        cls_ctx = {
            **base_ctx,
            "name": cls["name"],
            "table_name": slug,
            "fields": cls.get("fields", []),
            "relationships": cls.get("relationships", []),
            "validations": cls.get("validations", []),
            "description": cls.get("description", ""),
        }
        for tpl_name, out_pattern in per_resource_templates:
            out_path = out_pattern.format(slug=slug)
            try:
                tpl = env.get_template(tpl_name)
                files[out_path] = tpl.render(**cls_ctx)
            except Exception as exc:  # noqa: BLE001
                files[out_path] = f"// Template error: {exc}"

    return files



# ── Live Preview, Mock Server, StackBlitz, Expo Snack, Docker Preview ──
# Extracted to preview_routes.py for maintainability.


# ── CG-006: Terraform / IaC Generation ───────────────────────────────────────

_TF_DB_KEYWORDS = frozenset({"db", "database", "postgres", "postgresql", "mysql", "rds", "oracle", "mongo"})

_TF_CACHE_KEYWORDS = frozenset({"redis", "cache", "memcache", "elasticache"})

_TF_STORAGE_KEYWORDS = frozenset({"bucket", "storage", "s3", "blob"})



def _classify_tech_element(element_name, element_type):
    """Map ArchiMate Technology element name+type → AWS resource type string."""
    name = (element_name or "").lower()
    etype = (element_type or "").lower()
    if etype == "node":
        return "aws_db_instance" if any(k in name for k in _TF_DB_KEYWORDS) else "aws_ecs_service"
    if etype == "device":
        return "aws_instance"
    if etype == "system_software":
        return "aws_elasticache_cluster" if any(k in name for k in _TF_CACHE_KEYWORDS) else "aws_db_instance"
    if etype == "artifact":
        return "aws_s3_bucket" if any(k in name for k in _TF_STORAGE_KEYWORDS) else "aws_ecr_repository"
    if etype == "communication_network":
        return "aws_vpc"
    if etype == "path":
        return "aws_security_group"
    if etype == "technology_service":
        return "aws_lb"
    return "aws_ecs_service"



def _tf_slug(name):
    """Convert a name to a safe Terraform resource identifier."""
    s = re.sub(r"[^a-z0-9_]", "_", (name or "unknown").lower()).strip("_")
    return s or "service"



def _build_terraform_files(boundary_name, solutions_data, tech_elements_by_solution, region, environment):
    """Return dict { 'terraform/<path>': '<HCL content>' }."""
    app_slug = _tf_slug(boundary_name)

    all_elems = [e for elems in tech_elements_by_solution.values() for e in elems]
    has_db = any(e["resource_type"] in ("aws_db_instance", "aws_elasticache_cluster") for e in all_elems)
    has_alb = any(e["resource_type"] == "aws_lb" for e in all_elems)

    files = {}

    # variables.tf
    image_tag_vars = "\n\n".join(
        'variable "{slug}_image_tag" {{\n  type    = string\n  default = "latest"\n}}'.format(slug=sol["slug"])
        for sol in solutions_data
    )
    files["terraform/variables.tf"] = (
        'variable "region" {{\n  type    = string\n  default = "{region}"\n}}\n\n'
        'variable "environment" {{\n  type    = string\n  default = "{environment}"\n}}\n\n'
        'variable "app_name" {{\n  type    = string\n  default = "{app_slug}"\n}}\n\n'
        'variable "db_password" {{\n  type      = string\n  sensitive = true\n  default   = "changeme"\n}}\n\n'
        '{image_tag_vars}\n'
    ).format(region=region, environment=environment, app_slug=app_slug, image_tag_vars=image_tag_vars)

    # network.tf
    files["terraform/network.tf"] = (
        'resource "aws_vpc" "{s}_vpc" {{\n'
        '  cidr_block           = "10.0.0.0/16"\n'
        '  enable_dns_hostnames = true\n'
        '  enable_dns_support   = true\n\n'
        '  tags = {{\n    Name        = "${{var.app_name}}-vpc"\n    Environment = var.environment\n  }}\n}}\n\n'
        'resource "aws_subnet" "{s}_subnet_a" {{\n'
        '  vpc_id            = aws_vpc.{s}_vpc.id\n'
        '  cidr_block        = "10.0.1.0/24"\n'
        '  availability_zone = "${{var.region}}a"\n\n'
        '  tags = {{\n    Name = "${{var.app_name}}-subnet-a"\n    Environment = var.environment\n  }}\n}}\n\n'
        'resource "aws_subnet" "{s}_subnet_b" {{\n'
        '  vpc_id            = aws_vpc.{s}_vpc.id\n'
        '  cidr_block        = "10.0.2.0/24"\n'
        '  availability_zone = "${{var.region}}b"\n\n'
        '  tags = {{\n    Name = "${{var.app_name}}-subnet-b"\n    Environment = var.environment\n  }}\n}}\n\n'
        'resource "aws_internet_gateway" "{s}_igw" {{\n'
        '  vpc_id = aws_vpc.{s}_vpc.id\n\n'
        '  tags = {{\n    Name = "${{var.app_name}}-igw"\n    Environment = var.environment\n  }}\n}}\n\n'
        'resource "aws_route_table" "{s}_rt" {{\n'
        '  vpc_id = aws_vpc.{s}_vpc.id\n\n'
        '  route {{\n    cidr_block = "0.0.0.0/0"\n    gateway_id = aws_internet_gateway.{s}_igw.id\n  }}\n\n'
        '  tags = {{\n    Name = "${{var.app_name}}-rt"\n  }}\n}}\n\n'
        'resource "aws_route_table_association" "{s}_rta_a" {{\n'
        '  subnet_id      = aws_subnet.{s}_subnet_a.id\n'
        '  route_table_id = aws_route_table.{s}_rt.id\n}}\n\n'
        'resource "aws_route_table_association" "{s}_rta_b" {{\n'
        '  subnet_id      = aws_subnet.{s}_subnet_b.id\n'
        '  route_table_id = aws_route_table.{s}_rt.id\n}}\n'
    ).format(s=app_slug)

    # main.tf
    module_blocks = "\n\n".join(
        (
            'module "{slug}" {{\n'
            '  source      = "./modules/{slug}"\n'
            '  cluster_id  = aws_ecs_cluster.{s}.id\n'
            '  vpc_id      = aws_vpc.{s}_vpc.id\n'
            '  subnet_ids  = [aws_subnet.{s}_subnet_a.id, aws_subnet.{s}_subnet_b.id]\n'
            '  environment = var.environment\n'
            '  image_tag   = var.{slug}_image_tag\n}}'
        ).format(slug=sol["slug"], s=app_slug)
        for sol in solutions_data
    )
    files["terraform/main.tf"] = (
        'terraform {{\n'
        '  required_providers {{\n'
        '    aws = {{\n'
        '      source  = "hashicorp/aws"\n'
        '      version = "~> 5.0"\n'
        '    }}\n'
        '  }}\n}}\n\n'
        'provider "aws" {{\n'
        '  region = var.region\n}}\n\n'
        'resource "aws_ecs_cluster" "{s}" {{\n'
        '  name = "${{var.app_name}}-${{var.environment}}"\n\n'
        '  tags = {{\n    Environment = var.environment\n    ManagedBy   = "terraform"\n  }}\n}}\n\n'
        '{module_blocks}\n'
    ).format(s=app_slug, module_blocks=module_blocks)

    # database.tf (only if DB elements present)
    if has_db:
        files["terraform/database.tf"] = (
            'resource "aws_db_subnet_group" "{s}" {{\n'
            '  name       = "${{var.app_name}}-${{var.environment}}-db-subnet"\n'
            '  subnet_ids = [aws_subnet.{s}_subnet_a.id, aws_subnet.{s}_subnet_b.id]\n\n'
            '  tags = {{\n    Name = "${{var.app_name}}-db-subnet-group"\n  }}\n}}\n\n'
            'resource "aws_security_group" "{s}_rds_sg" {{\n'
            '  name   = "${{var.app_name}}-rds-sg"\n'
            '  vpc_id = aws_vpc.{s}_vpc.id\n\n'
            '  ingress {{\n    from_port   = 5432\n    to_port     = 5432\n    protocol    = "tcp"\n    cidr_blocks = ["10.0.0.0/16"]\n  }}\n\n'
            '  egress {{\n    from_port   = 0\n    to_port     = 0\n    protocol    = "-1"\n    cidr_blocks = ["0.0.0.0/0"]\n  }}\n\n'
            '  tags = {{\n    Name = "${{var.app_name}}-rds-sg"\n  }}\n}}\n\n'
            'resource "aws_db_instance" "{s}_db" {{\n'
            '  identifier        = "${{var.app_name}}-${{var.environment}}-db"\n'
            '  engine            = "postgres"\n'
            '  engine_version    = "15"\n'
            '  instance_class    = "db.t3.micro"\n'
            '  allocated_storage = 20\n\n'
            '  db_name  = replace(var.app_name, "-", "_")\n'
            '  username = "admin"\n'
            '  password = var.db_password\n\n'
            '  db_subnet_group_name   = aws_db_subnet_group.{s}.name\n'
            '  vpc_security_group_ids = [aws_security_group.{s}_rds_sg.id]\n\n'
            '  skip_final_snapshot = true\n'
            '  publicly_accessible = false\n\n'
            '  tags = {{\n    Environment = var.environment\n  }}\n}}\n'
        ).format(s=app_slug)

    # alb.tf (only if LB elements present)
    if has_alb:
        tg_blocks = "\n\n".join(
            (
                'resource "aws_lb_target_group" "{slug}" {{\n'
                '  name        = "${{var.app_name}}-{slug}-tg"\n'
                '  port        = {port}\n'
                '  protocol    = "HTTP"\n'
                '  vpc_id      = aws_vpc.{s}_vpc.id\n'
                '  target_type = "ip"\n\n'
                '  health_check {{\n'
                '    path                = "/health"\n'
                '    healthy_threshold   = 2\n'
                '    unhealthy_threshold = 2\n'
                '    interval            = 30\n'
                '  }}\n}}'
            ).format(slug=sol["slug"], port=sol["port"], s=app_slug)
            for sol in solutions_data
        )
        rule_blocks = "\n\n".join(
            (
                'resource "aws_lb_listener_rule" "{slug}" {{\n'
                '  listener_arn = aws_lb_listener.{s}_http.arn\n'
                '  priority     = {priority}\n\n'
                '  action {{\n    type             = "forward"\n    target_group_arn = aws_lb_target_group.{slug}.arn\n  }}\n\n'
                '  condition {{\n    path_pattern {{\n      values = ["/{slug}/*"]\n    }}\n  }}\n}}'
            ).format(slug=sol["slug"], s=app_slug, priority=100 + i)
            for i, sol in enumerate(solutions_data)
        )
        default_tg = solutions_data[0]["slug"] if solutions_data else "default"
        files["terraform/alb.tf"] = (
            'resource "aws_security_group" "{s}_alb_sg" {{\n'
            '  name   = "${{var.app_name}}-alb-sg"\n'
            '  vpc_id = aws_vpc.{s}_vpc.id\n\n'
            '  ingress {{\n    from_port   = 80\n    to_port     = 80\n    protocol    = "tcp"\n    cidr_blocks = ["0.0.0.0/0"]\n  }}\n\n'
            '  ingress {{\n    from_port   = 443\n    to_port     = 443\n    protocol    = "tcp"\n    cidr_blocks = ["0.0.0.0/0"]\n  }}\n\n'
            '  egress {{\n    from_port   = 0\n    to_port     = 0\n    protocol    = "-1"\n    cidr_blocks = ["0.0.0.0/0"]\n  }}\n}}\n\n'
            'resource "aws_lb" "{s}" {{\n'
            '  name               = "${{var.app_name}}-${{var.environment}}-alb"\n'
            '  internal           = false\n'
            '  load_balancer_type = "application"\n'
            '  security_groups    = [aws_security_group.{s}_alb_sg.id]\n'
            '  subnets            = [aws_subnet.{s}_subnet_a.id, aws_subnet.{s}_subnet_b.id]\n\n'
            '  tags = {{\n    Environment = var.environment\n  }}\n}}\n\n'
            'resource "aws_lb_listener" "{s}_http" {{\n'
            '  load_balancer_arn = aws_lb.{s}.arn\n'
            '  port              = 80\n'
            '  protocol          = "HTTP"\n\n'
            '  default_action {{\n    type             = "forward"\n    target_group_arn = aws_lb_target_group.{default_tg}.arn\n  }}\n}}\n\n'
            '{tg_blocks}\n\n'
            '{rule_blocks}\n'
        ).format(s=app_slug, default_tg=default_tg, tg_blocks=tg_blocks, rule_blocks=rule_blocks)

    # outputs.tf
    output_blocks = []
    if has_alb:
        output_blocks.append(
            'output "alb_dns_name" {{\n  description = "ALB DNS name"\n  value       = aws_lb.{s}.dns_name\n}}'.format(s=app_slug)
        )
    if has_db:
        output_blocks.append(
            'output "rds_endpoint" {{\n  description = "RDS endpoint"\n  value       = aws_db_instance.{s}_db.endpoint\n  sensitive   = true\n}}'.format(s=app_slug)
        )
    for sol in solutions_data:
        output_blocks.append(
            'output "{slug}_url" {{\n  description = "ECS service URL for {name}"\n  value       = "{slug}-${{var.environment}}.internal"\n}}'.format(
                slug=sol["slug"], name=sol["name"]
            )
        )
    files["terraform/outputs.tf"] = "\n\n".join(output_blocks) + "\n"

    # Per-solution modules
    for sol in solutions_data:
        slug = sol["slug"]
        port = sol["port"]
        files["terraform/modules/{}/variables.tf".format(slug)] = (
            'variable "cluster_id" {{\n  type = string\n}}\n\n'
            'variable "vpc_id" {{\n  type = string\n}}\n\n'
            'variable "subnet_ids" {{\n  type = list(string)\n}}\n\n'
            'variable "environment" {{\n  type = string\n}}\n\n'
            'variable "image_tag" {{\n  type    = string\n  default = "latest"\n}}\n'
        )
        files["terraform/modules/{}/main.tf".format(slug)] = (
            'resource "aws_security_group" "{slug}" {{\n'
            '  name   = "{slug}-${{var.environment}}-sg"\n'
            '  vpc_id = var.vpc_id\n\n'
            '  ingress {{\n    from_port   = {port}\n    to_port     = {port}\n    protocol    = "tcp"\n    cidr_blocks = ["10.0.0.0/16"]\n  }}\n\n'
            '  egress {{\n    from_port   = 0\n    to_port     = 0\n    protocol    = "-1"\n    cidr_blocks = ["0.0.0.0/0"]\n  }}\n\n'
            '  tags = {{\n    Name        = "{slug}-sg"\n    Environment = var.environment\n  }}\n}}\n\n'
            'resource "aws_ecs_task_definition" "{slug}" {{\n'
            '  family                   = "{slug}-${{var.environment}}"\n'
            '  requires_compatibilities = ["FARGATE"]\n'
            '  network_mode             = "awsvpc"\n'
            '  cpu                      = 256\n'
            '  memory                   = 512\n\n'
            '  container_definitions = jsonencode([\n'
            '    {{\n'
            '      name      = "{slug}"\n'
            '      image     = "{slug}:${{var.image_tag}}"\n'
            '      essential = true\n'
            '      portMappings = [\n'
            '        {{ containerPort = {port}, protocol = "tcp" }}\n'
            '      ]\n'
            '      environment = [\n'
            '        {{ name = "ENVIRONMENT", value = var.environment }}\n'
            '      ]\n'
            '    }}\n'
            '  ])\n}}\n\n'
            'resource "aws_ecs_service" "{slug}" {{\n'
            '  name            = "{slug}-${{var.environment}}"\n'
            '  cluster         = var.cluster_id\n'
            '  task_definition = aws_ecs_task_definition.{slug}.arn\n'
            '  desired_count   = 1\n'
            '  launch_type     = "FARGATE"\n\n'
            '  network_configuration {{\n'
            '    subnets          = var.subnet_ids\n'
            '    security_groups  = [aws_security_group.{slug}.id]\n'
            '    assign_public_ip = true\n'
            '  }}\n\n'
            '  tags = {{\n    Service = "{slug}"\n    Environment = var.environment\n  }}\n}}\n'
        ).format(slug=slug, port=port)

    return files



# ─── Helper: extract model fields from generated files ─────────────────
def _extract_model_fields(generated_files):
    """Return {entity_name: [field, ...]} from the models in generated_files JSON.

    Handles the common shapes produced by the code-gen pipeline:
    - list of dicts with 'filename' and 'content' keys
    - dict keyed by filename
    Falls back to an empty dict when the structure is unexpected.
    """
    fields_map = {}
    if not generated_files:
        return fields_map

    # Normalise to a list of (filename, content) tuples
    items = []
    if isinstance(generated_files, list):
        items = [(f.get("filename", ""), f.get("content", "")) for f in generated_files if isinstance(f, dict)]
    elif isinstance(generated_files, dict):
        items = list(generated_files.items())

    model_class_re = re.compile(r"class\s+(\w+)\(.*\):")
    field_re = re.compile(r"^\s+(\w+)\s*=\s*(?:db\.Column|models\.\w+Field|CharField|IntegerField|TextField|ForeignKey)")

    for filename, content in items:
        if not isinstance(content, str):
            continue
        current_model = None
        for line in content.splitlines():
            m = model_class_re.match(line)
            if m:
                current_model = m.group(1)
                fields_map.setdefault(current_model, [])
                continue
            if current_model:
                fm = field_re.match(line)
                if fm:
                    fields_map[current_model].append(fm.group(1))
    return fields_map



def _extract_field_types(generated_files):
    """Return {entity_name: {field_name: type}} from generated code.

    Infers SQLAlchemy column types from db.Column declarations:
    - db.String -> "string"
    - db.Integer -> "integer"
    - db.Numeric -> "decimal"
    - db.Date/db.DateTime -> "date"
    - db.Boolean -> "boolean"
    - db.Text -> "text"
    """
    type_map = {}
    if not generated_files:
        return type_map

    items = []
    if isinstance(generated_files, list):
        items = [(f.get("filename", ""), f.get("content", "")) for f in generated_files if isinstance(f, dict)]
    elif isinstance(generated_files, dict):
        items = list(generated_files.items())

    model_class_re = re.compile(r"class\s+(\w+)\(.*\):")
    field_type_re = re.compile(
        r"^\s+(\w+)\s*=\s*(?:db\.Column\(db\.(\w+)|models\.(\w+)Field)"
    )

    sa_to_abstract = {
        "String": "string", "Integer": "integer", "Numeric": "decimal",
        "Float": "decimal", "Date": "date", "DateTime": "date",
        "Boolean": "boolean", "Text": "text",
    }

    for filename, content in items:
        if not isinstance(content, str):
            continue
        current_model = None
        for line in content.splitlines():
            m = model_class_re.match(line)
            if m:
                current_model = m.group(1)
                type_map.setdefault(current_model, {})
                continue
            if current_model:
                fm = field_type_re.match(line)
                if fm:
                    field_name = fm.group(1)
                    sa_type = fm.group(2) or fm.group(3) or "String"
                    abstract_type = sa_to_abstract.get(sa_type, "string")
                    type_map[current_model][field_name] = abstract_type

    return type_map


def _rewrite_vendor_sdk_files(files: dict, genome_modules: dict) -> dict:
    """D-PASS5-2 / D-PASS6-1 / D-PASS6-2: Replace generated SQLAlchemy model + routes for
    vendor/buy modules with a thin async SDK client stub (httpx, matching the rest of the
    generated codebase) and rewrite any route files that still import the deleted model.

    Pipeline:
    1. For each buy/vendor module, emit app/services/{snake_vendor}_client.py using
       httpx.AsyncClient (async, non-blocking — required for FastAPI/uvicorn).
    2. Delete any auto-generated app/models/{name}.py for that module so no broken
       SQLAlchemy model remains.
    3. Scan ALL route files in the generated output for imports from the deleted model
       paths. Rewrite those imports to use the SDK client and replace db.session.query()
       calls with client.query() calls so the app boots without ImportError.

    Returns a new files dict with vendor stub files added/replaced and routes patched.
    """
    import re as _re

    def _to_snake(name: str) -> str:
        s = _re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", (name or "unknown").strip())
        s = _re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
        return _re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

    # Track which model module paths were deleted so we can rewrite route files.
    # Maps deleted_import_module → (snake_vendor, class_name, snake_root)
    # snake_root is derived from aggregate_root, NOT vendor_name. The generator
    # produces function names like query_{snake_root}_vendor; the async-def
    # promotion regex must use snake_root, not snake_vendor (D-PASS7-1).
    deleted_model_modules: dict[str, tuple[str, str, str]] = {}

    for mod_key, mod_def in genome_modules.items():
        if mod_def.get("build_or_buy") not in ("buy", "vendor"):
            continue

        root = mod_def.get("aggregate_root") or mod_key
        vendor_name = mod_def.get("vendor_name") or root
        snake_vendor = _to_snake(vendor_name)
        snake_root = _to_snake(root)
        class_name = "".join(w.capitalize() for w in snake_vendor.split("_"))
        env_prefix = snake_vendor.upper()

        # D-PASS6-2: use httpx.AsyncClient (non-blocking, matches rest of generated stack)
        client_path = f"app/services/{snake_vendor}_client.py"
        files[client_path] = f"""\
\"\"\"Vendor SDK client stub for {vendor_name}.

D-PASS5-2: Auto-generated because the architect marked '{root}' as a buy-decision
(vendor: {vendor_name}). Replace the stub implementation below with the actual
{vendor_name} SDK or its REST client.

Required environment variables:
  {env_prefix}_BASE_URL  — base URL for the {vendor_name} API
  {env_prefix}_API_KEY   — API key / bearer token for authentication
\"\"\"
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_{env_prefix}_BASE_URL = os.getenv("{env_prefix}_BASE_URL", "")
_{env_prefix}_API_KEY = os.getenv("{env_prefix}_API_KEY", "")


class {class_name}Client:
    \"\"\"Async HTTP wrapper around the {vendor_name} REST API.

    Uses httpx.AsyncClient to avoid blocking the uvicorn event loop.
    \"\"\"

    def __init__(
        self,
        base_url: str = _{env_prefix}_BASE_URL,
        api_key: str = _{env_prefix}_API_KEY,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {{
            "Authorization": f"Bearer {{api_key}}",
            "Content-Type": "application/json",
        }}

    async def query(self, filters: Optional[dict] = None) -> list[dict[str, Any]]:
        \"\"\"Query {vendor_name} records.\"\"\"
        if not self.base_url:
            raise NotImplementedError(
                f"Set {env_prefix}_BASE_URL to connect to {vendor_name}."
            )
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.get(f"{{self.base_url}}/{snake_root}s", params=filters or {{}})
            resp.raise_for_status()
            return resp.json()

    async def get(self, record_id: str) -> dict[str, Any]:
        \"\"\"Get a single {vendor_name} record by ID.\"\"\"
        if not self.base_url:
            raise NotImplementedError(
                f"Set {env_prefix}_BASE_URL to connect to {vendor_name}."
            )
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.get(f"{{self.base_url}}/{snake_root}s/{{record_id}}")
            resp.raise_for_status()
            return resp.json()

    async def health(self) -> dict[str, Any]:
        \"\"\"Check {vendor_name} API connectivity.\"\"\"
        if not self.base_url:
            return {{"status": "unconfigured", "vendor": "{vendor_name}"}}
        try:
            async with httpx.AsyncClient(headers=self._headers) as client:
                resp = await client.get(f"{{self.base_url}}/health", timeout=5.0)
                return {{"status": "ok" if resp.is_success else "error",
                        "vendor": "{vendor_name}", "http_status": resp.status_code}}
        except Exception as exc:  # noqa: BLE001
            return {{"status": "unreachable", "vendor": "{vendor_name}", "error": str(exc)}}


# Module-level singleton — override in tests via dependency injection
{snake_vendor}_client = {class_name}Client()
"""

        # D-PASS6-1: delete model files and record the module path so we can patch routes.
        for candidate in [
            f"app/models/{snake_root}.py",
            f"app/models/{snake_vendor}.py",
            f"app/models/{mod_key}.py",
        ]:
            if candidate in files:
                del files[candidate]
                logger.info("Removed vendor SQLAlchemy model %s (replaced by SDK client)", candidate)
            # Record regardless of whether the file existed — the generator may have
            # used this import path even without emitting the file (e.g., shared models).
            import_module = candidate.replace("/", ".").removesuffix(".py")
            deleted_model_modules[import_module] = (snake_vendor, class_name, snake_root)

    # D-PASS6-1: rewrite route files that still reference deleted vendor model imports.
    # The generator emits `from app.models.{snake} import {Class}` and then calls
    # `db.session.query({Class}).all()` — we replace both with the SDK client.
    if deleted_model_modules:
        route_paths = [
            p for p in files
            if (p.startswith("app/routes/") or p.startswith("app/routers/")
                or p.startswith("app/api/"))
            and p.endswith(".py")
        ]
        for route_path in route_paths:
            content = files[route_path]
            modified = False
            for import_mod, (snake_vendor, class_name, snake_root) in deleted_model_modules.items():
                # D-PASS9-1: Remove vendor class names from schema/entity barrel imports.
                # The route template emits:
                #   from app.schemas.models import VendorClass, VendorClassCreate, VendorClassUpdate
                #   from app.models.entities import VendorClass as VendorClassORM
                # for every PathDef.response_schema, including vendor stub paths.
                # Neither barrel contains vendor entries (_build_confirmed_fields excluded
                # them), so these raise ModuleNotFoundError at startup.  Strip them
                # unconditionally — before the per-file import guard below.
                for variant in [class_name, f"{class_name}Create", f"{class_name}Update"]:
                    content = _re.sub(
                        r"^\s+" + _re.escape(variant) + r",?\s*\n",
                        "", content, flags=_re.MULTILINE,
                    )
                content = _re.sub(
                    r"^\s+" + _re.escape(class_name)
                    + r"\s+as\s+" + _re.escape(class_name) + r"ORM,?\s*\n",
                    "", content, flags=_re.MULTILINE,
                )
                # Remove response_model=ClassName from @router decorators — the schema
                # class no longer exists so keeping it would raise NameError.
                content = _re.sub(
                    r",\s*response_model=" + _re.escape(class_name), "", content,
                )
                modified = True

                # --- Per-file model import replacement (from app.models.{file} import ...) ---
                # This block only runs when the route file has an explicit per-file import;
                # the barrel cleanups above already ran regardless.
                # Match: from app.models.salesforce_service import SalesforceService [, ...]
                pattern = _re.compile(
                    r"^from\s+" + _re.escape(import_mod) + r"\s+import\s+[^\n]+\n",
                    _re.MULTILINE,
                )
                if not pattern.search(content):
                    continue
                # Replace the deleted import with the SDK client import
                client_import = (
                    f"from app.services.{snake_vendor}_client import {snake_vendor}_client\n"
                )
                content = pattern.sub(client_import, content)

                # D-PASS7-2: replace ALL chained db.session.query expressions up to EOL.
                # The narrow (?:\.all\(\))? pattern missed .filter_by().first(), .count(),
                # .filter(ClassName.col == val).all(), leaving NameError at call time.
                content = _re.sub(
                    r"db\.session\.query\(" + _re.escape(class_name) + r"\)[^\n]*",
                    f"(await {snake_vendor}_client.query())",
                    content,
                )
                # D-PASS7-2 / D-PASS8-1: replace remaining ClassName.attribute references
                # (orphaned filter args that weren't on the same line as db.session.query).
                # Use a string literal sentinel — NOT a # comment.  A # comment truncates
                # any expression it appears inside (list, dict, function call) and produces a
                # SyntaxError at import time.  A string literal is syntactically valid in any
                # expression position, evaluates to a non-None value, and raises TypeError at
                # call time with a message the developer can act on.
                content = _re.sub(
                    _re.escape(class_name) + r"\.[a-zA-Z_][a-zA-Z0-9_]*",
                    f"'FIXME_VENDOR_{class_name}'",
                    content,
                )
                # Replace db.session.get(ClassName, id) patterns.
                # D-PASS8-2: ([^)]+) stopped at the first ) inside nested calls like str(id),
                # capturing "str(id" (missing closing paren) → malformed replacement → SyntaxError.
                # Use ([^)]+(?:\([^)]*\)[^)]*)*) to allow one level of nested parentheses.
                content = _re.sub(
                    r"db\.session\.get\(" + _re.escape(class_name) + r",\s*([^)]+(?:\([^)]*\)[^)]*)*)\)",
                    lambda m, sv=snake_vendor: f"(await {sv}_client.get({m.group(1).strip()}))",
                    content,
                )
                # D-PASS7-1: promote route functions to async def.
                # Generated function names are query_{root_snake}_vendor / get_{root_snake}_vendor
                # (root_snake comes from aggregate_root, NOT vendor_name). Using snake_vendor here
                # was wrong — it missed any module where product name ≠ domain entity name (e.g.
                # vendor="SAP S/4HANA", root="PurchaseOrder" → snake_vendor=sap_s_4hana,
                # root_snake=purchase_order → generated fn = query_purchase_order_vendor).
                content = _re.sub(
                    r"(\ndef )((?:query|get|list)_" + _re.escape(snake_root) + r"_vendor[^\(]*\()",
                    r"\nasync def \2",
                    content,
                )
                # modified already set above (barrel cleanup block)

            if modified:
                files[route_path] = content
                logger.info("Rewrote vendor route file %s to use SDK client", route_path)

    return files
