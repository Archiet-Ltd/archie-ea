"""
DeterministicCodeGenerator — Transform ProductSpecBundle into runnable code files.

Wave 1: Pure Jinja2 template rendering. No LLM. No network. Deterministic.
Wave 2: Renders confirmed specs (models, handlers, clients, k8s, helm, migrations).
"""
from app.modules.solutions_product.services.product_spec_bundle import InfraContext
import hashlib
import html
import logging
import os
import re
import yaml
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from jinja2 import BaseLoader, ChoiceLoader, Environment, FileSystemLoader, TemplateNotFound


class _DbTemplateLoader(BaseLoader):
    """Jinja2 loader that resolves templates from CodegenTemplateFile DB rows.

    Falls through (raises TemplateNotFound) for templates not in the DB,
    allowing the downstream FileSystemLoader to handle them.
    """

    def __init__(self, set_id):
        self.set_id = set_id

    def get_source(self, environment, template):
        from app.modules.codegen.models import CodegenTemplateFile
        tf = CodegenTemplateFile.query.filter_by(
            set_id=self.set_id, template_name=template
        ).first()
        if tf is None:
            raise TemplateNotFound(template)
        # Return (source, filename, uptodate). filename=None, uptodate=always-stale.
        return tf.content, None, lambda: False


@dataclass
class GeneratedFile:
    path: str
    content: str
    archimate_sources: list[int] = field(default_factory=list)


@dataclass
class GeneratedCodeBundle:
    solution_id: int
    bundle_id: str
    language: str
    spec_hash: str
    files: list[GeneratedFile] = field(default_factory=list)
    production_readiness: object = field(default=None, repr=False)


# Supported languages for code generation
SUPPORTED_LANGUAGES = {"python-fastapi", "python-flask", "go-chi", "salesforce-apex", "java-spring-boot", "react-shadcn", "react-native-expo", "sap-cap", "sap-btp-integration", "flask-nextjs", "flask-react"}

# Supported architecture styles
SUPPORTED_ARCH_STYLES = {"microservices", "event_driven", "serverless"}

# JSON Schema type → Python type mapping
_TYPE_MAP = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "float": "float",
    "decimal": "Decimal",  # Use Decimal not float for monetary/precision fields
    "boolean": "bool",
    "enum": "str",
    "timestamp": "datetime",
    "array": "list",
    "object": "dict",
}

# JSON Schema type → Go type mapping
_GO_TYPE_MAP = {
    "string": "string",
    "integer": "int64",
    "number": "float64",
    "boolean": "bool",
    "array": "[]interface{}",
    "object": "map[string]interface{}",
}

# JSON Schema type → Java type mapping
_JAVA_TYPE_MAP = {
    "string": "String",
    "str": "String",        # Python type alias
    "text": "String",
    "integer": "Long",
    "int": "Long",           # Python type alias
    "number": "BigDecimal",
    "float": "BigDecimal",   # Python type alias
    "decimal": "BigDecimal",
    "Decimal": "BigDecimal", # Python type alias (capitalized)
    "boolean": "Boolean",
    "bool": "Boolean",       # Python type alias
    "array": "List<Object>",
    "object": "Map<String, Object>",
    "datetime": "Instant",
    "timestamp": "Instant",
    "uuid": "String",
    "UUID": "String",        # Python type alias (capitalized)
    "json": "String",
}


def _map_json_type(json_type, json_format=None):
    """Map JSON Schema type+format to Python type annotation."""
    if json_format == "date-time":
        return "datetime"
    if json_format == "date":
        return "str"
    if json_format == "uuid":
        return "str"
    return _TYPE_MAP.get(json_type, "Any")


def _map_go_type(json_type, json_format=None):
    """Map JSON Schema type+format to Go type annotation."""
    if json_format == "date-time":
        return "time.Time"
    if json_format == "uuid":
        return "uuid.UUID"
    if json_format == "email":
        return "string"
    if json_format == "date":
        return "string"
    return _GO_TYPE_MAP.get(json_type, "interface{}")


_TYPE_NORMALIZE = {
    "str": "string", "int": "integer", "float": "number", "bool": "boolean",
    "Decimal": "decimal", "UUID": "uuid", "date-time": "datetime",
}


def _normalize_type(raw_type):
    """Normalize Python-style types to standard type names."""
    return _TYPE_NORMALIZE.get(raw_type, raw_type)


def _map_java_type(json_type, json_format=None):
    """Map JSON Schema type+format to Java type annotation."""
    if json_format == "date-time":
        return "Instant"
    if json_format == "uuid":
        return "String"
    if json_format == "email":
        return "String"
    if json_format == "date":
        return "String"
    return _JAVA_TYPE_MAP.get(json_type, "Object")


def _java_package(name):
    """Convert a solution name to a valid Java package segment (lowercase alphanumeric)."""
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", (name or "solution").strip())
    return cleaned.lower() or "solution"


def _salesforce_normalize_namespace(namespace: str) -> str:
    """Normalize a Salesforce namespace prefix to the platform-safe form."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", (namespace or "").strip())
    if not cleaned:
        return ""
    if not cleaned[0].isalpha():
        cleaned = f"N{cleaned}"
    return cleaned[:15]


def _salesforce_normalize_package_mode(package_mode: str, namespace_prefix: str) -> str:
    """Collapse packaging modes to Entelim's managed/unmanaged Salesforce variants."""
    normalized = (package_mode or "").strip().lower().replace("_", "-")
    if normalized in {"managed", "managed-package", "app-exchange", "appexchange"}:
        return "managed"
    if normalized in {"unmanaged", "source-only", "source"}:
        return "unmanaged"
    return "managed" if namespace_prefix else "unmanaged"


def _salesforce_custom_api_name(name: str, namespace_prefix: str) -> str:
    """Build a custom object/field API name, adding namespace only for managed output."""
    base = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]", "_", (name or "").strip())).strip("_")
    if not base:
        base = "Generated"
    api_name = f"{base}__c"
    return f"{namespace_prefix}__{api_name}" if namespace_prefix else api_name


def _salesforce_reference_object_api_name(foreign_key: str, namespace_prefix: str) -> str:
    """Build a lookup target object API name from a foreign-key reference."""
    target = (foreign_key or "").split(".")[0]
    return _salesforce_custom_api_name(_pascal(target), namespace_prefix)


def _salesforce_package_settings(bundle) -> dict:
    """Read optional package settings from bundle metadata without depending on UI wiring."""
    merged: dict = {}

    def _merge(source):
        if not isinstance(source, dict):
            return
        salesforce = source.get("salesforce")
        if isinstance(salesforce, dict):
            for key, value in salesforce.items():
                if value not in (None, ""):
                    merged[key] = value
        for key in (
            "package_mode",
            "package_name",
            "package_version_name",
            "package_version_number",
            "namespace",
            "namespace_prefix",
            "package_namespace",
        ):
            for candidate in (key, f"salesforce_{key}"):
                value = source.get(candidate)
                if value not in (None, ""):
                    merged[key] = value
                    break

    for attr_name in ("product_config", "ci_cd", "deployment", "seed_context"):
        _merge(getattr(bundle, attr_name, None))

    namespace_prefix = _salesforce_normalize_namespace(
        merged.get("namespace_prefix") or merged.get("namespace") or merged.get("package_namespace")
    )
    package_mode = _salesforce_normalize_package_mode(merged.get("package_mode"), namespace_prefix)
    if package_mode != "managed":
        namespace_prefix = ""

    package_name = (merged.get("package_name") or bundle.solution_name or "archie-salesforce-package").strip()
    return {
        "package_mode": package_mode,
        "is_managed_package": package_mode == "managed",
        "namespace_prefix": namespace_prefix,
        "package_name": package_name,
        "package_version_name": (merged.get("package_version_name") or "ver 1.0").strip(),
        "package_version_number": (merged.get("package_version_number") or "1.0.0.NEXT").strip(),
    }


def _salesforce_integration_dict(integration_def) -> dict:
    """Convert IntegrationDef-style objects into plain dictionaries."""
    if isinstance(integration_def, dict):
        result = dict(integration_def)
    elif hasattr(integration_def, "__dict__"):
        result = dict(vars(integration_def))
    else:
        result = {}
    endpoints = result.get("endpoints") or []
    normalized_endpoints = []
    for endpoint in endpoints:
        if isinstance(endpoint, dict):
            normalized_endpoints.append(dict(endpoint))
        elif hasattr(endpoint, "__dict__"):
            normalized_endpoints.append(dict(vars(endpoint)))
    result["endpoints"] = normalized_endpoints
    return result


def _salesforce_is_sap_integration(integration_name: str, integration_def: dict) -> bool:
    """Identify SAP/OData integrations that should emit the hardened sync class."""
    haystack = " ".join(
        str(part or "")
        for part in (
            integration_name,
            integration_def.get("protocol"),
            integration_def.get("base_url"),
            integration_def.get("direction"),
        )
    ).lower()
    return "sap" in haystack or "odata" in haystack


def _salesforce_extract_odata_service(integration_name: str, integration_def: dict) -> str:
    """Extract an SAP OData service name from integration metadata."""
    base_url = str(integration_def.get("base_url") or "")
    match = re.search(r"/sap/opu/odata/sap/([^/?]+)", base_url, re.IGNORECASE)
    if match:
        return match.group(1)
    for endpoint in integration_def.get("endpoints") or []:
        path = str(endpoint.get("path") or "")
        match = re.search(r"/sap/opu/odata/sap/([^/?]+)", path, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _salesforce_extract_source_entity(integration_name: str, integration_def: dict) -> str:
    """Extract the SAP entity-set stem for the generated integration class."""
    for endpoint in integration_def.get("endpoints") or []:
        path = str(endpoint.get("path") or "")
        match = re.search(r"/([A-Za-z0-9_]+)Set(?:\?|/|$)", path)
        if match:
            return match.group(1)
    candidate = _pascal(integration_name.replace("SAP", "").replace("sap", ""))
    return re.sub(r"[^A-Za-z0-9_]", "", candidate or "Entity")


def _salesforce_field_mappings(model_def: dict, field_api_name) -> list[dict]:
    """Project confirmed model fields into deterministic SAP→Salesforce mappings."""
    mappings = []
    for field_def in model_def.get("fields", []):
        field_name = field_def.get("name")
        if not field_name or field_name in {"id", "created_at", "updated_at"}:
            continue
        mappings.append(
            {
                "source": _pascal(field_name),
                "target": field_api_name(field_name),
            }
        )
    return mappings


# ── Display name helpers ──────────────────────────────────────────────────────
# Acronyms that should be preserved/restored in human-readable labels
_DISPLAY_ACRONYMS: dict = {
    "cicd": "CI/CD", "api": "API", "kpi": "KPI", "kpis": "KPIs",
    "iam": "IAM", "crm": "CRM", "erp": "ERP", "ui": "UI", "ux": "UX",
    "sla": "SLA", "sso": "SSO", "jwt": "JWT", "sdk": "SDK", "cli": "CLI",
    "url": "URL", "uri": "URI", "bpm": "BPM", "bi": "BI", "ai": "AI",
    "ml": "ML", "nlp": "NLP", "ocr": "OCR", "llm": "LLM", "qa": "QA",
    "cad": "CAD", "bim": "BIM", "iot": "IoT", "id": "ID",
    "saas": "SaaS", "paas": "PaaS",
}
# Connector words lowercased in mid-phrase (unless first word)
_DISPLAY_CONNECTORS: set = {"and", "or", "of", "the", "a", "an", "in", "on", "at", "to", "for", "with", "by"}
# Abstract nouns/system-names that are NOT meaningfully pluralised by appending 's'
_PLURALIZE_UNCOUNTABLE_EXTRA: set = {
    "management", "processing", "configuration", "integration", "monitoring",
    "automation", "infrastructure", "orchestration", "deployment", "authentication",
    "authorization", "governance", "intelligence", "compliance", "reporting",
    "analytics", "messaging", "networking", "operations", "administration",
    "extraction", "classification", "interpretation", "generation", "optimization",
    "normalization", "standardization", "synchronization", "aggregation",
}


def _humanize_pascal(pascal_name: str, meta_display: str = "") -> str:
    """Convert PascalCase entity name to a human-readable display label.

    Handles acronyms (CicdToolchain→CI/CD Toolchain, KpIs→KPIs),
    connector words (And→and), and explicit meta display names.

    Examples:
        "CicdToolchain"               → "CI/CD Toolchain"
        "KeyMetricsAndKpIs"           → "Key Metrics and KPIs"
        "IdentityAndAccessManagement" → "Identity and Access Management"
        "CoreBusiness"                → "Core Business"
    """
    if meta_display:
        return meta_display
    # Split on PascalCase word boundaries
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", pascal_name)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1 \2", s)
    words = s.split()
    result: list = []
    i = 0
    while i < len(words):
        word = words[i]
        low = word.lower()
        # Try combining current + next word as compound acronym (e.g. "Kp"+"Is" → "kpis" → "KPIs")
        if i + 1 < len(words) and len(word) <= 3 and low not in _DISPLAY_CONNECTORS:
            compound = low + words[i + 1].lower()
            if compound in _DISPLAY_ACRONYMS:
                result.append(_DISPLAY_ACRONYMS[compound])
                i += 2
                continue
        if low in _DISPLAY_ACRONYMS:
            result.append(_DISPLAY_ACRONYMS[low])
        elif i > 0 and low in _DISPLAY_CONNECTORS:
            result.append(low)  # lowercase connector mid-phrase
        else:
            result.append(word.capitalize())
        i += 1
    return " ".join(result) if result else pascal_name


def _snake(name):
    """Convert to snake_case. Prefixes with 'entity_' if result starts with a digit
    (Python identifiers cannot start with digits — e.g. '15 Years Of Payroll History')."""
    s = re.sub(r"[^a-zA-Z0-9]", "_", (name or "unknown").strip())
    result = re.sub(r"_+", "_", s).strip("_").lower()
    if result and result[0].isdigit():
        result = "entity_" + result
    return result


def _pascal_to_snake(name):
    """Convert PascalCase to snake_case (e.g. WorkOrder → work_order, EmployeeData → employee_data)."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", (name or "unknown").strip())
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _pluralize(word):
    """Pluralize an English word. Handles common patterns for table/endpoint names."""
    if not word:
        return word
    w = word.rstrip("_")
    suffix = word[len(w):]  # preserve trailing underscores
    low = w.lower()
    # Uncountable / already-plural nouns — return as-is
    _base_uncountable = {"data", "information", "research", "evidence", "equipment", "staff", "news"}
    if low in _base_uncountable or low in _PLURALIZE_UNCOUNTABLE_EXTRA:
        return word
    # Already plural (ends in s but not ss/us/is)
    if low.endswith("s") and not low.endswith(("ss", "us", "is")):
        return word
    # -is → -es (analysis → analyses)
    if low.endswith("is"):
        return w[:-2] + "es" + suffix
    # -ss, -sh, -ch, -x, -z → +es
    if low.endswith(("ss", "sh", "ch", "x", "z")):
        return w + "es" + suffix
    # consonant + y → -ies (history → histories, entity → entities)
    if low.endswith("y") and len(low) > 1 and low[-2] not in "aeiou":
        return w[:-1] + "ies" + suffix
    # -f → -ves (leaf → leaves), -fe → -ves (knife → knives)
    if low.endswith("fe"):
        return w[:-2] + "ves" + suffix
    if low.endswith("f") and not low.endswith("ff"):
        return w[:-1] + "ves" + suffix
    # Default: +s
    return w + "s" + suffix


def _camel(name):
    """Convert snake_case or space-separated to camelCase (e.g. employee_number → employeeNumber)."""
    parts = _pascal_to_snake(name).split("_")
    return parts[0] + "".join(w.capitalize() for w in parts[1:]) if parts else name


def _kebab(name):
    """Convert to kebab-case."""
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", (name or "unknown").strip())
    return re.sub(r"[\s_]+", "-", s).lower().strip("-")


def _pascal(name):
    """Convert to PascalCase. Handles snake_case, space-separated, AND existing PascalCase/camelCase.

    Examples:
        "work_order"      → "WorkOrder"
        "Work Order"      → "WorkOrder"
        "WorkOrder"       → "WorkOrder"  (preserved, not mangled to "Workorder")
        "workOrder"       → "WorkOrder"
        "API gateway"     → "ApiGateway"
        "work_order_line" → "WorkOrderLine"
    """
    sanitized = re.sub(r"[^a-zA-Z0-9\s_\-]", "", (name or "Unknown").strip())
    # First split on explicit separators (spaces, underscores, hyphens)
    segments = re.split(r"[\s_\-]+", sanitized)
    # Then split each segment on camelCase boundaries (e.g., "WorkOrder" → ["Work", "Order"])
    words = []
    for seg in segments:
        if not seg:
            continue
        # Split on transitions: lowercase→uppercase or end of uppercase run
        parts = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", seg)
        parts = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", parts)
        words.extend(parts.split("_"))
    result = "".join(w.capitalize() for w in words if w)
    result = result or "Unknown"
    # Python class names cannot start with a digit
    if result[0].isdigit():
        result = "Entity" + result
    return result


def _replace_path_params(path):
    """Replace {id} with 1 for test URLs."""
    return re.sub(r"\{[^}]+\}", "1", path)


class DeterministicCodeGenerator:
    """Generate runnable code from a ProductSpecBundle.

    Supports: python-fastapi, go-chi, salesforce-apex, java-spring-boot.
    Pure template rendering — deterministic output for the same input.
    """

    def __init__(self, language="python-fastapi"):
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Unsupported language: {language}. "
                f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
            )
        self.language = language
        templates_root = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates",
        )
        # Map language identifier to template directory name
        _LANG_DIR_MAP = {
            "react-native-expo": "react_native_expo",
            "flask-nextjs": "python_flask",
            "flask-react": "python_flask",
        }
        dir_name = _LANG_DIR_MAP.get(language, language.replace("-", "_"))
        template_dir = os.path.join(templates_root, dir_name)
        # Flask-based languages share many templates with python-fastapi; use
        # ChoiceLoader so Flask-specific overrides take priority and fastapi
        # acts as fallback.
        _fallback_dirs = []
        if language in ("python-flask", "flask-nextjs", "flask-react"):
            _fallback_dirs.append(os.path.join(templates_root, "python_fastapi"))
        _loaders = [FileSystemLoader(template_dir)] + [FileSystemLoader(d) for d in _fallback_dirs]
        self._env = Environment(
            loader=ChoiceLoader(_loaders),
            keep_trailing_newline=True,
            # autoescape only for HTML-suffixed templates: this same
            # Environment renders every generated file (Go, Python, YAML,
            # shell, JS as well as the UI's *.html.j2 templates), and a
            # blanket autoescape=True would HTML-escape '<'/'>'/'&' inside
            # generated Go generics, Python code, YAML, etc. -- corrupting
            # every generated backend. Scoped this way, entity/field labels
            # (architect-authored, unconstrained text) rendered into the
            # generated app's own UI templates are escaped, while every
            # non-HTML template is untouched. Found 11 Sep 2026 while
            # triaging the raw-html-escaping gate: this Environment had no
            # autoescape configuration at all.
            autoescape=lambda name: bool(name) and name.endswith((".html.j2", ".htm.j2")),
        )
        # Shared templates (Terraform, seed SQL, Makefile, README, .env) used by both languages
        shared_dir = os.path.join(templates_root, "shared")
        self._shared_env = Environment(
            loader=FileSystemLoader(shared_dir),
            keep_trailing_newline=True,
        )
        self._salesforce_validator = None
        if language == "salesforce-apex":
            from app.services.salesforce_validator import SalesforceValidator

            self._salesforce_validator = SalesforceValidator()

    def _validate_salesforce_code(self, file_path, content):
        """Raise immediately when generated Apex fails AppExchange blocking checks."""
        if not self._salesforce_validator or not file_path.endswith((".cls", ".trigger")):
            return
        if "@isTest" in content:
            return
        violations = self._salesforce_validator.validate_apex_code(content)
        blocking = [
            violation for violation in violations
            if violation.get("severity") in {"critical", "error"}
        ]
        if not blocking:
            return
        summary = ", ".join(
            f"{violation.get('code')}({violation.get('severity')})"
            for violation in blocking
        )
        raise ValueError(f"Salesforce validation failed for {file_path}: {summary}")

    def generate(self, bundle, template_set_id=None, solution_id=None):
        """Generate a complete code project from a ProductSpecBundle.

        Args:
            bundle: ProductSpecBundle with parsed OpenAPI/AsyncAPI data.
            template_set_id: Optional CodegenTemplateSet.id — DB templates in this
                set take precedence over filesystem templates; unmatched templates
                fall back to the built-in FileSystemLoader.
            solution_id: Optional int — when set, injects basePath=/apps/{id} into
                the Next.js frontend so it can be served behind an nginx subpath proxy.

        Returns:
            GeneratedCodeBundle with all generated files.
        """
        if template_set_id is not None:
            db_loader = _DbTemplateLoader(template_set_id)
            original_env_loader = self._env.loader
            original_shared_loader = self._shared_env.loader
            self._env.loader = ChoiceLoader([db_loader, original_env_loader])
            self._shared_env.loader = ChoiceLoader([db_loader, original_shared_loader])
        try:
            if self.language == "react-shadcn":
                result = self._generate_react_shadcn(bundle)
            elif self.language == "react-native-expo":
                result = self._generate_react_native(bundle)
            elif self.language == "flask-nextjs":
                result = self._generate_flask_nextjs(bundle, solution_id=solution_id)
            elif self.language == "flask-react":
                result = self._generate_flask_react(bundle)
            elif self.language == "go-chi":
                result = self._generate_go(bundle)
            elif self.language == "salesforce-apex":
                result = self._generate_salesforce(bundle)
            elif self.language == "java-spring-boot":
                result = self._generate_java(bundle)
            elif self.language == "sap-cap":
                result = self._generate_sap_cap(bundle)
            elif self.language == "sap-btp-integration":
                result = self._generate_sap_btp_integration(bundle)
            elif self.language == "python-flask":
                result = self._generate_flask(bundle)
            else:
                result = self._generate_python(bundle)

            # Generate Next.js + shadcn/ui frontend alongside Python backends
            # (flask-nextjs and flask-react handle their own frontend internally)
            if self.language in ("python-fastapi", "python-flask"):
                try:
                    frontend_files = self._generate_nextjs_frontend(bundle, solution_id=solution_id)
                    result.files.extend(frontend_files)
                except Exception as _fe_err:
                    logger.warning("Next.js frontend generation failed (non-fatal): %s", _fe_err)

            result.files.append(GeneratedFile(
                path="ARCHITECTURE.md",
                content=self._generate_architecture_md(bundle, result),
            ))
            # Deduplication guard: if two generation passes produce the same output
            # path (e.g. genome emits two modules with identical snake_name), the
            # second write silently overwrites the first — data loss.  Keep the first
            # occurrence; log a warning for every collision.
            _seen_paths: dict = {}
            _deduped: list = []
            for _f in result.files:
                if _f.path in _seen_paths:
                    logger.warning(
                        "codegen: duplicate output path '%s' — keeping first occurrence, "
                        "discarding second.  Genome likely has two modules with the same slug.",
                        _f.path,
                    )
                else:
                    _seen_paths[_f.path] = True
                    _deduped.append(_f)
            result.files = _deduped

            # Quality gate: strip empty files — templates that render no content
            # should not appear in the output.
            result.files = [
                f for f in result.files
                if f.content and f.content.strip()
                and not all(
                    line.startswith("#") or line.strip() == ""
                    for line in f.content.splitlines()
                )
            ]

            # Production readiness scoring — 13-dimension validation
            try:
                from app.modules.solutions_product.services.quality_gate_service import QualityGateService
                qg = QualityGateService()
                result.production_readiness = qg.validate(result)
            except Exception as _qg_err:
                logger.debug("QualityGateService skipped: %s", _qg_err)
                result.production_readiness = None

            return result
        finally:
            if template_set_id is not None:
                self._env.loader = original_env_loader
                self._shared_env.loader = original_shared_loader

    # ── Python/FastAPI generation ──

    def _generate_python(self, bundle):
        """Generate a complete Python/FastAPI project."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        bundle_id = f"code_{now}"

        files = []
        # Resolve auth early so main.py.j2 can include the auth router
        _idp_early = getattr(bundle, "identity_provider", None) or {}
        # Default to jwt-local when no IdP type is specified — every generated app
        # needs a working /auth/login endpoint so the Next.js frontend can authenticate.
        _idp_type = _idp_early.get("type") or "jwt-local"
        is_jwt_local = _idp_type == "jwt-local"

        # Extract NFR flags from infra_context so templates can conditionally inject middleware
        _nfr_flags = {nfr["flag"] for nfr in (bundle.infra_context.nfrs or []) if nfr.get("flag")}

        ctx = {
            "solution_id": bundle.solution_id,
            "solution_name": bundle.solution_name,
            "bundle_id": bundle_id,
            "spec_hash": bundle.spec_hash,
            "maturity_score": bundle.maturity_score,
            "generated_at": now,
            "is_jwt_local": is_jwt_local,
            "nfr_rate_limiting": "rate_limiting" in _nfr_flags,
            "nfr_pagination": "pagination" in _nfr_flags,
            "nfr_audit_trail": "audit_trail" in _nfr_flags,
            "nfr_rbac": "rbac" in _nfr_flags,
            "nfr_ssl_redirect": "ssl_redirect" in _nfr_flags,
            "nfr_sentry": "sentry" in _nfr_flags,
            "identity_provider": _idp_early,
            "genome_compliance": getattr(bundle, "_genome_compliance", {}) or {},
            "genome_business_model": getattr(bundle, "_genome_business_model", {}) or {},
            "genome_operations": getattr(bundle, "_genome_operations", {}) or {},
            "multi_tenancy": getattr(bundle, "multi_tenancy", {}) or {},
            "tenant_field": (getattr(bundle, "multi_tenancy", {}) or {}).get("tenant_field"),
            "workers": getattr(bundle, "workers", []) or [],
            "payment_config": getattr(bundle, "payment_config", {}) or {},
            "genome_behavioral": getattr(bundle, "_behavioral", {}) or {},
        }

        # 1. Generate main.py
        files.append(self._render_main(ctx, bundle.services))

        # 2. Generate route files (one per service)
        # Pipeline modules get behavioral templates; CRUD modules get standard routes.
        has_handlers = bool(getattr(bundle, "business_rules", None))
        bp_flows = getattr(bundle, "business_process_flows", {}) or {}
        confirmed = getattr(bundle, "confirmed_fields", {}) or {}
        _state_machines = getattr(bundle, "state_machines", {}) or {}
        _biz_rules = getattr(bundle, "business_rules", {}) or {}
        _validation_rules = self._extract_validation_rules(_biz_rules)
        _genome_modules = getattr(bundle, "_genome_modules", {}) or {}
        # Build reverse lookup: aggregate_root → (mod_key, mod_def)
        _root_to_mod = {}
        for _mk, _md in _genome_modules.items():
            _root_to_mod[_md.get("aggregate_root", "")] = (_mk, _md)
            _root_to_mod[_mk] = (_mk, _md)
        for svc in bundle.services:
            # Check if this service's module is a pipeline type
            _svc_snake = _snake(svc.name)
            _mod_def = _genome_modules.get(_svc_snake, {})
            # Fallback: match by aggregate_root (handles camelCase→snake_case mismatch)
            if not _mod_def:
                _match = _root_to_mod.get(svc.name) or _root_to_mod.get(_svc_snake)
                if _match:
                    _mod_def = _match[1]
            if _mod_def.get("module_type") == "pipeline":
                # Render pipeline service template instead of CRUD router
                pipeline_file = self._render_pipeline_service(ctx, svc, _mod_def, bundle)
                if pipeline_file:
                    files.append(pipeline_file)
                    # Also render pipeline-specific tests
                    pipeline_test = self._render_pipeline_test(ctx, svc, _mod_def, bundle)
                    if pipeline_test:
                        files.append(pipeline_test)
                    continue

            flow_steps = bp_flows.get(svc.name, [])
            files.append(self._render_route(
                ctx, svc, bundle.schemas, has_handlers=has_handlers,
                flow_steps=flow_steps, confirmed_fields=confirmed,
                state_machines=_state_machines, validation_rules=_validation_rules,
            ))

        # 3. Generate schema files (enhanced with constraints + field validators from rules)
        schema_data = self._parse_component_schemas(bundle.openapi, bundle.schemas)
        if schema_data:
            spec_metadata = {}
            confirmed = getattr(bundle, "confirmed_fields", {}) or {}
            for name, fields in confirmed.items():
                if fields:
                    spec_metadata[_pascal(name)] = {"version": 0, "hash": ""}
            # Extract per-field validators from validation rules for Pydantic @field_validator
            field_validators = self._extract_field_validators(_biz_rules)
            files.extend(self._render_schemas(
                ctx, schema_data, spec_metadata=spec_metadata,
                field_validators=field_validators,
            ))

        # 4. Generate contract tests
        files.append(self._render_tests(ctx, bundle.services))

        # 4a. Generate comprehensive test suite (behavioral, edge cases, security, k6)
        files.extend(self._render_test_suite(ctx, bundle))

        # 4b. Generate process-driven tests from business process flow steps
        # Always build test flows from services — each service's CRUD paths become test targets
        test_flows = dict(bp_flows)  # copy, don't mutate original
        # Add any service that doesn't have explicit flows — derive from CRUD paths
        for svc in bundle.services:
            if svc.name not in test_flows:
                svc_steps = []
                for p in svc.paths:
                    if p.summary:
                        svc_steps.append({
                            "action": p.operation_id,
                            "description": p.summary,
                            "method_body_hint": "",
                        })
                if svc_steps:
                    test_flows[svc.name] = svc_steps
        if test_flows:
            process_tests = self._generate_process_tests(ctx, test_flows, confirmed)
            if process_tests:
                files.append(process_tests)

        # 4c. Pre-classify integrations to set dependency flags before infra rendering
        integrations = getattr(bundle, "integrations", {}) or {}
        rest_integ, grpc_integ, async_integ = self._classify_integrations(integrations)
        ctx["has_kafka"] = bool(async_integ)
        ctx["has_grpc"] = bool(grpc_integ)

        # 4c. Resolve architecture style
        arch_style = self._get_arch_style(bundle)
        is_serverless = arch_style == "serverless"
        arch_config = self._get_arch_config(bundle)

        # 5. Generate infrastructure files (skip Dockerfile/K8s for serverless)
        if not is_serverless:
            files.append(self._render_simple("Dockerfile.j2", "Dockerfile", ctx))
            files.append(self._render_simple("docker_compose.yml.j2", "docker-compose.yml", ctx))
            try:
                files.append(self._render_simple("entrypoint.sh.j2", "entrypoint.sh", ctx))
            except Exception as exc:
                logger.debug("suppressed error in DeterministicCodeGenerator._generate_python (app/modules/solutions_product/services/deterministic_code_generator.py): %s", exc)
        files.append(self._render_simple("pyproject.toml.j2", "pyproject.toml", ctx))
        files.append(self._render_simple("GENERATED.md.j2", "GENERATED.md", ctx))

        # 5-claude. Generate CLAUDE.md — instructions for LLMs maintaining this codebase
        files.append(GeneratedFile(
            path="CLAUDE.md",
            content=self._generate_claude_md(bundle),
        ))

        # 5a. Generate bootstrap files (Makefile, .env.example, README.md, bootstrap.sh)
        files.extend(self._render_bootstrap_files(ctx, bundle))

        # 5b. Generate auth, database, and logging config modules
        # is_jwt_local already resolved in ctx above
        idp = getattr(bundle, "identity_provider", None) or {}
        auth_ctx = dict(ctx, identity_provider=idp)
        files.append(self._render_simple("requirements.txt.j2", "requirements.txt", ctx))
        files.append(self._render_auth_config(auth_ctx))
        files.append(self._render_simple("database.py.j2", "app/database.py", ctx))
        files.append(self._render_simple("logging_config.py.j2", "app/logging_config.py", ctx))
        # jwt-local: generate User model + auth router + email service
        if is_jwt_local:
            files.append(self._render_simple("user_model.py.j2", "app/models/user.py", ctx))
            files.append(self._render_simple("auth_router.py.j2", "app/routers/auth.py", ctx))
            # Account settings — always generated for jwt-local apps
            try:
                files.append(self._render_simple("account_settings.py.j2", "app/routers/account.py", ctx))
            except Exception as _e:
                logger.debug("account_settings template not available: %s", _e)
            try:
                files.append(self._render_simple("email_service.py.j2", "app/email_service.py", ctx))
            except Exception as _e:
                logger.debug("email_service template not available: %s", _e)

        # 5c. File storage service (always generated — apps need file uploads)
        try:
            files.append(self._render_simple("file_storage.py.j2", "app/file_storage.py", ctx))
        except Exception as _e:
            logger.debug("file_storage template not available: %s", _e)

        # 5d. Audit trail (always generated — enterprise requirement)
        try:
            files.append(self._render_simple("audit_service.py.j2", "app/audit_service.py", ctx))
        except Exception as _e:
            logger.debug("audit_service template not available: %s", _e)

        # 5e. Notification system with WebSocket (always generated)
        try:
            files.append(self._render_simple("notification_service.py.j2", "app/notification_service.py", ctx))
        except Exception as _e:
            logger.debug("notification_service template not available: %s", _e)

        # 6. Generate models from confirmed fields
        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        if confirmed_fields:
            models = self._build_model_context(bundle)
            files.extend(self._render_model(ctx, models))
            files.append(self._render_migration(ctx, models))
            files.extend(self._render_alembic_infra(ctx))
            # 6b. Seed script — Faker-based demo data, called on first boot
            try:
                files.append(self._render_template_file(
                    "seed_script.py.j2", "seed.py", ctx, models=models,
                ))
            except Exception as _e:
                logger.debug("seed_script template not available: %s", _e)
            # 6a. Domain error codes — typed errors instead of bare HTTPException
            try:
                files.append(self._render_template_file(
                    "errors.py.j2", "app/errors.py", ctx, models=models,
                ))
            except Exception as _e:
                logger.debug("errors template not available: %s", _e)

        # 7. Generate handlers from confirmed business rules
        business_rules = getattr(bundle, "business_rules", {}) or {}
        if business_rules:
            models_with_rules = self._build_handler_context(bundle)
            # Inject synthesized handler bodies if available
            self._inject_synthesized_bodies(models_with_rules, bundle)
            files.append(self._render_handler(ctx, models_with_rules))

        # 7a. Generate state machines from confirmed state_machine specs or inferred from rules
        state_machines = getattr(bundle, "state_machines", {}) or {}
        if state_machines:
            # Fix generic "status" field names using actual entity field definitions
            confirmed = getattr(bundle, "confirmed_fields", {}) or {}
            schemas = getattr(bundle, "schemas", {}) or {}
            for sm_name, sm_def in state_machines.items():
                sm_d = sm_def if isinstance(sm_def, dict) else vars(sm_def) if hasattr(sm_def, "__dict__") else {}
                cur_field = sm_d.get("field_name", sm_d.get("field", "status"))
                if cur_field == "status":
                    # Scan entity fields for a more specific *_status/*_state column
                    snake = _snake(sm_name)
                    fields = confirmed.get(snake, confirmed.get(sm_name, []))
                    if not fields:
                        schema = schemas.get(sm_name, {})
                        fields = [{"name": k} for k in schema.get("properties", {}).keys()]
                    for fd in fields:
                        fn = fd.get("name", "") if isinstance(fd, dict) else str(fd)
                        if fn.endswith("_status") or fn.endswith("_state"):
                            if isinstance(sm_def, dict):
                                sm_def["field"] = fn
                                sm_def["field_name"] = fn
                            elif hasattr(sm_def, "field_name"):
                                sm_def.field_name = fn
                            break
            files.append(self._render_state_machines(ctx, state_machines))

        # 7aa. Generate DMN decision tables from brief-extracted DecisionModelDef objects
        _decision_models = getattr(bundle, "decision_models", None) or []
        if _decision_models and self.language in ("python-fastapi", "python-flask"):
            try:
                _dm_ctx = dict(ctx, decision_models=_decision_models)
                _dm_content = self._render_template_file(
                    "decisions.py.j2", "app/decisions.py", _dm_ctx,
                )
                if _dm_content and _dm_content.content and _dm_content.content.strip():
                    files.append(_dm_content)
                # Generate accompanying tests
                _dm_test_content = self._render_template_file(
                    "tests/test_decisions.py.j2",
                    "tests/unit/test_decisions.py",
                    _dm_ctx,
                )
                if _dm_test_content and _dm_test_content.content and _dm_test_content.content.strip():
                    files.append(_dm_test_content)
            except Exception as _dm_err:
                logger.warning("Decision model template rendering failed (non-fatal): %s", _dm_err)

        # 7ab. Resilience patterns from risk register (circuit breakers, retries, DLQ)
        _resilience_cfg = getattr(bundle, "resilience_config", {}) or {}
        if _resilience_cfg and self.language in ("python-fastapi", "python-flask"):
            try:
                _res_ctx = dict(ctx,
                    circuit_breakers=_resilience_cfg.get("circuit_breakers", []),
                    retry_policies=_resilience_cfg.get("retry_policies", []),
                    dlq_topics=_resilience_cfg.get("dlq_topics", []),
                    chaos_test_targets=_resilience_cfg.get("chaos_test_targets", []),
                )
                _res_file = self._render_template_file(
                    "resilience.py.j2", "app/resilience.py", _res_ctx,
                )
                if _res_file and _res_file.content and _res_file.content.strip():
                    files.append(_res_file)
            except Exception as _res_err:
                logger.warning("Resilience template rendering failed (non-fatal): %s", _res_err)

        # 7ac. Compliance middleware from SolutionComplianceMapping
        _compliance_cfg = getattr(bundle, "compliance_config", {}) or {}
        if _compliance_cfg.get("frameworks") and self.language in ("python-fastapi", "python-flask"):
            try:
                _cmp_ctx = dict(ctx, compliance=_compliance_cfg)
                _cmp_file = self._render_template_file(
                    "compliance_middleware.py.j2", "app/compliance_middleware.py", _cmp_ctx,
                )
                if _cmp_file and _cmp_file.content and _cmp_file.content.strip():
                    files.append(_cmp_file)
            except Exception as _cmp_err:
                logger.warning("Compliance middleware template rendering failed (non-fatal): %s", _cmp_err)

        # 7ad. RBAC from SolutionStakeholder
        _rbac_cfg = getattr(bundle, "rbac_config", {}) or {}
        if _rbac_cfg.get("has_rbac") and self.language in ("python-fastapi", "python-flask"):
            try:
                _rbac_ctx = dict(ctx, rbac=_rbac_cfg)
                _rbac_file = self._render_template_file(
                    "rbac.py.j2", "app/rbac.py", _rbac_ctx,
                )
                if _rbac_file and _rbac_file.content and _rbac_file.content.strip():
                    files.append(_rbac_file)
            except Exception as _rbac_err:
                logger.warning("RBAC template rendering failed (non-fatal): %s", _rbac_err)

        # 7ae. Integration client stubs from SolutionIntegrationFlow
        _int_clients = getattr(bundle, "integration_clients", []) or []
        if _int_clients and self.language in ("python-fastapi", "python-flask"):
            try:
                _int_ctx = dict(ctx, integration_clients=_int_clients)
                _int_file = self._render_template_file(
                    "integration_clients.py.j2", "app/integration_clients.py", _int_ctx,
                )
                if _int_file and _int_file.content and _int_file.content.strip():
                    files.append(_int_file)
            except Exception as _int_err:
                logger.warning("Integration clients template rendering failed (non-fatal): %s", _int_err)

        # 7af. Prometheus observability from KPIs + SolutionOutcome
        _kpi_metrics = getattr(bundle, "kpi_metrics", []) or []
        if _kpi_metrics and self.language in ("python-fastapi", "python-flask"):
            try:
                _obs_ctx = dict(ctx, kpi_metrics=_kpi_metrics)
                _obs_file = self._render_template_file(
                    "observability.py.j2", "app/observability.py", _obs_ctx,
                )
                if _obs_file and _obs_file.content and _obs_file.content.strip():
                    files.append(_obs_file)
            except Exception as _obs_err:
                logger.warning("Observability template rendering failed (non-fatal): %s", _obs_err)

        # 7b. Generate domain validators from validation-type business rules
        validation_rules = self._extract_validation_rules(business_rules)
        if validation_rules:
            files.append(self._render_validators(ctx, validation_rules))

        # 7c. Generate service orchestration from UML sequence diagrams
        uml_diagrams = getattr(bundle, "uml_diagrams", []) or []
        if uml_diagrams:
            orch_file = self._render_orchestration(ctx, uml_diagrams)
            if orch_file:
                files.append(orch_file)

        # 7d. Webhook event delivery (conditional on genome.webhooks.enabled)
        _webhook_cfg = getattr(bundle, "webhook_config", {}) or {}
        if _webhook_cfg.get("enabled"):
            _wh_delivery = _webhook_cfg.get("delivery", {})
            _wh_ctx = dict(ctx,
                webhook_retry_attempts=_wh_delivery.get("retry_attempts", 3),
                webhook_retry_backoff=_wh_delivery.get("retry_backoff", "exponential"),
                webhook_subscriptions=_webhook_cfg.get("subscriptions", []),
                modules=getattr(bundle, "_genome_modules", {}) or {},
            )
            try:
                files.append(self._render_simple("webhook_service.py.j2", "app/webhook_service.py", _wh_ctx))
                files.append(self._render_simple("webhook_routes.py.j2", "app/routers/webhooks.py", _wh_ctx))
                files.append(self._render_simple("event_emitter.py.j2", "app/event_emitter.py", _wh_ctx))
            except Exception as _wh_err:
                logger.warning("Webhook template rendering failed: %s", _wh_err)

        # 7e-v2. Genome v2: workers, GraphQL, event-sourcing, feature flags, audit+, webhooks+, validators+, emitter+
        _workers = getattr(bundle, "workers", []) or []
        _api_protocol = getattr(bundle, "api_protocol", "rest") or "rest"
        _feature_flags = getattr(bundle, "feature_flags", {}) or {}
        _genome_events = getattr(bundle, "genome_events", {}) or {}
        _genome_projections = getattr(bundle, "genome_projections", {}) or {}
        _aggregate_types = getattr(bundle, "aggregate_types", {}) or {}
        _modules = getattr(bundle, "_genome_modules", {}) or {}

        # Workers: Celery app + per-worker tasks
        if _workers:
            _wk_ctx = dict(ctx, workers=_workers)
            try:
                files.append(self._render_template_file(
                    "workers/celery_app.py.j2", "app/workers/celery_app.py", _wk_ctx,
                ))
                for worker in _workers:
                    _task_ctx = dict(ctx, worker=worker)
                    files.append(self._render_template_file(
                        "workers/task.py.j2", f"app/workers/{worker['name']}.py", _task_ctx,
                    ))
                files.append(GeneratedFile(path="app/workers/__init__.py", content=""))
            except Exception as _e:
                logger.warning("Worker template rendering failed: %s", _e)

        # GraphQL: Strawberry schema + types
        if _api_protocol in ("graphql", "both"):
            _gql_entities = []
            for entity_name, fields in confirmed_fields.items():
                pascal_name = _pascal(entity_name)
                sm = _state_machines.get(pascal_name) or _state_machines.get(entity_name)
                _gql_entities.append({
                    "name": pascal_name,
                    "snake_name": _snake(entity_name),
                    "fields": fields if isinstance(fields, list) else [],
                    "has_state_machine": sm is not None,
                })
            if _gql_entities:
                _gql_ctx = dict(ctx, entities=_gql_entities)
                try:
                    files.append(self._render_template_file(
                        "graphql/schema.py.j2", "app/graphql/schema.py", _gql_ctx,
                    ))
                    files.append(self._render_template_file(
                        "graphql/types.py.j2", "app/graphql/types.py", _gql_ctx,
                    ))
                    files.append(GeneratedFile(path="app/graphql/__init__.py", content=""))
                except Exception as _e:
                    logger.warning("GraphQL template rendering failed: %s", _e)

        # Event-sourcing: events, aggregates, projections (per entity)
        _all_events = []
        for entity_snake, events_list in _genome_events.items():
            entity_pascal = _pascal(entity_snake)
            _all_events.extend(events_list)
            entity_data = {
                "name": entity_pascal,
                "snake_name": entity_snake,
                "events": events_list,
                "event_store": {},
            }
            for mod_key, mod_def in _modules.items():
                if _snake(mod_def.get("aggregate_root", mod_key)) == entity_snake:
                    entity_data["event_store"] = mod_def.get("event_store", {})
                    break
            try:
                _agg_ctx = dict(ctx, entity=entity_data)
                files.append(self._render_template_file(
                    "aggregates/aggregate.py.j2", f"app/aggregates/{entity_snake}_aggregate.py", _agg_ctx,
                ))
            except Exception as _e:
                logger.debug("Aggregate template for %s failed: %s", entity_snake, _e)

        if _all_events:
            try:
                _ev_ctx = dict(ctx, events=_all_events)
                files.append(self._render_template_file(
                    "events/event.py.j2", "app/events/event.py", _ev_ctx,
                ))
                files.append(GeneratedFile(path="app/events/__init__.py", content=""))
                files.append(GeneratedFile(path="app/aggregates/__init__.py", content=""))
            except Exception as _e:
                logger.debug("Event template rendering failed: %s", _e)

        _all_projections = []
        for entity_snake, proj_list in _genome_projections.items():
            _all_projections.extend(proj_list)
        if _all_projections:
            try:
                _proj_ctx = dict(ctx, projections=_all_projections)
                files.append(self._render_template_file(
                    "projections/projection.py.j2", "app/projections/projection.py", _proj_ctx,
                ))
                files.append(GeneratedFile(path="app/projections/__init__.py", content=""))
            except Exception as _e:
                logger.debug("Projection template rendering failed: %s", _e)

        # Feature flags
        if _feature_flags and _feature_flags.get("flags"):
            try:
                _ff_ctx = dict(ctx, feature_flags=_feature_flags)
                files.append(self._render_template_file(
                    "flags.py.j2", "app/flags.py", _ff_ctx,
                ))
            except Exception as _e:
                logger.debug("Feature flags template failed: %s", _e)

        # Enhanced audit (v2 — full system with middleware + CSV export)
        _audited_entities = []
        for entity_name, fields in confirmed_fields.items():
            for f in (fields if isinstance(fields, list) else []):
                _g = f.get if isinstance(f, dict) else lambda k, d=None: getattr(f, k, d)
                if _g("audit_track", False):
                    _audited_entities.append(_pascal(entity_name))
                    break
        if _audited_entities:
            try:
                _audit_ctx = dict(ctx,
                    audited_entities=_audited_entities,
                    multi_tenancy="multi_tenancy" in _nfr_flags,
                )
                files.append(self._render_template_file(
                    "audit/logger.py.j2", "app/audit/logger.py", _audit_ctx,
                ))
                files.append(GeneratedFile(path="app/audit/__init__.py", content=""))
            except Exception as _e:
                logger.debug("Audit logger (v2) template failed: %s", _e)

        # Enhanced webhooks (v2 — full system with HMAC + delivery history)
        if _webhook_cfg.get("enabled"):
            try:
                _whv2_ctx = dict(ctx,
                    webhook_events=_webhook_cfg.get("events", []),
                    webhook_retry_attempts=_webhook_cfg.get("retry_attempts", 3),
                    webhook_retry_backoff=_webhook_cfg.get("retry_backoff", "exponential"),
                    multi_tenancy="multi_tenancy" in _nfr_flags,
                )
                files.append(self._render_template_file(
                    "webhooks/delivery.py.j2", "app/webhooks/delivery.py", _whv2_ctx,
                ))
                files.append(GeneratedFile(path="app/webhooks/__init__.py", content=""))
            except Exception as _e:
                logger.debug("Webhook delivery (v2) template failed: %s", _e)

        # Stripe billing integration
        _payment_cfg = getattr(bundle, "payment_config", {}) or {}
        if _payment_cfg.get("provider") and _payment_cfg["provider"] != "none":
            try:
                _pay_ctx = dict(ctx,
                    payment_modes=_payment_cfg.get("modes", []),
                    webhook_secret_env=_payment_cfg.get("webhook_secret_env", "STRIPE_WEBHOOK_SECRET"),
                    payment_config=_payment_cfg,
                )
                files.append(self._render_template_file(
                    "payments/stripe_router.py.j2", "app/payments/stripe_router.py", _pay_ctx,
                ))
                files.append(GeneratedFile(path="app/payments/__init__.py", content=""))
            except Exception as _e:
                logger.debug("Stripe billing template failed: %s", _e)

        # Enhanced validators (v2 — natural-language pattern library)
        if _validation_rules:
            try:
                _val_ctx = dict(ctx, validation_rules=_validation_rules)
                files.append(self._render_template_file(
                    "validators/validator.py.j2", "app/validators/validators.py", _val_ctx,
                ))
            except Exception as _e:
                logger.debug("Validator (v2) template failed: %s", _e)

        # Event emitter (v2 — persists to domain_events + triggers webhooks)
        if _genome_events or _webhook_cfg.get("enabled"):
            try:
                _emit_ctx = dict(ctx, modules=_modules)
                files.append(self._render_template_file(
                    "events/emitter.py.j2", "app/events/emitter.py", _emit_ctx,
                ))
            except Exception as _e:
                logger.debug("Event emitter (v2) template failed: %s", _e)

        # 7e. Genome-driven infrastructure templates (multi-tenancy, telemetry, API keys)
        # These render conditionally based on NFR flags set by genome_to_bundle.
        if "multi_tenancy" in _nfr_flags:
            try:
                files.append(self._render_simple(
                    "tenant_middleware.py.j2", "app/middleware/tenant.py", ctx,
                ))
            except Exception as _e:
                logger.debug("tenant_middleware template not available: %s", _e)

        if "api_keys" in _nfr_flags:
            try:
                files.append(self._render_simple(
                    "api_keys.py.j2", "app/routers/api_keys.py", ctx,
                ))
            except Exception as _e:
                logger.debug("api_keys template not available: %s", _e)

        # Telemetry is always generated when observability NFR is present
        # (genome sets this by default; legacy bundles may not have it)
        _observability = any(
            nfr.get("flag") == "opentelemetry"
            or "telemetry" in (nfr.get("description") or "").lower()
            for nfr in (bundle.infra_context.nfrs or [])
        )
        if _observability or "opentelemetry" in _nfr_flags:
            try:
                files.append(self._render_simple(
                    "telemetry.py.j2", "app/telemetry.py", ctx,
                ))
            except Exception as _e:
                logger.debug("telemetry template not available: %s", _e)

        # 7f. Export service (CSV/JSON export per entity)
        if confirmed_fields:
            try:
                models_for_export = self._build_model_context(bundle)
                _table_names_export = {name: _pluralize(name.lower()) for name in models_for_export}
                files.append(self._render_template_file(
                    "export_service.py.j2", "app/routers/export.py", ctx,
                    models=models_for_export, table_names=_table_names_export,
                ))
            except Exception as _e:
                logger.debug("export_service template not available: %s", _e)

        # 7g. Search service (full-text search across entities)
        if confirmed_fields:
            try:
                models_for_search = self._build_model_context(bundle)
                _table_names_search = {name: _pluralize(name.lower()) for name in models_for_search}
                files.append(self._render_template_file(
                    "search_service.py.j2", "app/routers/search.py", ctx,
                    models=models_for_search, table_names=_table_names_search,
                ))
            except Exception as _e:
                logger.debug("search_service template not available: %s", _e)

        # 8. Generate integration clients (routed by communication_type)
        if integrations:
            if rest_integ:
                files.append(self._render_client(ctx, rest_integ))
            if async_integ:
                files.append(self._render_template_file(
                    "kafka_producer.py.j2", "app/clients/kafka_producers.py",
                    ctx, integrations=async_integ,
                ))
                files.append(self._render_template_file(
                    "kafka_consumer.py.j2", "app/clients/kafka_consumers.py",
                    ctx, integrations=async_integ,
                ))
            if grpc_integ:
                files.append(self._render_template_file(
                    "grpc_client.py.j2", "app/clients/grpc_clients.py",
                    ctx, integrations=grpc_integ,
                ))

        # 9. Generate deployment manifests (skip K8s/Helm for serverless)
        deployment = getattr(bundle, "deployment", None)
        if deployment and not is_serverless:
            deploy_data = {bundle.solution_name: vars(deployment)}
            files.append(self._render_k8s(ctx, deploy_data))
            files.append(self._render_helm(ctx, vars(deployment), bundle.solution_name))

        # 9a. Generate K8s NetworkPolicy from NFR specs with infrastructure enforcement
        nfr_specs = getattr(bundle, "nfr_specs", []) or []
        if nfr_specs and not is_serverless:
            np_file = self._render_network_policies(ctx, nfr_specs, bundle.solution_name)
            if np_file:
                files.append(np_file)

        # 10. Generate CI/CD pipeline
        ci_cd_config = getattr(bundle, "ci_cd", None) or {}
        files.extend(self._render_cicd(ctx, ci_cd_config))

        # 10b. Generate Terraform configs and seed data
        files.extend(self._render_terraform(ctx, bundle))

        # 10c. Generate Keycloak realm config (OIDC auth with MFA policies)
        idp_type = (getattr(bundle, "identity_provider", None) or {}).get("type")
        if idp_type == "oidc":
            try:
                _shared_env = self._shared_env
                kc_template = _shared_env.get_template("keycloak_realm.json.j2")
                mfa_setting = "none"
                for nfr in (bundle.infra_context.nfrs or []):
                    if nfr.get("flag") == "mfa":
                        desc = nfr.get("description", "")
                        if "required_for_all" in desc:
                            mfa_setting = "required_for_all"
                        elif "required_for_admin" in desc:
                            mfa_setting = "required_for_admin"
                kc_idp = dict(bundle.identity_provider or {})
                kc_idp["mfa"] = mfa_setting
                kc_ctx = {**ctx, "identity_provider": kc_idp}
                kc_content = kc_template.render(**kc_ctx)
                files.append(GeneratedFile(
                    path="infrastructure/keycloak/realm-import.json",
                    content=kc_content,
                ))
            except Exception as _e:
                logger.debug("keycloak_realm template not available: %s", _e)

        # 10d. Generate Prometheus alerting rules
        try:
            _shared_env = self._shared_env
            prom_template = _shared_env.get_template("prometheus_alerts.yml.j2")
            prom_content = prom_template.render(**ctx)
            files.append(GeneratedFile(
                path="infrastructure/monitoring/alerts.yml",
                content=prom_content,
            ))
        except Exception as _e:
            logger.debug("prometheus_alerts template not available: %s", _e)

        # 11. Architecture style-specific templates
        files.extend(self._render_arch_style_files(
            ctx, bundle, arch_style, arch_config,
        ))

        # 12. Generate admin UI
        admin_entities = self._build_admin_entities(bundle)
        if admin_entities:
            files.append(self._render_admin_ui(ctx, admin_entities, is_go=False))

        # 13. Generate product UI (dashboard + entity views + workflows)
        files.extend(self._render_product_ui(ctx, bundle, is_go=False))

        # 13b. Generate production-grade product UI (single-page, shadcn-style)
        if admin_entities:
            try:
                state_machines = getattr(bundle, "state_machines", {}) or {}
                # Build set of API paths that actually have routes, to filter phantom entities
                _api_paths = set()
                for svc in (bundle.services or []):
                    for p in (svc.paths or []):
                        _api_paths.add(p.path.split("{")[0].rstrip("/"))
                product_entities = []
                for ent in admin_entities:
                    # Skip entities whose api_path has no matching route
                    ent_api_prefix = ent.get("api_path", "").rstrip("/")
                    if _api_paths and ent_api_prefix not in _api_paths:
                        continue
                    pe = dict(ent)
                    # Find state machine for this entity
                    sm_key = ent.get("name_snake", "")
                    sm = state_machines.get(_pascal(sm_key))
                    if sm:
                        sm_data = sm if isinstance(sm, dict) else vars(sm) if hasattr(sm, "__dict__") else {}
                        transitions_raw = sm_data.get("transitions", [])
                        # Use the compiler's field name, but if it's generic "status",
                        # scan entity fields for a more specific *_status/*_state column
                        sm_field = sm_data.get("field_name", sm_data.get("field", "status"))
                        if sm_field == "status":
                            for f in ent.get("fields", []):
                                fn = f.get("name", "")
                                if fn.endswith("_status") or fn.endswith("_state"):
                                    sm_field = fn
                                    break
                        pe["status_field"] = sm_field
                        pe["states"] = sm_data.get("states", [])
                        # Convert transitions to JSON-serializable dicts
                        pe["transitions"] = []
                        for t in transitions_raw:
                            if hasattr(t, "from_state"):
                                pe["transitions"].append({"from": t.from_state, "to": t.to_state, "trigger": t.trigger})
                            elif isinstance(t, dict):
                                pe["transitions"].append(t)
                    else:
                        pe["status_field"] = None
                        pe["states"] = []
                        pe["transitions"] = []

                    # Ensure status field is within the first 7 displayed columns
                    sf = pe.get("status_field")
                    if sf and pe.get("fields"):
                        fields = pe["fields"]
                        sf_idx = next((i for i, f in enumerate(fields) if f.get("name") == sf), -1)
                        if sf_idx >= 7:
                            # Move status field to position 2 (after first field, usually name/id)
                            fields.insert(2, fields.pop(sf_idx))

                    product_entities.append(pe)

                tpl = self._env.get_template("product_ui.html.j2")
                product_html = tpl.render(
                    **ctx,
                    project_name=ctx.get("solution_name", "App"),
                    entities=product_entities,
                    state_machines=state_machines,
                )
                files.append(GeneratedFile(path="app/static/index.html", content=product_html))
            except Exception as _pui_err:
                logger.debug("product_ui template not available: %s", _pui_err)

        # 14. Generate server-rendered UI pages (Jinja2 + Alpine.js + Tailwind)
        # python-fastapi and python-flask always generate a Next.js frontend (see generate() → _generate_nextjs_frontend)
        # which handles all page routing. Generating Jinja2 server-side pages alongside Next.js
        # creates a confusing duplicate structure (templates/ + frontend/app/) and dead code.
        if self.language not in ("python-fastapi", "python-flask"):
            files.extend(self._generate_ui_pages(ctx, bundle))

        # Package structure init files
        files.append(GeneratedFile(path="app/__init__.py", content=""))
        files.append(GeneratedFile(path="app/routers/__init__.py", content=""))
        files.append(GeneratedFile(path="app/schemas/__init__.py", content=""))
        if confirmed_fields:
            files.append(GeneratedFile(path="app/models/__init__.py", content=""))
        if business_rules:
            files.append(GeneratedFile(path="app/handlers/__init__.py", content=""))
        if state_machines:
            # Re-export state machine classes so routes can do: from app.state_machines import XStateMachine
            _sm_imports = []
            for _sm_name in state_machines:
                _sm_imports.append(f"from app.state_machines.machines import {_sm_name}State, {_sm_name}StateMachine, InvalidTransitionError  # noqa: F401")
            _sm_init_content = "# Auto-generated re-exports\n" + "\n".join(dict.fromkeys(_sm_imports)) + "\n"
            files.append(GeneratedFile(path="app/state_machines/__init__.py", content=_sm_init_content))
        if validation_rules:
            files.append(GeneratedFile(path="app/validators/__init__.py", content=""))
        if uml_diagrams:
            files.append(GeneratedFile(path="app/orchestrators/__init__.py", content=""))
        if integrations:
            files.append(GeneratedFile(path="app/clients/__init__.py", content=""))
        files.append(GeneratedFile(path="tests/__init__.py", content=""))

        # OpenAPI spec — serialized from bundle.openapi (present in all bundles)
        files.append(self._render_openapi_yaml(bundle))

        # Deterministic test suite — Jinja2 templates (conftest, CRUD, tenant, auth, etc.)
        # Falls back to legacy inline conftest if templates are unavailable
        tmpl_tests = self._render_template_tests(ctx, bundle)
        if tmpl_tests:
            files.extend(tmpl_tests)
        else:
            files.append(self._render_conftest(ctx))

        # Prometheus metrics + Grafana dashboard for FastAPI
        files.extend(self._render_python_monitoring(ctx, bundle))

        # API versioning router (v1/v2 with deprecation middleware)
        files.extend(self._render_api_versioning(ctx))

        # One-click deploy manifests
        files.append(GeneratedFile(
            path="railway.json",
            content='{\n  "$schema": "https://railway.app/railway.schema.json",\n'
                    '  "build": {\n    "builder": "DOCKERFILE",\n    "dockerfilePath": "Dockerfile"\n  },\n'
                    '  "deploy": {\n    "numReplicas": 1,\n    "restartPolicyType": "ON_FAILURE",\n'
                    '    "restartPolicyMaxRetries": 10\n  }\n}\n',
        ))

        # Deploy script (platform auto-detection)
        try:
            files.append(self._render_simple("deploy.sh.j2", "deploy.sh", ctx))
        except Exception as _e:
            logger.debug("deploy.sh template not available: %s", _e)

        # Bandit security scanner config
        try:
            files.append(self._render_simple(".bandit.j2", ".bandit", ctx))
        except Exception as _e:
            logger.debug(".bandit template not available: %s", _e)

        # ArchiMate LLM context docs — shared templates, motivation-layer driven
        try:
            import os as _os
            _shared_dir = _os.path.join(
                _os.path.dirname(_os.path.dirname(__file__)), "templates", "shared",
            )
            from jinja2 import Environment as _J2Env, FileSystemLoader as _FSL
            _shared_env = _J2Env(loader=_FSL(_shared_dir), keep_trailing_newline=True)
            _motivation = self._build_motivation_context(bundle.solution_id) or {}
            _confirmed = getattr(bundle, "confirmed_fields", {}) or {}
            _svc_list = bundle.services or []
            _has_kafka = bool(getattr(bundle, "integrations", {}) or {})
            _has_gr = False
            _bp_flows = getattr(bundle, "business_process_flows", {}) or {}
            _has_biz = bool(getattr(bundle, "business_rules", {}) or {})

            _entity_table = [
                {
                    "name": _pascal(ent),
                    "snake_name": _snake(ent),
                    "table": _pluralize(_snake(ent)),
                    "key_fields": ", ".join(
                        (f.get("name") or "") if isinstance(f, dict) else f.name
                        for f in (flds or [])[:4]
                    ),
                    "relates_to": ", ".join(
                        f"`{(f.get('foreign_key') or '')}` (via `{(f.get('name') or '')}`)"
                        for f in (flds or []) if isinstance(f, dict) and f.get("foreign_key")
                    ) or "—",
                }
                for ent, flds in _confirmed.items()
            ]

            _shared_ctx = {
                **ctx,
                "solution_name": bundle.solution_name,
                "solution_id": bundle.solution_id,
                "spec_hash": bundle.spec_hash,
                "constraints": _motivation.get("constraints", []),
                "principles": _motivation.get("principles", []),
                "requirements": _motivation.get("requirements", []),
                "stakeholders": _motivation.get("stakeholders", []),
                "meanings": _motivation.get("meanings", []),
                "values": _motivation.get("values", []),
                "goals": _motivation.get("goals", []),
                "problem_statement": _motivation.get("problem_statement", ""),
                "services": [
                    {"name": s.name, "paths": s.paths}
                    for s in _svc_list
                ],
                "entities": _entity_table,
                "entity_table": _entity_table,
                "process_flows": _bp_flows,
                "has_kafka": _has_kafka,
                "has_grpc": _has_gr,
                "has_business_rules": _has_biz,
                "is_mobile": False,
                "has_frontend": False,
                "app_name": bundle.solution_name,
            }

            for _tpl_name, _out_path in [
                ("PRINCIPLES.md.j2", "PRINCIPLES.md"),
                ("DOMAIN.md.j2", "DOMAIN.md"),
                ("DEPENDENCIES.md.j2", "DEPENDENCIES.md"),
                ("CHECKLIST.md.j2", "CHECKLIST.md"),
            ]:
                try:
                    _content = _shared_env.get_template(_tpl_name).render(**_shared_ctx)
                    if _content and _content.strip():
                        files.append(GeneratedFile(path=_out_path, content=_content))
                except Exception as _te:
                    logger.debug("Shared template %s skipped: %s", _tpl_name, _te)
        except Exception as _e:
            logger.debug("ArchiMate LLM context docs skipped: %s", _e)

        return GeneratedCodeBundle(
            solution_id=bundle.solution_id,
            bundle_id=bundle_id,
            language=self.language,
            spec_hash=bundle.spec_hash,
            files=files,
        )

    def _generate_flask(self, bundle):
        """Generate a complete Python/Flask project (Blueprint CRUD + Marshmallow + Flask-JWT-Extended).

        Mirrors _generate_python() in structure — same ctx dict, same shared helpers.
        Key differences:
        - Template dir:  python_flask/
        - Per-entity:    blueprint.py.j2  (not route.py.j2)  → app/blueprints/{name}_bp.py
        - Per-entity:    model.py.j2      (Flask-SQLAlchemy, includes to_dict())
        - Per-entity:    schema.py.j2     (Marshmallow, not Pydantic)
        - Auth:          auth_bp.py.j2    (Flask-JWT-Extended)
        - Entry point:   app/__init__.py.j2 + wsgi.py.j2 (app factory)
        - No main.py, no uvicorn
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        bundle_id = f"code_{now}"

        files = []

        _idp_early = getattr(bundle, "identity_provider", None) or {}
        _idp_type = _idp_early.get("type") or "jwt-local"
        is_jwt_local = _idp_type == "jwt-local"

        _nfr_flags = {nfr["flag"] for nfr in (bundle.infra_context.nfrs or []) if nfr.get("flag")}

        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        _state_machines = getattr(bundle, "state_machines", {}) or {}
        _biz_rules = getattr(bundle, "business_rules", {}) or {}
        _validation_rules = self._extract_validation_rules(_biz_rules)
        getattr(bundle, "business_process_flows", {}) or {}

        ctx = {
            "solution_id": bundle.solution_id,
            "solution_name": bundle.solution_name,
            "bundle_id": bundle_id,
            "spec_hash": bundle.spec_hash,
            "maturity_score": bundle.maturity_score,
            "generated_at": now,
            "is_jwt_local": is_jwt_local,
            "nfr_rate_limiting": "rate_limiting" in _nfr_flags,
            "nfr_pagination": "pagination" in _nfr_flags,
            "nfr_audit_trail": "audit_trail" in _nfr_flags,
            "nfr_rbac": "rbac" in _nfr_flags,
            "nfr_ssl_redirect": "ssl_redirect" in _nfr_flags,
            "nfr_sentry": "sentry" in _nfr_flags,
            "identity_provider": _idp_early,
            "genome_compliance": getattr(bundle, "_genome_compliance", {}) or {},
            "genome_business_model": getattr(bundle, "_genome_business_model", {}) or {},
            "genome_operations": getattr(bundle, "_genome_operations", {}) or {},
            "multi_tenancy": getattr(bundle, "multi_tenancy", {}) or {},
            "tenant_field": (getattr(bundle, "multi_tenancy", {}) or {}).get("tenant_field"),
            "workers": getattr(bundle, "workers", []) or [],
            "payment_config": getattr(bundle, "payment_config", {}) or {},
            "services": [
                {"name": s.name, "tag": s.tag}
                for s in (bundle.services or [])
            ],
        }

        # Pre-classify integrations so ctx flags are ready for templates
        integrations = getattr(bundle, "integrations", {}) or {}
        rest_integ, grpc_integ, async_integ = self._classify_integrations(integrations)
        ctx["has_kafka"] = bool(async_integ)
        ctx["has_grpc"] = bool(grpc_integ)
        arch_style = self._get_arch_style(bundle)
        self._get_arch_config(bundle)
        is_serverless = arch_style == "serverless"

        # 1. App factory + WSGI entry point
        files.append(self._render_simple("app/__init__.py.j2", "app/__init__.py", ctx))
        files.append(self._render_simple("wsgi.py.j2", "wsgi.py", ctx))
        files.append(self._render_simple("config.py.j2", "config.py", ctx))
        files.append(self._render_simple("database.py.j2", "database.py", ctx))

        # 2. Blueprint + model + schema per service
        bool(_biz_rules)
        for svc in (bundle.services or []):
            tag_snake = svc.tag.lower().replace(" ", "_") if svc.tag else svc.name.lower().replace(" ", "_")
            entity_name = tag_snake
            fields_raw = confirmed_fields.get(entity_name) or confirmed_fields.get(svc.name) or []
            sm_key = _pascal(entity_name)
            sm = _state_machines.get(sm_key) or _state_machines.get(entity_name)
            sm_data = {}
            if sm:
                sm_data = sm if isinstance(sm, dict) else vars(sm) if hasattr(sm, "__dict__") else {}

            # Searchable fields = string/text fields not tagged as FK
            searchable = [
                f["name"] if isinstance(f, dict) else f.name
                for f in (fields_raw if isinstance(fields_raw, list) else [])
                if (f.get("type") if isinstance(f, dict) else getattr(f, "type", "")) in ("string", "text")
                and not (f.get("foreign_key") if isinstance(f, dict) else getattr(f, "foreign_key", False))
            ]

            svc_ctx = dict(
                ctx,
                entity_name=entity_name,
                fields=fields_raw if isinstance(fields_raw, list) else [],
                searchable_fields=searchable[:3],  # limit to 3 OR clauses
                state_machine=sm_data,
                has_state_machine=bool(sm_data),
            )
            # Blueprint
            try:
                files.append(self._render_template_file(
                    "blueprint.py.j2", f"app/blueprints/{entity_name}_bp.py", svc_ctx,
                ))
            except Exception as _be:
                logger.warning("Flask blueprint render failed for %s: %s", entity_name, _be)
            # Model
            try:
                files.append(self._render_template_file(
                    "model.py.j2", f"app/models/{entity_name}.py", svc_ctx,
                ))
            except Exception as _me:
                logger.warning("Flask model render failed for %s: %s", entity_name, _me)
            # Schema
            try:
                files.append(self._render_template_file(
                    "schema.py.j2", f"app/schemas/{entity_name}_schema.py", svc_ctx,
                ))
            except Exception as _se:
                logger.warning("Flask schema render failed for %s: %s", entity_name, _se)
            # Tests
            try:
                files.append(self._render_template_file(
                    "test_entity.py.j2", f"tests/test_{entity_name}.py", svc_ctx,
                ))
            except Exception as _te:
                logger.debug("Flask test_entity render failed for %s: %s", entity_name, _te)

        # 3. Auth blueprint
        try:
            files.append(self._render_simple("auth_bp.py.j2", "app/blueprints/auth_bp.py", ctx))
        except Exception as _ae:
            logger.warning("Flask auth_bp render failed: %s", _ae)

        # 4. Requirements + infra files
        files.append(self._render_simple("requirements.txt.j2", "requirements.txt", ctx))
        if not is_serverless:
            files.append(self._render_simple("Dockerfile.j2", "Dockerfile", ctx))
            files.append(self._render_simple("docker-compose.yml.j2", "docker-compose.yml", ctx))

        files.append(self._render_simple("GENERATED.md.j2", "GENERATED.md", ctx))

        # 5. Bootstrap files (Makefile, README, .env.example, bootstrap.sh)
        files.extend(self._render_bootstrap_files(ctx, bundle))

        # 6. CI/CD + Terraform
        ci_cd_config = getattr(bundle, "ci_cd", None) or {}
        files.extend(self._render_cicd(ctx, ci_cd_config))
        files.extend(self._render_terraform(ctx, bundle))

        # 7. Deployment manifests
        deployment = getattr(bundle, "deployment", None)
        if deployment and not is_serverless:
            deploy_data = {bundle.solution_name: vars(deployment)}
            files.append(self._render_k8s(ctx, deploy_data))
            files.append(self._render_helm(ctx, vars(deployment), bundle.solution_name))

        # 8. State machines
        if _state_machines:
            files.append(self._render_state_machines(ctx, _state_machines))

        # 9. Integration clients
        if rest_integ:
            files.append(self._render_client(ctx, rest_integ))

        # 10. OpenAPI spec
        files.append(self._render_openapi_yaml(bundle))

        # 11. CLAUDE.md LLM context
        files.append(GeneratedFile(
            path="CLAUDE.md",
            content=self._generate_claude_md(bundle),
        ))

        # 12. Admin UI (same template as FastAPI — resolved via ChoiceLoader fallback)
        admin_entities = self._build_admin_entities(bundle)
        if admin_entities:
            files.append(self._render_admin_ui(ctx, admin_entities, is_go=False))

        # 13. Package __init__ files
        files.append(GeneratedFile(path="app/blueprints/__init__.py", content=""))
        files.append(GeneratedFile(path="app/models/__init__.py", content=""))
        files.append(GeneratedFile(path="app/schemas/__init__.py", content=""))
        files.append(GeneratedFile(path="tests/__init__.py", content=""))

        return GeneratedCodeBundle(
            solution_id=bundle.solution_id,
            bundle_id=bundle_id,
            language=self.language,
            spec_hash=bundle.spec_hash,
            files=files,
        )

    # ── CLAUDE.md — LLM agent instructions ──

    def _generate_claude_md(self, bundle) -> str:
        """Generate CLAUDE.md — full architectural context for LLM agents maintaining this app."""
        name = bundle.solution_name or "Application"
        idp = getattr(bundle, "identity_provider", None) or {}
        confirmed = getattr(bundle, "confirmed_fields", {}) or {}
        business_rules = getattr(bundle, "business_rules", {}) or {}
        state_machines = getattr(bundle, "state_machines", {}) or {}
        integrations = getattr(bundle, "integrations", {}) or {}

        auth_type = idp.get("type", "jwt-local")
        auth_label = "Keycloak OIDC" if auth_type == "oidc" else "JWT local"
        genome_version = "1.0.0"
        has_keycloak = auth_type == "oidc" or idp.get("preset") == "keycloak"

        # Pull motivation context for constraints/principles to front-load
        motivation = self._build_motivation_context(bundle.solution_id) or {}
        constraints = motivation.get("constraints", [])
        principles = motivation.get("principles", [])

        L = []  # line accumulator
        L.append(f"# CLAUDE.md — {name}")
        L.append("")
        L.append("> **Read this file completely before making any changes.**")
        L.append("> The sections are ordered by importance: Hard Rules first, then context.")
        L.append("")

        # ── HARD RULES (front-loaded — most important for LLM correctness) ──
        L.append("## HARD RULES — Non-Negotiable Constraints")
        L.append("")
        L.append("> These are architectural constraints derived from the ArchiMate model.")
        L.append("> Violating any one of these produces INCORRECT software, not just bad style.")
        L.append("> See `PRINCIPLES.md` for full details and enforcement locations.")
        L.append("")

        # Emit constraints from motivation layer if present, else use safe defaults
        if constraints:
            for c in constraints:
                enforcement = (c.get("enforcement") or "MUST").upper()
                marker = "🚫 MUST NOT" if "NOT" in enforcement else "✅ MUST"
                L.append(f"- **{marker}**: {c['name']}")
                if c.get("description"):
                    L.append(f"  → {c['description']}")
        else:
            L.append("- 🚫 **MUST NOT** put secrets in source code — use `os.environ` / `.env`")
            L.append("- 🚫 **MUST NOT** call `Query.all()` on any entity without `filter_by(organization_id=...)`")
            L.append("- 🚫 **MUST NOT** add routes without updating `openapi.yaml` first")
            if self.language == "python-flask":
                L.append("- 🚫 **MUST NOT** access protected routes without `@jwt_required()` decorator")
                L.append("- 🚫 **MUST NOT** use async SQLAlchemy — Flask uses synchronous SQLAlchemy")
                L.append("- ✅ **MUST** run `flask db upgrade` after every model field change")
            else:
                L.append("- 🚫 **MUST NOT** bypass `Depends(get_current_user)` on protected routes")
                L.append("- ✅ **MUST** add an Alembic migration for every model field change")
            L.append("- 🚫 **MUST NOT** write raw SQL — use SQLAlchemy ORM only")
            L.append("- 🚫 **MUST NOT** store JWTs in localStorage (frontend) or AsyncStorage (mobile)")
            L.append("- ✅ **MUST** run `pytest tests/ -q` before every commit")
            L.append("- ✅ **MUST** update `openapi.yaml` when changing any route signature")

        L.append("")

        # ── Architecture ──
        L.append("## Architecture")
        L.append("")
        L.append(f"- **Framework**: {self.language}")
        _db_desc = "SQLAlchemy (sync) + Flask-Migrate" if self.language == "python-flask" else "PostgreSQL with async SQLAlchemy"
        L.append(f"- **Database**: {_db_desc}")
        L.append(f"- **Auth**: {auth_label}")
        L.append(f"- **Genome version**: {genome_version}")
        L.append(f"- **Generated by**: Entelim (spec hash: {bundle.spec_hash})")
        L.append("")

        # ── Entities ──
        if confirmed:
            L.append("## Entities")
            L.append("")
            for entity_name, fields in confirmed.items():
                pascal_name = _pascal(entity_name)
                table_name = _pluralize(_snake(entity_name))
                L.append(f"### {pascal_name}")
                L.append(f"- **Table**: `{table_name}`")

                # Fields with types
                if fields:
                    field_strs = []
                    for f in fields:
                        _g = f.get if isinstance(f, dict) else lambda k, d=None: getattr(f, k, d)
                        fname = _g("name", "")
                        ftype = _g("type", "string")
                        req = " (required)" if _g("required", False) else ""
                        fk = f" FK→{_g('foreign_key')}" if _g("foreign_key") else ""
                        field_strs.append(f"`{fname}`: {ftype}{req}{fk}")
                    L.append(f"- **Fields**: {', '.join(field_strs)}")

                # State machine
                sm = state_machines.get(pascal_name) or state_machines.get(entity_name)
                if sm:
                    states = sm.states if hasattr(sm, "states") else (sm.get("states") or [])
                    transitions = sm.transitions if hasattr(sm, "transitions") else (sm.get("transitions") or [])
                    L.append(f"- **State machine** (`status`): {' → '.join(states)}")
                    for t in transitions:
                        trigger = t.trigger if hasattr(t, "trigger") else t.get("trigger", "")
                        from_s = t.from_state if hasattr(t, "from_state") else t.get("from", "")
                        to_s = t.to_state if hasattr(t, "to_state") else t.get("to", "")
                        L.append(f"  - `{trigger}`: {from_s} → {to_s}")

                # Foreign keys (relationships)
                fk_fields = []
                for f in (fields or []):
                    _g = f.get if isinstance(f, dict) else lambda k, d=None: getattr(f, k, d)
                    if _g("foreign_key"):
                        fk_fields.append(f"`{_g('name')}` → `{_g('foreign_key')}`")
                if fk_fields:
                    L.append(f"- **Relationships**: {', '.join(fk_fields)}")
                L.append("")

        # ── Business Rules ──
        if business_rules:
            L.append("## Business Rules")
            L.append("")
            for entity_name, rules in business_rules.items():
                if not rules:
                    continue
                for rule in rules[:10]:
                    rule_id = rule.id if hasattr(rule, "id") else (rule.get("id") or rule.get("name", "rule"))
                    trigger = rule.trigger if hasattr(rule, "trigger") else rule.get("trigger", "")
                    desc = rule.description if hasattr(rule, "description") else rule.get("description", trigger)
                    L.append(f"- **{rule_id}**: {desc} (enforced in `app/handlers/business_logic.py`)")
            L.append("")
            L.append("**Do not weaken or remove business rules without explicit approval.**")
            L.append("")

        # ── API Endpoints ──
        if bundle.services:
            L.append("## API Endpoints")
            L.append("")
            for svc in bundle.services:
                L.append(f"### {svc.name}")
                resource = _snake(svc.tag) if svc.tag else _snake(svc.name)
                for p in (svc.paths or []):
                    L.append(f"- `{p.method.upper()} {p.path}` — {p.summary}")
                # State machine transition endpoints
                pascal_svc = _pascal(svc.name)
                sm = state_machines.get(pascal_svc) or state_machines.get(svc.name)
                if sm:
                    transitions = sm.transitions if hasattr(sm, "transitions") else (sm.get("transitions") or [])
                    seen = set()
                    for t in transitions:
                        trigger = t.trigger if hasattr(t, "trigger") else t.get("trigger", "")
                        from_s = t.from_state if hasattr(t, "from_state") else t.get("from", "")
                        to_s = t.to_state if hasattr(t, "to_state") else t.get("to", "")
                        if trigger and trigger not in seen:
                            seen.add(trigger)
                            L.append(f"- `POST /api/{resource}s/{{id}}/{trigger}` — {from_s} → {to_s}")
                    L.append(f"- `GET /api/{resource}s/{{id}}/available-actions` — list valid transitions")
                L.append("")

        # ── File Structure ──
        L.append("## File Structure")
        L.append("")
        L.append("```")
        L.append("app/")
        L.append("  main.py              # FastAPI entry point + middleware")
        L.append("  database.py          # Async SQLAlchemy engine + session")
        L.append("  models/              # SQLAlchemy ORM models (one per entity)")
        L.append("  schemas/             # Pydantic request/response schemas")
        L.append("  routes/              # FastAPI routers (one per service)")
        if business_rules:
            L.append("  handlers/            # Business rule enforcement")
        if state_machines:
            L.append("  state_machines/      # State machine classes")
        if integrations:
            L.append("  clients/             # External API clients")
        L.append("tests/                 # Contract + behavioral + security tests")
        L.append("ARCHITECTURE.md        # Full architecture context + traceability")
        L.append("CLAUDE.md              # This file")
        L.append("openapi.yaml           # OpenAPI 3.1 spec (authoritative)")
        L.append("docker-compose.yml     # Local dev orchestration")
        L.append("Dockerfile             # Multi-stage production build")
        if has_keycloak:
            L.append("infrastructure/keycloak/  # Keycloak realm import")
        L.append("```")
        L.append("")

        # ── Development ──
        L.append("## Development")
        L.append("")
        L.append("```bash")
        L.append(f"docker-compose up --build  # starts app + database{' + Keycloak' if has_keycloak else ''}")
        L.append("pytest tests/ -v           # run all tests")
        L.append("alembic upgrade head       # apply migrations")
        L.append("```")
        L.append("")

        # ── Rules for AI Agents ──
        L.append("## Rules for AI Agents")
        L.append("")
        L.append("1. **Read HARD RULES above before touching any file.**")
        L.append("2. **Read `PRINCIPLES.md`** — architectural principles with code implications and enforcement files.")
        L.append("3. **Read `DOMAIN.md`** — domain vocabulary and ubiquitous language. Never rename domain terms.")
        L.append("4. **Read `DEPENDENCIES.md`** — before changing any interface, check the impact matrix.")
        L.append("5. **Read `ARCHITECTURE.md`** — full ArchiMate traceability + acceptance criteria.")
        L.append("6. **Run tests before and after changes.** `pytest tests/ -v` must pass.")
        L.append("7. **API contracts in `openapi.yaml` are authoritative.** Routes must match the spec.")
        L.append("8. **Business rules in `handlers/` are architecture-enforced.** Don't remove or weaken them.")
        L.append(f"9. **Auth: {auth_label}.** Don't bypass `Depends(get_current_user)` on any protected route.")
        L.append("10. **Every model field change needs an Alembic migration.** No bare `db.create_all()`.")
        L.append("")
        if principles:
            L.append("## Key Architecture Principles (summary)")
            L.append("")
            for p in principles[:5]:
                L.append(f"- **{p['name']}**: {p.get('description', '')[:120]}")
            if len(principles) > 5:
                L.append(f"- *… and {len(principles) - 5} more in `PRINCIPLES.md`*")
            L.append("")

        # ── Regeneration ──
        L.append("## Regeneration")
        L.append("")
        L.append("This code was generated from an Architectural Genome. To regenerate:")
        L.append("1. Edit `architectural_genome.yaml` to change the architecture")
        L.append(f"2. Run: `POST /solutions/{bundle.solution_id}/codegen/generate {{\"generation_mode\": \"genome\"}}`")
        L.append("3. Modified files will be detected and warned before overwrite (drift detection)")
        L.append("")

        L.append("---")
        L.append(f"*Generated by Entelim for solution {bundle.solution_id} (spec hash: {bundle.spec_hash})*")

        return "\n".join(L) + "\n"

    # ── Traceability document ──

    def _generate_architecture_md(self, bundle, result: "GeneratedCodeBundle") -> str:
        """Generate ARCHITECTURE.md tracing ArchiMate elements → generated files.

        Maps:
        - confirmed_fields keys (DataObject names) → SQLAlchemy model files
        - bundle.services (ApplicationServices) → route files
        - archimate_source_ids in GeneratedFile → ArchiMate element IDs
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"# Architecture Traceability — {bundle.solution_name}",
            "",
            f"> Generated: {now}  ",
            f"> Solution ID: {bundle.solution_id}  ",
            f"> Spec hash: `{bundle.spec_hash}`",
            "",
            "This document maps every ArchiMate element in the solution blueprint to the",
            "generated source files that implement it.",
            "",
        ]

        # Section 0: Motivation & Decision Context (Stakeholders, Goals, Constraints, Decisions)
        motivation = self._build_motivation_context(bundle.solution_id)
        if motivation:
            lines.append("## Why This Was Built")
            lines.append("")
            if motivation.get("problem_statement"):
                lines.append("### Problem Statement")
                lines.append("")
                lines.append(motivation["problem_statement"])
                lines.append("")

            if motivation.get("stakeholders"):
                lines.append("### Stakeholders & Drivers")
                lines.append("")
                lines.append("| Stakeholder | Type | Drivers |")
                lines.append("|-------------|------|---------|")
                for s in motivation["stakeholders"]:
                    lines.append(f"| {s['name']} | {s.get('type', 'Stakeholder')} | {s.get('drivers', '')} |")
                lines.append("")

            if motivation.get("drivers"):
                lines.append("### Drivers")
                lines.append("")
                for d in motivation["drivers"]:
                    desc = f" — {d['description']}" if d.get('description') else ""
                    lines.append(f"- **{d['name']}**{desc}")
                lines.append("")

            if motivation.get("assessments"):
                lines.append("### Assessments")
                lines.append("")
                for a in motivation["assessments"]:
                    desc = f" — {a['description']}" if a.get('description') else ""
                    lines.append(f"- **{a['name']}**{desc}")
                lines.append("")

            if motivation.get("goals"):
                lines.append("### Goals & Outcomes")
                lines.append("")
                for g in motivation["goals"]:
                    gtype = f" *({g['type']})*" if g.get('type') and g['type'] != 'Goal' else ""
                    desc = f" — {g['description']}" if g.get('description') else ""
                    lines.append(f"- **{g['name']}**{gtype}{desc}")
                lines.append("")

            if motivation.get("requirements"):
                lines.append("### Requirements")
                lines.append("")
                for r in motivation["requirements"]:
                    priority = f" `[{r['priority']}]`" if r.get('priority') else ""
                    lines.append(f"- {r['name']}{priority}")
                lines.append("")

            if motivation.get("constraints"):
                lines.append("### Constraints")
                lines.append("")
                lines.append("| Constraint | Source | Enforcement |")
                lines.append("|------------|--------|-------------|")
                for c in motivation["constraints"]:
                    lines.append(f"| {c['name']} | {c.get('source', 'Architecture')} | {c.get('enforcement', 'MUST')} |")
                lines.append("")

            if motivation.get("principles"):
                lines.append("### Architecture Principles")
                lines.append("")
                for p in motivation["principles"]:
                    lines.append(f"- **{p['name']}**: {p.get('description', '')}")
                lines.append("")

            if motivation.get("decisions"):
                lines.append("### Architecture Decisions")
                lines.append("")
                lines.append("| Decision | Choice | Rationale |")
                lines.append("|----------|--------|-----------|")
                for d in motivation["decisions"]:
                    lines.append(f"| {d['name']} | {d.get('choice', '')} | {d.get('rationale', '')} |")
                lines.append("")

            if motivation.get("meanings"):
                lines.append("### Meanings")
                lines.append("")
                lines.append("*How concepts in this solution should be interpreted:*")
                lines.append("")
                for m in motivation["meanings"]:
                    desc = f" — {m['description']}" if m.get('description') else ""
                    lines.append(f"- **{m['name']}**{desc}")
                lines.append("")

            if motivation.get("values"):
                lines.append("### Values")
                lines.append("")
                lines.append("*What this solution delivers to stakeholders:*")
                lines.append("")
                for v in motivation["values"]:
                    desc = f" — {v['description']}" if v.get('description') else ""
                    lines.append(f"- **{v['name']}**{desc}")
                lines.append("")

            if motivation.get("nfrs"):
                lines.append("### Non-Functional Requirements")
                lines.append("")
                for n in motivation["nfrs"]:
                    lines.append(f"- **{n['name']}**: {n.get('value', '')}")
                lines.append("")

        # Section 0b: Acceptance Criteria (inferred from motivation + business rules)
        try:
            from app.modules.codegen.services.acceptance_criteria_generator import AcceptanceCriteriaGenerator
            ac_gen = AcceptanceCriteriaGenerator()
            acceptance_criteria = ac_gen.generate(bundle.solution_id)
            if acceptance_criteria:
                lines.append("## Acceptance Criteria")
                lines.append("")
                lines.append(f"*{len(acceptance_criteria)} criteria inferred from goals, requirements, constraints, and business rules.*")
                lines.append("")
                for ac in acceptance_criteria:
                    lines.append(f"### {ac['id']}: {ac['title']}")
                    lines.append("")
                    lines.append(f"- **Priority:** {ac['priority']}")
                    lines.append(f"- **Category:** {ac['category']}")
                    lines.append(f"- **GIVEN:** {ac['given']}")
                    lines.append(f"- **WHEN:** {ac['when']}")
                    lines.append(f"- **THEN:** {ac['then']}")
                    lines.append(f"- **Verification:** {ac['verification']}")
                    if ac.get("source"):
                        src = ac["source"]
                        lines.append(f"- **Source:** {src.get('type', '')} — {src.get('name', '')}")
                    lines.append("")
        except Exception as _ac_exc:
            logger.debug("AC generation for ARCHITECTURE.md skipped: %s", _ac_exc)

        # Section 1: Data model traceability (DataObject → SQLAlchemy model)
        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        lines.append("## Data Model")
        lines.append("")
        if confirmed_fields:
            lines.append("| ArchiMate DataObject | Generated File | SQLAlchemy Class | Fields |")
            lines.append("|----------------------|----------------|-----------------|--------|")
            for model_name, fields_list in confirmed_fields.items():
                pascal_name = _pascal(model_name)
                file_path = f"app/models/{model_name.lower()}.py"
                field_count = len(fields_list) if fields_list else 0
                field_names = ", ".join(
                    (f.get("name") or f.get("field_name") or "") if isinstance(f, dict) else f.name
                    for f in (fields_list or [])[:5]
                )
                if field_count > 5:
                    field_names += f", … (+{field_count - 5} more)"
                lines.append(
                    f"| {pascal_name} | `{file_path}` | `{pascal_name}` | {field_names or '*(inferred)*'} |"
                )
        else:
            lines.append("*No confirmed data model — fields were inferred from capability names.*")
            lines.append("")
            lines.append("| ArchiMate DataObject | Generated File | SQLAlchemy Class |")
            lines.append("|----------------------|----------------|-----------------|")
            for svc in bundle.services:
                pascal_name = _pascal(svc.name)
                file_path = f"app/models/{svc.name.lower()}.py"
                lines.append(
                    f"| {pascal_name} | `{file_path}` | `{pascal_name}` |"
                )
        lines.append("")

        # Section 2: Service/route traceability (ApplicationService → route file)
        lines.append("## Application Services")
        lines.append("")
        if bundle.services:
            lines.append("| ArchiMate ApplicationService | Generated File | Routes |")
            lines.append("|------------------------------|----------------|--------|")
            for svc in bundle.services:
                route_file = f"app/routes/{svc.name.lower()}_routes.py"
                route_list = ", ".join(
                    f"`{p.method.upper()} {p.path}`"
                    for p in (svc.paths or [])[:4]
                )
                if len(svc.paths or []) > 4:
                    route_list += f" … (+{len(svc.paths) - 4} more)"
                lines.append(
                    f"| {svc.name} | `{route_file}` | {route_list or '*(CRUD)*'} |"
                )
        else:
            lines.append("*No services defined in this bundle.*")
        lines.append("")

        # Section 3: ArchiMate source cross-reference (files with explicit source IDs)
        sourced_files = [
            f for f in result.files
            if getattr(f, "archimate_sources", None)
        ]
        if sourced_files:
            lines.append("## ArchiMate Source Cross-Reference")
            lines.append("")
            lines.append("| Generated File | ArchiMate Element IDs |")
            lines.append("|----------------|-----------------------|")
            for gf in sourced_files:
                ids = ", ".join(str(i) for i in gf.archimate_sources)
                lines.append(f"| `{gf.path}` | {ids} |")
            lines.append("")

        # Section 4: File inventory
        lines.append("## Generated File Inventory")
        lines.append("")
        lines.append(f"Total files: **{len(result.files) + 1}** (including this document)")
        lines.append("")
        lines.append("```")
        for gf in result.files:
            lines.append(gf.path)
        lines.append("ARCHITECTURE.md")
        lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("*Architecture context + traceability auto-generated by Entelim*")
        lines.append("*LLMs: use this document to understand WHY this code exists and WHAT constraints apply.*")

        return "\n".join(lines) + "\n"

    @staticmethod
    def _build_motivation_context(solution_id):
        """Extract Motivation layer context from solution's ArchiMate elements and journey state.

        Pulls: Stakeholders, Drivers, Goals, Requirements, Constraints, Principles
        from the ArchiMate Motivation layer + solution.problem_clarification + ACM properties.
        Returns a dict with keys: problem_statement, stakeholders, goals, requirements,
        constraints, principles, decisions, nfrs. All optional — returns {} if no data.
        """
        try:
            from app.models.solution_models import Solution
            from app.models.solution_archimate_element import SolutionArchiMateElement
            from app.models.archimate_core import ArchiMateElement

            solution = Solution.query.get(solution_id)
            if not solution:
                return {}

            result = {}

            # Problem statement from journey state
            js = solution.journey_state if hasattr(solution, "journey_state") and solution.journey_state else {}
            pc = solution.problem_clarification if hasattr(solution, "problem_clarification") else None
            brief = ""
            if isinstance(js, dict):
                brief = js.get("enriched_brief") or js.get("problem_statement") or ""
            if not brief and isinstance(pc, dict):
                brief = pc.get("enriched_brief") or pc.get("original") or ""
            elif not brief and isinstance(pc, str):
                brief = pc
            if brief:
                result["problem_statement"] = brief[:2000]

            # ArchiMate Motivation layer elements
            links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
            element_ids = [link.element_id for link in links]
            if not element_ids:
                return result

            elements = ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids)).all()
            spec_map = {link.element_id: (link.spec_data or {}) for link in links}

            _MOTIVATION_TYPES = {
                "Stakeholder", "Driver", "Assessment", "Goal", "Outcome",
                "Principle", "Requirement", "Constraint", "Meaning", "Value",
            }

            stakeholders = []
            drivers = []
            assessments = []
            goals = []
            requirements = []
            constraints = []
            principles = []
            meanings = []
            values = []

            for e in elements:
                spec = spec_map.get(e.id, {})
                if e.type not in _MOTIVATION_TYPES:
                    continue

                if e.type == "Stakeholder":
                    # Find associated drivers
                    assoc_drivers = [
                        el.name for el in elements
                        if el.type == "Driver" and el.id in element_ids
                    ]
                    stakeholders.append({
                        "name": e.name,
                        "type": e.type,
                        "description": e.description or "",
                        "drivers": ", ".join(assoc_drivers[:3]) if assoc_drivers else "",
                    })
                elif e.type == "Driver":
                    drivers.append({
                        "name": e.name,
                        "description": e.description or "",
                    })
                elif e.type == "Assessment":
                    assessments.append({
                        "name": e.name,
                        "description": e.description or "",
                    })
                elif e.type in ("Goal", "Outcome"):
                    goals.append({
                        "name": e.name,
                        "type": e.type,
                        "description": e.description or "",
                    })
                elif e.type == "Requirement":
                    priority = ""
                    if isinstance(spec, dict):
                        p = spec.get("priority")
                        if isinstance(p, dict):
                            priority = p.get("value", "")
                        elif isinstance(p, str):
                            priority = p
                    requirements.append({
                        "name": e.name,
                        "priority": priority or "MUST",
                    })
                elif e.type == "Constraint":
                    constraints.append({
                        "name": e.name,
                        "source": e.description[:80] if e.description else "Architecture",
                        "enforcement": "MUST",
                    })
                elif e.type == "Principle":
                    principles.append({
                        "name": e.name,
                        "description": e.description or "",
                    })
                elif e.type == "Meaning":
                    meanings.append({
                        "name": e.name,
                        "description": e.description or "",
                    })
                elif e.type == "Value":
                    values.append({
                        "name": e.name,
                        "description": e.description or "",
                    })

            if stakeholders:
                result["stakeholders"] = stakeholders
            if drivers:
                result["drivers"] = drivers
            if assessments:
                result["assessments"] = assessments
            if goals:
                result["goals"] = goals
            if requirements:
                result["requirements"] = requirements
            if meanings:
                result["meanings"] = meanings
            if values:
                result["values"] = values
            if constraints:
                result["constraints"] = constraints
            if principles:
                result["principles"] = principles

            # Architecture decisions from ACM properties (build_or_buy, deployment_model)
            decisions = []
            nfrs = []
            for e in elements:
                if e.type != "ApplicationComponent":
                    continue
                acm = e.acm_properties or {}
                bob = acm.get("build_or_buy")
                if bob:
                    val = bob.get("value", bob) if isinstance(bob, dict) else bob
                    if val:
                        decisions.append({
                            "name": e.name,
                            "choice": str(val),
                            "rationale": bob.get("rationale", "") if isinstance(bob, dict) else "",
                        })
                # NFRs from element properties
                for nfr_key in ("availability_target", "scalability_pattern", "data_classification"):
                    nfr_val = acm.get(nfr_key)
                    if nfr_val:
                        v = nfr_val.get("value", nfr_val) if isinstance(nfr_val, dict) else nfr_val
                        if v and v != "TBD":
                            nfrs.append({"name": f"{e.name}: {nfr_key.replace('_', ' ')}", "value": str(v)})

            if decisions:
                result["decisions"] = decisions[:20]
            if nfrs:
                # Deduplicate
                seen = set()
                unique_nfrs = []
                for n in nfrs:
                    key = n["name"]
                    if key not in seen:
                        seen.add(key)
                        unique_nfrs.append(n)
                result["nfrs"] = unique_nfrs[:15]

            return result

        except Exception as exc:
            logger.warning("Failed to build motivation context for solution %d: %s", solution_id, exc)
            return {}

    # ── Go/Chi generation ──

    def _generate_go(self, bundle):
        """Generate a complete Go/Chi microservice project."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        bundle_id = f"code_{now}"

        files = []
        _idp = getattr(bundle, "identity_provider", None) or {}
        ctx = {
            "solution_id": bundle.solution_id,
            "solution_name": bundle.solution_name,
            "bundle_id": bundle_id,
            "spec_hash": bundle.spec_hash,
            "maturity_score": bundle.maturity_score,
            "generated_at": now,
            "identity_provider": _idp,
            "multi_tenancy": getattr(bundle, "multi_tenancy", {}) or {},
            "tenant_field": (getattr(bundle, "multi_tenancy", {}) or {}).get("tenant_field"),
            "workers": getattr(bundle, "workers", []) or [],
            "payment_config": getattr(bundle, "payment_config", {}) or {},
        }

        # 1. Generate main.go (chi router, middleware, graceful shutdown)
        svc_data = [{"name": s.name, "tag": _snake(s.tag)} for s in bundle.services]
        template = self._env.get_template("main.go.j2")
        content = template.render(**ctx, services=svc_data)
        files.append(GeneratedFile(path="main.go", content=content))

        # 2. Generate routes.go (all service route handlers in one file)
        go_services = []
        for svc in bundle.services:
            archimate_ids = []
            paths = []
            for p in svc.paths:
                if p.archimate_source_id:
                    archimate_ids.append(p.archimate_source_id)
                paths.append({
                    "path": p.path,
                    "method": p.method.lower(),
                    "operation_id": p.operation_id,
                    "summary": p.summary,
                    "request_schema": p.request_schema if p.request_schema != "dict" else None,
                    "response_schema": p.response_schema if p.response_schema != "dict" else None,
                    "archimate_source_id": p.archimate_source_id,
                })
            go_services.append({
                "name": svc.name,
                "tag": svc.tag,
                "paths": paths,
                "archimate_ids": archimate_ids or ["N/A"],
            })
        template = self._env.get_template("routes.go.j2")
        content = template.render(
            **ctx,
            services=go_services,
            archimate_ids=["N/A"],
            used_schemas=[],
            paths=[],
        )
        files.append(GeneratedFile(path="routes.go", content=content))

        # 3. Generate middleware.go (JWT, logging, rate limiting)
        idp = getattr(bundle, "identity_provider", None) or {}
        mw_ctx = dict(ctx, identity_provider=idp)
        files.append(self._render_middleware(mw_ctx))

        # 4. Generate config.go (envconfig pattern, includes OIDC fields)
        files.append(self._render_simple("config.go.j2", "config.go", mw_ctx))

        # 5. Generate contract tests
        test_services = []
        for svc in bundle.services:
            test_paths = []
            for p in svc.paths:
                # p.path already contains the full URL (e.g. /api/users) — use it directly
                test_url = _replace_path_params(p.path)
                example_body = None
                if p.request_schema and p.method.upper() in ("POST", "PUT", "PATCH"):
                    example_body = "{}"
                test_paths.append({
                    "method": p.method.lower(),
                    "operation_id": p.operation_id,
                    "summary": p.summary,
                    "full_url": test_url,
                    "example_body": example_body,
                })
            test_services.append({"name": svc.name, "tag": svc.tag, "paths": test_paths})

        template = self._env.get_template("test_contracts.go.j2")
        content = template.render(**ctx, services=test_services)
        files.append(GeneratedFile(path="main_test.go", content=content))

        # 5a. Generate comprehensive test suite (behavioral, edge cases, security, k6)
        files.extend(self._render_test_suite(ctx, bundle, is_go=True))

        # 5b. Pre-classify integrations to set dependency flags before infra rendering
        integrations = getattr(bundle, "integrations", {}) or {}
        rest_integ, grpc_integ, async_integ = self._classify_integrations(integrations)
        ctx["has_kafka"] = bool(async_integ)
        ctx["has_grpc"] = bool(grpc_integ)

        # 5c. Resolve architecture style
        arch_style = self._get_arch_style(bundle)
        is_serverless = arch_style == "serverless"
        arch_config = self._get_arch_config(bundle)

        # 6. Generate infrastructure files (skip Dockerfile/docker-compose for serverless)
        if not is_serverless:
            files.append(self._render_simple("Dockerfile.j2", "Dockerfile", ctx))
            files.append(self._render_simple("docker_compose.yml.j2", "docker-compose.yml", ctx))
        files.append(self._render_simple("go.mod.j2", "go.mod", ctx))
        files.append(self._render_simple("GENERATED.md.j2", "GENERATED.md", ctx))

        # 6a. Generate bootstrap files (Makefile, .env.example, README.md, bootstrap.sh)
        files.extend(self._render_bootstrap_files(ctx, bundle))

        # 7. Generate models from confirmed fields
        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        if confirmed_fields:
            models = self._build_model_context(bundle)
            template = self._env.get_template("models.go.j2")
            content = template.render(**ctx, models=models)
            files.append(GeneratedFile(path="models.go", content=content))

            # Generate repository
            template = self._env.get_template("repository.go.j2")
            content = template.render(**ctx, models=models)
            files.append(GeneratedFile(path="repository.go", content=content))

            # Generate migration
            rev = hashlib.md5(bundle_id.encode(), usedforsecurity=False).hexdigest()[:12]
            template = self._env.get_template("migration.go.j2")
            content = template.render(**ctx, models=models, revision_id=rev)
            files.append(GeneratedFile(path="migration.go", content=content))

        # 8. Generate handlers from confirmed business rules
        business_rules = getattr(bundle, "business_rules", {}) or {}
        if business_rules:
            models_with_rules = self._build_handler_context(bundle)
            template = self._env.get_template("handlers.go.j2")
            content = template.render(**ctx, models=models_with_rules)
            files.append(GeneratedFile(path="handlers.go", content=content))

        # 8b. Genome-driven infrastructure templates (Go equivalents)
        _nfr_flags_go = {nfr["flag"] for nfr in (bundle.infra_context.nfrs or []) if nfr.get("flag")}
        if "multi_tenancy" in _nfr_flags_go:
            try:
                files.append(self._render_simple(
                    "tenant_middleware.go.j2", "tenant_middleware.go", ctx,
                ))
            except Exception as exc:
                logger.debug("suppressed error in DeterministicCodeGenerator._generate_go (app/modules/solutions_product/services/deterministic_code_generator.py): %s", exc)
        if "api_keys" in _nfr_flags_go:
            try:
                files.append(self._render_simple(
                    "api_keys.go.j2", "api_keys.go", ctx,
                ))
            except Exception as exc:
                logger.debug("suppressed error in DeterministicCodeGenerator._generate_go (app/modules/solutions_product/services/deterministic_code_generator.py): %s", exc)
        if "opentelemetry" in _nfr_flags_go:
            try:
                files.append(self._render_simple(
                    "telemetry.go.j2", "telemetry.go", ctx,
                ))
            except Exception as exc:
                logger.debug("suppressed error in DeterministicCodeGenerator._generate_go (app/modules/solutions_product/services/deterministic_code_generator.py): %s", exc)

        # 9. Generate integration clients (routed by communication_type)
        if integrations:
            if rest_integ:
                template = self._env.get_template("client.go.j2")
                content = template.render(**ctx, integrations=rest_integ)
                files.append(GeneratedFile(path="client.go", content=content))
            if async_integ:
                files.append(self._render_template_file(
                    "kafka_producer.go.j2", "kafka_producer.go",
                    ctx, integrations=async_integ,
                ))
                files.append(self._render_template_file(
                    "kafka_consumer.go.j2", "kafka_consumer.go",
                    ctx, integrations=async_integ,
                ))
            if grpc_integ:
                files.append(self._render_template_file(
                    "grpc_client.go.j2", "grpc_client.go",
                    ctx, integrations=grpc_integ,
                ))

        # 10. Generate deployment manifests (skip K8s/Helm for serverless)
        deployment = getattr(bundle, "deployment", None)
        if deployment and not is_serverless:
            deploy_data = {bundle.solution_name: vars(deployment)}
            template = self._env.get_template("k8s_deployment.yaml.j2")
            content = template.render(**ctx, deployments=deploy_data)
            files.append(GeneratedFile(path="k8s/deployment.yaml", content=content))

            template = self._env.get_template("helm_values.yaml.j2")
            helm_ctx = dict(ctx, solution_name=bundle.solution_name)
            content = template.render(**helm_ctx, deployment=vars(deployment))
            files.append(GeneratedFile(path="helm/values.yaml", content=content))

        # 11. Generate CI/CD pipeline
        ci_cd_config = getattr(bundle, "ci_cd", None) or {}
        files.extend(self._render_cicd(ctx, ci_cd_config))

        # 12. Generate Terraform configs and seed data
        files.extend(self._render_terraform(ctx, bundle))

        # 13. Architecture style-specific templates
        files.extend(self._render_arch_style_files(
            ctx, bundle, arch_style, arch_config,
        ))

        # 14. Generate admin UI
        admin_entities = self._build_admin_entities(bundle)
        if admin_entities:
            files.append(self._render_admin_ui(ctx, admin_entities, is_go=True))

        # One-click deploy manifests
        files.append(GeneratedFile(
            path="railway.json",
            content='{\n  "$schema": "https://railway.app/railway.schema.json",\n'
                    '  "build": {\n    "builder": "DOCKERFILE",\n    "dockerfilePath": "Dockerfile"\n  },\n'
                    '  "deploy": {\n    "numReplicas": 1,\n    "restartPolicyType": "ON_FAILURE",\n'
                    '    "restartPolicyMaxRetries": 10\n  }\n}\n',
        ))

        # 15. Generate product UI (full SPA)
        files.extend(self._render_product_ui(ctx, bundle, is_go=True))

        # 16. Generate observability files (Grafana dashboard, Prometheus alerts, runbook)
        files.extend(self._render_observability_files(ctx, bundle))

        # 17. Generate mock server + mock data JSON
        files.extend(self._render_mock_server(ctx, bundle))

        # 18. Generate structured Go project files (internal/ layout)
        # These complement the flat-file templates with a proper Go project structure.
        files.extend(self._render_go_structured_templates(ctx, bundle))

        return GeneratedCodeBundle(
            solution_id=bundle.solution_id,
            bundle_id=bundle_id,
            language=self.language,
            spec_hash=bundle.spec_hash,
            files=files,
        )

    @staticmethod
    def _build_mermaid_diagram(services, integrations=None):
        """Build a Mermaid flowchart from service definitions and integrations.

        Returns a string suitable for embedding in a ```mermaid code block.
        """
        lines = ["graph LR"]
        lines.append("    Client([Client])")

        if not services:
            lines.append("    Client --> API[API]")
            lines.append("    API --> DB[(Database)]")
            return "\n".join(lines)

        # API gateway node
        lines.append("    API[API Gateway]")
        lines.append("    Client --> API")

        # One node per service
        for svc in services:
            node_id = _snake(svc.tag if hasattr(svc, "tag") else svc.get("tag", "svc"))
            node_label = svc.name if hasattr(svc, "name") else svc.get("name", node_id)
            lines.append(f"    {node_id}[{node_label}]")
            lines.append(f"    API --> {node_id}")

        # Database
        lines.append("    DB[(PostgreSQL)]")
        for svc in services:
            node_id = _snake(svc.tag if hasattr(svc, "tag") else svc.get("tag", "svc"))
            lines.append(f"    {node_id} --> DB")

        # Integration targets
        if integrations:
            for name in integrations:
                ext_id = _snake(name)[:20]
                lines.append(f"    {ext_id}([{name}])")
                lines.append(f"    API --> {ext_id}")

        return "\n".join(lines)

    def _build_observability_context(self, ctx, bundle):
        """Build template context for observability templates (dashboard, alerts, runbook).

        Extracts SLAs, integrations, endpoints, and entities from the bundle
        into a flat structure suitable for Jinja2 rendering.

        Returns:
            dict with keys: service_name, slas, integrations, endpoints, entities,
            primary_sla_ms, owner_team, architecture_style, criticality,
            service_description, port, spec_hash.
        """
        service_name = _kebab(bundle.solution_name)

        # Parse SLAs
        slas = []
        for raw_sla in (bundle.infra_context.slas or []):
            name = raw_sla.get("name", "default")
            response_ms = raw_sla.get("response_time_ms") or 500
            slas.append({
                "name": name,
                "name_snake": _snake(name),
                "value_ms": response_ms,
                "threshold_seconds": response_ms / 1000.0,
                "response_time_ms": response_ms,
            })

        # Parse integrations
        integration_list = []
        raw_integrations = getattr(bundle, "integrations", {}) or {}
        for int_name, contract in raw_integrations.items():
            if isinstance(contract, dict):
                protocol = contract.get("protocol", "rest")
                auth_method = contract.get("auth_method", "none")
                sla_data = contract.get("sla", {})
                sla_latency = sla_data.get("max_latency_ms") if isinstance(sla_data, dict) else None
                base_url = contract.get("base_url", "")
                doc_url = contract.get("documentation_url", "")
                fallback = contract.get("fallback_behavior", "")
            else:
                protocol = getattr(contract, "protocol", "rest")
                auth_method = getattr(contract, "auth_method", "none")
                sla_latency = getattr(contract, "sla_latency_ms", None)
                base_url = getattr(contract, "base_url", "")
                doc_url = getattr(contract, "documentation_url", "")
                fallback = getattr(contract, "fallback_behavior", "")

            display_name = int_name.replace("_", " ").replace("flow ", "").strip()
            integration_list.append({
                "name": display_name,
                "name_snake": _snake(display_name),
                "protocol": protocol,
                "auth_method": auth_method,
                "sla_latency_ms": sla_latency,
                "base_url": base_url,
                "documentation_url": doc_url,
                "fallback_behavior": fallback,
                "health_check": f"{base_url}/health" if base_url else "N/A",
            })

        # Parse endpoints from services
        endpoints = []
        for svc in bundle.services:
            for p in svc.paths:
                endpoints.append({
                    "method": p.method,
                    "path": p.path,
                    "summary": p.summary,
                })

        # Parse entities from confirmed fields or service tags
        entities = []
        confirmed = getattr(bundle, "confirmed_fields", {}) or {}
        if confirmed:
            for entity_name in confirmed:
                entities.append({
                    "name": _pascal(entity_name),
                    "name_snake": _snake(entity_name),
                })
        else:
            for svc in bundle.services:
                entities.append({
                    "name": svc.name,
                    "name_snake": _snake(svc.tag),
                })

        # Primary SLA (first one, or default 500ms)
        primary_sla_ms = slas[0]["value_ms"] if slas else 500

        # Architecture style
        arch_style = self._get_arch_style(bundle)

        # Criticality from quality attributes
        criticality = "standard"
        for nfr in (bundle.infra_context.nfrs or []):
            if "critical" in (nfr.get("category", "") + nfr.get("name", "")).lower():
                criticality = "high"
                break

        # Owner team from solution metadata
        owner_team = "Architecture Team"
        try:
            sol_meta = getattr(bundle, "metadata_json", None) or {}
            if isinstance(sol_meta, dict) and sol_meta.get("owner_team"):
                owner_team = sol_meta["owner_team"]
        except Exception as e:
            logger.debug("Could not read owner_team from metadata: %s", e)

        is_python = self.language == "python-fastapi"
        port = "8000" if is_python else "8080"

        return dict(
            ctx,
            service_name=service_name,
            service_description=f"Service generated from Entelim solution #{bundle.solution_id} ({bundle.solution_name}).",
            slas=slas,
            integrations=integration_list,
            endpoints=endpoints,
            entities=entities,
            primary_sla_ms=primary_sla_ms,
            owner_team=owner_team,
            architecture_style=arch_style,
            criticality=criticality,
            port=port,
        )

    def _render_observability_files(self, ctx, bundle):
        """Render monitoring/grafana-dashboard.json, monitoring/prometheus-alerts.yaml, and docs/RUNBOOK.md.

        Uses the shared templates with observability context built from the bundle.

        Returns:
            list[GeneratedFile]
        """
        obs_ctx = self._build_observability_context(ctx, bundle)
        files = []

        # Grafana dashboard
        template = self._shared_env.get_template("grafana_dashboard.json.j2")
        content = template.render(**obs_ctx)
        files.append(GeneratedFile(path="monitoring/grafana-dashboard.json", content=content))

        # Prometheus alerts
        template = self._shared_env.get_template("prometheus_alerts.yaml.j2")
        content = template.render(**obs_ctx)
        files.append(GeneratedFile(path="monitoring/prometheus-alerts.yaml", content=content))

        # Runbook
        template = self._shared_env.get_template("runbook.md.j2")
        content = template.render(**obs_ctx)
        files.append(GeneratedFile(path="docs/RUNBOOK.md", content=content))

        return files

    def _render_shared_template(self, template_name, output_path, ctx):
        """Render a shared template with the base context."""
        template = self._shared_env.get_template(template_name)
        content = template.render(**ctx)
        return GeneratedFile(path=output_path, content=content)

    def _render_bootstrap_files(self, ctx, bundle):
        """Render Makefile, .env.example, README.md, and bootstrap.sh.

        These files provide the one-command setup experience for generated projects.
        """
        files = []
        project_name = _kebab(bundle.solution_name)
        db_name = _snake(bundle.solution_name)
        idp = getattr(bundle, "identity_provider", None) or {}
        integrations = getattr(bundle, "integrations", {}) or {}

        # Language-specific settings
        is_python = self.language in ("python-fastapi", "python-flask")
        is_flask = self.language == "python-flask"
        port = "5000" if is_flask else ("8000" if is_python else "8080")
        test_cmd = "pytest tests/ -v" if is_python else "go test ./... -v"
        lint_cmd = "ruff check ." if is_python else "golangci-lint run"
        migrate_cmd = "flask db upgrade" if is_flask else ("alembic upgrade head" if is_python else "./migrate")
        seed_cmd = "python seed.py"  # Seeder wired to entrypoint.sh

        # Build Mermaid diagram from services
        mermaid = self._build_mermaid_diagram(bundle.services, integrations)

        # Build service path data for README endpoint table
        svc_data = []
        for svc in bundle.services:
            paths = []
            for p in svc.paths:
                paths.append({
                    "path": p.path,
                    "method": p.method,
                    "summary": p.summary,
                })
            svc_data.append({
                "name": svc.name,
                "tag": svc.tag,
                "paths": paths,
            })

        bootstrap_ctx = dict(
            ctx,
            project_name=project_name,
            db_name=db_name,
            language=self.language,
            port=port,
            image_name=project_name,
            test_command=test_cmd,
            lint_command=lint_cmd,
            migrate_command=migrate_cmd,
            seed_command=seed_cmd,
            staging_namespace=f"{project_name}-staging",
            prod_namespace=f"{project_name}-prod",
            identity_provider=idp,
            integrations=integrations,
            mermaid_diagram=mermaid,
            services=svc_data,
        )

        # Shared templates (both languages)
        files.append(self._render_shared_template(
            "Makefile.j2", "Makefile", bootstrap_ctx,
        ))
        files.append(self._render_shared_template(
            "env_example.j2", ".env.example", bootstrap_ctx,
        ))
        files.append(self._render_shared_template(
            "README.md.j2", "README.md", bootstrap_ctx,
        ))
        files.append(self._render_shared_template(
            "gitignore.j2", ".gitignore", bootstrap_ctx,
        ))

        # Language-specific bootstrap script
        template = self._env.get_template("bootstrap.sh.j2")
        content = template.render(**bootstrap_ctx)
        files.append(GeneratedFile(path="bootstrap.sh", content=content))

        return files

    def _render_mock_server(self, ctx, bundle):
        """Render mock_server.py and mock_data.json from seed data.

        Uses SeedDataGenerator to produce realistic mock data, then embeds it
        in a standalone zero-dependency Python HTTP server script.
        """
        import json as _json
        from app.modules.solutions_product.services.seed_data_generator import (
            SeedDataGenerator,
        )

        files = []
        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        if not confirmed_fields:
            return files

        # Generate seed data
        gen = SeedDataGenerator(row_count=10)
        seed_entities = gen.generate(confirmed_fields, solution_name=bundle.solution_name)

        # Convert SQL-formatted seed rows to JSON-friendly dicts
        mock_data = {}
        entities = []
        for entity in seed_entities:
            table = entity["table_name"]
            rows = []
            for row_vals in entity["rows"]:
                record = {}
                for col_name, val in zip(entity["columns"], row_vals):
                    if val == "NULL":
                        record[col_name] = None
                    elif val in ("true", "false"):
                        record[col_name] = val == "true"
                    elif isinstance(val, str) and val.startswith("'") and val.endswith("'"):
                        record[col_name] = val[1:-1].replace("''", "'")
                    else:
                        try:
                            record[col_name] = int(val)
                        except (ValueError, TypeError):
                            try:
                                record[col_name] = float(val)
                            except (ValueError, TypeError):
                                record[col_name] = val
                rows.append(record)
            mock_data[table] = rows
            entities.append({
                "name": entity["name"],
                "name_snake": table,
            })

        # 1. Generate standalone mock_data.json
        mock_json_str = _json.dumps(mock_data, indent=2, default=str)
        files.append(GeneratedFile(
            path="mock_data.json",
            content=mock_json_str,
        ))

        # 2. Generate mock_server.py from template
        try:
            template = self._env.get_template("mock_server.py.j2")
            content = template.render(
                **ctx,
                mock_data_json=mock_json_str,
                entities=entities,
            )
            files.append(GeneratedFile(path="mock_server.py", content=content))
        except Exception as e:
            # Template may not exist for all languages — skip gracefully
            logger.debug("Mock server template not available for %s: %s", self.language, e)

        return files

    def _render_terraform(self, ctx, bundle):
        """Render Terraform configs and seed data into terraform/ directory."""
        files = []
        deployment = getattr(bundle, "deployment", None)
        db_config = {}
        if deployment:
            dep_vars = vars(deployment) if hasattr(deployment, "__dict__") else deployment
            db_config = dep_vars if isinstance(dep_vars, dict) else {}

        project_name = _kebab(bundle.solution_name)
        db_name = _snake(bundle.solution_name)

        tf_ctx = dict(
            ctx,
            database=db_config,
            project_name=project_name,
            db_name=db_name,
        )

        # Terraform config files
        for template_name, output_name in [
            ("terraform_main.tf.j2", "terraform/main.tf"),
            ("terraform_rds.tf.j2", "terraform/rds.tf"),
            ("terraform_variables.tf.j2", "terraform/variables.tf"),
            ("terraform_outputs.tf.j2", "terraform/outputs.tf"),
        ]:
            template = self._shared_env.get_template(template_name)
            content = template.render(**tf_ctx)
            files.append(GeneratedFile(path=output_name, content=content))

        # Seed data removed — the generated app has a full admin UI for data entry.
        # Users create their own domain-specific records through the forms.

        return files

    @staticmethod
    def _topo_sort_models(models: dict) -> dict:
        """Topologically sort models so FK dependencies are seeded first.

        Entities with no foreign keys come first. Entities referencing
        other entities come after their dependencies.
        """
        if not models:
            return models

        # Build dependency graph: model_name → set of referenced model names
        deps = {}
        name_lower_map = {k.lower(): k for k in models}  # for case-insensitive FK lookup
        for name, mdef in models.items():
            refs = set()
            for f in mdef.get("fields", []):
                fk = f.get("foreign_key") or ""
                if fk and "." in fk:
                    ref_entity = fk.split(".")[0]
                    # Map FK target to actual model name
                    ref_key = name_lower_map.get(ref_entity.lower())
                    if ref_key and ref_key != name:
                        refs.add(ref_key)
                elif f.get("name", "").endswith("_id") and f.get("name") != "id":
                    # Infer reference from field name: owner_id → Owner
                    stem = f["name"][:-3]  # remove _id
                    ref_key = name_lower_map.get(stem.lower()) or name_lower_map.get(stem.lower() + "s")
                    if ref_key and ref_key != name:
                        refs.add(ref_key)
            deps[name] = refs

        # Kahn's algorithm for topological sort
        in_degree = {n: 0 for n in models}
        for name, refs in deps.items():
            for ref in refs:
                if ref in in_degree:
                    in_degree[name] += 1

        queue = [n for n, d in in_degree.items() if d == 0]
        sorted_names = []
        while queue:
            node = queue.pop(0)
            sorted_names.append(node)
            for name, refs in deps.items():
                if node in refs:
                    in_degree[name] -= 1
                    if in_degree[name] == 0:
                        queue.append(name)

        # Add any remaining (cyclic deps) at the end
        for name in models:
            if name not in sorted_names:
                sorted_names.append(name)

        # Return ordered dict
        from collections import OrderedDict
        return OrderedDict((k, models[k]) for k in sorted_names if k in models)

    def _render_main(self, ctx, services):
        """Render main.py with router includes."""
        template = self._env.get_template("main.py.j2")
        svc_data = [{"name": s.name, "tag": _snake(s.tag)} for s in services]
        content = template.render(**ctx, services=svc_data)
        return GeneratedFile(path="app/main.py", content=content)

    def _derive_searchable_fields(self, used_schemas: set, confirmed_fields: dict) -> list:
        """Return field names suitable for SQL ILIKE search for the current service entity.

        Looks up the primary entity (from used_schemas) in confirmed_fields and returns
        all string/text fields that are not primary keys, foreign keys, or auto-managed
        (id, status, created_at, updated_at).  The resulting list drives the
        ``{% if searchable_fields %}`` block in route.py.j2.
        """
        # Expanded exclusion set: audit/system fields that exist in genome but NOT in ORM
        # (prevents AttributeError → 500 in the generated search query)
        _excluded = {
            "id", "status", "created_at", "updated_at",
            # audit log fields
            "action", "resource_type", "resource_id", "ip_address", "user_agent",
            # multi-tenancy / ownership
            "organization_id", "tenant_id", "owner_id", "created_by", "updated_by",
            "deleted_by", "reviewer_id",
            # soft-delete / lifecycle
            "deleted_at",
            # file/pipeline fields
            "file_path", "mime_type", "file_size_bytes", "job_status",
        }
        _searchable_types = {"string", "text", "varchar", "str"}

        # Try to match used_schema names against confirmed_fields keys (both snake_case)
        for schema_name in used_schemas:
            sk = _snake(schema_name)
            fields_dict = confirmed_fields.get(sk) or confirmed_fields.get(schema_name)
            if fields_dict is None:
                # Try prefix match (e.g., "work_order" → "work_orders")
                for cf_key in confirmed_fields:
                    if _snake(cf_key) == sk or sk.startswith(_snake(cf_key)):
                        fields_dict = confirmed_fields[cf_key]
                        break
            if fields_dict is None:
                continue
            # confirmed_fields values may be a dict {name: def} or a list of defs
            if isinstance(fields_dict, dict):
                field_list = [{"name": k, **v} for k, v in fields_dict.items()]
            else:
                field_list = fields_dict
            result = []
            for f in field_list:
                fname = f.get("name", "")
                ftype = f.get("type", "")
                if (
                    fname
                    and fname not in _excluded
                    and not f.get("primary_key")
                    and not f.get("foreign_key")
                    and not fname.endswith("_id")
                    and ftype.lower() in _searchable_types
                ):
                    result.append(fname)
            if result:
                return result
        return []

    def _render_pipeline_service(self, ctx, service, mod_def, bundle):
        """Render a pipeline service file for a behavioral (BFG) module.

        Uses pipeline_service.py.j2 instead of route.py.j2 to generate
        an orchestrator class with typed steps, integration stubs, and
        quality constraint assertions.
        """
        try:
            template = self._env.get_template("pipeline_service.py.j2")
        except Exception:
            logger.warning("pipeline_service.py.j2 template not found, falling back to standard route")
            return None

        pipeline = mod_def.get("pipeline", {})
        steps = pipeline.get("steps", [])
        if isinstance(steps, list):
            pipeline_steps = steps
        else:
            pipeline_steps = []

        # Collect integration names from steps
        integration_names = []
        for step in pipeline_steps:
            integ = step.get("integration")
            if integ and integ not in integration_names:
                integration_names.append(integ)

        sla = pipeline.get("sla", {})
        trigger = pipeline.get("trigger", "api_call")

        # Quality constraints from behavioral context
        getattr(bundle, "_genome_modules", {})
        quality_constraints = []
        # Try to get from the genome's _behavioral section
        genome_behavioral = ctx.get("genome_behavioral", {})
        if genome_behavioral:
            quality_constraints = genome_behavioral.get("quality_constraints", [])

        pipeline_ctx = dict(
            ctx,
            module_key=_pascal_to_snake(service.name),
            module=mod_def,
            pipeline_steps=pipeline_steps,
            pipeline_description=pipeline.get("description", mod_def.get("_rationale", "")),
            integration_names=integration_names,
            sla=sla,
            trigger=trigger,
            quality_constraints=quality_constraints,
        )

        content = template.render(**pipeline_ctx)
        route_name = _snake(service.name)
        return GeneratedFile(
            path=f"app/routers/{route_name}.py",
            content=content,
            archimate_sources=mod_def.get("archimate_element_ids", []),
        )

    def _render_pipeline_test(self, ctx, service, mod_def, bundle):
        """Render pipeline-specific tests using test_pipeline.py.j2."""
        try:
            template = self._env.get_template("tests/test_pipeline.py.j2")
        except Exception:
            logger.warning("tests/test_pipeline.py.j2 not found, skipping pipeline tests")
            return None

        pipeline = mod_def.get("pipeline", {})
        steps = pipeline.get("steps", []) if isinstance(pipeline.get("steps"), list) else []

        integration_names = []
        for step in steps:
            integ = step.get("integration")
            if integ and integ not in integration_names:
                integration_names.append(integ)

        sla = pipeline.get("sla", {})
        genome_behavioral = ctx.get("genome_behavioral", {})
        quality_constraints = genome_behavioral.get("quality_constraints", []) if genome_behavioral else []

        test_ctx = dict(
            ctx,
            module_key=_snake(service.name),
            module=mod_def,
            aggregate_root=mod_def.get("aggregate_root", _pascal(_snake(service.name))),
            pipeline_steps=steps,
            integration_names=integration_names,
            sla=sla,
            quality_constraints=quality_constraints,
        )

        content = template.render(**test_ctx)
        route_name = _snake(service.name)
        return GeneratedFile(
            path=f"tests/test_{route_name}_pipeline.py",
            content=content,
        )

    def _render_route(self, ctx, service, schemas, has_handlers=False, flow_steps=None,
                      confirmed_fields=None, state_machines=None, validation_rules=None):
        """Render a route file for a single service group."""
        template = self._env.get_template("route.py.j2")

        archimate_ids = []
        used_schemas = set()
        path_data = []
        for p in service.paths:
            if p.archimate_source_id:
                archimate_ids.append(p.archimate_source_id)
            if p.request_schema and p.request_schema != "dict" and not getattr(p, 'vendor', False):
                used_schemas.add(p.request_schema)
            if p.response_schema and p.response_schema != "dict" and not getattr(p, 'vendor', False):
                used_schemas.add(p.response_schema)
            path_data.append({
                "path": p.path,
                "method": p.method.lower(),
                "operation_id": p.operation_id,
                "summary": p.summary,
                "request_schema": p.request_schema if p.request_schema != "dict" else None,
                "response_schema": p.response_schema if p.response_schema != "dict" else None,
                "archimate_source_id": p.archimate_source_id,
                "vendor": getattr(p, 'vendor', False),
            })

        # Determine which entities in this route have validators or state machines
        _sm = state_machines or {}
        _vr = validation_rules or {}
        validator_entities = set(_vr.keys()) & used_schemas
        # Match state machine entities: state_machine_entities must use the same
        # names as used_schemas (PascalCase schema names) so that the template's
        # `{% if schema_name in state_machine_entities %}` check resolves correctly.
        # _sm_key_lookup maps schema_name -> _sm key so we can still call _sm[key].
        state_machine_entities = set()
        _sm_key_lookup: dict = {}
        for sm_key in _sm:
            if sm_key in used_schemas:
                state_machine_entities.add(sm_key)
                _sm_key_lookup[sm_key] = sm_key
            else:
                for s in used_schemas:
                    if s.startswith(sm_key):
                        state_machine_entities.add(s)   # store schema name, not sm_key
                        _sm_key_lookup[s] = sm_key
                        break
        # Build state machine class name map: schema_name -> actual generated class
        # e.g. "CoreBusinessApi" (schema) -> "CoreStateMachine" (from SM key "Core")
        state_machine_class_names = {
            schema: _sm_key_lookup.get(schema, schema) + "StateMachine"
            for schema in state_machine_entities
        }
        # Build state machine transition data for route template
        sm_transitions = {}
        for entity in state_machine_entities:
            _real_key = _sm_key_lookup.get(entity, entity)
            sm = _sm.get(_real_key) or _sm.get(entity) or {}
            raw_transitions = []
            if isinstance(sm, dict):
                raw_transitions = sm.get("transitions", [])
            elif hasattr(sm, "transitions"):
                # StateMachineDef dataclass — convert TransitionDef objects to dicts
                raw_transitions = [
                    {
                        "trigger": t.trigger,
                        "from_state": t.from_state,
                        "to_state": t.to_state,
                        "guard": vars(t.guard) if t.guard else None,
                        "actions": [vars(a) for a in (t.actions or [])],
                    }
                    for t in sm.transitions
                ]
            # Deduplicate by trigger name — the state machine class handles
            # multi-source transitions internally (e.g., cancel from draft OR assigned).
            # The route only needs one handler per trigger.
            seen_triggers = set()
            deduped = []
            for t in raw_transitions:
                trigger = t.get("trigger") or (t.trigger if hasattr(t, "trigger") else "")
                if trigger and trigger not in seen_triggers:
                    seen_triggers.add(trigger)
                    deduped.append(t)
            sm_transitions[entity] = deduped

        content = template.render(
            **ctx,
            archimate_ids=archimate_ids or ["N/A"],
            used_schemas=sorted(used_schemas),
            paths=path_data,
            flow_steps=flow_steps or [],
            has_validators=bool(validator_entities),
            has_state_machines=bool(state_machine_entities),
            validator_entities=validator_entities,
            state_machine_entities=state_machine_entities,
            state_machine_class_names=state_machine_class_names,
            state_machine_transitions=sm_transitions,
            # Pass the CRUD list path as base for SM transition URLs (e.g., /api/work_orders)
            _resource_base_path=service.paths[0].path if service.paths else f"/api/{_snake(service.tag)}s",
            # Derive searchable fields from confirmed field definitions for this service.
            # Include all string/text fields that are not PKs, FKs, or auto-managed.
            searchable_fields=self._derive_searchable_fields(used_schemas, confirmed_fields or {}),
        )

        # NOTE: _synthesize_route_implementations() was previously called here to
        # send each route to an LLM for stub-filling. This made the "deterministic"
        # generator secretly non-deterministic and added ~17s per route (10 routes =
        # 170s). LLM enrichment belongs in generate_stream's hybrid phase, not here.

        tag_snake = _snake(service.tag)
        return GeneratedFile(
            path=f"app/routers/{tag_snake}.py",
            content=content,
            archimate_sources=archimate_ids,
        )

    def _synthesize_route_implementations(self, rendered_content, service_name,
                                           flow_steps, confirmed_fields, paths):
        """Replace HTTPException(501) stubs with real implementations using LLM.

        Sends the rendered route file + schema context + business process steps
        to the LLM and asks it to fill in the endpoint bodies.
        """
        try:
            from app.modules.ai_chat.services.llm_service import LLMService
            provider, model = LLMService._get_configured_provider()
        except Exception:
            logger.debug("LLM not available for route synthesis — keeping stubs")
            return rendered_content

        # Build schema context from confirmed fields
        schema_context = ""
        fields = confirmed_fields.get(service_name, [])
        if fields:
            field_lines = []
            for f in fields:
                fname = f.get("name", f["name"]) if isinstance(f, dict) else f.name
                ftype = f.get("type", "str") if isinstance(f, dict) else getattr(f, "type", "str")
                field_lines.append(f"  - {fname}: {ftype}")
            schema_context = f"## {service_name} Fields\n" + "\n".join(field_lines)

        # Build flow steps context
        steps_context = ""
        if flow_steps:
            step_lines = []
            for s in flow_steps:
                step_lines.append(
                    f"- {s.get('action', 'step')}(): {s.get('description', '')} "
                    f"[hint: {s.get('method_body_hint', '')}]"
                )
            steps_context = "## Business Process Steps\n" + "\n".join(step_lines)

        prompt = f"""You are a senior Python/FastAPI developer. Below is a generated route file with stub implementations.
Replace EVERY `raise HTTPException(status_code=501, detail="Not yet implemented -- deterministic scaffold only")`
with a real, working implementation.

## Current Code
```python
{rendered_content}
```

{schema_context}

{steps_context}

## Rules for CRUD endpoints (list, get, create, update, delete)
1. Use SQLAlchemy async session patterns: `db.execute(select(Model).where(...))`, `db.add()`, `await db.commit()`.
2. Add `from sqlalchemy import select` and `from sqlalchemy.ext.asyncio import AsyncSession` to imports.
3. Add a `db: AsyncSession = Depends(get_db)` parameter to each endpoint.
4. List: query all, return list. Get: by ID, 404 if not found.
5. Create: validate body, create model instance from body fields, db.add(), commit, return 201.
6. Update: get by ID, update each field from body, commit, return. Delete: get by ID, delete, 204.

## CRITICAL — Rules for Business Process endpoints
7. If the code contains a "Business Process Implementation Guide" comment block, those steps are REAL BUSINESS LOGIC that MUST be implemented as actual code — NOT as generic CRUD.
8. Each numbered step in the guide (e.g. "1. validate_order() — Check if order_id exists and status is valid") MUST become a real function call or inline implementation in the corresponding endpoint.
9. The "hint" after each step describes the actual logic. Implement it. For example:
   - "aggregate data from multiple sources" → query multiple tables, combine results
   - "perform risk analysis using predefined models" → compute a score from field values
   - "Check if order_id exists and status is valid" → query by ID, check status field value
   - "send alert" → log the alert, return it in the response
10. Do NOT reduce business process endpoints to simple CRUD. They are workflow endpoints with multiple steps.

## General rules
11. Return the COMPLETE file — every line, not just the changed parts.
12. Keep ALL existing comments (archimate-source, Business Process Guide, etc.).
13. Do NOT add placeholder/fake data. Use real DB queries.
14. Return ONLY the Python code — no markdown fences, no explanation."""

        try:
            raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
        except Exception as e:
            logger.error("Route synthesis LLM call failed for %s: %s", service_name, e)
            return rendered_content

        if not raw_text:
            return rendered_content

        # Strip markdown fences if present
        text = raw_text.strip()
        text = re.sub(r'^```(?:python)?\s*', '', text)
        text = re.sub(r'\s*```\s*$', '', text)

        # Validate: LLM output must have at least as many @router endpoints as the template
        original_endpoint_count = rendered_content.count("@router.")
        synth_endpoint_count = text.count("@router.")

        if "router = APIRouter()" not in text:
            logger.warning("Route synthesis missing APIRouter for %s — keeping template", service_name)
            return rendered_content

        if synth_endpoint_count < original_endpoint_count:
            logger.warning(
                "Route synthesis for %s lost endpoints (%d -> %d) — keeping template",
                service_name, original_endpoint_count, synth_endpoint_count,
            )
            return rendered_content

        if synth_endpoint_count == 0:
            logger.warning("Route synthesis for %s has zero @router endpoints — keeping template", service_name)
            return rendered_content

        logger.info("Route synthesis succeeded for %s (%d -> %d chars, %d endpoints)",
                    service_name, len(rendered_content), len(text), synth_endpoint_count)
        return text

    def _generate_process_tests(self, ctx, bp_flows, confirmed_fields):
        """Generate pytest test cases from business process flow steps.

        Each flow step becomes a test: happy path + error path.
        Returns a GeneratedFile or None.
        """
        if not bp_flows:
            return None

        lines = [
            "# GENERATED BY Entelim — Process-driven tests from ArchiMate BusinessProcess elements",
            f"# solution_id: {ctx.get('solution_id', '?')}",
            f"# bundle_id: {ctx.get('bundle_id', '?')}",
            "# Each test maps to a BusinessProcess step with traceability.",
            "# DO NOT EDIT IN PLACE — REGENERATE FROM ARCHITECTURE",
            "",
            "import pytest",
            "from httpx import AsyncClient, ASGITransport",
            "from app.main import app",
            "",
            "",
            "@pytest.fixture",
            "async def client():",
            '    transport = ASGITransport(app=app)',
            '    async with AsyncClient(transport=transport, base_url="http://test") as c:',
            "        yield c",
            "",
        ]

        for service_name, steps in bp_flows.items():
            snake_svc = _snake(service_name)
            lines.append("")
            lines.append(f"# ═══ {service_name} process tests ═══")
            lines.append("")
            lines.append(f"class Test{service_name}Process:")
            lines.append(f'    """Tests derived from {service_name} BusinessProcess steps."""')
            lines.append("")

            # Get confirmed fields for this service to generate realistic test data
            fields = confirmed_fields.get(service_name, [])
            sample_fields = {}
            for f in fields[:5]:
                fname = f.get("name", f["name"]) if isinstance(f, dict) else f.name
                ftype = f.get("type", "str") if isinstance(f, dict) else getattr(f, "type", "str")
                if fname in ("id", "created_at", "updated_at"):
                    continue
                if ftype == "str":
                    sample_fields[fname] = f'"test_{fname}"'
                elif ftype == "int":
                    sample_fields[fname] = "1"
                elif ftype == "float" or ftype == "Decimal":
                    sample_fields[fname] = "1.0"
                elif ftype == "bool":
                    sample_fields[fname] = "True"
                else:
                    sample_fields[fname] = f'"test_{fname}"'

            api_path = f"/api/{snake_svc}s"

            # Create test: happy path
            lines.append("    @pytest.mark.asyncio")
            lines.append(f"    async def test_create_{snake_svc}_happy_path(self, client: AsyncClient):")
            lines.append(f'        """Happy path: create a {service_name} with valid data."""')
            if sample_fields:
                field_json = ", ".join(f'"{k}": {v}' for k, v in sample_fields.items())
                lines.append(f"        resp = await client.post('{api_path}', json={{{field_json}}})")
            else:
                lines.append(f"        resp = await client.post('{api_path}', json={{}})")
            lines.append("        assert resp.status_code in (200, 201)")
            lines.append("")

            # List test
            lines.append("    @pytest.mark.asyncio")
            lines.append(f"    async def test_list_{snake_svc}(self, client: AsyncClient):")
            lines.append(f'        """List all {service_name} records."""')
            lines.append(f"        resp = await client.get('{api_path}')")
            lines.append("        assert resp.status_code == 200")
            lines.append("")

            # Per-step tests
            for i, step in enumerate(steps):
                action = step.get("action", f"step_{i + 1}")
                desc = step.get("description", action)
                hint = step.get("method_body_hint", "")

                # Happy path for this step
                lines.append("    @pytest.mark.asyncio")
                lines.append(f"    async def test_{action}_happy_path(self, client: AsyncClient):")
                lines.append(f'        """Process step: {desc}"""')
                lines.append(f"        # Business logic: {hint}" if hint else f"        # Step: {desc}")
                lines.append("        # Scaffold: implement by creating prerequisite data, calling the endpoint, and asserting the outcome")
                lines.append("        response = await client.post(\"/api/placeholder\", json={{}})")
                lines.append("        assert response.status_code in (200, 201, 204)")
                lines.append("")

                # Error path for this step
                lines.append("    @pytest.mark.asyncio")
                lines.append(f"    async def test_{action}_invalid_input(self, client: AsyncClient):")
                lines.append(f'        """Error path: {action} with invalid/missing data."""')
                lines.append(f"        resp = await client.post('{api_path}', json={{}})")
                lines.append("        assert resp.status_code in (400, 422)")
                lines.append("")

        content = "\n".join(lines) + "\n"
        return GeneratedFile(path="tests/test_process_flows.py", content=content)

    def _parse_component_schemas(self, openapi, raw_schemas):
        """Parse OpenAPI components.schemas into template-friendly dicts."""
        component_schemas = openapi.get("components", {}).get("schemas", {})
        # Merge with raw_schemas from spec generator
        all_schemas = {**component_schemas, **raw_schemas}

        result = {}
        for schema_name, schema_def in all_schemas.items():
            if schema_name == "ErrorResponse":
                continue  # Skip standard error schema

            properties = schema_def.get("properties", {})
            required_fields = set(schema_def.get("required", []))

            fields = []
            for field_name, field_def in properties.items():
                json_type = field_def.get("type", "string")
                json_format = field_def.get("format")
                python_type = _map_json_type(json_type, json_format)

                fields.append({
                    "name": _snake(field_name),
                    "python_type": python_type,
                    "required": field_name in required_fields,
                    "readonly": field_name == "id"
                        or _snake(field_name) in ("created_at", "updated_at"),
                    "enum_values": field_def.get("enum"),
                    "max_length": field_def.get("maxLength"),
                    "default": field_def.get("default"),
                })

            if fields:
                result[_pascal(schema_name)] = fields

        return result

    def _render_schemas(self, ctx, schema_data, spec_metadata=None, field_validators=None):
        """Render Pydantic schemas — one file per entity plus a barrel export."""
        template = self._env.get_template("schema.py.j2")
        spec_metadata = spec_metadata or {}
        field_validators = field_validators or {}

        files = []
        schema_imports = []

        for schema_name, fields in schema_data.items():
            snake = _snake(schema_name)
            single = {schema_name: fields}
            single_fv = {schema_name: field_validators[schema_name]} if schema_name in field_validators else {}
            single_meta = {schema_name: spec_metadata[schema_name]} if schema_name in spec_metadata else {}
            content = template.render(**ctx, schemas=single, spec_metadata=single_meta, field_validators=single_fv)
            files.append(GeneratedFile(path=f"app/schemas/{snake}.py", content=content))
            schema_imports.append((snake, schema_name))

        # Barrel re-export
        barrel = [
            "# GENERATED BY Entelim — barrel export for all schemas",
            f"# solution_id: {ctx.get('solution_id', '')}",
            "",
        ]
        for snake, class_name in sorted(schema_imports):
            barrel.append(f"from app.schemas.{snake} import {class_name}, {class_name}Create, {class_name}Update  # noqa: F401")
        barrel.append("")
        files.append(GeneratedFile(path="app/schemas/models.py", content="\n".join(barrel)))

        return files

    def _render_tests(self, ctx, services):
        """Render contract test file."""
        template = self._env.get_template("test_contracts.py.j2")

        test_services = []
        for svc in services:
            test_paths = []
            for p in svc.paths:
                # p.path already contains the full URL (e.g. /api/users) — use directly
                test_url = _replace_path_params(p.path)

                # Build example body for POST/PUT
                example_body = None
                if p.request_schema and p.method.upper() in ("POST", "PUT", "PATCH"):
                    example_body = "{}"

                test_paths.append({
                    "method": p.method.lower(),
                    "operation_id": p.operation_id,
                    "summary": p.summary,
                    "full_url": test_url,
                    "example_body": example_body,
                })
            test_services.append({"name": svc.name, "paths": test_paths})

        content = template.render(**ctx, services=test_services)
        return GeneratedFile(path="tests/test_contracts.py", content=content)

    def _render_test_suite(self, ctx, bundle, is_go=False):
        """Render comprehensive test suite: behavioral, edge cases, security, k6.

        Uses TestGenerator to extract test data from the bundle, then renders
        language-specific templates.
        """
        from app.modules.solutions_product.services.test_generator import TestGenerator

        files = []
        gen = TestGenerator()
        test_data = gen.generate_all(bundle)

        first_svc = bundle.services[0] if bundle.services else None
        api_path = f"/{_kebab(first_svc.tag)}" if first_svc else "/api"

        valid_payloads = {}
        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        for ename, flds in confirmed_fields.items():
            payload = {}
            for f in flds:
                nm = f.get("name", "") if isinstance(f, dict) else getattr(f, "name", "")
                ft = f.get("type", "string") if isinstance(f, dict) else getattr(f, "type", "string")
                ev = f.get("enum") if isinstance(f, dict) else getattr(f, "enum", None)
                if ev:
                    payload[nm] = ev[0]
                elif ft == "string":
                    payload[nm] = f"test_{nm}"
                elif ft == "integer":
                    payload[nm] = 1
                elif ft == "number":
                    payload[nm] = 1.0
                elif ft == "boolean":
                    payload[nm] = True
                else:
                    payload[nm] = f"test_{nm}"
            valid_payloads[ename] = payload

        svc_list = [
            {
                "name": s.name,
                "tag": _snake(s.tag),
                "base_path": f"/api/v1/{_pluralize(_snake(s.name)).replace('_', '-')}",
                "paths": [
                    {"path": p.path, "method": p.method,
                     "operation_id": p.operation_id, "summary": p.summary or ""}
                    for p in (s.paths or [])
                ],
            }
            for s in bundle.services
        ]
        test_ctx = dict(
            ctx,
            api_path=api_path,
            valid_payloads=valid_payloads,
            protected_endpoint=api_path,
            services=svc_list,
            confirmed_fields=confirmed_fields,
        )

        if test_data["behavioral"]:
            tpl = "test_behavioral.go.j2" if is_go else "test_behavioral.py.j2"
            out = "behavioral_test.go" if is_go else "tests/test_behavioral.py"
            tmpl = self._env.get_template(tpl)
            files.append(GeneratedFile(path=out, content=tmpl.render(**test_ctx, behavioral_tests=test_data["behavioral"])))

        if test_data["edge_cases"]:
            tpl = "test_edge_cases.go.j2" if is_go else "test_edge_cases.py.j2"
            out = "edge_cases_test.go" if is_go else "tests/test_edge_cases.py"
            tmpl = self._env.get_template(tpl)
            files.append(GeneratedFile(path=out, content=tmpl.render(**test_ctx, edge_case_tests=test_data["edge_cases"])))

        if test_data["security"]:
            tpl = "test_security.go.j2" if is_go else "test_security.py.j2"
            out = "security_test.go" if is_go else "tests/test_security.py"
            tmpl = self._env.get_template(tpl)
            files.append(GeneratedFile(path=out, content=tmpl.render(**test_ctx, security_tests=test_data["security"])))

        if test_data["load_test"]:
            tmpl = self._env.get_template("k6_load_test.js.j2")
            files.append(GeneratedFile(path="tests/k6_load_test.js", content=tmpl.render(**test_ctx, load_config=test_data["load_test"])))

        # OWASP Top 10 comprehensive security tests (A01–A08)
        if not is_go and svc_list:
            try:
                sec_tmpl = self._env.get_template("tests/test_security.py.j2")
                sec_content = sec_tmpl.render(**test_ctx)
                if sec_content and sec_content.strip():
                    files.append(GeneratedFile(path="tests/test_security_owasp.py", content=sec_content))
            except Exception as _sec_e:
                logger.debug("OWASP security test template skipped: %s", _sec_e)

        # k6 smoke + sustained load tests
        if not is_go:
            try:
                k6_smoke_tmpl = self._env.get_template("tests/load/k6_smoke.js.j2")
                files.append(GeneratedFile(
                    path="tests/load/k6_smoke.js",
                    content=k6_smoke_tmpl.render(**test_ctx),
                ))
                k6_load_tmpl = self._env.get_template("tests/load/k6_load.js.j2")
                files.append(GeneratedFile(
                    path="tests/load/k6_load.js",
                    content=k6_load_tmpl.render(**test_ctx),
                ))
            except Exception as _k6_e:
                logger.debug("k6 load test templates skipped: %s", _k6_e)

        return files

    def _render_simple(self, template_name, output_path, ctx):
        """Render a simple template with just the base context."""
        template = self._env.get_template(template_name)
        content = template.render(**ctx)
        return GeneratedFile(path=output_path, content=content)

    def _render_template_file(self, template_name, output_path, ctx, **extra):
        """Render a template with base context plus extra kwargs."""
        template = self._env.get_template(template_name)
        content = template.render(**ctx, **extra)
        return GeneratedFile(path=output_path, content=content)

    def _render_go_structured_templates(self, ctx, bundle):
        """Render Go/Chi structured project templates (internal/ layout).

        Generates files in a proper Go project structure:
        internal/config/, internal/models/, internal/dto/, internal/repository/,
        internal/service/, internal/handlers/, internal/router/, internal/middleware/,
        plus migrations/, Makefile, Dockerfile.
        """
        files = []
        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        models = self._build_model_context(bundle) if confirmed_fields else {}
        state_machines = getattr(bundle, "state_machines", {}) or {}
        business_rules = getattr(bundle, "business_rules", {}) or {}

        # Attach state machine + business rules + endpoint_path to models for templates
        for name, model_def in models.items():
            sm = state_machines.get(name) or state_machines.get(_pascal_to_snake(name))
            if sm:
                sm_dict = sm if isinstance(sm, dict) else vars(sm) if hasattr(sm, "__dict__") else {}
                sm_transitions = []
                for t in sm_dict.get("transitions", []):
                    td = t if isinstance(t, dict) else vars(t) if hasattr(t, "__dict__") else {}
                    sm_transitions.append({
                        "trigger": td.get("trigger", ""),
                        "from_state": td.get("from_state", ""),
                        "to_state": td.get("to_state", ""),
                    })
                model_def["state_machine"] = {
                    "field_name": sm_dict.get("field_name", "status"),
                    "states": sm_dict.get("states", []),
                    "initial_state": sm_dict.get("initial_state", ""),
                    "transitions": sm_transitions,
                }
            if not model_def.get("state_machine"):
                model_def["state_machine"] = None
            model_def["business_rules"] = business_rules.get(name, []) or business_rules.get(
                _pascal_to_snake(name), [])
            model_def["endpoint_path"] = _pluralize(_pascal_to_snake(name)).replace("_", "-")

        svc_data = [{"name": s.name, "tag": _snake(s.tag)} for s in bundle.services]
        model_ctx = dict(ctx, models=models, services=svc_data)

        # Simple templates (no model context needed)
        _safe = self._render_go_safe
        files.extend(_safe("config/config.go.j2", "internal/config/config.go", ctx))
        files.extend(_safe("database/db.go.j2", "internal/database/db.go", ctx))
        files.extend(_safe("dto/pagination.go.j2", "internal/dto/pagination.go", ctx))
        files.extend(_safe("middleware/auth.go.j2", "internal/middleware/auth.go", ctx))
        files.extend(_safe("middleware/tenant.go.j2", "internal/middleware/tenant.go", ctx))
        files.extend(_safe("middleware/audit.go.j2", "internal/middleware/audit.go", ctx))
        files.extend(_safe("middleware/ratelimit.go.j2", "internal/middleware/ratelimit.go", ctx))
        files.extend(_safe("router/router.go.j2", "internal/router/router.go", model_ctx))
        files.extend(_safe("Makefile.j2", "Makefile", ctx))
        files.extend(_safe("Dockerfile.go.j2", "Dockerfile.multi", ctx))

        # Model-dependent templates
        if models:
            files.extend(_safe("models/model.go.j2", "internal/models/models.go", model_ctx))
            files.extend(_safe("dto/dto.go.j2", "internal/dto/dto.go", model_ctx))
            files.extend(_safe("repository/repository.go.j2", "internal/repository/repository.go", model_ctx))
            files.extend(_safe("service/service.go.j2", "internal/service/service.go", model_ctx))
            files.extend(_safe("handlers/handler.go.j2", "internal/handlers/handler.go", model_ctx))
            files.extend(_safe("migrations/001_initial.sql.j2", "migrations/001_initial.sql", model_ctx))
            files.extend(_safe("tests/handler_test.go.j2", "tests/handler_test.go", model_ctx))

        return files

    def _render_go_safe(self, template_name, output_path, ctx):
        """Render a Go subdirectory template, returning empty list on failure."""
        try:
            template = self._env.get_template(template_name)
            content = template.render(**ctx)
            return [GeneratedFile(path=output_path, content=content)]
        except Exception as e:
            logger.debug("Go structured template %s failed: %s", template_name, e)
            return []

    def _render_java_spring_templates(self, ctx, bundle, java_base, test_base):
        """Render Java/Spring Boot structured templates from java_spring/ directory.

        Generates the new modular template files into the standard Spring Boot
        project structure alongside the existing java_spring_boot/ templates.
        """
        files = []
        templates_root = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "templates",
        )
        spring_dir = os.path.join(templates_root, "java_spring")
        if not os.path.isdir(spring_dir):
            return files

        spring_env = Environment(
            loader=FileSystemLoader(spring_dir),
            keep_trailing_newline=True,
        )

        models = self._build_java_model_context(bundle)
        svc_data = [{"name": s.name, "tag": _snake(s.tag)} for s in bundle.services]
        model_ctx = dict(ctx, models=models, services=svc_data)

        def _safe(tpl, out, tpl_ctx):
            try:
                t = spring_env.get_template(tpl)
                return [GeneratedFile(path=out, content=t.render(**tpl_ctx))]
            except Exception as e:
                logger.debug("Java spring template %s failed: %s", tpl, e)
                return []

        # Config
        files.extend(_safe("config/JpaConfig.java.j2", f"{java_base}/config/JpaConfig.java", ctx))
        files.extend(_safe("config/OpenApiConfig.java.j2", f"{java_base}/config/OpenApiConfig.java", ctx))

        # Security
        files.extend(_safe("security/JwtFilter.java.j2", f"{java_base}/security/JwtFilter.java", ctx))
        files.extend(_safe("security/JwtService.java.j2", f"{java_base}/security/JwtService.java", ctx))

        # DTOs
        files.extend(_safe("dto/PaginatedResponse.java.j2", f"{java_base}/dto/PaginatedResponse.java", ctx))

        if models:
            # Per-entity model/dto/repository files using new templates
            files.extend(_safe("model/StatusEnum.java.j2", f"{java_base}/model/StatusEnums.java", model_ctx))
            files.extend(_safe("db/migration/V001__initial.sql.j2",
                               "src/main/resources/db/migration/V001__initial.sql", model_ctx))

            for model_name, model_def in models.items():
                single = {model_name: model_def}
                single_ctx = dict(ctx, models=single)
                ep = model_def.get("endpoint_path", _pluralize(_pascal_to_snake(model_name)).replace("_", "-"))
                sm_transitions = []
                sm = model_def.get("state_machine")
                if sm and sm.get("transitions"):
                    sm_transitions = sm["transitions"]
                (dict(ctx,
                    entity_name=model_name,
                    endpoint_path=ep,
                    archimate_ids=["N/A"],
                    state_machine_transitions=sm_transitions,
                    models=single,
                ))
                files.extend(_safe(
                    "dto/CreateRequest.java.j2",
                    f"{java_base}/dto/{model_name}CreateRequest.java",
                    single_ctx,
                ))
                files.extend(_safe(
                    "dto/UpdateRequest.java.j2",
                    f"{java_base}/dto/{model_name}UpdateRequest.java",
                    single_ctx,
                ))
                files.extend(_safe(
                    "dto/Response.java.j2",
                    f"{java_base}/dto/{model_name}Response.java",
                    single_ctx,
                ))

            # Controller tests
            files.extend(_safe(
                "test/ControllerTest.java.j2",
                f"{test_base}/ControllerTest.java",
                model_ctx,
            ))

        # Dockerfile
        files.extend(_safe("Dockerfile.java.j2", "Dockerfile.multi", ctx))

        return files

    @staticmethod
    def _classify_integrations(integrations):
        """Split integrations by communication_type into REST, gRPC, and async buckets.

        Defaults to sync_rest when communication_type is not set (backward compatible).

        Returns:
            (rest_dict, grpc_dict, async_dict)
        """
        rest = {}
        grpc_map = {}
        async_map = {}
        for name, contract in integrations.items():
            comm_type = None
            if isinstance(contract, dict):
                comm_type = contract.get("communication_type")
            else:
                comm_type = getattr(contract, "communication_type", None)

            if comm_type == "sync_grpc":
                grpc_map[name] = contract
            elif comm_type in ("async_event", "async_queue"):
                async_map[name] = contract
            else:
                # Default: sync_rest (backward compatible)
                rest[name] = contract
        return rest, grpc_map, async_map

    def _render_auth_config(self, ctx):
        """Render auth_config.py with identity provider context."""
        template = self._env.get_template("auth_config.py.j2")
        content = template.render(**ctx)
        return GeneratedFile(path="app/auth_config.py", content=content)

    def _render_middleware(self, ctx):
        """Render middleware.go with identity provider context."""
        template = self._env.get_template("middleware.go.j2")
        content = template.render(**ctx)
        return GeneratedFile(path="middleware.go", content=content)

    # --- Wave 2: Confirmed spec rendering methods ---

    @staticmethod
    def _pluralize(word):
        """English pluralization — delegates to module-level _pluralize."""
        return _pluralize(word)

    def _render_model(self, ctx, models):
        """Render SQLAlchemy models — one file per entity for navigability.

        Generates app/models/<snake_name>.py per entity plus an
        app/models/entities.py barrel that re-exports all models (so
        existing imports like `from app.models.entities import Foo` keep working).
        """
        template = self._env.get_template("model.py.j2")
        all_table_names = {name: self._pluralize(name.lower()) for name in models}

        files = []
        model_imports = []

        for model_name, model_def in models.items():
            snake = _snake(model_name)
            single = {model_name: model_def}
            table_names = {model_name: all_table_names[model_name]}
            content = template.render(**ctx, models=single, table_names=table_names)
            path = f"app/models/{snake}.py"
            archimate_ids = []
            for item in model_def.get("fields", []):
                if hasattr(item, "archimate_source_id") and item.archimate_source_id:
                    archimate_ids.append(item.archimate_source_id)
            files.append(GeneratedFile(path=path, content=content, archimate_sources=archimate_ids))
            model_imports.append((snake, model_name))

        # Barrel file re-exports all models for convenience
        barrel_lines = [
            "# GENERATED BY Entelim — barrel export for all entity models",
            f"# solution_id: {ctx.get('solution_id', '')}",
            "",
        ]
        for snake, class_name in sorted(model_imports):
            barrel_lines.append(f"from app.models.{snake} import {class_name}  # noqa: F401")
        barrel_lines.append("")
        files.append(GeneratedFile(
            path="app/models/entities.py",
            content="\n".join(barrel_lines),
        ))

        return files

    def _render_handler(self, ctx, models):
        """Render business logic handlers from confirmed rules.

        After rendering the deterministic stub template, attempts live LLM
        synthesis for each handler method.  If synthesis succeeds and passes
        validation, the stub body is replaced with the generated body.  If
        synthesis fails or is unavailable, the stub body is kept with a
        # TODO comment explaining the failure reason.
        """
        template = self._env.get_template("handler.py.j2")
        content = template.render(**ctx, models=models)

        # Attempt live synthesis only when the bundle carries business rules
        # context so we have something meaningful to pass to the LLM.
        if models:
            content = self._synthesize_handler_bodies(content, models, ctx)

        return GeneratedFile(path="app/handlers/business_logic.py", content=content)

    # ── Live handler body synthesis ─────────────────────────────────────

    # Matches the def line + body (up to the next def/class at same/lower indent
    # or end of string).  Group 1 = indentation before def, Group 2 = everything
    # from def ... to (but not including) the next def/class at the same indent.
    _STUB_PATTERN = re.compile(
        r"^(?P<indent>[ \t]*)def (?P<name>\w+)\(self, entity(?:[^)]*)\):.*?"
        r"(?:\n(?:[ \t]*\"\"\".*?\"\"\"[ \t]*\n))?"  # optional docstring (non-greedy, single-line fence only)
        r"(?P<body>(?:(?![ \t]*(?:def |class )).*\n)*)",
        re.MULTILINE,
    )

    def _synthesize_handler_bodies(self, rendered_content, models, ctx):
        """Post-process rendered handler source: replace stub bodies with LLM bodies.

        For each handler method that contains only a stub body (pass / raise
        NotImplementedError), call BusinessLogicSynthesizer to generate a real
        body.  On success, replace; on failure, annotate with # TODO comment.

        Args:
            rendered_content: The fully rendered handler.py string.
            models: The handler context dict (model_name -> model_def with business_rules).
            ctx: Template context dict (provides solution_id etc. for logging).

        Returns:
            Updated content string.
        """
        try:
            from app.modules.solutions_product.services.business_logic_synthesizer import (
                BusinessLogicSynthesizer,
            )
        except ImportError:
            logger.debug("BusinessLogicSynthesizer not available — keeping stubs")
            return rendered_content

        synthesizer = BusinessLogicSynthesizer()

        # Build a lookup: method_name → (business_rules, fields, integrations)
        # from the model context so we can pass the right context to each call.
        method_context = {}
        for model_name, model_def in models.items():
            rules = model_def.get("business_rules", [])
            fields = model_def.get("fields", [])
            integrations = model_def.get("integrations", {}) or {}
            quality_attrs = model_def.get("quality_attributes", [])
            for rule in rules:
                trigger = rule.get("trigger", "handle") if isinstance(rule, dict) else getattr(rule, "trigger", "handle")
                method_key = f"{trigger}_{model_name.lower()}"
                method_context[method_key] = {
                    "element_name": model_name,
                    "element_type": model_def.get("element_type", "component"),
                    "business_rules": [rule],
                    "fields": fields,
                    "integrations": integrations,
                    "quality_attrs": quality_attrs,
                }

        lines = rendered_content.splitlines(keepends=True)
        output = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.lstrip()
            # Detect a def line inside a Handler class
            if stripped.startswith("def ") and "self, entity" in line:
                indent = line[: len(line) - len(line.lstrip())]
                # Collect this def line + docstring + body
                def_line = line
                body_lines = []
                i += 1
                # Consume optional docstring
                if i < len(lines) and '"""' in lines[i]:
                    body_lines.append(lines[i])
                    if lines[i].count('"""') >= 2 and lines[i].strip() != '"""':
                        # single-line docstring
                        pass
                    else:
                        i += 1
                        while i < len(lines) and '"""' not in lines[i]:
                            body_lines.append(lines[i])
                            i += 1
                        if i < len(lines):
                            body_lines.append(lines[i])  # closing """
                    i += 1
                # Consume body until next def/class at same or lower indent or EOF
                while i < len(lines):
                    next_line = lines[i]
                    next_stripped = next_line.lstrip()
                    next_indent = next_line[: len(next_line) - len(next_stripped)]
                    if next_stripped and len(next_indent) <= len(indent) and (
                        next_stripped.startswith("def ")
                        or next_stripped.startswith("class ")
                    ):
                        break
                    body_lines.append(next_line)
                    i += 1

                # Check if body is a stub (only pass / raise NotImplementedError)
                "".join(body_lines)
                non_empty = [
                    bl.strip() for bl in body_lines
                    if bl.strip() and not bl.strip().startswith("#") and '"""' not in bl
                ]
                is_stub = all(
                    item in ("pass", "raise NotImplementedError", "return")
                    or item.startswith("raise NotImplementedError")
                    for item in non_empty
                ) if non_empty else True

                if is_stub:
                    # Extract method name from def line
                    m = re.search(r"def (\w+)\(", def_line)
                    method_name = m.group(1) if m else ""
                    ctx_data = method_context.get(method_name)
                    synthesized_body = None
                    failure_reason = "no business rule context for method"

                    if ctx_data:
                        try:
                            result = synthesizer.synthesize_handler_body(
                                element_name=ctx_data["element_name"],
                                element_type=ctx_data["element_type"],
                                business_rules=ctx_data["business_rules"],
                                fields=ctx_data["fields"],
                                integration_contracts=ctx_data["integrations"],
                                quality_attributes=ctx_data["quality_attrs"],
                            )
                            if result and result.get("synthesized"):
                                synthesized_body = result["handler_code"]
                            elif result:
                                failure_reason = result.get("reasoning", "validation failed")
                            else:
                                failure_reason = "LLM unavailable or returned None"
                        except Exception as exc:
                            logger.debug(
                                "Live synthesis failed for %s.%s: %s",
                                ctx_data["element_name"], method_name, exc,
                            )
                            failure_reason = str(exc)

                    output.append(def_line)
                    output.extend(body_lines)  # keep docstring lines

                    if synthesized_body:
                        # Replace stub lines with generated body (strip docstring lines already added)
                        # Remove the non-docstring stub body we already appended and replace
                        # Actually we need to emit the def + docstring, then the new body.
                        # Easier: build the replacement and substitute into output list.
                        # Pop back the body lines we just appended (before docstring):
                        # Simpler: emit everything, then patch.
                        # Instead: rebuild cleanly.
                        # Back out: remove the body_lines we just added
                        del output[-len(body_lines):]
                        # Re-add docstring lines only
                        ([
                            bl for bl in body_lines if '"""' in bl or (
                                body_lines and '"""' in body_lines[0] and
                                body_lines.index(bl) <= next(
                                    (j for j, x in enumerate(body_lines) if j > 0 and '"""' in x),
                                    len(body_lines),
                                )
                            )
                        ])
                        # Simpler docstring detection: take lines up through closing """
                        doc_end = 0
                        if body_lines and '"""' in body_lines[0]:
                            doc_end = 1
                            if body_lines[0].count('"""') < 2 or body_lines[0].strip() == '"""':
                                # multi-line: find closing
                                for j in range(1, len(body_lines)):
                                    doc_end = j + 1
                                    if '"""' in body_lines[j]:
                                        break
                        for dl in body_lines[:doc_end]:
                            output.append(dl)
                        # Emit synthesized body with correct indentation
                        body_indent = indent + "    "
                        for bline in synthesized_body.splitlines():
                            output.append(body_indent + bline.lstrip() + "\n" if bline.strip() else "\n")
                    else:
                        # Keep existing stub but annotate with TODO
                        # Remove body lines we appended, re-add with TODO prepended
                        del output[-len(body_lines):]
                        todo_line = f"{indent}    # NOTE: LLM generation failed validation ({failure_reason}) — body kept as-is\n"
                        output.append(todo_line)
                        output.extend(body_lines)
                else:
                    # Non-stub body: keep as-is (already synthesized or non-trivial)
                    output.append(def_line)
                    output.extend(body_lines)
            else:
                output.append(line)
                i += 1

        return "".join(output)

    def _render_client(self, ctx, integrations):
        """Render integration clients from confirmed contracts."""
        template = self._env.get_template("client.py.j2")
        content = template.render(**ctx, integrations=integrations)
        return GeneratedFile(path="app/clients/integrations.py", content=content)

    def _render_state_machines(self, ctx, state_machines):
        """Render state machine classes from confirmed state machine definitions."""
        template = self._env.get_template("state_machine.py.j2")
        content = template.render(**ctx, state_machines=state_machines)
        return GeneratedFile(path="app/state_machines/machines.py", content=content)

    def _render_validators(self, ctx, validation_rules):
        """Render domain validators from validation-type business rules."""
        template = self._env.get_template("validators.py.j2")
        content = template.render(**ctx, validation_rules=validation_rules)
        return GeneratedFile(path="app/validators/domain.py", content=content)

    @staticmethod
    def _extract_validation_rules(business_rules):
        """Extract validation and authorization rules from business rules dict.

        Returns a dict of entity_name -> [rule_dicts] containing only rules
        that should compile into validators (type: validation, authorization, computed_field).
        """
        result = {}
        for entity_name, rules in (business_rules or {}).items():
            if not isinstance(rules, list):
                continue
            entity_validators = []
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                rule_type = rule.get("type", "")
                if rule_type in ("validation", "authorization", "computed_field"):
                    entity_validators.append(rule)
            if entity_validators:
                result[_pascal(entity_name)] = entity_validators
        return result

    @staticmethod
    def _extract_field_validators(business_rules):
        """Extract per-field Pydantic validators from validation rules.

        Returns dict of model_name -> [{field_name, operator, value, rule_id, error_message}]
        for use in schema.py.j2's @field_validator decorators.
        """
        result = {}
        for entity_name, rules in (business_rules or {}).items():
            if not isinstance(rules, list):
                continue
            model_name = _pascal(entity_name)
            validators = []
            for rule in rules:
                if not isinstance(rule, dict) or rule.get("type") != "validation":
                    continue
                rule_id = rule.get("id", "rule")
                error_msg = rule.get("error_message", "")
                for pc in (rule.get("preconditions") or []):
                    if not isinstance(pc, dict):
                        continue
                    field_name = pc.get("field_name") or pc.get("field", "")
                    # Strip entity prefix (e.g. "order.total" -> "total")
                    if "." in field_name:
                        field_name = field_name.split(".")[-1]
                    if not field_name:
                        continue
                    op = pc.get("operator", "")
                    # Only include operators that translate to Pydantic validators
                    if op in ("matches", "in", "gt", ">", "gte", ">=", "lt", "<", "between"):
                        validators.append({
                            "field_name": field_name,
                            "operator": op,
                            "value": pc.get("value"),
                            "rule_id": rule_id,
                            "error_message": error_msg or f"{field_name} validation failed ({rule_id})",
                            "description": rule.get("description", ""),
                        })
            if validators:
                result[model_name] = validators
        return result

    def _render_network_policies(self, ctx, nfr_specs, app_name):
        """Render K8s NetworkPolicy manifests from NFR specs with infrastructure enforcement."""
        infra_nfrs = [
            n for n in nfr_specs
            if isinstance(n, dict)
            and n.get("enforcement") == "infrastructure"
            and n.get("category") in ("network_security", "access_control")
        ]
        if not infra_nfrs:
            return None

        policies = []
        for nfr in infra_nfrs:
            target = nfr.get("target", {}) if isinstance(nfr.get("target"), dict) else {}
            constraints = nfr.get("constraints", {}) if isinstance(nfr.get("constraints"), dict) else {}
            merged = {**target, **constraints}
            policies.append({
                "name": nfr.get("name", "default-policy"),
                "id": nfr.get("id", ""),
                "pod_selector": merged.get("pod_selector", app_name),
                "ingress_rules": merged.get("ingress_rules", []),
                "egress_rules": merged.get("egress_rules", []),
            })

        template = self._env.get_template("k8s_network_policy.yaml.j2")
        content = template.render(**ctx, app_name=app_name, nfr_policies=policies)
        return GeneratedFile(path="k8s/network-policies.yaml", content=content)

    def _render_orchestration(self, ctx, uml_diagrams):
        """Render service orchestration classes from UML sequence diagrams."""
        if not uml_diagrams:
            return None

        orchestrators = []
        for diag in uml_diagrams:
            steps_data = []
            unique_services = set()
            for i, step in enumerate(diag.steps if hasattr(diag, "steps") else (diag.get("steps") or [])):
                target = step.target_lifeline if hasattr(step, "target_lifeline") else step.get("target_lifeline", "")
                message = step.message if hasattr(step, "message") else step.get("message", "")
                is_async = step.is_async if hasattr(step, "is_async") else step.get("is_async", False)
                hint = step.method_body_hint if hasattr(step, "method_body_hint") else step.get("method_body_hint")
                return_val = step.return_value if hasattr(step, "return_value") else step.get("return_value")

                svc_name = re.sub(r"[^a-zA-Z0-9]", "_", target.strip()).strip("_").lower()
                method_name = re.sub(r"[^a-zA-Z0-9]", "_", message.strip()).strip("_").lower()
                unique_services.add(svc_name)
                steps_data.append({
                    "target_service": svc_name,
                    "method_name": method_name or f"step_{i + 1}",
                    "message": message,
                    "is_async": is_async,
                    "method_body_hint": hint or "",
                    "return_var": return_val or f"result_{i + 1}",
                })

            diag_name = diag.name if hasattr(diag, "name") else diag.get("name", f"Sequence{len(orchestrators)}")
            name = re.sub(r"[^a-zA-Z0-9]", "", diag_name.replace(" ", "_").title().replace("_", ""))
            orchestrators.append({
                "name": name,
                "steps": steps_data,
                "unique_services": sorted(unique_services),
            })

        template = self._env.get_template("orchestrator.py.j2")
        content = template.render(**ctx, orchestrators=orchestrators)
        return GeneratedFile(path="app/orchestrators/orchestrators.py", content=content)

    def _render_k8s(self, ctx, deployments):
        """Render Kubernetes deployment manifests."""
        template = self._env.get_template("k8s_deployment.yaml.j2")
        content = template.render(**ctx, deployments=deployments)
        return GeneratedFile(path="k8s/deployment.yaml", content=content)

    def _render_helm(self, ctx, deployment, solution_name):
        """Render Helm values from deployment spec."""
        template = self._env.get_template("helm_values.yaml.j2")
        # ctx already contains solution_name, override with explicit value
        helm_ctx = dict(ctx, solution_name=solution_name)
        content = template.render(**helm_ctx, deployment=deployment)
        return GeneratedFile(path="helm/values.yaml", content=content)

    def _render_openapi_yaml(self, bundle) -> GeneratedFile:
        """Serialize bundle.openapi to openapi.yaml in the generated bundle."""
        spec = bundle.openapi or {}
        # Ensure mandatory OpenAPI 3.x top-level fields are present
        if "openapi" not in spec:
            spec = dict(spec, openapi="3.1.0")
        if "info" not in spec:
            spec = dict(spec, info={"title": bundle.solution_name, "version": "0.1.0"})
        content = yaml.dump(spec, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return GeneratedFile(path="openapi.yaml", content=content)

    def _render_conftest(self, ctx) -> GeneratedFile:
        """Generate tests/conftest.py with the correct async SQLAlchemy engine pattern.

        Uses 'async with engine.begin()' — NOT the broken 'create_all(bind=engine)' pattern
        that raises sqlalchemy.exc.InvalidRequestError with async engines.
        """
        solution_name = ctx.get("solution_name", "app")
        content = f'''"""tests/conftest.py — deterministic async test fixtures.

Generated by Entelim deterministic code generator for {solution_name}.
DO NOT EDIT — regenerate via the codegen workbench.
"""
import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import Base, get_db

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def async_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    async_session_factory = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
'''
        return GeneratedFile(path="tests/conftest.py", content=content)

    def _build_services_map(self, bundle):
        """Build {ModelName: service_obj_with_base_path} for test templates."""
        result = {}
        for svc in (bundle.services or []):
            model_name = _pascal(svc.name)
            base_path = svc.paths[0].path.split("{")[0].rstrip("/") if svc.paths else f"/api/{_snake(svc.name)}s"
            # Attach base_path as attribute-style access in templates
            svc_proxy = type("SvcProxy", (), {
                "name": svc.name,
                "tag": svc.tag,
                "base_path": base_path,
                "paths": svc.paths,
            })()
            result[model_name] = svc_proxy
        return result

    def _render_template_tests(self, ctx, bundle) -> list:
        """Render the Jinja2-based test suite: conftest, CRUD, tenant isolation,
        auth, state machine, business rules, and factories.

        Falls back gracefully if templates are missing (older installations).
        """
        services_map = self._build_services_map(bundle)
        state_machines = getattr(bundle, "state_machines", {}) or {}
        business_rules = getattr(bundle, "business_rules", {}) or {}
        test_ctx = dict(ctx, services_map=services_map, state_machines=state_machines, business_rules=business_rules)
        files = []
        template_files = [
            ("tests/conftest.py.j2", "tests/conftest.py"),
            ("tests/factories.py.j2", "tests/factories.py"),
            ("tests/test_crud.py.j2", "tests/test_crud.py"),
            ("tests/test_tenant_isolation.py.j2", "tests/test_tenant_isolation.py"),
            ("tests/test_auth.py.j2", "tests/test_auth.py"),
        ]
        if state_machines:
            template_files.append(("tests/test_state_machine.py.j2", "tests/test_state_machine.py"))
        if business_rules:
            template_files.append(("tests/test_business_rules.py.j2", "tests/test_business_rules.py"))
        for tpl_name, out_path in template_files:
            try:
                tpl = self._env.get_template(tpl_name)
                files.append(GeneratedFile(path=out_path, content=tpl.render(**test_ctx)))
            except Exception as _e:
                logger.debug("Template %s not available: %s", tpl_name, _e)
        return files

    def _render_python_monitoring(self, ctx, bundle) -> list:
        """Render Prometheus metrics module, scrape config, and Grafana dashboard
        from python_fastapi/monitoring/ templates.
        """
        services_map = self._build_services_map(bundle)
        mon_ctx = dict(ctx, services_map=services_map)
        files = []
        template_files = [
            ("monitoring/metrics.py.j2", "app/monitoring/metrics.py"),
            ("monitoring/prometheus.yml.j2", "monitoring/prometheus.yml"),
            ("monitoring/grafana_dashboard.json.j2", "monitoring/grafana_dashboard.json"),
        ]
        for tpl_name, out_path in template_files:
            try:
                tpl = self._env.get_template(tpl_name)
                files.append(GeneratedFile(path=out_path, content=tpl.render(**mon_ctx)))
            except Exception as _e:
                logger.debug("Monitoring template %s not available: %s", tpl_name, _e)
        return files

    def _render_api_versioning(self, ctx) -> list:
        """Render api_versioning.py from python_fastapi/api_versioning.py.j2."""
        files = []
        try:
            tpl = self._env.get_template("api_versioning.py.j2")
            files.append(GeneratedFile(path="app/api_versioning.py", content=tpl.render(**ctx)))
        except Exception as _e:
            logger.debug("api_versioning template not available: %s", _e)
        return files

    def _render_migration(self, ctx, models):
        """Render Alembic migration from confirmed fields."""
        rev = hashlib.md5(ctx.get("bundle_id", "").encode(), usedforsecurity=False).hexdigest()[:12]
        template = self._env.get_template("alembic_migration.py.j2")
        content = template.render(**ctx, models=models, revision_id=rev)
        return GeneratedFile(path="alembic/versions/001_initial.py", content=content)

    def _render_alembic_infra(self, ctx) -> list:
        """Generate alembic.ini and alembic/env.py so `alembic upgrade head` works out of the box.

        alembic.ini points script_location at the alembic/ directory.
        alembic/env.py reads DATABASE_URL from the environment (matching database.py),
        normalises postgres:// → postgresql+asyncpg://, and runs migrations via an
        async engine so it stays consistent with the app's async SQLAlchemy setup.
        """
        sol_id = ctx.get("solution_id", "unknown")
        bundle_id = ctx.get("bundle_id", "unknown")
        header = f"# GENERATED BY Entelim — solution_id: {sol_id}, bundle_id: {bundle_id}\n"

        alembic_ini = (
            f"{header}"
            "# DO NOT EDIT IN PLACE — regenerate from Entelim architecture workbench\n\n"
            "[alembic]\n"
            "script_location = alembic\n"
            "prepend_sys_path = .\n"
            "version_path_separator = os\n"
            "# DATABASE_URL is read from the environment in alembic/env.py;\n"
            "# this value is a placeholder and is overridden at runtime.\n"
            "sqlalchemy.url = sqlite+aiosqlite:///./app.db\n\n"
            "[loggers]\n"
            "keys = root,sqlalchemy,alembic\n\n"
            "[handlers]\n"
            "keys = console\n\n"
            "[formatters]\n"
            "keys = generic\n\n"
            "[logger_root]\n"
            "level = WARN\n"
            "handlers = console\n"
            "qualname =\n\n"
            "[logger_sqlalchemy]\n"
            "level = WARN\n"
            "handlers =\n"
            "qualname = sqlalchemy.engine\n\n"
            "[logger_alembic]\n"
            "level = INFO\n"
            "handlers =\n"
            "qualname = alembic\n\n"
            "[handler_console]\n"
            "class = StreamHandler\n"
            "args = (sys.stderr,)\n"
            "level = NOTSET\n"
            "formatter = generic\n\n"
            "[formatter_generic]\n"
            "format = %(levelname)-5.5s [%(name)s] %(message)s\n"
            "datefmt = %H:%M:%S\n"
        )

        alembic_env_py = (
            f"{header}"
            "# DO NOT EDIT IN PLACE — regenerate from Entelim architecture workbench\n\n"
            "import asyncio\n"
            "import os\n"
            "from logging.config import fileConfig\n\n"
            "from sqlalchemy.ext.asyncio import create_async_engine\n"
            "from alembic import context\n\n"
            "config = context.config\n"
            "if config.config_file_name is not None:\n"
            "    fileConfig(config.config_file_name)\n\n"
            "# Read DATABASE_URL from environment — mirrors database.py normalisation logic.\n"
            "_raw_url = os.environ.get('DATABASE_URL', 'sqlite+aiosqlite:///./app.db')\n"
            "if _raw_url.startswith('postgres://'):\n"
            "    _database_url = _raw_url.replace('postgres://', 'postgresql+asyncpg://', 1)\n"
            "elif _raw_url.startswith('postgresql://') and '+asyncpg' not in _raw_url:\n"
            "    _database_url = _raw_url.replace('postgresql://', 'postgresql+asyncpg://', 1)\n"
            "else:\n"
            "    _database_url = _raw_url\n\n"
            "# Pull in app metadata for autogenerate support (graceful fallback if not available).\n"
            "try:\n"
            "    from app.models import Base\n"
            "    target_metadata = Base.metadata\n"
            "except ImportError:\n"
            "    target_metadata = None\n\n\n"
            "def run_migrations_offline() -> None:\n"
            "    context.configure(\n"
            "        url=_database_url,\n"
            "        target_metadata=target_metadata,\n"
            "        literal_binds=True,\n"
            "        dialect_opts={'paramstyle': 'named'},\n"
            "    )\n"
            "    with context.begin_transaction():\n"
            "        context.run_migrations()\n\n\n"
            "def _do_run_migrations(connection) -> None:\n"
            "    context.configure(connection=connection, target_metadata=target_metadata)\n"
            "    with context.begin_transaction():\n"
            "        context.run_migrations()\n\n\n"
            "async def _run_async_migrations() -> None:\n"
            "    engine = create_async_engine(_database_url)\n"
            "    async with engine.connect() as conn:\n"
            "        await conn.run_sync(_do_run_migrations)\n"
            "    await engine.dispose()\n\n\n"
            "def run_migrations_online() -> None:\n"
            "    asyncio.run(_run_async_migrations())\n\n\n"
            "if context.is_offline_mode():\n"
            "    run_migrations_offline()\n"
            "else:\n"
            "    run_migrations_online()\n"
        )

        return [
            GeneratedFile(path="alembic.ini", content=alembic_ini),
            GeneratedFile(path="alembic/env.py", content=alembic_env_py),
        ]

    # ── Admin UI generation ──

    # Auto-generated fields that should be excluded from create/edit forms
    _AUTO_FIELDS = {"id", "created_at", "updated_at", "deleted_at"}

    # JSON Schema type → admin form input type mapping
    _ADMIN_TYPE_MAP = {
        "string": "string",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "timestamp": "date-time",
        "array": "string",
        "object": "string",
    }

    def _build_admin_entities(self, bundle):
        """Build entity definitions for the admin UI template.

        Derives entities from two sources (in priority order):
        1. confirmed_fields (spec_data from SolutionArchiMateElement)
        2. OpenAPI component schemas (fallback)

        Returns:
            List of entity dicts with name_snake, name_display, api_path, and fields.
        """
        entities = []

        # Build a map of service tags → actual API paths from the bundle OpenAPI paths.
        # Paths are full (e.g. /api/users) — use the list path (no path params) for admin UI.
        tag_prefix_map = {}
        for svc in (bundle.services or []):
            tag_snake = _snake(svc.tag)
            # Find the list path (no {param}) from service paths; fall back to /api/{tag}s
            list_path = next(
                (p.path for p in svc.paths if "{" not in p.path),
                f"/api/{self._pluralize(tag_snake)}",
            )
            tag_prefix_map[tag_snake] = list_path

        # Source 1: confirmed fields (higher fidelity — architect-verified)
        confirmed = getattr(bundle, "confirmed_fields", {}) or {}
        seen_entities = set()
        for name, field_defs in confirmed.items():
            snake_name = _snake(name)
            pascal_name = _pascal(name)
            seen_entities.add(pascal_name)

            # Find API path: match to service tag, or default to /api/<plural>
            api_path = tag_prefix_map.get(snake_name, f"/api/{self._pluralize(snake_name)}")

            fields = []
            for f in field_defs:
                field_name = f.get("name", "")
                field_type = f.get("type", "string")
                field_format = f.get("format")

                # Map to admin display type
                enum_vals = f.get("enum_values") or f.get("enum") or []
                if field_format == "date-time" or field_type == "timestamp":
                    admin_type = "date-time"
                elif field_type == "enum" and enum_vals:
                    admin_type = "enum"
                elif field_type in ("text", "description"):
                    admin_type = "text"
                elif f.get("foreign_key"):
                    admin_type = "string"  # FK IDs are UUIDs (strings), not integers
                else:
                    admin_type = self._ADMIN_TYPE_MAP.get(field_type, "string")

                fields.append({
                    "name": _snake(field_name),
                    "display_name": field_name.replace("_", " ").title(),
                    "type": admin_type,
                    "required": f.get("required", False),
                    "is_auto": _snake(field_name) in self._AUTO_FIELDS,
                    "is_fk": bool(f.get("foreign_key")),
                    "enum_values": enum_vals if admin_type == "enum" else [],
                })

            entities.append({
                "name_snake": snake_name,
                "name_display": pascal_name,
                "api_path": api_path,
                "fields": fields,
            })

        # Source 2: OpenAPI schemas (fallback for entities without confirmed fields)
        # When confirmed_fields are present the architect has explicitly defined the domain
        # model — skip OpenAPI-derived schemas to avoid polluting entity names with
        # infrastructure/ArchiMate element names (e.g. "Workorderdatabase" instead of "WorkOrder").
        if confirmed:
            return entities

        component_schemas = bundle.openapi.get("components", {}).get("schemas", {})
        all_schemas = {**component_schemas, **(bundle.schemas or {})}
        for schema_name, schema_def in all_schemas.items():
            pascal_name = _pascal(schema_name)
            if pascal_name in seen_entities or pascal_name == "ErrorResponse":
                continue

            snake_name = _snake(schema_name)
            api_path = tag_prefix_map.get(snake_name, f"/api/{self._pluralize(snake_name)}")

            properties = schema_def.get("properties", {})
            required_fields = set(schema_def.get("required", []))
            if not properties:
                continue

            fields = []
            for field_name, field_def in properties.items():
                json_type = field_def.get("type", "string")
                json_format = field_def.get("format")

                if json_format == "date-time":
                    admin_type = "date-time"
                elif field_def.get("enum"):
                    admin_type = "enum"
                else:
                    admin_type = self._ADMIN_TYPE_MAP.get(json_type, "string")

                fields.append({
                    "name": _snake(field_name),
                    "display_name": field_name.replace("_", " ").title(),
                    "type": admin_type,
                    "required": field_name in required_fields,
                    "is_auto": _snake(field_name) in self._AUTO_FIELDS,
                    "enum_values": field_def.get("enum", []) if admin_type == "enum" else [],
                })

            entities.append({
                "name_snake": snake_name,
                "name_display": pascal_name,
                "api_path": api_path,
                "fields": fields,
            })

        return entities

    def _render_admin_ui(self, ctx, entities, is_go=False):
        """Render the admin UI HTML from entity definitions.

        Args:
            ctx: Base template context.
            entities: List of entity dicts from _build_admin_entities.
            is_go: If True, output to static/admin.html (Go). Otherwise app/static/admin.html (Python).

        Returns:
            GeneratedFile with the rendered admin HTML.
        """
        project_name = _kebab(ctx.get("solution_name", "service"))
        admin_ctx = dict(
            ctx,
            project_name=project_name,
            entities=entities,
        )
        template = self._env.get_template("admin_ui.html.j2")
        content = template.render(**admin_ctx)
        output_path = "static/admin.html" if is_go else "app/static/admin.html"
        return GeneratedFile(path=output_path, content=content)

    # ── Product UI generation (full SPA) ──

    _STATUS_COLORS = {
        "active": "emerald", "approved": "emerald", "completed": "emerald",
        "published": "emerald", "enabled": "emerald", "resolved": "emerald",
        "pending": "amber", "review": "amber", "in_progress": "amber",
        "processing": "amber", "submitted": "amber", "open": "amber",
        "rejected": "red", "failed": "red", "error": "red",
        "cancelled": "red", "blocked": "red", "critical": "red",
        "draft": "zinc", "inactive": "zinc", "closed": "zinc",
        "archived": "zinc", "disabled": "zinc", "unknown": "zinc",
    }

    def _build_ui_entities(self, bundle):
        """Build enriched entity definitions for the product UI."""
        admin_entities = self._build_admin_entities(bundle)
        business_rules = getattr(bundle, "business_rules", {}) or {}
        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        enriched = []
        entity_name_map = {}

        for ent in admin_entities:
            snake = ent["name_snake"]
            pascal = ent["name_display"]
            ent["name_plural"] = pascal + "s"

            label_field = "id"
            for f in ent["fields"]:
                if f["name"] == "name" and f["type"] == "string":
                    label_field = "name"
                    break
                if f["name"] not in self._AUTO_FIELDS and f["type"] == "string" and label_field == "id":
                    label_field = f["name"]
            ent["label_field"] = label_field

            status_field = None
            for f in ent["fields"]:
                if f["name"] == "status" or (f.get("enum_values") and "status" in f["name"]):
                    status_field = f["name"]
                    break
            ent["status_field"] = status_field

            transitions = []
            all_states = set()
            rules = business_rules.get(snake, business_rules.get(pascal, []))
            if isinstance(rules, list):
                for rule in rules:
                    if isinstance(rule, dict) and rule.get("type") == "state_transition":
                        from_state, to_state = None, None
                        for pc in rule.get("preconditions", []):
                            if isinstance(pc, dict) and pc.get("field") == status_field:
                                from_state = pc.get("value") or pc.get("equals")
                            elif isinstance(pc, str) and "==" in pc:
                                from_state = pc.split("==")[-1].strip().strip("'\"")
                        for pc in rule.get("postconditions", []):
                            if isinstance(pc, dict) and pc.get("field") == status_field:
                                to_state = pc.get("value") or pc.get("equals")
                            elif isinstance(pc, str) and "==" in pc:
                                to_state = pc.split("==")[-1].strip().strip("'\"")
                        if from_state and to_state:
                            action = rule.get("trigger", f"{from_state}_to_{to_state}").replace("_", " ").title()
                            transitions.append({"from_state": from_state, "to_state": to_state, "action": action})
                            all_states.update([from_state, to_state])

            if status_field:
                for f in ent["fields"]:
                    if f["name"] == status_field and f.get("enum_values"):
                        all_states.update(f["enum_values"])

            ent["state_transitions"] = transitions
            ent["all_states"] = sorted(all_states) if all_states else []
            ent["related_entities"] = []

            confirmed_defs = confirmed_fields.get(snake, confirmed_fields.get(pascal, []))
            confirmed_map = {}
            if isinstance(confirmed_defs, list):
                for cd in confirmed_defs:
                    if isinstance(cd, dict):
                        confirmed_map[_snake(cd.get("name", ""))] = cd

            for f in ent["fields"]:
                cd = confirmed_map.get(f["name"], {})
                f["format"] = cd.get("format")
                f["constraints"] = cd.get("constraints", {})
                f["is_fk"] = bool(cd.get("foreign_key") or (f["name"].endswith("_id") and f["name"] != "id"))
                if f["is_fk"]:
                    fk_entity = f["name"].replace("_id", "")
                    f["fk_api_path"] = f"/api/{fk_entity}s"
                    f["fk_entity_display"] = _pascal(fk_entity)
                    f["fk_label_field"] = "name"
                else:
                    f["fk_api_path"] = ""
                    f["fk_entity_display"] = ""
                    f["fk_label_field"] = ""

            entity_name_map[snake] = ent
            enriched.append(ent)

        for ent in enriched:
            for f in ent["fields"]:
                if f["is_fk"]:
                    fk_entity = f["name"].replace("_id", "")
                    related = entity_name_map.get(fk_entity)
                    if related:
                        ent["related_entities"].append({
                            "name_snake": related["name_snake"],
                            "name_display": related["name_display"],
                            "name_plural": related["name_plural"],
                            "api_base_path": related["api_path"],
                            "label_field": related["label_field"],
                            "status_field": related.get("status_field"),
                        })
        return enriched

    def _build_ui_workflows(self, bundle, ui_entities):
        """Build workflow definitions from BusinessProcess ArchiMate elements."""
        workflows = []
        entity_map = {e["name_snake"]: e for e in ui_entities}
        try:
            from app.models.solution_models import SolutionArchiMateElement
            elements = SolutionArchiMateElement.query.filter_by(solution_id=bundle.solution_id).all()
            procs = [e for e in elements if e.element_type and "businessprocess" in e.element_type.lower().replace(" ", "")]
            svcs = [e for e in elements if e.element_type and "applicationservice" in e.element_type.lower().replace(" ", "")]

            for proc in procs:
                proc_name = proc.element_name or f"Process_{proc.element_id}"
                snake = _snake(proc_name)
                proc_tokens = set(snake.split("_"))
                matching = [s for s in svcs if set(_snake(s.element_name or "").split("_")) & proc_tokens]
                if not matching:
                    matching = svcs[:5]
                steps = []
                for svc in matching:
                    svc_name = svc.element_name or f"Step_{svc.element_id}"
                    sd = svc.spec_data or {}
                    step_fields = []
                    for fd in (sd.get("fields") or [])[:8]:
                        step_fields.append({
                            "name": _snake(fd.get("name", "")),
                            "display_name": fd.get("name", "").replace("_", " ").title(),
                            "type": self._ADMIN_TYPE_MAP.get(fd.get("type", "string"), "string"),
                            "required": fd.get("required", False),
                            "enum_values": fd.get("enum", []),
                        })
                    steps.append({
                        "name": svc_name.replace("_", " ").title(),
                        "description": sd.get("description", ""),
                        "entity": _snake(svc_name), "fields": step_fields,
                        "action": sd.get("action"), "action_label": sd.get("action_label"),
                    })
                primary = next((entity_map[k] for k in entity_map if set(k.split("_")) & proc_tokens), None)
                workflows.append({
                    "name_snake": snake, "name_display": proc_name.replace("_", " ").title(),
                    "steps": steps, "primary_entity": primary,
                    "entities_involved": [primary["name_snake"]] if primary else [],
                })
        except Exception as e:
            logger.debug("Could not extract workflows from business rules: %s", e)
        return workflows

    def _build_ui_navigation(self, ui_entities, workflows, roles):
        """Build sidebar navigation items."""
        nav = []
        for ent in ui_entities:
            nav.append({"type": "entity", "path": f"{ent['name_snake']}s", "label": ent["name_plural"], "roles": []})
        for wf in workflows:
            nav.append({"type": "workflow", "path": wf["name_snake"], "label": wf["name_display"], "roles": []})
        return nav

    def _build_ui_quick_actions(self, ui_entities, workflows):
        """Build quick action buttons for the dashboard."""
        actions = []
        for ent in ui_entities:
            actions.append({"label": f"New {ent['name_display']}", "handler": f"openCreateForm('{ent['name_snake']}', '{ent['api_path']}', '{ent['name_display']}')"})
        for wf in workflows:
            actions.append({"label": f"Start {wf['name_display']}", "handler": f"navigate('/workflow/{wf['name_snake']}')"})
        return actions[:8]

    def _render_product_ui(self, ctx, bundle, is_go=False):
        """Render the complete product UI SPA into ui/ directory."""
        files = []
        ui_entities = self._build_ui_entities(bundle)
        if not ui_entities:
            return files

        workflows = self._build_ui_workflows(bundle, ui_entities)
        idp = getattr(bundle, "identity_provider", None) or {}
        roles = idp.get("roles", [])
        navigation = self._build_ui_navigation(ui_entities, workflows, roles)
        quick_actions = self._build_ui_quick_actions(ui_entities, workflows)
        project_name = _kebab(ctx.get("solution_name", "service"))

        status_colors = dict(self._STATUS_COLORS)
        for ent in ui_entities:
            for state in ent.get("all_states", []):
                key = state.lower().replace(" ", "_").replace("-", "_")
                if key not in status_colors:
                    status_colors[key] = "zinc"

        ui_ctx = dict(ctx, project_name=project_name, entities=ui_entities,
                       workflows=workflows, navigation=navigation,
                       quick_actions=quick_actions, roles=roles, status_colors=status_colors)

        try:
            tpl = self._env.get_template("ui/app_js.html.j2")
            files.append(GeneratedFile(path="ui/static/app.js", content=tpl.render(**ui_ctx)))
        except Exception as e:
            logger.debug("UI app.js template not available: %s", e)
        try:
            tpl = self._env.get_template("ui/styles_css.html.j2")
            files.append(GeneratedFile(path="ui/static/styles.css", content=tpl.render(**ui_ctx)))
        except Exception as e:
            logger.debug("UI styles.css template not available: %s", e)

        index_html = self._render_product_ui_index(ui_ctx)
        if index_html:
            files.append(GeneratedFile(path="ui/index.html", content=index_html))
        return files

    def _render_product_ui_index(self, ui_ctx):
        """Render product UI index.html by composing section templates."""
        try:
            dashboard_html = self._env.get_template("ui/dashboard.html.j2").render(**ui_ctx)
            list_tpl = self._env.get_template("ui/entity_list.html.j2")
            detail_tpl = self._env.get_template("ui/entity_detail.html.j2")

            entity_sections = ""
            for ent in ui_ctx["entities"]:
                ent_ctx = dict(ui_ctx, ent=ent)
                # Only name_snake is used below; name_display was unpacked and never
                # read. Unpacking a single value rather than a pair, since dropping
                # one name from a tuple target is the one F841 shape that cannot be
                # rewritten mechanically.
                sn = ent["name_snake"]
                entity_sections += (
                    f'\n            <div x-show="currentRoute === \'/{sn}s\'" x-cloak>{list_tpl.render(**ent_ctx)}</div>\n'  # raw-html-ok: list_tpl.render() is a *.html.j2 template, now autoescaped by the Environment configured above
                    f'            <div x-show="currentRoute.match(/^\\/{sn}s\\/\\d+$/)" x-cloak>{detail_tpl.render(**ent_ctx)}</div>\n'  # raw-html-ok: detail_tpl.render() is a *.html.j2 template, now autoescaped by the Environment configured above
                )

            workflow_sections = ""
            if ui_ctx.get("workflows"):
                wf_tpl = self._env.get_template("ui/workflow.html.j2")
                for wf in ui_ctx["workflows"]:
                    wf_ctx = dict(ui_ctx, wf=wf)
                    sn = wf["name_snake"]
                    workflow_sections += f'\n            <div x-show="currentRoute === \'/workflow/{sn}\'" x-cloak>{wf_tpl.render(**wf_ctx)}</div>\n'  # raw-html-ok: wf_tpl.render() is a *.html.j2 template, now autoescaped by the Environment configured above

            nav_html = self._build_nav_html(ui_ctx.get("navigation", []))
            pn = ui_ctx.get("project_name", "Service")
            sid = ui_ctx.get("solution_id", 0)
            bid = ui_ctx.get("bundle_id", "")
            return self._compose_shell_html(pn, sid, bid, nav_html, dashboard_html, entity_sections, workflow_sections)
        except Exception:
            return None

    @staticmethod
    def _build_nav_html(navigation):
        """Build sidebar navigation HTML."""
        parts = []
        for nav in navigation:
            p = nav["path"]
            # label is ent["name_plural"]/wf["name_display"] -- architect-
            # authored freeform text from the solution design UI, unlike
            # path (already sanitized via _snake()). Escape it before it
            # reaches the generated app's own sidebar HTML.
            lbl = html.escape(nav["label"])
            if nav["type"] == "entity":
                icon = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 10h16M4 14h16M4 18h16"></path></svg>'
                parts.append(f'<a href="#/{p}" @click="navigate(\'/{p}\')" :class="currentRoute.startsWith(\'/{p}\') ? \'bg-zinc-800 text-zinc-100\' : \'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50\'" class="flex items-center gap-2 px-3 py-2 text-sm rounded-md transition-colors">{icon} {lbl}</a>')
            elif nav["type"] == "workflow":
                icon = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path></svg>'
                parts.append(f'<a href="#/workflow/{p}" @click="navigate(\'/workflow/{p}\')" :class="currentRoute.startsWith(\'/workflow/{p}\') ? \'bg-zinc-800 text-zinc-100\' : \'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50\'" class="flex items-center gap-2 px-3 py-2 text-sm rounded-md transition-colors">{icon} {lbl}</a>')
        return "\n            ".join(parts)

    @staticmethod
    def _compose_shell_html(project_name, solution_id, bundle_id, nav_html, dashboard_html, entity_sections, workflow_sections):
        """Compose the final SPA shell HTML."""
        # project_name is architect-authored freeform text (the same class of
        # value fixed via autoescape for the *.html.j2 templates above); this
        # method builds its shell with a raw Python f-string, so it needs its
        # own explicit escape() -- Jinja's autoescape doesn't apply here.
        project_name = html.escape(project_name)
        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>
{project_name}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>tailwind.config = {{ darkMode: 'class', theme: {{ extend: {{ colors: {{ primary: {{ 600: '#2563eb', 700: '#1d4ed8' }} }} }} }} }};</script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <link rel="stylesheet" href="/static/styles.css">
    <style>[x-cloak] {{ display: none !important; }}</style>
</head>
<body class="bg-zinc-950 text-zinc-100 min-h-screen dark">
<div x-data="appShell()" x-init="init()" class="flex h-screen overflow-hidden">
    <aside class="w-60 flex-shrink-0 border-r border-zinc-800 bg-zinc-900/50 flex flex-col">
        <div class="p-4 border-b border-zinc-800">
            <h1 class="text-sm font-bold tracking-tight truncate">
{project_name}</h1>
            <p class="text-[10px] text-zinc-500 mt-0.5">Solution #{solution_id}</p>
        </div>
        <nav class="flex-1 overflow-y-auto py-2 px-2 space-y-0.5">
            <a href="#/" @click="navigate('/')" :class="currentRoute === '/' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50'" class="flex items-center gap-2 px-3 py-2 text-sm rounded-md transition-colors">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-4 0h4"></path></svg>
                Dashboard
            </a>
            {nav_html}
        </nav>
        <div class="p-3 border-t border-zinc-800">
            <div class="flex items-center gap-2">
                <span class="w-2 h-2 rounded-full" :class="healthOk ? 'bg-emerald-500' : 'bg-red-500'"></span>
                <span class="text-xs text-zinc-500" x-text="healthOk ? 'Healthy' : 'Unhealthy'"></span>
            </div>
            <div class="flex gap-2 mt-2">
                <a href="/docs" target="_blank" class="text-[10px] text-zinc-500 hover:text-zinc-300">API Docs</a>
                <a href="/health" target="_blank" class="text-[10px] text-zinc-500 hover:text-zinc-300">Health</a>
            </div>
        </div>
    </aside>
    <main class="flex-1 overflow-y-auto">
        <header class="sticky top-0 z-30 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-sm px-6 py-3 flex items-center justify-between">
            <nav class="flex items-center gap-1 text-sm text-zinc-500">
                <template x-for="(crumb, i) in breadcrumbs" :key="i">
                    <span class="flex items-center gap-1"><span x-show="i > 0" class="text-zinc-600">/</span><span :class="i === breadcrumbs.length - 1 ? 'text-zinc-200' : ''" x-text="crumb"></span></span>
                </template>
            </nav>
            <span class="text-xs text-zinc-600">
{bundle_id}</span>
        </header>
        <div class="p-6">
            <div x-show="currentRoute === '/'" x-cloak>
{dashboard_html}</div>
{entity_sections}{workflow_sections}
        </div>
    </main>
    <div x-show="formPanel.open" x-cloak class="fixed inset-0 z-50 flex justify-end" @keydown.escape.window="formPanel.open = false">
        <div class="absolute inset-0 bg-black/50" @click="formPanel.open = false"></div>
        <div class="relative w-full max-w-lg bg-zinc-900 border-l border-zinc-800 overflow-y-auto" @click.stop>
            <div class="sticky top-0 bg-zinc-900 border-b border-zinc-800 px-6 py-4 flex items-center justify-between">
                <h3 class="text-lg font-semibold" x-text="formPanel.title"></h3>
                <button @click="formPanel.open = false" class="text-zinc-500 hover:text-zinc-300"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg></button>
            </div>
            <div class="p-6" id="form-container"></div>
        </div>
    </div>
    <div class="fixed top-4 right-4 z-[70] space-y-2">
        <template x-for="t in toasts" :key="t.id">
            <div x-transition class="px-4 py-2 rounded-lg text-sm font-medium shadow-lg" :class="t.type === 'error' ? 'bg-red-600 text-white' : t.type === 'warning' ? 'bg-amber-600 text-white' : 'bg-emerald-600 text-white'" x-text="t.message"></div>
        </template>
    </div>
    <div x-show="confirmDlg.open" x-cloak class="fixed inset-0 z-[80] flex items-center justify-center">
        <div class="absolute inset-0 bg-black/50" @click="confirmDlg.cancel()"></div>
        <div class="relative bg-zinc-900 border border-zinc-800 rounded-lg p-6 max-w-sm w-full mx-4" @click.stop>
            <h3 class="text-lg font-semibold mb-2" x-text="confirmDlg.title"></h3>
            <p class="text-zinc-400 text-sm mb-4" x-text="confirmDlg.message"></p>
            <div class="flex gap-2 justify-end">
                <button @click="confirmDlg.cancel()" class="px-3 py-1.5 text-sm rounded-md border border-zinc-700 text-zinc-400 hover:text-zinc-100">Cancel</button>
                <button @click="confirmDlg.accept()" class="px-3 py-1.5 text-sm font-medium rounded-md bg-red-600 text-white hover:bg-red-700">Confirm</button>
            </div>
        </div>
    </div>
</div>
<script src="/static/app.js"></script>
</body>
</html>'''

    # ── Server-rendered UI pages (Jinja2 + Alpine.js + Tailwind) ──

    def _build_page_entities(self, bundle):
        """Build entity defs enriched with genome views section data for page templates.

        Returns a list of entity dicts with view_columns, view_filters,
        view_default_sort, view_actions, view_sections, view_create_fields,
        plus standard fields from _build_ui_entities.
        """
        ui_entities = self._build_ui_entities(bundle)
        genome_modules = getattr(bundle, "_genome_modules", {}) or {}

        for ent in ui_entities:
            snake = ent["name_snake"]
            # Find genome module matching this entity
            mod = genome_modules.get(snake) or genome_modules.get(_pascal(snake)) or {}
            views = mod.get("views", {})

            # List view config
            list_view = views.get("list", {})
            ent["view_columns"] = list_view.get("columns") or [
                f["name"] for f in ent["fields"][:7] if f["name"] != "deleted_at"
            ]
            ent["view_filters"] = list_view.get("filters") or (
                [ent["status_field"]] if ent.get("status_field") else []
            )
            ent["view_default_sort"] = list_view.get("default_sort") or {
                "field": "id", "dir": "desc",
            }
            ent["view_actions"] = list_view.get("actions") or ["view", "edit", "delete"]

            # Detail view config
            detail_view = views.get("detail", {})
            ent["view_sections"] = detail_view.get("sections") or ["summary"]

            # Create view config — map field names back to field dicts
            create_view = views.get("create", {})
            create_field_names = create_view.get("fields")
            if create_field_names:
                field_map = {f["name"]: f for f in ent["fields"]}
                ent["view_create_fields"] = [
                    field_map[fn] for fn in create_field_names if fn in field_map
                ]
            else:
                ent["view_create_fields"] = [
                    f for f in ent["fields"]
                    if not f["is_auto"] and f["name"] not in ("id", "deleted_at", "created_at", "updated_at")
                ]

        return ui_entities

    def _generate_ui_pages(self, ctx, bundle):
        """Generate server-rendered Jinja2 pages for the FastAPI app.

        Produces:
        - templates/layout.html — base layout
        - templates/_components.html — reusable macros
        - templates/dashboard.html — home page
        - templates/{entity}/list.html — per-entity list page
        - templates/{entity}/detail.html — per-entity detail page
        - templates/{entity}/create.html — per-entity create/edit form
        - app/routers/pages.py — page route handlers
        - static/css/app.css — custom styles
        - static/js/app.js — shared utilities
        """
        files = []
        page_entities = self._build_page_entities(bundle)
        if not page_entities:
            return files

        idp = getattr(bundle, "identity_provider", None) or {}
        roles = idp.get("roles", [])
        product = getattr(bundle, "product_config", {}) or {}

        page_ctx = dict(
            ctx,
            entities=page_entities,
            identity_provider=idp,
            roles=roles,
            product=product,
        )

        # Layout + components + dashboard (rendered once)
        try:
            tpl = self._env.get_template("ui/pages/layout.html.j2")
            files.append(GeneratedFile(
                path="templates/layout.html",
                content=tpl.render(**page_ctx),
            ))
        except Exception as e:
            logger.debug("layout.html.j2 not available: %s", e)
            return files

        try:
            tpl = self._env.get_template("ui/pages/_components.html.j2")
            files.append(GeneratedFile(
                path="templates/_components.html",
                content=tpl.render(**page_ctx),
            ))
        except Exception as e:
            logger.debug("_components.html.j2 not available: %s", e)

        try:
            tpl = self._env.get_template("ui/pages/dashboard.html.j2")
            files.append(GeneratedFile(
                path="templates/dashboard.html",
                content=tpl.render(**page_ctx),
            ))
        except Exception as e:
            logger.debug("dashboard.html.j2 not available: %s", e)

        # Landing page (public, no auth required)
        try:
            _genome_modules = getattr(bundle, "_genome_modules", {}) or {}
            _problem = {}
            # Try to get problem statement from genome or solution metadata
            for _mk, _mv in _genome_modules.items():
                if isinstance(_mv, dict) and _mv.get("_rationale"):
                    _problem["statement"] = _mv["_rationale"]
                    break

            from datetime import datetime as _dt
            landing_ctx = dict(page_ctx,
                product_name=product.get("name") or ctx.get("solution_name", "App"),
                product_tagline=product.get("tagline", ""),
                product_logo=product.get("logo_url", ""),
                product_favicon=product.get("favicon_url", ""),
                primary_color=product.get("primary_color", "#2563eb"),
                accent_color=product.get("accent_color", "#10b981"),
                problem_statement=_problem.get("statement", ""),
                success_metrics=[],
                now_year=_dt.now().year,
            )
            tpl = self._env.get_template("ui/pages/landing.html.j2")
            files.append(GeneratedFile(
                path="templates/landing.html",
                content=tpl.render(**landing_ctx),
            ))
        except Exception as e:
            logger.debug("landing.html.j2 not available: %s", e)

        # Per-entity pages
        list_tpl = self._env.get_template("ui/pages/list_page.html.j2")
        detail_tpl = self._env.get_template("ui/pages/detail_page.html.j2")
        create_tpl = self._env.get_template("ui/pages/create_page.html.j2")

        for ent in page_entities:
            ent_ctx = dict(page_ctx, entity=ent)
            snake = ent["name_snake"]
            try:
                files.append(GeneratedFile(
                    path=f"templates/{snake}/list.html",
                    content=list_tpl.render(**ent_ctx),
                ))
                files.append(GeneratedFile(
                    path=f"templates/{snake}/detail.html",
                    content=detail_tpl.render(**ent_ctx),
                ))
                files.append(GeneratedFile(
                    path=f"templates/{snake}/create.html",
                    content=create_tpl.render(**ent_ctx),
                ))
            except Exception as e:
                logger.warning("Failed to render page templates for %s: %s", snake, e)

        # Page routes
        try:
            tpl = self._env.get_template("ui/pages/page_routes.py.j2")
            files.append(GeneratedFile(
                path="app/routers/pages.py",
                content=tpl.render(**page_ctx),
            ))
        except Exception as e:
            logger.debug("page_routes.py.j2 not available: %s", e)

        # Static files
        try:
            tpl = self._env.get_template("ui/pages/app_css.j2")
            files.append(GeneratedFile(
                path="static/css/app.css",
                content=tpl.render(**page_ctx),
            ))
        except Exception as e:
            logger.debug("app_css.j2 not available: %s", e)

        try:
            tpl = self._env.get_template("ui/pages/app_js.j2")
            files.append(GeneratedFile(
                path="static/js/app.js",
                content=tpl.render(**page_ctx),
            ))
        except Exception as e:
            logger.debug("app_js.j2 not available: %s", e)

        return files

    def _build_model_context(self, bundle):
        """Build template context for model generation.

        Includes table-level constraints (indexes, composite keys, unique constraints,
        check constraints) from the enriched IR, plus per-model audit_trail flags
        derived from genome module definitions.
        """
        models = {}
        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        tables = getattr(bundle, "tables", {}) or {}

        # Build audit_trail lookup: snake_name → bool, from genome modules
        _genome_modules = getattr(bundle, "_genome_modules", {}) or {}
        _audit_by_snake = {}
        for mod_key, mod_def in _genome_modules.items():
            if not isinstance(mod_def, dict):
                continue
            root = mod_def.get("aggregate_root", mod_key)
            snake = _pascal_to_snake(_pascal(root))
            at = mod_def.get("audit_trail", {})
            _audit_by_snake[snake] = bool(at.get("enabled")) if isinstance(at, dict) else bool(at)

        for name, field_defs in confirmed_fields.items():
            pascal_name = _pascal(name)
            snake_name = _pascal_to_snake(pascal_name)
            # Ensure every model has created_at / updated_at audit columns
            existing_names = {f.get("name") for f in field_defs}
            field_defs = list(field_defs)  # copy so we don't mutate the bundle
            if "created_at" not in existing_names:
                field_defs.append({"name": "created_at", "type": "datetime", "readonly": True})
            if "updated_at" not in existing_names:
                field_defs.append({"name": "updated_at", "type": "datetime", "readonly": False})
            enums = {}
            for f in field_defs:
                ev = f.get("enum") or f.get("enum_values")
                if f.get("type") == "enum" and ev:
                    enums[f"{pascal_name}{f['name'].capitalize()}Enum"] = ev
                    f["enum"] = ev  # normalize for template access
                # Map default_value to default for template access
                if f.get("default_value") and not f.get("default"):
                    f["default"] = f["default_value"]
            # Merge table-level constraints from enriched IR
            table_constraints = tables.get(name, {}) if isinstance(tables.get(name), dict) else {}
            table_name = _pluralize(snake_name)
            # Per-model audit_trail: from genome module or fallback to global NFR flag
            audit_trail_enabled = _audit_by_snake.get(snake_name, _audit_by_snake.get(name, False))
            models[pascal_name] = {
                "fields": field_defs,
                "table_name": table_name,
                "description": f"{pascal_name} entity",
                "enums": enums,
                "composite_indexes": table_constraints.get("composite_indexes", []),
                "unique_constraints": table_constraints.get("unique_constraints", []),
                "audit_trail": audit_trail_enabled,
            }
        return models

    def _inject_synthesized_bodies(self, models, bundle):
        """Inject LLM-synthesized handler bodies into model context.

        Reads synthesized_handlers from spec_data (populated by
        BusinessLogicSynthesizer.synthesize_all) and attaches them to
        the handler context so the Jinja template can render real bodies
        instead of 501 stubs.
        """
        try:
            from app.models.solution_archimate_element import SolutionArchiMateElement
            links = SolutionArchiMateElement.query.filter_by(
                solution_id=bundle.solution_id
            ).all()

            synth_map = {}
            for link in links:
                sd = link.spec_data or {}
                elem_name = getattr(link, "element_name", None) or f"element_{link.element_id}"
                handlers = sd.get("synthesized_handlers", {})
                if handlers:
                    synth_map[elem_name] = handlers

            for name, model_def in models.items():
                # Match by original element name (pre-PascalCase)
                for elem_name, handlers in synth_map.items():
                    pascal = _pascal(elem_name)
                    if pascal == name and elem_name in handlers:
                        synth = handlers[elem_name]
                        model_def["synthesized_body"] = synth.get("handler_code", "")
                        model_def["synthesized_imports"] = synth.get("imports", [])
                        model_def["synthesized_confidence"] = synth.get("confidence", 0)
                        model_def["is_synthesized"] = True
                        break
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(
                "Could not inject synthesized bodies: %s", e
            )

    def _render_cicd(self, ctx, ci_cd_config):
        """Render CI/CD pipeline files based on provider configuration.

        Args:
            ctx: Base template context (solution_id, solution_name, etc.).
            ci_cd_config: Dict with provider, container_registry, namespaces, etc.

        Returns:
            List of GeneratedFile for the selected CI/CD provider.
        """
        files = []
        if not ci_cd_config:
            ci_cd_config = {}

        # Default to github_actions if not specified
        provider = ci_cd_config.get("provider", "github_actions")
        cicd_ctx = dict(ctx, ci_cd=ci_cd_config)

        if provider == "gitlab_ci":
            template = self._env.get_template("gitlab_ci.yml.j2")
            content = template.render(**cicd_ctx)
            files.append(GeneratedFile(path=".gitlab-ci.yml", content=content))
        else:
            # Default: github_actions
            template = self._env.get_template("github_actions.yml.j2")
            content = template.render(**cicd_ctx)
            files.append(GeneratedFile(path=".github/workflows/deploy.yml", content=content))

        return files

    def _build_handler_context(self, bundle):
        """Build template context for handler generation."""
        models = {}
        business_rules = getattr(bundle, "business_rules", {}) or {}
        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        for name, rules in business_rules.items():
            pascal_name = _pascal(name)
            models[pascal_name] = {
                "fields": confirmed_fields.get(name, []),
                "business_rules": rules,
                "description": f"{pascal_name} business logic",
                "version": 0,
                "hash": "",
            }
        return models

    # ── Architecture style helpers ──

    @staticmethod
    def _get_arch_style(bundle):
        """Extract the primary architecture style from bundle metadata.

        Returns one of: microservices, event_driven, serverless.
        Falls back to 'microservices' for unknown or missing values.
        """
        arch = getattr(bundle, "architecture_style", None) or {}
        primary = arch.get("primary", "microservices") if isinstance(arch, dict) else "microservices"
        if primary not in SUPPORTED_ARCH_STYLES:
            return "microservices"
        return primary

    @staticmethod
    def _get_arch_config(bundle):
        """Extract the full architecture style config from bundle metadata."""
        return getattr(bundle, "architecture_style", None) or {}

    def _render_arch_style_files(self, ctx, bundle, arch_style, arch_config):
        """Render architecture style-specific template files.

        Args:
            ctx: Base template context.
            bundle: ProductSpecBundle.
            arch_style: One of microservices, event_driven, serverless.
            arch_config: Full architecture_style dict with patterns, service_mesh, api_gateway.

        Returns:
            List of GeneratedFile.
        """
        files = []
        events = getattr(bundle, "events", []) or []
        services = getattr(bundle, "services", []) or []
        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        deployment = getattr(bundle, "deployment", None)

        # Build service data for templates
        svc_data = []
        for svc in services:
            svc_data.append({
                "name": svc.name,
                "tag": svc.tag,
                "paths": [
                    {
                        "path": p.path,
                        "method": p.method.lower(),
                        "operation_id": p.operation_id,
                        "summary": p.summary,
                    }
                    for p in svc.paths
                ],
            })

        # Build event data for templates
        event_data = []
        for evt in events:
            event_data.append({
                "name": evt.name,
                "channel": evt.channel,
                "payload_schema": evt.payload_schema,
            })

        style_ctx = dict(
            ctx,
            events=event_data,
            services=svc_data,
            confirmed_fields=confirmed_fields,
        )

        # Event-driven templates
        if arch_style == "event_driven":
            files.extend(self._render_event_driven(style_ctx))

        # Serverless templates
        if arch_style == "serverless":
            deploy_vars = vars(deployment) if deployment and hasattr(deployment, "__dict__") else {}
            serverless_ctx = dict(style_ctx, deployment=deploy_vars or {})
            files.extend(self._render_serverless(serverless_ctx))

        # Service mesh (Istio) — any style can have a service mesh
        service_mesh = arch_config.get("service_mesh")
        if service_mesh == "istio":
            files.extend(self._render_istio(style_ctx))

        # API gateway (Kong) — any style can have an API gateway
        api_gateway = arch_config.get("api_gateway")
        if api_gateway == "kong":
            files.extend(self._render_kong(style_ctx))

        return files

    def _render_event_driven(self, ctx):
        """Render event-driven architecture templates."""
        files = []
        if self.language == "python-fastapi":
            files.append(self._render_template_file(
                "event_handler.py.j2", "app/events/event_handler.py", ctx,
            ))
            files.append(self._render_template_file(
                "event_schema.py.j2", "app/events/event_schema.py", ctx,
            ))
            files.append(self._render_template_file(
                "saga_orchestrator.py.j2", "app/events/saga_orchestrator.py", ctx,
            ))
            files.append(GeneratedFile(path="app/events/__init__.py", content=""))
        else:
            # Go
            files.append(self._render_template_file(
                "event_handler.go.j2", "event_handler.go", ctx,
            ))
            files.append(self._render_template_file(
                "event_schema.go.j2", "event_schema.go", ctx,
            ))
            files.append(self._render_template_file(
                "saga_orchestrator.go.j2", "saga_orchestrator.go", ctx,
            ))
        return files

    def _render_serverless(self, ctx):
        """Render serverless architecture templates."""
        files = []
        if self.language == "python-fastapi":
            files.append(self._render_template_file(
                "lambda_handler.py.j2", "lambda_handler.py", ctx,
            ))
            files.append(self._render_template_file(
                "sam_template.yml.j2", "template.yaml", ctx,
            ))
            files.append(self._render_template_file(
                "serverless_config.py.j2", "app/serverless_config.py", ctx,
            ))
        else:
            # Go
            files.append(self._render_template_file(
                "lambda_handler.go.j2", "lambda_handler.go", ctx,
            ))
            files.append(self._render_template_file(
                "sam_template.yml.j2", "template.yaml", ctx,
            ))
        return files

    def _render_istio(self, ctx):
        """Render Istio service mesh manifests."""
        files = []
        template = self._shared_env.get_template("istio_virtual_service.yaml.j2")
        content = template.render(**ctx)
        files.append(GeneratedFile(
            path="k8s/istio-virtual-service.yaml", content=content,
        ))

        template = self._shared_env.get_template("istio_peer_auth.yaml.j2")
        content = template.render(**ctx)
        files.append(GeneratedFile(
            path="k8s/istio-peer-auth.yaml", content=content,
        ))
        return files

    def _render_kong(self, ctx):
        """Render Kong API gateway config."""
        template = self._shared_env.get_template("kong_config.yaml.j2")
        content = template.render(**ctx)
        return [GeneratedFile(path="kong/kong.yaml", content=content)]

    # ── Java/Spring Boot generation ──

    def _generate_java(self, bundle):
        """Generate a complete Java Spring Boot 3.2 project from a ProductSpecBundle.

        Output structure:
          src/main/java/com/archie/generated/<package>/  — Application, controllers, services, models, repos, DTOs, config
          src/test/java/com/archie/generated/<package>/  — Integration tests
          src/main/resources/                            — application.yml
          pom.xml, Dockerfile, docker-compose.yml
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        bundle_id = f"java_{now}"

        files = []
        pkg = _java_package(bundle.solution_name)
        app_class = _pascal(bundle.solution_name)
        idp = getattr(bundle, "identity_provider", None) or {}
        integrations = getattr(bundle, "integrations", {}) or {}
        getattr(bundle, "confirmed_fields", {}) or {}
        getattr(bundle, "business_rules", {}) or {}

        _, grpc_integ, async_integ = self._classify_integrations(integrations)

        ctx = {
            "solution_id": bundle.solution_id,
            "solution_name": bundle.solution_name,
            "bundle_id": bundle_id,
            "spec_hash": bundle.spec_hash,
            "maturity_score": bundle.maturity_score,
            "generated_at": now,
            "java_package": pkg,
            "java_app_class": app_class,
            "identity_provider": idp,
            "has_kafka": bool(async_integ),
            "has_grpc": bool(grpc_integ),
        }

        java_base = f"src/main/java/com/archie/generated/{pkg}"
        test_base = f"src/test/java/com/archie/generated/{pkg}"

        # 1. Application entry point
        template = self._env.get_template("Application.java.j2")
        content = template.render(**ctx)
        files.append(GeneratedFile(
            path=f"{java_base}/{app_class}Application.java",
            content=content,
        ))

        # 2. Build model context from confirmed fields + services
        models = self._build_java_model_context(bundle)

        # 3-6. Per-entity files: Model, Repository, Service, DTO
        if models:
            model_tpl = self._env.get_template("Model.java.j2")
            repo_tpl = self._env.get_template("Repository.java.j2")
            svc_tpl = self._env.get_template("Service.java.j2")
            req_tpl = self._env.get_template("Request.java.j2")
            resp_tpl = self._env.get_template("Response.java.j2")

            for model_name, model_def in models.items():
                single = {model_name: model_def}
                files.append(GeneratedFile(
                    path=f"{java_base}/model/{model_name}.java",
                    content=model_tpl.render(**ctx, models=single),
                ))
                files.append(GeneratedFile(
                    path=f"{java_base}/repository/{model_name}Repository.java",
                    content=repo_tpl.render(**ctx, models=single),
                ))
                files.append(GeneratedFile(
                    path=f"{java_base}/service/{model_name}Service.java",
                    content=svc_tpl.render(**ctx, models=single),
                ))
                files.append(GeneratedFile(
                    path=f"{java_base}/dto/{model_name}Request.java",
                    content=req_tpl.render(**ctx, models=single),
                ))
                files.append(GeneratedFile(
                    path=f"{java_base}/dto/{model_name}Response.java",
                    content=resp_tpl.render(**ctx, models=single),
                ))

        # Build a case-insensitive index so svc.name "workorder" resolves to "WorkOrder"
        _models_lower = {k.lower(): k for k in (models or {})}

        # 7. REST controllers (one per service/entity)
        for svc in bundle.services:
            entity_name = _pascal(svc.name)
            # Prefer the confirmed model name's casing over the OpenAPI-derived svc.name
            entity_name = _models_lower.get(entity_name.lower(), entity_name)
            archimate_ids = [
                p.archimate_source_id for p in svc.paths if p.archimate_source_id
            ]
            paths = []
            for p in svc.paths:
                paths.append({
                    "path": p.path,
                    "method": p.method,
                    "operation_id": p.operation_id,
                    "summary": p.summary,
                    "request_schema": p.request_schema if p.request_schema != "dict" else None,
                    "response_schema": p.response_schema if p.response_schema != "dict" else None,
                    "archimate_source_id": p.archimate_source_id,
                })
            # Find state machine transitions for this entity
            sm_transitions = []
            model_def = models.get(entity_name, {}) if models else {}
            sm = model_def.get("state_machine") if isinstance(model_def, dict) else None
            if sm and sm.get("transitions"):
                sm_transitions = sm["transitions"]

            # Get endpoint path from model context, fall back to kebab-case
            ep = model_def.get("endpoint_path") if isinstance(model_def, dict) else None
            if not ep:
                ep = _pluralize(_pascal_to_snake(entity_name)).replace("_", "-")

            ctrl_ctx = dict(ctx,
                entity_name=entity_name,
                endpoint_path=ep,
                paths=paths,
                archimate_ids=archimate_ids or ["N/A"],
                state_machine_transitions=sm_transitions,
            )
            template = self._env.get_template("Controller.java.j2")
            content = template.render(**ctrl_ctx)
            files.append(GeneratedFile(
                path=f"{java_base}/controller/{entity_name}Controller.java",
                content=content,
                archimate_sources=archimate_ids,
            ))

        # 8. Security configuration
        sec_ctx = dict(ctx, identity_provider=idp)
        template = self._env.get_template("SecurityConfig.java.j2")
        content = template.render(**sec_ctx)
        files.append(GeneratedFile(
            path=f"{java_base}/config/SecurityConfig.java",
            content=content,
        ))

        # 8b. Global exception handler — always generated (RFC 7807 Problem Details)
        try:
            files.append(self._render_simple(
                "GlobalExceptionHandler.java.j2",
                f"{java_base}/exception/GlobalExceptionHandler.java",
                ctx,
            ))
        except Exception as _e:
            logger.debug("GlobalExceptionHandler.java template failed: %s", _e)

        # 8c. Genome-driven infrastructure templates (Java equivalents)
        _nfr_flags_java = {nfr["flag"] for nfr in (bundle.infra_context.nfrs or []) if nfr.get("flag")}
        if "multi_tenancy" in _nfr_flags_java:
            try:
                template = self._env.get_template("TenantFilter.java.j2")
                files.append(GeneratedFile(
                    path=f"{java_base}/config/TenantFilter.java",
                    content=template.render(**ctx),
                ))
                template = self._env.get_template("TenantContext.java.j2")
                files.append(GeneratedFile(
                    path=f"{java_base}/config/TenantContext.java",
                    content=template.render(**ctx),
                ))
            except Exception as exc:
                logger.debug("suppressed error in DeterministicCodeGenerator._generate_java (app/modules/solutions_product/services/deterministic_code_generator.py): %s", exc)
        if "api_keys" in _nfr_flags_java:
            try:
                template = self._env.get_template("ApiKeyEntity.java.j2")
                files.append(GeneratedFile(
                    path=f"{java_base}/apikeys/ApiKeyEntity.java",
                    content=template.render(**ctx),
                ))
            except Exception as exc:
                logger.debug("suppressed error in DeterministicCodeGenerator._generate_java (app/modules/solutions_product/services/deterministic_code_generator.py): %s", exc)
        if "opentelemetry" in _nfr_flags_java:
            try:
                template = self._env.get_template("ObservabilityConfig.java.j2")
                files.append(GeneratedFile(
                    path=f"{java_base}/config/ObservabilityConfig.java",
                    content=template.render(**ctx),
                ))
            except Exception as exc:
                logger.debug("suppressed error in DeterministicCodeGenerator._generate_java (app/modules/solutions_product/services/deterministic_code_generator.py): %s", exc)

        # Keycloak realm + Prometheus alerts (shared templates, same as Python/Go)
        _idp_type_java = idp.get("type") if idp else None
        if _idp_type_java == "oidc":
            try:
                kc_template = self._shared_env.get_template("keycloak_realm.json.j2")
                mfa_setting = "none"
                for nfr in (bundle.infra_context.nfrs or []):
                    if nfr.get("flag") == "mfa":
                        desc = nfr.get("description", "")
                        if "required_for_all" in desc:
                            mfa_setting = "required_for_all"
                        elif "required_for_admin" in desc:
                            mfa_setting = "required_for_admin"
                kc_idp = dict(idp)
                kc_idp["mfa"] = mfa_setting
                kc_ctx = dict(ctx, identity_provider=kc_idp)
                files.append(GeneratedFile(
                    path="infrastructure/keycloak/realm-import.json",
                    content=kc_template.render(**kc_ctx),
                ))
            except Exception as _kc_err:
                logger.debug("keycloak_realm template failed for Java: %s", _kc_err)
        try:
            prom_template = self._shared_env.get_template("prometheus_alerts.yml.j2")
            files.append(GeneratedFile(
                path="infrastructure/monitoring/alerts.yml",
                content=prom_template.render(**ctx),
            ))
        except Exception as exc:
            logger.debug("suppressed error in DeterministicCodeGenerator._generate_java (app/modules/solutions_product/services/deterministic_code_generator.py): %s", exc)

        # 9. Spring Boot config (YAML with dev + prod profiles)
        template = self._env.get_template("application.yml.j2")
        content = template.render(**ctx)
        files.append(GeneratedFile(
            path="src/main/resources/application.yml",
            content=content,
        ))

        # 9b. Test profile config
        try:
            template = self._env.get_template("application-test.yml.j2")
            content = template.render(**ctx)
            files.append(GeneratedFile(
                path="src/main/resources/application-test.yml",
                content=content,
            ))
        except Exception as exc:
            logger.debug("suppressed error in DeterministicCodeGenerator._generate_java (app/modules/solutions_product/services/deterministic_code_generator.py): %s", exc)

        # 10. Maven POM
        template = self._env.get_template("pom.xml.j2")
        content = template.render(**ctx)
        files.append(GeneratedFile(path="pom.xml", content=content))

        # 11. Docker infrastructure
        template = self._env.get_template("Dockerfile.j2")
        content = template.render(**ctx)
        files.append(GeneratedFile(path="Dockerfile", content=content))

        template = self._env.get_template("docker-compose.yml.j2")
        content = template.render(**ctx)
        files.append(GeneratedFile(path="docker-compose.yml", content=content))

        # 12. Integration tests
        test_services = []
        for svc in bundle.services:
            test_paths = []
            for p in svc.paths:
                # p.path already contains the full URL (e.g. /api/users) — use directly
                test_url = _replace_path_params(p.path)
                test_paths.append({
                    "method": p.method,
                    "operation_id": p.operation_id,
                    "summary": p.summary,
                    "full_url": test_url,
                    "archimate_source_id": p.archimate_source_id,
                })
            test_services.append({"name": svc.name, "tag": svc.tag, "paths": test_paths})

        test_ctx = dict(ctx, services=test_services)
        template = self._env.get_template("ApplicationTests.java.j2")
        content = template.render(**test_ctx)
        files.append(GeneratedFile(
            path=f"{test_base}/{app_class}ApplicationTests.java",
            content=content,
        ))

        # 13. Audit service (always generated — enterprise requirement)
        try:
            files.append(self._render_simple(
                "AuditService.java.j2",
                f"{java_base}/audit/AuditService.java",
                ctx,
            ))
        except Exception as _e:
            logger.debug("AuditService.java template failed: %s", _e)

        # 14. Webhook system (conditional on genome config)
        _webhook_cfg_java = getattr(bundle, "webhook_config", {}) or {}
        if _webhook_cfg_java.get("enabled"):
            _wh_delivery = _webhook_cfg_java.get("delivery", {})
            _wh_ctx = dict(ctx,
                webhook_retry_attempts=_wh_delivery.get("retry_attempts", 3),
                webhook_retry_backoff=_wh_delivery.get("retry_backoff", "exponential"),
            )
            try:
                files.append(self._render_simple(
                    "WebhookService.java.j2",
                    f"{java_base}/webhook/WebhookService.java",
                    _wh_ctx,
                ))
                files.append(self._render_simple(
                    "WebhookController.java.j2",
                    f"{java_base}/webhook/WebhookController.java",
                    _wh_ctx,
                ))
            except Exception as _wh_err:
                logger.warning("Java webhook template failed: %s", _wh_err)

        # 15. Export + Search controllers (require models)
        if models:
            try:
                files.append(self._render_template_file(
                    "ExportController.java.j2",
                    f"{java_base}/controller/ExportController.java",
                    ctx, models=models,
                ))
            except Exception as _e:
                logger.debug("ExportController.java template failed: %s", _e)

            try:
                files.append(self._render_template_file(
                    "SearchController.java.j2",
                    f"{java_base}/controller/SearchController.java",
                    ctx, models=models,
                ))
            except Exception as _e:
                logger.debug("SearchController.java template failed: %s", _e)

        # 16. Seed data (CommandLineRunner, runs with --spring.profiles.active=seed)
        if models:
            try:
                files.append(self._render_template_file(
                    "SeedData.java.j2",
                    f"{java_base}/config/SeedData.java",
                    ctx, models=models,
                ))
            except Exception as _e:
                logger.debug("SeedData.java template failed: %s", _e)

        # 17. Flyway baseline migration
        if models:
            try:
                files.append(self._render_template_file(
                    "FlywayMigration.sql.j2",
                    "src/main/resources/db/migration/V1__baseline.sql",
                    ctx, models=models,
                ))
            except Exception as _e:
                logger.debug("FlywayMigration.sql template failed: %s", _e)

        # 18. Behavioral + security tests
        if models:
            try:
                files.append(self._render_template_file(
                    "BehavioralTests.java.j2",
                    f"{test_base}/BehavioralTests.java",
                    ctx, models=models, services=bundle.services,
                ))
            except Exception as _e:
                logger.debug("BehavioralTests.java template failed: %s", _e)

        # 19. CI/CD pipeline (GitHub Actions)
        ci_cd_config = getattr(bundle, "ci_cd", None) or {}
        try:
            files.append(self._render_template_file(
                "github_actions.yml.j2",
                ".github/workflows/ci.yml",
                ctx, ci_cd=ci_cd_config,
            ))
        except Exception as _e:
            logger.debug("Java GitHub Actions template failed: %s", _e)

        # 20. Shared bootstrap files (README, Makefile, .env.example)
        files.extend(self._render_java_bootstrap_files(ctx, bundle))

        # One-click deploy manifest
        files.append(GeneratedFile(
            path="railway.json",
            content='{\n  "$schema": "https://railway.app/railway.schema.json",\n'
                    '  "build": {\n    "builder": "DOCKERFILE",\n    "dockerfilePath": "Dockerfile"\n  },\n'
                    '  "deploy": {\n    "numReplicas": 1,\n    "restartPolicyType": "ON_FAILURE",\n'
                    '    "restartPolicyMaxRetries": 10\n  }\n}\n',
        ))

        # 21. Render structured Java/Spring Boot templates from java_spring/ directory
        files.extend(self._render_java_spring_templates(ctx, bundle, java_base, test_base))

        return GeneratedCodeBundle(
            solution_id=bundle.solution_id,
            bundle_id=bundle_id,
            language=self.language,
            spec_hash=bundle.spec_hash,
            files=files,
        )

    def _build_java_model_context(self, bundle):
        """Build Java-specific model context from confirmed fields and services.

        Produces a dict keyed by PascalCase model name, each value containing:
          - fields: list of field dicts with Java-typed names
          - description: model description string
          - enums: dict of enum_name -> [values]
          - business_rules: list of rule dicts (if present)
          - version / hash: spec metadata
        """
        models = {}
        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        business_rules = getattr(bundle, "business_rules", {}) or {}

        # Prefer confirmed fields; fall back to service names when fields are absent
        if confirmed_fields:
            for name, field_defs in confirmed_fields.items():
                pascal_name = _pascal(name)
                enums = {}
                # Managed fields are added by the template — filter them from UML input
                _MANAGED_FIELDS = {"id", "created_at", "updated_at", "deleted_at", "createdat", "updatedat", "deletedat"}
                java_fields = []
                for f in field_defs:
                    _g = f.get if isinstance(f, dict) else lambda k, d=None: getattr(f, k, d)
                    raw_name = _g("name", "")
                    if _snake(raw_name).replace("_", "") in _MANAGED_FIELDS or _g("primary_key", False):
                        continue
                    raw_type = _normalize_type(_g("type", "string"))
                    raw_format = _g("format")
                    java_type = _map_java_type(raw_type, raw_format)
                    field_dict = {
                        "name": _camel(raw_name),
                        "column_name": _pascal_to_snake(raw_name),
                        "type": raw_type,
                        "format": raw_format,
                        "java_type": java_type,
                        "required": _g("required", False),
                        "readonly": _g("readonly", False),
                        "primary_key": _g("name", "") == "id" or _g("primary_key", False),
                        "description": _g("description", ""),
                        "max_length": _g("max_length"),
                        "unique": _g("unique", False),
                        "foreign_key": _g("foreign_key"),
                        "enum_values": _g("enum_values") or _g("enum") or [],
                    }
                    constraints = _g("constraints")
                    if constraints and isinstance(constraints, dict):
                        field_dict["max_length"] = constraints.get("maxLength") or field_dict["max_length"]
                        field_dict["unique"] = constraints.get("unique", False)
                    if raw_type == "enum" and field_dict["enum_values"]:
                        enum_key = f"{pascal_name}{_g('name', '').capitalize()}Enum"
                        enums[enum_key] = field_dict["enum_values"]
                    java_fields.append(field_dict)

                # Attach state machine if present
                state_machines = getattr(bundle, "state_machines", {}) or {}
                sm = state_machines.get(name) or state_machines.get(pascal_name)
                sm_data = None
                if sm:
                    sm_dict = sm if isinstance(sm, dict) else vars(sm) if hasattr(sm, "__dict__") else {}
                    sm_transitions = []
                    for t in sm_dict.get("transitions", []):
                        td = t if isinstance(t, dict) else vars(t) if hasattr(t, "__dict__") else {}
                        sm_transitions.append({
                            "trigger": td.get("trigger", ""),
                            "from_state": td.get("from_state", ""),
                            "to_state": td.get("to_state", ""),
                        })
                    sm_data = {
                        "field_name": sm_dict.get("field_name", "status"),
                        "states": sm_dict.get("states", []),
                        "initial_state": sm_dict.get("initial_state", ""),
                        "transitions": sm_transitions,
                        "is_enum": any(f.get("type") == "enum" for f in field_defs if isinstance(f, dict) and f.get("name") == sm_dict.get("field_name", "status")),
                    }

                # Attach field-level constraints for DTO validation
                for fd in java_fields:
                    orig = next((f for f in field_defs if isinstance(f, dict) and _snake(f.get("name", "")) == fd.get("column_name", "")), None)
                    if orig:
                        fd["constraints"] = orig.get("constraints") or {}

                # Compute table name and API path: PascalCase → snake_case/kebab-case plural
                table_name = _pluralize(_pascal_to_snake(pascal_name))
                endpoint_path = _pluralize(_pascal_to_snake(pascal_name)).replace("_", "-")

                models[pascal_name] = {
                    "fields": java_fields,
                    "table_name": table_name,
                    "endpoint_path": endpoint_path,
                    "description": f"{pascal_name} entity",
                    "enums": enums,
                    "business_rules": business_rules.get(name, []) or [],
                    "state_machine": sm_data,
                    "indexes": [],
                    "version": 0,
                    "hash": "",
                }
        else:
            # No confirmed fields — derive minimal models from service names
            for svc in bundle.services:
                pascal_name = _pascal(svc.name)
                models[pascal_name] = {
                    "fields": [],
                    "description": f"{pascal_name} entity (no confirmed fields)",
                    "enums": {},
                    "business_rules": business_rules.get(svc.name, []) or [],
                    "state_machine": None,
                    "indexes": [],
                    "version": 0,
                    "hash": "",
                }

        return models

    def _render_java_bootstrap_files(self, ctx, bundle):
        """Render shared bootstrap files (README, Makefile, .env.example) for Java projects."""
        files = []
        project_name = _kebab(bundle.solution_name)
        db_name = _snake(bundle.solution_name)
        idp = getattr(bundle, "identity_provider", None) or {}
        integrations = getattr(bundle, "integrations", {}) or {}

        svc_data = []
        for svc in bundle.services:
            paths = []
            for p in svc.paths:
                paths.append({
                    "path": p.path,
                    "method": p.method,
                    "summary": p.summary,
                })
            svc_data.append({"name": svc.name, "tag": svc.tag, "paths": paths})

        mermaid = self._build_mermaid_diagram(bundle.services, integrations)

        bootstrap_ctx = dict(
            ctx,
            project_name=project_name,
            db_name=db_name,
            language=self.language,
            port="8080",
            image_name=project_name,
            test_command="./mvnw test",
            lint_command="./mvnw checkstyle:check",
            migrate_command="./mvnw flyway:migrate",
            seed_command="./mvnw exec:java -Dexec.mainClass=DataSeeder",
            staging_namespace=f"{project_name}-staging",
            prod_namespace=f"{project_name}-prod",
            identity_provider=idp,
            integrations=integrations,
            mermaid_diagram=mermaid,
            services=svc_data,
        )

        files.append(self._render_shared_template("README.md.j2", "README.md", bootstrap_ctx))
        files.append(self._render_shared_template("Makefile.j2", "Makefile", bootstrap_ctx))
        files.append(self._render_shared_template("env_example.j2", ".env.example", bootstrap_ctx))
        return files

    # ── Next.js + shadcn/ui frontend generation ──

    _LUCIDE_ICON_MAP = {
        "work_order": "ClipboardList",
        "customer": "Users",
        "technician": "Wrench",
        "equipment": "Package",
        "time_entry": "Clock",
        "material": "Layers",
        "vendor": "Building",
        "contract": "FileText",
        "purchase_order": "ShoppingCart",
        "invoice": "Receipt",
        "approval": "CheckCircle",
        "tender": "FileSearch",
        "claim": "Shield",
        "policy": "FileCheck",
        "claimant": "UserCheck",
        "assessment": "ClipboardCheck",
        "payment": "CreditCard",
        "patient": "Heart",
        "appointment": "Calendar",
        "provider": "Stethoscope",
        "medical_record": "FileLock",
        "prescription": "Pill",
        "billing": "DollarSign",
        "project": "FolderKanban",
        "task": "CheckSquare",
        "team_member": "User",
        "sprint": "Zap",
        "time_log": "Timer",
        "comment": "MessageSquare",
    }

    def _generate_nextjs_frontend(self, bundle, solution_id=None):
        """Generate a Next.js + shadcn/ui frontend alongside the FastAPI backend.

        Renders into frontend/ subdirectory. Produces:
          - Static shadcn/ui primitives (components/ui/)
          - Layout shell (sidebar + topbar)
          - Per-entity pages (list, detail, create, edit)
          - Dashboard, API client, providers
          - Config files (package.json, tailwind, tsconfig, next.config)
        """
        templates_root = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "templates", "nextjs_shadcn",
        )
        fe_env = Environment(
            loader=FileSystemLoader(templates_root),
            keep_trailing_newline=True,
        )

        files = []
        confirmed = getattr(bundle, "confirmed_fields", {}) or {}
        state_machines = getattr(bundle, "state_machines", {}) or {}
        idp = getattr(bundle, "identity_provider", None) or {}
        solution_slug = _kebab(bundle.solution_name)
        auth_type = idp.get("type", "jwt-local")

        # Build entity → genome module metadata lookup (user_facing + display_name).
        # _genome_modules maps module_key → mod_def; each mod_def may have multiple entities.
        # Explicit genome values take priority; heuristic fallback for legacy genomes.
        _INTERNAL_NAMES = frozenset({
            "audit_log", "audit_trail", "audit_entry", "access_log",
            "permission", "role", "session", "token", "access_token", "refresh_token",
            "config", "configuration", "system_setting", "app_setting",
            "workflow_state", "state_transition", "event_log", "system_log",
            "migration", "schema_version", "feature_flag", "webhook_log",
            "notification_log", "email_log", "background_job", "celery_task",
        })
        _INTERNAL_SUFFIXES = (
            "_log", "_audit", "_config", "_setting", "_token",
            "_permission", "_migration", "_version_history",
        )
        _INTERNAL_PREFIXES = ("system_", "internal_", "audit_", "admin_config_")

        def _is_user_facing_heuristic(snake: str) -> bool:
            if snake in _INTERNAL_NAMES:
                return False
            if any(snake.startswith(p) for p in _INTERNAL_PREFIXES):
                return False
            if any(snake.endswith(s) for s in _INTERNAL_SUFFIXES):
                return False
            return True

        # Build entity_name (snake) → {user_facing, display_name, module_type} from genome modules.
        # Use entities list as the authoritative source of entity names (not fields keys)
        # because modules without field definitions still need user_facing metadata.
        _entity_meta: dict = {}
        for _mk, _md in (getattr(bundle, "_genome_modules", {}) or {}).items():
            _uf_genome = _md.get("user_facing")  # None = not set in genome
            _dn_genome = _md.get("display_name") or _md.get("label") or ""
            _mt_genome = _md.get("module_type") or ""
            # Pipeline modules are always user-facing — they ARE the product's AI features.
            if _mt_genome == "pipeline" and _uf_genome is None:
                _uf_genome = True
            # Primary: iterate entities list (always present, even when no fields defined)
            _entity_names = _md.get("entities") or []
            # Secondary: add any entity keys from fields dict (covers cases where entities list is missing)
            _ef = _md.get("confirmed_fields", {}) or _md.get("fields", {})
            _all_entity_names = list(_entity_names) + [_en for _en in _ef if _en not in _entity_names]
            # Also index by the module key itself (covers single-entity modules)
            if not _all_entity_names:
                _all_entity_names = [_pascal(_mk)]
            for _en in _all_entity_names:
                _es = _snake(_en)
                _uf = _uf_genome if _uf_genome is not None else _is_user_facing_heuristic(_es)
                _entity_meta[_es] = {"user_facing": _uf, "display_name": _dn_genome, "module_type": _mt_genome}
            # Also index by module key so lookup succeeds for single-entity modules
            _uf_mk = _uf_genome if _uf_genome is not None else _is_user_facing_heuristic(_mk)
            _entity_meta[_mk] = {"user_facing": _uf_mk, "display_name": _dn_genome, "module_type": _mt_genome}

        # Build module metadata for sidebar/dashboard
        modules = []
        for entity_name in confirmed:
            pascal_name = _pascal(entity_name)
            snake_name = _snake(entity_name)
            icon = self._LUCIDE_ICON_MAP.get(snake_name, "Box")
            has_sm = pascal_name in state_machines or entity_name in state_machines
            # Build field metadata for Zod schemas + FK pickers
            mod_fields = []
            for f in (confirmed[entity_name] or []):
                _g = f.get if isinstance(f, dict) else lambda k, d=None: getattr(f, k, d)
                ftype = _g("type", "string")
                # Resolve foreign entity metadata for EntityPicker rendering.
                # foreign_key is stored as "table.column" (e.g., "technicians.id").
                _fk_raw = _g("foreign_key") or ""
                _fk_table = str(_fk_raw).split(".")[0] if _fk_raw else ""
                # Infer singular entity name from plural table name (naive de-pluralize).
                _fk_singular = _fk_table[:-1] if _fk_table.endswith("s") else _fk_table
                _foreign_entity = _pascal(_fk_singular) if _fk_singular else ""
                _foreign_slug = _snake(_fk_singular) if _fk_singular else ""
                mod_fields.append({
                    "name": _g("name", ""),
                    "type": ftype,
                    "ts_type": {"string": "string", "text": "string", "integer": "number",
                                "float": "number", "decimal": "number", "boolean": "boolean",
                                "datetime": "string", "date": "string", "enum": "string",
                                "uuid": "string", "json": "any"}.get(ftype, "string"),
                    "required": _g("required", False),
                    "primary_key": _g("name", "") == "id" or _g("primary_key", False),
                    "max_length": _g("max_length"),
                    "enum_values": _g("enum_values") or _g("enum") or [],
                    # FK picker metadata — used by entity_create.tsx.j2 / entity_edit.tsx.j2
                    "foreign_key": bool(_fk_raw),
                    "foreign_entity": _foreign_entity,
                    "foreign_entity_slug": _foreign_slug,
                    "foreign_api_resource": _fk_table,  # plural table name = API resource
                })
            slug_plural = _pluralize(snake_name)
            api_res = _pluralize(_pascal_to_snake(entity_name))
            _meta = _entity_meta.get(snake_name, {})
            _display = _humanize_pascal(pascal_name, _meta.get("display_name"))
            # Pluralize the last word of display_name for nav labels
            _display_words = _display.split()
            if _display_words:
                _display_words[-1] = _pluralize(_display_words[-1])
            _display_plural = " ".join(_display_words)
            modules.append({
                "name": pascal_name,
                "display_name": _display,
                "display_name_plural": _display_plural,
                "slug": snake_name,
                "slug_plural": slug_plural,
                "api_resource": api_res,
                "endpoint_path": api_res.replace("_", "-"),
                "icon": icon,
                "has_state_machine": has_sm,
                "entity_count": 1,
                "fields": mod_fields,
                "module_type": _meta.get("module_type", ""),
                "user_facing": _meta.get("user_facing", _is_user_facing_heuristic(snake_name)),
            })

        # Fallback to services if no confirmed fields
        if not modules:
            for svc in bundle.services:
                snake_name = _snake(svc.name)
                slug_plural = _pluralize(snake_name)
                svc_api_res = _pluralize(_pascal_to_snake(svc.name))
                icon = self._LUCIDE_ICON_MAP.get(snake_name, "Box")
                _svc_pascal = _pascal(svc.name)
                _svc_display = _humanize_pascal(_svc_pascal)
                _svc_words = _svc_display.split()
                if _svc_words:
                    _svc_words[-1] = _pluralize(_svc_words[-1])
                modules.append({
                    "name": _svc_pascal,
                    "display_name": _svc_display,
                    "display_name_plural": " ".join(_svc_words),
                    "slug": snake_name,
                    "slug_plural": slug_plural,
                    "api_resource": svc_api_res,
                    "endpoint_path": svc_api_res.replace("_", "-"),
                    "icon": icon,
                    "has_state_machine": False,
                    "entity_count": 1,
                    "user_facing": True,
                })

        # Safety net: if ALL modules are non-user-facing, elevate the first 6 to user_facing=True.
        # This prevents a completely empty sidebar (better a few service pages than none at all).
        if modules and not any(m.get("user_facing") for m in modules):
            for m in modules[:6]:
                m["user_facing"] = True

        _prod = getattr(bundle, "product_config", {}) or {}
        _problem = getattr(bundle, "problem", {}) or {}
        base_ctx = {
            "solution_name": bundle.solution_name,
            "solution_slug": solution_slug,
            "auth": auth_type,
            "modules": modules,
            "roles": idp.get("roles", ["admin", "user"]),
            # Product branding (from wizard product section)
            "tagline": _prod.get("tagline") or "",
            "primary_color": _prod.get("primary_color") or "#2563eb",
            "accent_color": _prod.get("accent_color") or "#7c3aed",
            "support_email": _prod.get("support_email") or "",
            "terms_url": _prod.get("terms_url") or "",
            "privacy_url": _prod.get("privacy_url") or "",
            # Problem context for marketing copy
            "problem_statement": _problem.get("statement") or "",
        }
        _sid = solution_id or getattr(bundle, "solution_id", None)
        base_ctx["base_path"] = f"/apps/{_sid}" if _sid else ""

        # ── Static config files ──
        for tpl, out in [
            ("package.json.j2", "frontend/package.json"),
            ("tailwind.config.ts.j2", "frontend/tailwind.config.ts"),
            ("tsconfig.json.j2", "frontend/tsconfig.json"),
            ("next.config.js.j2", "frontend/next.config.js"),
            ("postcss.config.js.j2", "frontend/postcss.config.js"),
            ("Dockerfile.j2", "frontend/Dockerfile"),
            ("app/globals.css.j2", "frontend/app/globals.css"),
            ("app/providers.tsx.j2", "frontend/app/providers.tsx"),
            ("app/error.tsx.j2", "frontend/app/error.tsx"),
            ("app/loading.tsx.j2", "frontend/app/loading.tsx"),
            ("lib/utils.ts.j2", "frontend/lib/utils.ts"),
        ]:
            try:
                tmpl = fe_env.get_template(tpl)
                content = tmpl.render(**base_ctx)
                if content and content.strip():
                    files.append(GeneratedFile(path=out, content=content))
            except Exception as e:
                logger.debug("Next.js template %s skipped: %s", tpl, e)

        # ── Auth: middleware + v2 auth context + require_auth (login page is in jwt-local block below) ──
        for tpl, out in [
            ("app/login/layout.tsx.j2", "frontend/app/login/layout.tsx"),
            ("middleware_auth.ts.j2", "frontend/middleware.ts"),
            ("lib/auth_context.tsx.j2", "frontend/lib/auth-context.tsx"),
            ("components/require_auth.tsx.j2", "frontend/components/require-auth.tsx"),
            ("components/entity_picker.tsx.j2", "frontend/components/entity-picker.tsx"),
        ]:
            try:
                tmpl = fe_env.get_template(tpl)
                content = tmpl.render(**base_ctx)
                if content and content.strip():
                    files.append(GeneratedFile(path=out, content=content))
            except Exception as e:
                logger.debug("Auth template %s skipped: %s", tpl, e)

        # ── v2 layouts: root layout with AuthProvider, protected layout with sidebar ──
        entity_slugs = [_snake(e) for e in confirmed]
        for tpl, out in [
            ("app/layout_root.tsx.j2", "frontend/app/layout.tsx"),
            ("app/not_found.tsx.j2", "frontend/app/not-found.tsx"),
        ]:
            try:
                tmpl = fe_env.get_template(tpl)
                content = tmpl.render(**base_ctx, entity_slugs=entity_slugs)
                if content and content.strip():
                    files.append(GeneratedFile(path=out, content=content))
            except Exception as e:
                logger.debug("v2 layout template %s skipped: %s", tpl, e)

        try:
            tmpl = fe_env.get_template("app/protected_layout.tsx.j2")
            content = tmpl.render(**base_ctx, entity_slugs=entity_slugs)
            if content and content.strip():
                files.append(GeneratedFile(
                    path="frontend/app/(protected)/layout.tsx",
                    content=content,
                ))
        except Exception as e:
            logger.debug("Protected layout skipped: %s", e)

        # ── Static shadcn/ui components ──
        for comp in [
            "button", "badge", "input", "table", "card", "skeleton",
            "separator", "label", "textarea", "select", "dialog",
            "dropdown-menu", "switch", "tooltip", "avatar", "toast", "form",
            "command", "popover", "progress", "tabs",
        ]:
            tpl_name = f"components/ui/{comp}.tsx.j2"
            try:
                tmpl = fe_env.get_template(tpl_name)
                content = tmpl.render(**base_ctx)
                if content and content.strip():
                    files.append(GeneratedFile(
                        path=f"frontend/components/ui/{comp}.tsx",
                        content=content,
                    ))
            except Exception as e:
                logger.debug("shadcn component %s skipped: %s", comp, e)

        # ── Data table component ──
        try:
            tmpl = fe_env.get_template("components/data-table.tsx.j2")
            content = tmpl.render(**base_ctx)
            if content and content.strip():
                files.append(GeneratedFile(
                    path="frontend/components/data-table.tsx",
                    content=content,
                ))
        except Exception as e:
            logger.debug("Data table skipped: %s", e)

        # ── Zod schemas from confirmed fields ──
        try:
            tmpl = fe_env.get_template("lib/schemas.ts.j2")
            content = tmpl.render(**base_ctx)
            if content and content.strip():
                files.append(GeneratedFile(
                    path="frontend/lib/schemas.ts",
                    content=content,
                ))
        except Exception as e:
            logger.debug("Zod schemas skipped: %s", e)

        # ── Auth pages (jwt-local only) ──
        if auth_type == "jwt-local":
            for _auth_tpl, _auth_out in [
                ("app/login/page.tsx.j2",             "frontend/app/login/page.tsx"),
                ("app/forgot-password/page.tsx.j2",   "frontend/app/forgot-password/page.tsx"),
                ("app/reset-password/page.tsx.j2",    "frontend/app/reset-password/page.tsx"),
            ]:
                try:
                    tmpl = fe_env.get_template(_auth_tpl)
                    content = tmpl.render(**base_ctx)
                    if content and content.strip():
                        files.append(GeneratedFile(path=_auth_out, content=content))
                except Exception as e:
                    logger.debug("Auth page %s skipped: %s", _auth_tpl, e)

        # ── Layout shell (sidebar, topbar) ──
        for tpl, out in [
            ("components/layout/sidebar.tsx.j2", "frontend/components/layout/sidebar.tsx"),
            ("components/layout/topbar.tsx.j2", "frontend/components/layout/topbar.tsx"),
        ]:
            try:
                tmpl = fe_env.get_template(tpl)
                content = tmpl.render(**base_ctx)
                if content and content.strip():
                    files.append(GeneratedFile(path=out, content=content))
            except Exception as e:
                logger.debug("Layout template %s skipped: %s", tpl, e)

        # ── Marketing landing page at root "/" ──
        try:
            tmpl = fe_env.get_template("app/marketing_page.tsx.j2")
            files.append(GeneratedFile(
                path="frontend/app/page.tsx",
                content=tmpl.render(**base_ctx),
            ))
        except Exception as e:
            logger.debug("Marketing page skipped: %s", e)

        # ── Standalone admin preview HTML (no build step, served by Entelim preview tab) ──
        try:
            tmpl = fe_env.get_template("admin_preview.html.j2")
            files.append(GeneratedFile(
                path="frontend/admin.html",
                content=tmpl.render(**base_ctx),
            ))
        except Exception as e:
            logger.debug("Admin preview HTML skipped: %s", e)

        # ── Dashboard at "/dashboard" (inside (protected) route group) ──
        try:
            tmpl = fe_env.get_template("app/page.tsx.j2")
            files.append(GeneratedFile(
                path="frontend/app/(protected)/dashboard/page.tsx",
                content=tmpl.render(**base_ctx),
            ))
        except Exception as e:
            logger.debug("Dashboard page skipped: %s", e)

        # ── API client ──
        try:
            tmpl = fe_env.get_template("lib/api.ts.j2")
            files.append(GeneratedFile(
                path="frontend/lib/api.ts",
                content=tmpl.render(**base_ctx),
            ))
        except Exception as e:
            logger.debug("API client skipped: %s", e)

        # ── Per-entity pages ──
        list_tmpl = fe_env.get_template("app/entity_list.tsx.j2")
        form_tmpl = fe_env.get_template("app/entity_form.tsx.j2")
        detail_tmpl = fe_env.get_template("app/entity_detail.tsx.j2")
        # v2 create/edit templates (fall back to entity_form.tsx.j2 if not available)
        try:
            create_tmpl_v2 = fe_env.get_template("app/entity_create.tsx.j2")
        except Exception:
            create_tmpl_v2 = None
        try:
            edit_tmpl_v2 = fe_env.get_template("app/entity_edit.tsx.j2")
        except Exception:
            edit_tmpl_v2 = None

        _genome_modules_fe = getattr(bundle, "_genome_modules", {}) or {}

        # Pre-load pipeline frontend templates
        try:
            _pipeline_upload_tmpl = fe_env.get_template("app/pipeline_upload.tsx.j2")
        except Exception:
            _pipeline_upload_tmpl = None
        try:
            _pipeline_result_tmpl = fe_env.get_template("app/pipeline_result.tsx.j2")
        except Exception:
            _pipeline_result_tmpl = None

        for entity_name, fields in confirmed.items():
            pascal_name = _pascal(entity_name)
            snake_name = _snake(entity_name)

            # Check if this entity belongs to a pipeline module — render pipeline
            # pages (upload + result) instead of CRUD pages (list/create/detail/edit)
            _pipeline_mod = None
            for _mk, _md in _genome_modules_fe.items():
                if _md.get("module_type") == "pipeline" and (
                    _pascal(_mk) == pascal_name
                    or _md.get("aggregate_root") == pascal_name
                    or _mk == entity_name
                ):
                    _pipeline_mod = _md
                    break

            if _pipeline_mod and _pipeline_upload_tmpl and _pipeline_result_tmpl:
                api_resource = _pluralize(_pascal_to_snake(entity_name))
                # Use actual module key from _genome_modules_fe iteration
                _actual_mk = next(
                    (k for k, v in _genome_modules_fe.items() if v is _pipeline_mod), snake_name
                )
                pipeline_ctx = {
                    **base_ctx,
                    "entity_name": pascal_name,
                    "entity_slug": snake_name,
                    "display_name": _pipeline_mod.get("display_name") or pascal_name,
                    "screen_title": _pipeline_mod.get("display_name") or pascal_name,
                    "screen_description": _pipeline_mod.get("description") or f"Upload a document for {_pipeline_mod.get('display_name', pascal_name)} processing",
                    "api_resource": api_resource,
                    "module_key": _actual_mk,
                    "module": _pipeline_mod,
                    "fields": [],
                }
                files.append(GeneratedFile(
                    path=f"frontend/app/(protected)/{snake_name}/page.tsx",
                    content=_pipeline_upload_tmpl.render(**pipeline_ctx),
                ))
                files.append(GeneratedFile(
                    path=f"frontend/app/(protected)/{snake_name}/[id]/page.tsx",
                    content=_pipeline_result_tmpl.render(**pipeline_ctx),
                ))
                continue  # skip CRUD pages for this entity

            # Get state machine info
            sm = state_machines.get(pascal_name) or state_machines.get(entity_name)
            has_sm = sm is not None
            sm_field = "status"
            transitions = []
            if sm:
                sm_field = sm.field_name if hasattr(sm, "field_name") else (sm.get("field", "status"))
                raw_trans = sm.transitions if hasattr(sm, "transitions") else (sm.get("transitions") or [])
                seen = set()
                for t in raw_trans:
                    trigger = t.trigger if hasattr(t, "trigger") else t.get("trigger", "")
                    from_s = t.from_state if hasattr(t, "from_state") else t.get("from", "")
                    to_s = t.to_state if hasattr(t, "to_state") else t.get("to", "")
                    if trigger and trigger not in seen:
                        seen.add(trigger)
                        transitions.append({"trigger": trigger, "from_state": from_s, "to_state": to_s})

            # Build field list for templates
            # Pre-compute lowercase→original lookup for FK entity inference (Fix B)
            _confirmed_lower = {k.lower(): k for k in confirmed}
            entity_fields = []
            for f in (fields or []):
                _g = f.get if isinstance(f, dict) else lambda k, d=None: getattr(f, k, d)
                ftype = _g("type", "string")
                # Fix B: resolve foreign_entity for FK fields
                # Strategy 1: explicit foreign_key reference string e.g. "technicians.id"
                # Strategy 2: infer from _id suffix — "technician_id" → entity "Technician"
                fe_val = _g("foreign_entity")
                fk_val = _g("foreign_key") or ""
                if not fe_val and fk_val and "." in fk_val:
                    fe_val = _pascal(fk_val.split(".")[0])  # "technicians.id" → "Technician"
                if not fe_val:
                    field_name = _g("name", "")
                    if field_name.endswith("_id") and field_name != "id":
                        candidate = field_name[:-3]  # strip "_id" → "technician"
                        candidate_pascal = _pascal(candidate)  # "Technician"
                        # Match against confirmed dict keys (may be snake_case or PascalCase)
                        if candidate.lower() in _confirmed_lower or candidate_pascal.lower() in _confirmed_lower:
                            fe_val = candidate_pascal
                            # Synthesize foreign_key reference when not explicitly set
                            if not fk_val:
                                fk_val = _pluralize(_pascal_to_snake(fe_val)) + ".id"
                entity_fields.append({
                    "name": _g("name", ""),
                    "type": ftype,
                    "ts_type": {"string": "string", "text": "string", "integer": "number",
                                "float": "number", "decimal": "number", "boolean": "boolean",
                                "datetime": "string", "date": "string", "enum": "string",
                                "uuid": "string", "json": "any"}.get(ftype, "string"),
                    "required": _g("required", False),
                    "primary_key": _g("name", "") == "id" or _g("primary_key", False),
                    "nullable": not _g("required", False),
                    "max_length": _g("max_length"),
                    "enum_values": _g("enum_values") or _g("enum") or [],
                    "foreign_key": fk_val or None,
                    "foreign_entity": fe_val,
                    # Correct snake_case singular for EntityPicker entityType prop
                    "foreign_entity_slug": _pascal_to_snake(fe_val) if fe_val else None,
                    # Correct plural API resource path (e.g. "work_orders") for EntityPicker apiResource prop
                    "foreign_api_resource": _pluralize(_pascal_to_snake(fe_val)) if fe_val else None,
                })

            # Fix A: inject state machine states into the state_machine_field's enum_values
            # so the template renders a <Select> instead of <Input> for the status field
            if sm and sm_field:
                sm_states = []
                raw_states = sm.states if hasattr(sm, "states") else sm.get("states") or []
                for s in raw_states:
                    sm_states.append(s if isinstance(s, str) else s.get("name", str(s)))
                for ef in entity_fields:
                    if ef["name"] == sm_field and not ef.get("enum_values"):
                        ef["enum_values"] = sm_states

            # Fix C: inject known enum_values for common field names when missing
            # Covers deterministic (UML) mode where enum_values aren't in the snapshot
            _KNOWN_ENUMS = {
                "priority": ["low", "medium", "high", "critical"],
                "severity": ["low", "medium", "high", "critical"],
                "urgency": ["low", "medium", "high", "critical"],
                "status": ["draft", "active", "completed", "archived"],
            }
            for ef in entity_fields:
                if not ef.get("enum_values") and ef["name"] in _KNOWN_ENUMS:
                    ef["enum_values"] = _KNOWN_ENUMS[ef["name"]]

            # api_resource = correct snake_case plural for REST endpoint
            # _snake("WorkOrder") = "workorder" (wrong for API paths)
            # _pascal_to_snake("WorkOrder") = "work_order" → pluralizes to "work_orders" (correct)
            api_resource = _pluralize(_pascal_to_snake(entity_name))
            # Human-readable display names for UI headings and labels
            _ent_meta = _entity_meta.get(snake_name, {})
            _ent_display = _humanize_pascal(pascal_name, _ent_meta.get("display_name"))
            _ent_display_words = _ent_display.split()
            if _ent_display_words:
                _ent_display_words[-1] = _pluralize(_ent_display_words[-1])
            _ent_display_plural = " ".join(_ent_display_words)
            entity_ctx = {
                **base_ctx,
                "entity_name": pascal_name,
                "entity_slug": snake_name,
                "api_resource": api_resource,
                "fields": entity_fields,
                "has_state_machine": has_sm,
                "state_machine_field": sm_field,
                "transitions": transitions,
                "display_name": _ent_display,
                "display_name_plural": _ent_display_plural,
            }

            # List page — inside (protected) route group so layout.tsx sidebar applies
            files.append(GeneratedFile(
                path=f"frontend/app/(protected)/{snake_name}/page.tsx",
                content=list_tmpl.render(**entity_ctx),
            ))

            # Create form (prefer v2 template with Zod + field-type rendering)
            if create_tmpl_v2:
                files.append(GeneratedFile(
                    path=f"frontend/app/(protected)/{snake_name}/new/page.tsx",
                    content=create_tmpl_v2.render(**entity_ctx),
                ))
            else:
                create_ctx = {**entity_ctx, "is_edit": False}
                files.append(GeneratedFile(
                    path=f"frontend/app/(protected)/{snake_name}/new/page.tsx",
                    content=form_tmpl.render(**create_ctx),
                ))

            # Detail page
            files.append(GeneratedFile(
                path=f"frontend/app/(protected)/{snake_name}/[id]/page.tsx",
                content=detail_tmpl.render(**entity_ctx),
            ))

            # Edit form (prefer v2 template with pre-populated fields + PATCH)
            if edit_tmpl_v2:
                files.append(GeneratedFile(
                    path=f"frontend/app/(protected)/{snake_name}/[id]/edit/page.tsx",
                    content=edit_tmpl_v2.render(**entity_ctx),
                ))
            else:
                edit_ctx = {**entity_ctx, "is_edit": True}
                files.append(GeneratedFile(
                    path=f"frontend/app/(protected)/{snake_name}/[id]/edit/page.tsx",
                    content=form_tmpl.render(**edit_ctx),
                ))

        # ── Admin pages (conditional on admin_panel capability) ──
        _has_admin = any(m.get("slug") == "admin_panel" or "admin" in str(m) for m in modules)
        # Also check bundle-level capability flags
        _bundle_nfrs = set()
        for nfr in (getattr(bundle, "infra_context", None) or InfraContext()).nfrs or []:
            if isinstance(nfr, dict) and nfr.get("flag"):
                _bundle_nfrs.add(nfr["flag"])

        # ── Admin dashboard (shadcn/ui, always generated) ──
        _render_admin = True
        if _render_admin:
            admin_ctx = {**base_ctx}
            for tpl, out in [
                ("app/admin/page.tsx.j2", "frontend/app/(protected)/admin/page.tsx"),
                ("app/account/page.tsx.j2", "frontend/app/(protected)/account/page.tsx"),
            ]:
                try:
                    tmpl = fe_env.get_template(tpl)
                    content = tmpl.render(**admin_ctx)
                    if content and content.strip():
                        files.append(GeneratedFile(path=out, content=content))
                except Exception as e:
                    logger.debug("Admin template %s skipped: %s", tpl, e)

        # ── Test infrastructure (Jest + Playwright) ──
        for tpl, out in [
            ("jest.config.ts.j2", "frontend/jest.config.ts"),
            ("jest.setup.ts.j2", "frontend/jest.setup.ts"),
            ("playwright.config.ts.j2", "frontend/playwright.config.ts"),
            ("__tests__/auth.test.tsx.j2", "frontend/__tests__/auth.test.tsx"),
            ("__tests__/accessibility.test.tsx.j2", "frontend/__tests__/accessibility.test.tsx"),
            ("e2e/auth.spec.ts.j2", "frontend/e2e/auth.spec.ts"),
        ]:
            try:
                tmpl = fe_env.get_template(tpl)
                content = tmpl.render(**base_ctx)
                if content and content.strip():
                    files.append(GeneratedFile(path=out, content=content))
            except Exception as e:
                logger.debug("Next.js test template %s skipped: %s", tpl, e)

        # Per-entity component tests + E2E journeys
        for entity_name, fields in confirmed.items():
            pascal_name = _pascal(entity_name)
            snake_name = _snake(entity_name)
            slug_plural = _pluralize(snake_name)
            entity_fields = []
            for f in (fields or []):
                _g = f.get if isinstance(f, dict) else lambda k, d=None: getattr(f, k, d)
                ftype = _g("type", "string")
                entity_fields.append({
                    "name": _g("name", ""),
                    "type": ftype,
                    "required": _g("required", False),
                })
            test_entity_ctx = {
                **base_ctx,
                "entity_name": pascal_name,
                "resource_name": slug_plural.replace("_", "-"),
                "confirmed_fields": entity_fields,
            }
            for tpl, out in [
                ("__tests__/[resource].test.tsx.j2", f"frontend/__tests__/{snake_name}.test.tsx"),
                ("e2e/[resource].spec.ts.j2", f"frontend/e2e/{snake_name}.spec.ts"),
            ]:
                try:
                    tmpl = fe_env.get_template(tpl)
                    content = tmpl.render(**test_entity_ctx)
                    if content and content.strip():
                        files.append(GeneratedFile(path=out, content=content))
                except Exception as e:
                    logger.debug("Next.js per-entity test template %s skipped: %s", tpl, e)

        # ── ArchiMate LLM context docs for the frontend ──
        try:
            import os as _fe_os
            _shared_dir = _fe_os.path.join(
                _fe_os.path.dirname(_fe_os.path.dirname(__file__)), "templates", "shared",
            )
            from jinja2 import Environment as _J2Env, FileSystemLoader as _FSLFE
            _shared_env = _J2Env(loader=_FSLFE(_shared_dir), keep_trailing_newline=True)
            _motivation = self._build_motivation_context(bundle.solution_id) or {}
            _entity_fe = [
                {"name": _pascal(e), "snake_name": _snake(e), "table": _pluralize(_snake(e)), "key_fields": ""}
                for e in (getattr(bundle, "confirmed_fields", {}) or {}).keys()
            ]
            _fe_shared_ctx = {
                **base_ctx,
                "solution_name": bundle.solution_name,
                "solution_id": bundle.solution_id,
                "spec_hash": bundle.spec_hash,
                "constraints": _motivation.get("constraints", []),
                "principles": _motivation.get("principles", []),
                "requirements": _motivation.get("requirements", []),
                "stakeholders": _motivation.get("stakeholders", []),
                "meanings": _motivation.get("meanings", []),
                "problem_statement": _motivation.get("problem_statement", ""),
                "services": [{"name": s.name, "paths": s.paths} for s in (bundle.services or [])],
                "entities": _entity_fe,
                "entity_table": _entity_fe,
                "process_flows": getattr(bundle, "business_process_flows", {}) or {},
                "has_kafka": False,
                "has_grpc": False,
                "has_business_rules": False,
                "is_mobile": False,
                "has_frontend": True,
                "app_name": bundle.solution_name,
            }
            for _tpl_name, _out_path in [
                ("PRINCIPLES.md.j2", "frontend/PRINCIPLES.md"),
                ("DOMAIN.md.j2", "frontend/DOMAIN.md"),
                ("DEPENDENCIES.md.j2", "frontend/DEPENDENCIES.md"),
            ]:
                try:
                    _content = _shared_env.get_template(_tpl_name).render(**_fe_shared_ctx)
                    if _content and _content.strip():
                        files.append(GeneratedFile(path=_out_path, content=_content))
                except Exception as _fte:
                    logger.debug("Frontend LLM doc %s skipped: %s", _tpl_name, _fte)
        except Exception as _fe_e:
            logger.debug("Frontend ArchiMate LLM docs skipped: %s", _fe_e)

        # ── GitHub Actions CI workflow — mandatory output ──
        try:
            _ci_tpl = fe_env.get_template(".github/workflows/ci.yml.j2")
            _ci_content = _ci_tpl.render(**base_ctx)
            if _ci_content and _ci_content.strip():
                files.append(GeneratedFile(
                    path="frontend/.github/workflows/ci.yml",
                    content=_ci_content,
                ))
        except Exception as _ci_e:
            logger.debug("CI workflow template skipped: %s", _ci_e)

        return files

    # ── React Native / Expo generation ──

    # TypeScript type mapping for React Native Zod schemas
    _TS_TYPE_MAP = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "array": "list",
        "object": "dict",
    }

    def _generate_react_shadcn(self, bundle):
        """Generate a standalone Next.js + shadcn/ui frontend from a ProductSpecBundle."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        bundle_id = f"fe_{now}"
        files = self._generate_nextjs_frontend(bundle)
        return GeneratedCodeBundle(
            solution_id=bundle.solution_id,
            bundle_id=bundle_id,
            language="react-shadcn",
            spec_hash=bundle.spec_hash,
            files=files,
        )

    def _generate_flask_nextjs(self, bundle, solution_id=None):
        """Generate Flask + Next.js 14 + shadcn/ui full-stack project.

        Backend: Flask Blueprints + Flask-SQLAlchemy + Marshmallow + Flask-JWT-Extended
        Frontend: Next.js 14 App Router + shadcn/ui + Tailwind (in frontend/ subdirectory)

        Reuses _generate_flask() for the backend and _generate_nextjs_frontend() for
        the frontend — same output quality as react-shadcn but with Flask instead of FastAPI.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        bundle_id = f"flask_nextjs_{now}"

        # Backend: full Flask project
        flask_result = self._generate_flask(bundle)
        files = list(flask_result.files)

        # Frontend: Next.js 14 + shadcn/ui in frontend/ subdirectory
        try:
            frontend_files = self._generate_nextjs_frontend(bundle, solution_id=solution_id)
            files.extend(frontend_files)
        except Exception as _fe_err:
            logger.warning("flask-nextjs: Next.js frontend generation failed (non-fatal): %s", _fe_err)

        # Flask-specific CORS config so Next.js dev server can reach the API
        files.append(GeneratedFile(path="CORS_SETUP.md", content=(
            "# CORS Setup for Flask + Next.js\n\n"
            "Flask needs CORS enabled for the Next.js dev server (`localhost:3000`) to call the API.\n\n"
            "```bash\npip install Flask-Cors\n```\n\n"
            "In `app/__init__.py`, after creating the Flask app:\n\n"
            "```python\nfrom flask_cors import CORS\nCORS(app, origins=['http://localhost:3000'], supports_credentials=True)\n```\n\n"
            "## Running both servers\n\n"
            "```bash\n# Terminal 1 — Flask API on :5000\npython wsgi.py\n\n"
            "# Terminal 2 — Next.js frontend on :3000\ncd frontend && npm install && npm run dev\n```\n\n"
            "The Next.js frontend calls the Flask API at `http://localhost:5000/api`.\n"
            "In production, deploy Flask behind nginx and serve Next.js as a static export or on a separate origin.\n"
        )))

        return GeneratedCodeBundle(
            solution_id=bundle.solution_id,
            bundle_id=bundle_id,
            language="flask-nextjs",
            spec_hash=bundle.spec_hash,
            files=files,
        )

    def _generate_flask_react(self, bundle):
        """Generate Flask + React 18 SPA (Vite) full-stack project.

        Backend: Flask Blueprints + Flask-SQLAlchemy + Marshmallow + Flask-JWT-Extended
        Frontend: Vite + React 18 + TypeScript + shadcn/ui + React Router v6 (in frontend/ subdirectory)

        Generates a complete SPA with per-entity CRUD pages and a typed API client
        that points to the Flask backend.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        bundle_id = f"flask_react_{now}"

        # Backend: full Flask project
        flask_result = self._generate_flask(bundle)
        files = list(flask_result.files)

        # Frontend: Vite + React SPA
        try:
            react_files = self._generate_vite_react_spa(bundle)
            files.extend(react_files)
        except Exception as _fe_err:
            logger.warning("flask-react: Vite React SPA generation failed (non-fatal): %s", _fe_err)

        files.append(GeneratedFile(path="CORS_SETUP.md", content=(
            "# CORS Setup for Flask + React (Vite)\n\n"
            "Flask needs CORS enabled for the Vite dev server (`localhost:5173`) to call the API.\n\n"
            "```bash\npip install Flask-Cors\n```\n\n"
            "In `app/__init__.py`, after creating the Flask app:\n\n"
            "```python\nfrom flask_cors import CORS\nCORS(app, origins=['http://localhost:5173'], supports_credentials=True)\n```\n\n"
            "## Running both servers\n\n"
            "```bash\n# Terminal 1 — Flask API on :5000\npython wsgi.py\n\n"
            "# Terminal 2 — React SPA on :5173\ncd frontend && npm install && npm run dev\n```\n\n"
            "The Vite dev server proxies `/api` requests to Flask (configured in `frontend/vite.config.ts`).\n"
        )))

        return GeneratedCodeBundle(
            solution_id=bundle.solution_id,
            bundle_id=bundle_id,
            language="flask-react",
            spec_hash=bundle.spec_hash,
            files=files,
        )

    def _generate_vite_react_spa(self, bundle):
        """Generate Vite + React 18 + TypeScript + shadcn/ui SPA files.

        Produces a complete React SPA in the frontend/ subdirectory with:
        - Vite config (with /api proxy to Flask :5000)
        - React Router v6 with per-entity routes
        - Per-entity list, detail, and create/edit pages
        - Typed API client (fetch wrapper pointing to /api)
        - shadcn/ui + Tailwind CSS setup
        - Auth context with JWT token storage
        """
        import json as _json_mod
        def _json_dumps(obj, **kw):
            return _json_mod.dumps(obj, **kw)
        files = []
        solution_slug = _kebab(bundle.solution_name)
        services = bundle.services or []

        # Build entity contexts (same shape used by Next.js generator)
        entities = []
        for svc in services:
            name = svc.name or ""
            tag = svc.tag or name
            slug = _kebab(tag)
            pascal = _pascal(tag)
            entities.append({"name": name, "slug": slug, "pascal": pascal, "tag": tag})

        # ── package.json ──
        files.append(GeneratedFile(
            path="frontend/package.json",
            content=_json_dumps({
                "name": f"{solution_slug}-frontend",
                "version": "0.1.0",
                "private": True,
                "type": "module",
                "scripts": {
                    "dev": "vite",
                    "build": "tsc && vite build",
                    "preview": "vite preview",
                    "lint": "eslint src --ext ts,tsx --report-unused-disable-directives --max-warnings 0",
                },
                "dependencies": {
                    "react": "^18.3.1",
                    "react-dom": "^18.3.1",
                    "react-router-dom": "^6.23.1",
                    "@radix-ui/react-dialog": "^1.1.1",
                    "@radix-ui/react-dropdown-menu": "^2.1.1",
                    "@radix-ui/react-label": "^2.1.0",
                    "@radix-ui/react-select": "^2.1.1",
                    "@radix-ui/react-slot": "^1.1.0",
                    "@radix-ui/react-toast": "^1.2.1",
                    "class-variance-authority": "^0.7.0",
                    "clsx": "^2.1.1",
                    "lucide-react": "^0.400.0",
                    "tailwind-merge": "^2.3.0",
                    "tailwindcss-animate": "^1.0.7",
                    "zod": "^3.23.8",
                },
                "devDependencies": {
                    "@types/react": "^18.3.3",
                    "@types/react-dom": "^18.3.0",
                    "@vitejs/plugin-react": "^4.3.1",
                    "autoprefixer": "^10.4.19",
                    "eslint": "^8.57.0",
                    "eslint-plugin-react-hooks": "^4.6.2",
                    "eslint-plugin-react-refresh": "^0.4.7",
                    "postcss": "^8.4.38",
                    "tailwindcss": "^3.4.4",
                    "typescript": "^5.4.5",
                    "vite": "^5.3.1",
                },
            }, indent=2),
        ))

        # ── vite.config.ts ──
        files.append(GeneratedFile(
            path="frontend/vite.config.ts",
            content=(
                "import { defineConfig } from 'vite'\n"
                "import react from '@vitejs/plugin-react'\n"
                "import path from 'path'\n\n"
                "export default defineConfig({\n"
                "  plugins: [react()],\n"
                "  resolve: {\n"
                "    alias: { '@': path.resolve(__dirname, './src') },\n"
                "  },\n"
                "  server: {\n"
                "    port: 5173,\n"
                "    proxy: {\n"
                "      '/api': {\n"
                "        target: 'http://localhost:5000',\n"
                "        changeOrigin: true,\n"
                "      },\n"
                "    },\n"
                "  },\n"
                "})\n"
            ),
        ))

        # ── index.html ──
        files.append(GeneratedFile(
            path="frontend/index.html",
            content=(
                "<!doctype html>\n<html lang=\"en\">\n  <head>\n"
                "    <meta charset=\"UTF-8\" />\n"
                "    <link rel=\"icon\" type=\"image/svg+xml\" href=\"/vite.svg\" />\n"
                "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />\n"
                f"    <title>{html.escape(bundle.solution_name)}</title>\n"
                "  </head>\n  <body>\n"
                "    <div id=\"root\"></div>\n"
                "    <script type=\"module\" src=\"/src/main.tsx\"></script>\n"
                "  </body>\n</html>\n"
            ),
        ))

        # ── tsconfig.json ──
        files.append(GeneratedFile(
            path="frontend/tsconfig.json",
            content=_json_dumps({
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
                    "noUnusedLocals": True,
                    "noUnusedParameters": True,
                    "noFallthroughCasesInSwitch": True,
                    "baseUrl": ".",
                    "paths": {"@/*": ["./src/*"]},
                },
                "include": ["src"],
                "references": [{"path": "./tsconfig.node.json"}],
            }, indent=2),
        ))

        # ── tailwind.config.js ──
        (", ".join([
            f'"./src/pages/{e["pascal"]}/**/*.{{ts,tsx}}"' for e in entities
        ]) if entities else "")
        files.append(GeneratedFile(
            path="frontend/tailwind.config.js",
            content=(
                "/** @type {import('tailwindcss').Config} */\n"
                "export default {\n"
                "  darkMode: ['class'],\n"
                "  content: [\n"
                "    './index.html',\n"
                "    './src/**/*.{ts,tsx,js,jsx}',\n"
                "  ],\n"
                "  theme: {\n"
                "    extend: {\n"
                "      colors: {\n"
                "        border: 'hsl(var(--border))',\n"
                "        input: 'hsl(var(--input))',\n"
                "        ring: 'hsl(var(--ring))',\n"
                "        background: 'hsl(var(--background))',\n"
                "        foreground: 'hsl(var(--foreground))',\n"
                "        primary: {\n"
                "          DEFAULT: 'hsl(var(--primary))',\n"
                "          foreground: 'hsl(var(--primary-foreground))',\n"
                "        },\n"
                "        muted: {\n"
                "          DEFAULT: 'hsl(var(--muted))',\n"
                "          foreground: 'hsl(var(--muted-foreground))',\n"
                "        },\n"
                "        card: {\n"
                "          DEFAULT: 'hsl(var(--card))',\n"
                "          foreground: 'hsl(var(--card-foreground))',\n"
                "        },\n"
                "      },\n"
                "      borderRadius: {\n"
                "        lg: 'var(--radius)',\n"
                "        md: 'calc(var(--radius) - 2px)',\n"
                "        sm: 'calc(var(--radius) - 4px)',\n"
                "      },\n"
                "    },\n"
                "  },\n"
                "  plugins: [require('tailwindcss-animate')],\n"
                "}\n"
            ),
        ))

        # ── components.json (shadcn/ui registry) ──
        files.append(GeneratedFile(
            path="frontend/components.json",
            content=_json_dumps({
                "$schema": "https://ui.shadcn.com/schema.json",
                "style": "default",
                "rsc": False,
                "tsx": True,
                "tailwind": {
                    "config": "tailwind.config.js",
                    "css": "src/index.css",
                    "baseColor": "slate",
                    "cssVariables": True,
                    "prefix": "",
                },
                "aliases": {
                    "components": "@/components",
                    "utils": "@/lib/utils",
                },
            }, indent=2),
        ))

        # ── src/index.css ──
        files.append(GeneratedFile(
            path="frontend/src/index.css",
            content=(
                "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n\n"
                "@layer base {\n"
                "  :root {\n"
                "    --background: 0 0% 100%;\n"
                "    --foreground: 222.2 84% 4.9%;\n"
                "    --card: 0 0% 100%;\n"
                "    --card-foreground: 222.2 84% 4.9%;\n"
                "    --primary: 222.2 47.4% 11.2%;\n"
                "    --primary-foreground: 210 40% 98%;\n"
                "    --muted: 210 40% 96.1%;\n"
                "    --muted-foreground: 215.4 16.3% 46.9%;\n"
                "    --border: 214.3 31.8% 91.4%;\n"
                "    --input: 214.3 31.8% 91.4%;\n"
                "    --ring: 222.2 84% 4.9%;\n"
                "    --radius: 0.5rem;\n"
                "  }\n"
                "  .dark {\n"
                "    --background: 222.2 84% 4.9%;\n"
                "    --foreground: 210 40% 98%;\n"
                "    --card: 222.2 84% 4.9%;\n"
                "    --card-foreground: 210 40% 98%;\n"
                "    --primary: 210 40% 98%;\n"
                "    --primary-foreground: 222.2 47.4% 11.2%;\n"
                "    --muted: 217.2 32.6% 17.5%;\n"
                "    --muted-foreground: 215 20.2% 65.1%;\n"
                "    --border: 217.2 32.6% 17.5%;\n"
                "    --input: 217.2 32.6% 17.5%;\n"
                "    --ring: 212.7 26.8% 83.9%;\n"
                "  }\n}\n\n"
                "@layer base {\n"
                "  * { @apply border-border; }\n"
                "  body { @apply bg-background text-foreground; }\n"
                "}\n"
            ),
        ))

        # ── src/main.tsx ──
        files.append(GeneratedFile(
            path="frontend/src/main.tsx",
            content=(
                "import React from 'react'\n"
                "import ReactDOM from 'react-dom/client'\n"
                "import { BrowserRouter } from 'react-router-dom'\n"
                "import App from './App'\n"
                "import './index.css'\n\n"
                "ReactDOM.createRoot(document.getElementById('root')!).render(\n"
                "  <React.StrictMode>\n"
                "    <BrowserRouter>\n"
                "      <App />\n"
                "    </BrowserRouter>\n"
                "  </React.StrictMode>,\n"
                ")\n"
            ),
        ))

        # ── src/lib/utils.ts ──
        files.append(GeneratedFile(
            path="frontend/src/lib/utils.ts",
            content=(
                "import { type ClassValue, clsx } from 'clsx'\n"
                "import { twMerge } from 'tailwind-merge'\n\n"
                "export function cn(...inputs: ClassValue[]) {\n"
                "  return twMerge(clsx(inputs))\n"
                "}\n"
            ),
        ))

        # ── src/lib/api.ts ──
        entity_types = "\n".join([
            f"export interface {e['pascal']} {{\n  id: number\n  [key: string]: unknown\n}}" for e in entities
        ])
        entity_api_fns = "\n\n".join([
            (
                f"// {e['pascal']} API\n"
                f"export const {e['pascal']}Api = {{\n"
                f"  list: (params?: Record<string, string>) => apiFetch<{e['pascal']}[]>(`/{e['slug']}s`, {{ params }}),\n"
                f"  get: (id: number) => apiFetch<{e['pascal']}>(`/{e['slug']}s/${{id}}`),\n"
                f"  create: (data: Partial<{e['pascal']}>) => apiFetch<{e['pascal']}>(`/{e['slug']}s`, {{ method: 'POST', body: data }}),\n"
                f"  update: (id: number, data: Partial<{e['pascal']}>) => apiFetch<{e['pascal']}>(`/{e['slug']}s/${{id}}`, {{ method: 'PATCH', body: data }}),\n"
                f"  delete: (id: number) => apiFetch<void>(`/{e['slug']}s/${{id}}`, {{ method: 'DELETE' }}),\n"
                f"}}"
            ) for e in entities
        ])
        files.append(GeneratedFile(
            path="frontend/src/lib/api.ts",
            content=(
                "const BASE_URL = '/api'\n\n"
                "interface FetchOptions {\n"
                "  method?: string\n"
                "  body?: unknown\n"
                "  params?: Record<string, string>\n"
                "}\n\n"
                "async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {\n"
                "  const { method = 'GET', body, params } = options\n"
                "  const token = localStorage.getItem('access_token')\n"
                "  const url = new URL(`${BASE_URL}${path}`, window.location.origin)\n"
                "  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v))\n"
                "  const headers: HeadersInit = { 'Content-Type': 'application/json' }\n"
                "  if (token) (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`\n"
                "  const res = await fetch(url.toString(), {\n"
                "    method,\n"
                "    headers,\n"
                "    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),\n"
                "  })\n"
                "  if (!res.ok) throw new Error(`API ${method} ${path} failed: ${res.status}`)\n"
                "  if (res.status === 204) return undefined as T\n"
                "  return res.json() as Promise<T>\n"
                "}\n\n"
                "// Auth\n"
                "export const AuthApi = {\n"
                "  login: (email: string, password: string) =>\n"
                "    apiFetch<{ access_token: string; refresh_token: string }>('/auth/login', {\n"
                "      method: 'POST', body: { email, password },\n"
                "    }),\n"
                "  logout: () => { localStorage.removeItem('access_token'); localStorage.removeItem('refresh_token') },\n"
                "}\n\n"
                + entity_types + "\n\n"
                + entity_api_fns + "\n"
            ),
        ))

        # ── src/App.tsx ──
        imports = "\n".join([
            f"import {e['pascal']}List from './pages/{e['pascal']}/List'"
            for e in entities
        ])
        routes = "\n          ".join([
            f'<Route path="/{e["slug"]}s" element={{<{e["pascal"]}List />}} />'
            for e in entities
        ])
        nav_links = "\n          ".join([
            f'<NavLink to="/{e["slug"]}s" className={{{{isActive}} => isActive ? "font-semibold text-primary" : "text-muted-foreground hover:text-foreground"}}>{e["pascal"]}s</NavLink>'
            for e in entities
        ])
        files.append(GeneratedFile(
            path="frontend/src/App.tsx",
            content=(
                "import { Routes, Route, NavLink } from 'react-router-dom'\n"
                + imports + "\n\n"
                "export default function App() {\n"
                "  return (\n"
                "    <div className=\"min-h-screen bg-background\">\n"
                "      <header className=\"border-b border-border bg-card\">\n"
                "        <nav className=\"container mx-auto px-4 py-3 flex items-center gap-6\">\n"
                f"          <span className=\"font-semibold text-foreground\">{html.escape(bundle.solution_name)}</span>\n"
                "          " + nav_links + "\n"
                "        </nav>\n"
                "      </header>\n"
                "      <main className=\"container mx-auto px-4 py-6\">\n"
                "        <Routes>\n"
                "          " + routes + "\n"
                "          <Route path=\"/\" element={<div className=\"text-muted-foreground\">Select a resource from the navigation.</div>} />\n"
                "        </Routes>\n"
                "      </main>\n"
                "    </div>\n"
                "  )\n"
                "}\n"
            ),
        ))

        # ── Per-entity List page ──
        for ent in entities:
            pascal = ent["pascal"]
            slug = ent["slug"]
            files.append(GeneratedFile(
                path=f"frontend/src/pages/{pascal}/List.tsx",
                content=(
                    f"import {{ useEffect, useState }} from 'react'\n"
                    f"import {{ {pascal}Api, type {pascal} }} from '@/lib/api'\n\n"
                    f"export default function {pascal}List() {{\n"
                    f"  const [items, setItems] = useState<{pascal}[]>([])\n"
                    f"  const [loading, setLoading] = useState(true)\n"
                    f"  const [error, setError] = useState<string | null>(null)\n\n"
                    f"  useEffect(() => {{\n"
                    f"    {pascal}Api.list()\n"
                    f"      .then(setItems)\n"
                    f"      .catch(e => setError(e.message))\n"
                    f"      .finally(() => setLoading(false))\n"
                    f"  }}, [])\n\n"
                    f"  if (loading) return <div className=\"text-muted-foreground\">Loading {pascal}s…</div>\n"
                    f"  if (error) return <div className=\"text-destructive\">Error: {{error}}</div>\n\n"
                    f"  return (\n"
                    f"    <div className=\"space-y-4\">\n"
                    f"      <div className=\"flex items-center justify-between\">\n"
                    f"        <h1 className=\"text-xl font-semibold text-foreground\">{pascal}s</h1>\n"
                    f"        <span className=\"text-sm text-muted-foreground\">{{items.length}} records</span>\n"
                    f"      </div>\n"
                    f"      <div className=\"rounded-md border border-border\">\n"
                    f"        <table className=\"w-full text-sm\">\n"
                    f"          <thead className=\"bg-muted/50\">\n"
                    f"            <tr><th className=\"px-4 py-2 text-left font-medium text-muted-foreground\">ID</th></tr>\n"
                    f"          </thead>\n"
                    f"          <tbody>\n"
                    f"            {{items.map(item => (\n"
                    f"              <tr key={{item.id}} className=\"border-t border-border hover:bg-muted/30\">\n"
                    f"                <td className=\"px-4 py-2 text-foreground\">{{item.id}}</td>\n"
                    f"              </tr>\n"
                    f"            ))}}\n"
                    f"          </tbody>\n"
                    f"        </table>\n"
                    f"      </div>\n"
                    f"    </div>\n"
                    f"  )\n"
                    f"}}\n"
                ),
            ))

        return files

    def _generate_react_native(self, bundle):
        """Generate a complete React Native / Expo project from a ProductSpecBundle.

        Output structure:
          app/                    — Expo Router screens (file-based routing)
          app/(tabs)/             — Tab navigator screens (home, profile)
          app/<entity>/           — Per-entity CRUD screens (list, detail, create)
          src/lib/                — API client, Zod schemas, offline cache, push notifications
          src/providers/          — AuthProvider, BiometricGate
          package.json, app.json, eas.json, tsconfig.json, babel/metro/tailwind configs
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

        files = []
        idp = getattr(bundle, "identity_provider", None) or {}
        getattr(bundle, "confirmed_fields", {}) or {}
        getattr(bundle, "business_rules", {}) or {}
        mobile_config = getattr(bundle, "mobile_config", {}) or {}

        auth_type = idp.get("type", "jwt-local")
        solution_slug = _kebab(bundle.solution_name)
        # Build a valid reverse-domain bundle ID (App Store requires this format)
        _bundle_slug = re.sub(r"[^a-z0-9]", "", solution_slug.lower()) or "app"
        bundle_id = mobile_config.get("bundle_id") or f"com.archie.{_bundle_slug}"

        # Build class contexts for templates (same shape as Java model context)
        classes = self._build_rn_class_context(bundle)

        # Mobile-specific config from genome
        offline_cfg = mobile_config.get("offline", {})
        offline_tier = offline_cfg.get("tier", 1)
        offline_entities = offline_cfg.get("offline_entities", [])
        push_cfg = mobile_config.get("push_notifications", {})
        push_channels = push_cfg.get("channels", []) if push_cfg.get("enabled") else []
        mobile_features = mobile_config.get("features", [])

        _mob_prod = getattr(bundle, "product_config", {}) or {}
        _mob_problem = getattr(bundle, "problem", {}) or {}
        ctx = {
            "solution_id": bundle.solution_id,
            "solution_name": bundle.solution_name,
            "solution_slug": solution_slug,
            "bundle_id": bundle_id,
            "spec_hash": bundle.spec_hash,
            "generated_at": now,
            "auth": auth_type,
            "classes": classes,
            "offline_tier": offline_tier,
            "offline_entities": offline_entities,
            "push_channels": push_channels,
            "mobile_features": mobile_features,
            # Product branding for splash screen + app metadata
            "tagline": _mob_prod.get("tagline") or "",
            "primary_color": _mob_prod.get("primary_color") or "#2563eb",
            "accent_color": _mob_prod.get("accent_color") or "#7c3aed",
        }

        # ── 1. Project config files ──
        for tpl_name, out_path in [
            ("package.json.j2", "mobile/package.json"),
            ("app.json.j2", "mobile/app.json"),
            ("eas.json.j2", "mobile/eas.json"),
            ("tsconfig.json.j2", "mobile/tsconfig.json"),
            ("babel.config.js.j2", "mobile/babel.config.js"),
            ("metro.config.js.j2", "mobile/metro.config.js"),
            ("tailwind.config.js.j2", "mobile/tailwind.config.js"),
            ("global.css.j2", "mobile/global.css"),
            (".env.example.j2", "mobile/.env.example"),
            ("gitignore.j2", "mobile/.gitignore"),
            ("README.md.j2", "mobile/README.md"),
        ]:
            try:
                tmpl = self._env.get_template(tpl_name)
                content = tmpl.render(**ctx)
                if content and content.strip():
                    files.append(GeneratedFile(path=out_path, content=content))
            except Exception as exc:
                logger.warning("RN template %s skipped: %s", tpl_name, exc)

        # ── 2. Root layout + splash screen + home screen ──
        # Splash screen component (always generated)
        try:
            splash_tmpl = self._env.get_template("app/splash.tsx.j2")
            files.append(GeneratedFile(
                path="mobile/app/splash.tsx",
                content=splash_tmpl.render(**ctx),
            ))
        except Exception as exc:
            logger.debug("RN splash screen skipped: %s", exc)

        tmpl = self._env.get_template("app/_layout.tsx.j2")
        files.append(GeneratedFile(
            path="mobile/app/_layout.tsx",
            content=tmpl.render(**ctx, has_splash=True),
        ))

        tmpl = self._env.get_template("app/index.tsx.j2")
        files.append(GeneratedFile(
            path="mobile/app/index.tsx",
            content=tmpl.render(**ctx),
        ))

        # ── 3. Tab screens ──
        tmpl = self._env.get_template("app/(tabs)/_layout.tsx.j2")
        files.append(GeneratedFile(
            path="mobile/app/(tabs)/_layout.tsx",
            content=tmpl.render(**ctx),
        ))

        tmpl = self._env.get_template("app/(tabs)/profile.tsx.j2")
        files.append(GeneratedFile(
            path="mobile/app/(tabs)/profile.tsx",
            content=tmpl.render(**ctx),
        ))

        # ── 4. Auth screens (only when jwt-local) ──
        if auth_type == "jwt-local":
            tmpl = self._env.get_template("app/login.tsx.j2")
            files.append(GeneratedFile(
                path="mobile/app/login.tsx",
                content=tmpl.render(**ctx),
            ))
            tmpl = self._env.get_template("app/register.tsx.j2")
            files.append(GeneratedFile(
                path="mobile/app/register.tsx",
                content=tmpl.render(**ctx),
            ))
            tmpl = self._env.get_template("src/providers/AuthProvider.tsx.j2")
            files.append(GeneratedFile(
                path="mobile/src/providers/AuthProvider.tsx",
                content=tmpl.render(**ctx),
            ))
            if "biometric_auth" in mobile_features:
                tmpl = self._env.get_template("src/providers/BiometricGate.tsx.j2")
                files.append(GeneratedFile(
                    path="mobile/src/providers/BiometricGate.tsx",
                    content=tmpl.render(**ctx),
                ))

        # ── 5. API client + Zod schemas ──
        tmpl = self._env.get_template("src/lib/api.ts.j2")
        files.append(GeneratedFile(
            path="mobile/src/lib/api.ts",
            content=tmpl.render(**ctx),
        ))
        tmpl = self._env.get_template("src/lib/schemas.ts.j2")
        files.append(GeneratedFile(
            path="mobile/src/lib/schemas.ts",
            content=tmpl.render(**ctx),
        ))

        # ── 6. Per-entity CRUD screens ──
        list_tmpl = self._env.get_template("app/[resource]/index.tsx.j2")
        detail_tmpl = self._env.get_template("app/[resource]/[id].tsx.j2")
        create_tmpl = self._env.get_template("app/[resource]/new.tsx.j2")

        for cls in classes:
            entity_ctx = {
                **ctx,
                "name": cls["name"],
                "table_name": cls["table_name"],
                "fields": cls["fields"],
                "description": cls.get("description", ""),
            }
            files.append(GeneratedFile(
                path=f"mobile/app/{cls['table_name']}/index.tsx",
                content=list_tmpl.render(**entity_ctx),
            ))
            files.append(GeneratedFile(
                path=f"mobile/app/{cls['table_name']}/[id].tsx",
                content=detail_tmpl.render(**entity_ctx),
            ))
            files.append(GeneratedFile(
                path=f"mobile/app/{cls['table_name']}/new.tsx",
                content=create_tmpl.render(**entity_ctx),
            ))

        # ── 7. Offline cache (always generated — tier 1 is read-only cache) ──
        tmpl = self._env.get_template("src/lib/offline_cache.ts.j2")
        files.append(GeneratedFile(
            path="mobile/src/lib/offline_cache.ts",
            content=tmpl.render(**ctx),
        ))

        # ── 8. Push notifications (only when channels defined) ──
        if push_channels or "push_notifications" in str(mobile_features):
            tmpl = self._env.get_template("src/lib/push_notifications.ts.j2")
            files.append(GeneratedFile(
                path="mobile/src/lib/push_notifications.ts",
                content=tmpl.render(**ctx),
            ))

        # ── Snack-compatible preview (single-file, no heavy deps) ──
        try:
            admin_entities = self._build_admin_entities(bundle)
            snack_entities = []
            _sm = getattr(bundle, "state_machines", {}) or {}
            for ent in admin_entities:
                sm_key = _pascal(ent["name_snake"])
                sm = _sm.get(sm_key) or _sm.get(ent["name_snake"])
                sm_dict = sm if isinstance(sm, dict) else vars(sm) if hasattr(sm, "__dict__") else {}
                snack_entities.append({
                    **ent,
                    "status_field": sm_dict.get("field_name") if sm_dict else None,
                    "states": sm_dict.get("states", []) if sm_dict else [],
                })
            snack_tpl = self._env.get_template("snack_preview.js.j2")
            snack_content = snack_tpl.render(
                solution_id=bundle.solution_id,
                solution_name=bundle.solution_name,
                entities=snack_entities,
            )
            files.append(GeneratedFile(path="mobile/_snack_preview.js", content=snack_content))
        except Exception as _e:
            logger.debug("snack_preview.js.j2 not available: %s", _e)

        # ── 9. Test infrastructure (Jest + react-native-testing-library) ──
        rn_templates_root = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "templates", "react_native_expo",
        )
        from jinja2 import Environment as _JinjaEnv, FileSystemLoader as _FSL
        rn_test_env = _JinjaEnv(loader=_FSL(rn_templates_root), keep_trailing_newline=True)

        for tpl, out in [
            ("jest.config.js.j2", "mobile/jest.config.js"),
            ("jest.setup.js.j2", "mobile/jest.setup.js"),
            ("__tests__/auth.test.tsx.j2", "mobile/__tests__/auth.test.tsx"),
        ]:
            try:
                tmpl = rn_test_env.get_template(tpl)
                content = tmpl.render(**ctx)
                if content and content.strip():
                    files.append(GeneratedFile(path=out, content=content))
            except Exception as _te:
                logger.debug("RN test template %s skipped: %s", tpl, _te)

        # Per-entity component tests
        for cls in classes:
            _rn_entity_ctx = {
                **ctx,
                "entity_name": cls["name"],
                "resource_name": cls["table_name"],
                "confirmed_fields": [
                    {"name": f["name"], "type": f.get("type", "string"), "required": not f.get("nullable", True)}
                    for f in cls.get("fields", [])
                ],
            }
            try:
                tmpl = rn_test_env.get_template("__tests__/[resource].test.tsx.j2")
                content = tmpl.render(**_rn_entity_ctx)
                if content and content.strip():
                    files.append(GeneratedFile(
                        path=f"mobile/__tests__/{cls['table_name']}.test.tsx",
                        content=content,
                    ))
            except Exception as _te:
                logger.debug("RN per-entity test template skipped for %s: %s", cls["name"], _te)

        result = GeneratedCodeBundle(
            solution_id=bundle.solution_id,
            bundle_id=bundle_id,
            language="react-native-expo",
            spec_hash=bundle.spec_hash,
            files=files,
        )

        # ArchiMate LLM context docs for React Native (mobile-aware variants)
        try:
            import os as _os
            _shared_dir = _os.path.join(
                _os.path.dirname(_os.path.dirname(__file__)), "templates", "shared",
            )
            from jinja2 import Environment as _J2Env, FileSystemLoader as _FSLRN
            _shared_env = _J2Env(loader=_FSLRN(_shared_dir), keep_trailing_newline=True)
            _motivation = self._build_motivation_context(bundle.solution_id) or {}
            _confirmed = getattr(bundle, "confirmed_fields", {}) or {}
            _svc_list = bundle.services or []
            _bp_flows = getattr(bundle, "business_process_flows", {}) or {}
            _entity_table_rn = [
                {
                    "name": _pascal(ent),
                    "snake_name": _snake(ent),
                    "table": _pluralize(_snake(ent)),
                    "key_fields": ", ".join(
                        (f.get("name") or "") if isinstance(f, dict) else f.name
                        for f in (flds or [])[:4]
                    ),
                }
                for ent, flds in _confirmed.items()
            ]
            _rn_shared_ctx = {
                **ctx,
                "solution_name": bundle.solution_name,
                "solution_id": bundle.solution_id,
                "spec_hash": bundle.spec_hash,
                "constraints": _motivation.get("constraints", []),
                "principles": _motivation.get("principles", []),
                "requirements": _motivation.get("requirements", []),
                "stakeholders": _motivation.get("stakeholders", []),
                "meanings": _motivation.get("meanings", []),
                "problem_statement": _motivation.get("problem_statement", ""),
                "services": [{"name": s.name, "paths": s.paths} for s in _svc_list],
                "entities": _entity_table_rn,
                "entity_table": _entity_table_rn,
                "process_flows": _bp_flows,
                "has_kafka": False,
                "has_grpc": False,
                "has_business_rules": bool(getattr(bundle, "business_rules", {})),
                "is_mobile": True,
                "has_frontend": False,
                "app_name": bundle.solution_name,
            }
            for _tpl_name, _out_path in [
                ("PRINCIPLES.md.j2", "mobile/PRINCIPLES.md"),
                ("DOMAIN.md.j2", "mobile/DOMAIN.md"),
                ("DEPENDENCIES.md.j2", "mobile/DEPENDENCIES.md"),
                ("CHECKLIST.md.j2", "mobile/CHECKLIST.md"),
            ]:
                try:
                    _content = _shared_env.get_template(_tpl_name).render(**_rn_shared_ctx)
                    if _content and _content.strip():
                        result.files.append(GeneratedFile(path=_out_path, content=_content))
                except Exception as _te:
                    logger.debug("RN shared template %s skipped: %s", _tpl_name, _te)
        except Exception as _e:
            logger.debug("ArchiMate LLM context docs skipped for RN: %s", _e)

        return result

    def _build_rn_class_context(self, bundle):
        """Build React Native template-friendly class list from confirmed fields / services.

        Each class dict contains:
          - name: PascalCase entity name
          - table_name: snake_case for URL segments and query keys
          - description: human-readable description
          - fields: list of field dicts with name, type, primary_key, nullable
          - validations: list of validation rule dicts
        """
        classes = []
        confirmed_fields = getattr(bundle, "confirmed_fields", {}) or {}
        getattr(bundle, "business_rules", {}) or {}

        if confirmed_fields:
            for name, field_defs in confirmed_fields.items():
                pascal_name = _pascal(name)
                table_name = _snake(name)
                fields = []
                validations = []

                for f in field_defs:
                    _g = f.get if isinstance(f, dict) else lambda k, d=None: getattr(f, k, d)
                    raw_type = _g("type", "string")
                    field_name = _snake(_g("name", ""))
                    is_pk = field_name == "id" or _g("primary_key", False)
                    nullable = _g("nullable", not _g("required", False))

                    # Map JSON Schema types to Python-style types that Zod templates expect
                    ts_type = self._TS_TYPE_MAP.get(raw_type, "str")
                    if raw_type == "enum":
                        ts_type = "str"

                    fields.append({
                        "name": field_name,
                        "type": ts_type,
                        "primary_key": is_pk,
                        "nullable": nullable,
                    })

                    # Extract validation rules for Zod
                    constraints = _g("constraints") if isinstance(f, dict) else None
                    if constraints and isinstance(constraints, dict):
                        if constraints.get("minLength"):
                            validations.append({"field": field_name, "rule": "min_length", "value": constraints["minLength"]})
                        if constraints.get("maxLength"):
                            validations.append({"field": field_name, "rule": "max_length", "value": constraints["maxLength"]})
                    max_len = _g("max_length")
                    if max_len:
                        validations.append({"field": field_name, "rule": "max_length", "value": max_len})

                # Always ensure an id field exists
                if not any(f["name"] == "id" for f in fields):
                    fields.insert(0, {"name": "id", "type": "int", "primary_key": True, "nullable": False})

                classes.append({
                    "name": pascal_name,
                    "table_name": table_name,
                    "description": f"Manage {pascal_name} records",
                    "fields": fields,
                    "validations": validations,
                })
        else:
            # No confirmed fields — derive from service names with basic CRUD fields
            for svc in bundle.services:
                pascal_name = _pascal(svc.name)
                table_name = _snake(svc.name)
                classes.append({
                    "name": pascal_name,
                    "table_name": table_name,
                    "description": f"Manage {pascal_name} records",
                    "fields": [
                        {"name": "id", "type": "int", "primary_key": True, "nullable": False},
                        {"name": "name", "type": "str", "primary_key": False, "nullable": False},
                        {"name": "description", "type": "str", "primary_key": False, "nullable": True},
                        {"name": "created_at", "type": "datetime", "primary_key": False, "nullable": True},
                    ],
                    "validations": [],
                })

        return classes

    # ── Salesforce/Apex generation ──

    def _generate_salesforce(self, bundle):
        """Generate a complete Salesforce DX project from a ProductSpecBundle.

        Output structure matches sfdx-project layout:
          force-app/main/default/objects/   — Custom Object metadata XML
          force-app/main/default/classes/   — Apex service + trigger handler + test classes
          force-app/main/default/triggers/  — Apex triggers
          force-app/main/default/lwc/       — Lightning Web Components
          force-app/main/default/permissionsets/ — Permission sets
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        bundle_id = f"sf_{now}"

        files = []
        confirmed = getattr(bundle, "confirmed_fields", {}) or {}
        business_rules = getattr(bundle, "business_rules", {}) or {}
        integrations = getattr(bundle, "integrations", {}) or {}
        sf_package = _salesforce_package_settings(bundle)
        namespace_prefix = sf_package["namespace_prefix"]

        ctx = {
            "solution_id": bundle.solution_id,
            "solution_name": bundle.solution_name,
            "bundle_id": bundle_id,
            "spec_hash": bundle.spec_hash,
            "generated_at": now,
            **sf_package,
            "sf_object_api_name": lambda name: _salesforce_custom_api_name(_pascal(name), namespace_prefix),
            "sf_field_api_name": lambda name: _salesforce_custom_api_name(_snake(name), namespace_prefix),
            "sf_reference_object_api_name": lambda foreign_key: _salesforce_reference_object_api_name(foreign_key, namespace_prefix),
        }
        test_data_factory_name = f"{re.sub(r'[^A-Za-z0-9]', '', bundle.solution_name) or 'Generated'}TestDataFactory"
        permission_set_name = f"{_snake(bundle.solution_name)}_Access"
        permission_set_test_name = f"{re.sub(r'[^A-Za-z0-9]', '', bundle.solution_name) or 'Generated'}AccessTest"

        # Build model definitions from confirmed fields + service paths
        # Build a case-insensitive index of confirmed_fields keys so that service
        # names like "workorder" (from OpenAPI slugs) resolve to "WorkOrder" (from
        # element_name in the DB) rather than generating "Workorder".
        _confirmed_lower = {k.lower(): k for k in confirmed}

        models = {}
        for svc in bundle.services:
            model_name = _pascal(svc.name)
            # Prefer the confirmed key's original casing when available
            if model_name not in confirmed:
                canonical = _confirmed_lower.get(model_name.lower())
                if canonical:
                    model_name = canonical
            model_def = {
                "description": "",
                "archimate_source_id": None,
                "fields": [],
                "enums": {},
            }
            # Populate from confirmed fields if available.
            # Fields may be FieldDef dataclass instances OR plain dicts (from bridge).
            if model_name in confirmed:
                for fdef in confirmed[model_name]:
                    _g = fdef.get if isinstance(fdef, dict) else lambda k, d=None: getattr(fdef, k, d)
                    fd = {
                        "name": _snake(_g("name", "")),
                        "type": _g("type", "string"),
                        "format": _g("format"),
                        "required": _g("required", False),
                        "readonly": _g("readonly", False),
                        "primary_key": _g("name", "") == "id" or _g("primary_key", False),
                        "description": _g("description", ""),
                        "max_length": _g("max_length"),
                        "unique": _g("unique", False),
                        "foreign_key": _g("foreign_key"),
                        "enum_values": _g("enum_values") or _g("enum") or [],
                    }
                    constraints = _g("constraints")
                    if constraints and isinstance(constraints, dict):
                        fd["max_length"] = constraints.get("maxLength")
                        fd["unique"] = constraints.get("unique", False)
                    model_def["fields"].append(fd)
            # Extract archimate source from first path
            if svc.paths:
                model_def["archimate_source_id"] = svc.paths[0].archimate_source_id
            models[model_name] = model_def

        # 1. Custom Object metadata XML (one per model)
        for model_name, model_def in models.items():
            obj_ctx = dict(ctx, model_name=model_name, model_def=model_def)
            template = self._env.get_template("custom_object.xml.j2")
            content = template.render(**obj_ctx)
            object_api_name = ctx["sf_object_api_name"](model_name)
            files.append(GeneratedFile(
                path=f"force-app/main/default/objects/{object_api_name}/{object_api_name}.object-meta.xml",
                content=content,
                archimate_sources=[model_def["archimate_source_id"]] if model_def["archimate_source_id"] else [],
            ))

        # 2. Apex service classes (one per model)
        for model_name, model_def in models.items():
            svc_match = next((s for s in bundle.services if _pascal(s.name) == model_name), None)
            archimate_ids = [p.archimate_source_id for p in (svc_match.paths if svc_match else []) if p.archimate_source_id]
            svc_ctx = dict(ctx,
                service_name=model_name,
                model_name=model_name,
                fields=model_def["fields"],
                paths=svc_match.paths if svc_match else [],
                archimate_ids=archimate_ids,
            )
            template = self._env.get_template("apex_service.cls.j2")
            content = template.render(**svc_ctx)
            self._validate_salesforce_code(f"force-app/main/default/classes/{model_name}Service.cls", content)
            files.append(GeneratedFile(
                path=f"force-app/main/default/classes/{model_name}Service.cls",
                content=content,
                archimate_sources=archimate_ids,
            ))

        # 3. Apex triggers + trigger handlers (one per model)
        for model_name, model_def in models.items():
            rules = business_rules.get(model_name, [])
            if not isinstance(rules, list):
                rules = []

            # Trigger
            trig_ctx = dict(ctx, model_name=model_name, business_rules=rules)
            template = self._env.get_template("apex_trigger.trigger.j2")
            content = template.render(**trig_ctx)
            self._validate_salesforce_code(f"force-app/main/default/triggers/{model_name}Trigger.trigger", content)
            files.append(GeneratedFile(
                path=f"force-app/main/default/triggers/{model_name}Trigger.trigger",
                content=content,
            ))

            # Trigger handler
            handler_ctx = dict(ctx, model_name=model_name, fields=model_def["fields"], business_rules=rules)
            template = self._env.get_template("apex_trigger_handler.cls.j2")
            content = template.render(**handler_ctx)
            self._validate_salesforce_code(f"force-app/main/default/classes/{model_name}TriggerHandler.cls", content)
            files.append(GeneratedFile(
                path=f"force-app/main/default/classes/{model_name}TriggerHandler.cls",
                content=content,
            ))

        # 4. Test data factory (one per package)
        factory_ctx = dict(
            ctx,
            test_data_factory_name=test_data_factory_name,
            models=models,
        )
        template = self._env.get_template("test_data_factory.cls.j2")
        content = template.render(**factory_ctx)
        self._validate_salesforce_code(
            f"force-app/main/default/classes/{test_data_factory_name}.cls",
            content,
        )
        files.append(GeneratedFile(
            path=f"force-app/main/default/classes/{test_data_factory_name}.cls",
            content=content,
        ))

        # 5. Apex test classes (one per service)
        for model_name, model_def in models.items():
            test_ctx = dict(ctx,
                service_name=model_name,
                model_name=model_name,
                fields=model_def["fields"],
                test_data_factory_name=test_data_factory_name,
            )
            template = self._env.get_template("apex_test.cls.j2")
            content = template.render(**test_ctx)
            files.append(GeneratedFile(
                path=f"force-app/main/default/classes/{model_name}ServiceTest.cls",
                content=content,
            ))

        # 6. Lightning Web Components (one list view per model)
        lwc_components = []
        for model_name, model_def in models.items():
            component_name = _snake(model_name) + "List"
            lwc_components.append(component_name)
            lwc_ctx = dict(ctx,
                model_name=model_name,
                service_name=model_name,
                fields=model_def["fields"],
                component_name=_pascal(component_name),
            )
            for tmpl_name, file_ext in [("lwc_list.html.j2", ".html"), ("lwc_list.js.j2", ".js"), ("lwc_list.css.j2", ".css"), ("lwc_meta.xml.j2", ".js-meta.xml")]:
                template = self._env.get_template(tmpl_name)
                content = template.render(**lwc_ctx)
                files.append(GeneratedFile(
                    path=f"force-app/main/default/lwc/{component_name}/{component_name}{file_ext}",
                    content=content,
                ))

        # 7. Permission set (one for the whole solution)
        perm_ctx = dict(ctx, models=models)
        template = self._env.get_template("permission_set.xml.j2")
        content = template.render(**perm_ctx)
        files.append(GeneratedFile(
            path=f"force-app/main/default/permissionsets/{permission_set_name}.permissionset-meta.xml",
            content=content,
        ))

        permission_test_ctx = dict(
            ctx,
            models=models,
            permission_set_name=permission_set_name,
            permission_set_test_name=permission_set_test_name,
        )
        template = self._env.get_template("permission_set_test.cls.j2")
        content = template.render(**permission_test_ctx)
        self._validate_salesforce_code(
            f"force-app/main/default/classes/{permission_set_test_name}.cls",
            content,
        )
        files.append(GeneratedFile(
            path=f"force-app/main/default/classes/{permission_set_test_name}.cls",
            content=content,
        ))

        # 8. Named Credentials (one per integration)
        for integ_name, integ_def in integrations.items():
            normalized_integration = _salesforce_integration_dict(integ_def)
            nc_ctx = dict(ctx, integration_name=_snake(integ_name), integration_def=normalized_integration)
            template = self._env.get_template("named_credential.xml.j2")
            content = template.render(**nc_ctx)
            files.append(GeneratedFile(
                path=f"force-app/main/default/namedCredentials/{_snake(integ_name)}.namedCredential-meta.xml",
                content=content,
            ))

            if not models or not _salesforce_is_sap_integration(integ_name, normalized_integration):
                continue
            sap_service = _salesforce_extract_odata_service(integ_name, normalized_integration)
            if not sap_service:
                continue
            primary_model_name, primary_model_def = next(iter(models.items()))
            # Apex class names must be valid identifiers — _pascal strips spaces and
            # illegal chars like ()+ from the solution name (e.g. "… (CPI + MuleSoft)").
            integration_class = f"{_pascal(bundle.solution_name)}SapIntegration"
            integration_ctx = dict(
                ctx,
                sap_named_credential=_snake(integ_name),
                sap_odata_service=sap_service,
                source_entity=_salesforce_extract_source_entity(integ_name, normalized_integration),
                target_object=ctx["sf_object_api_name"](primary_model_name),
                field_mappings=_salesforce_field_mappings(primary_model_def, ctx["sf_field_api_name"]),
                integration_class=integration_class,
            )
            template = self._env.get_template("sap_to_sf_integration.cls.j2")
            content = template.render(**integration_ctx)
            integration_class_name = f"{integration_class}.cls"
            self._validate_salesforce_code(
                f"force-app/main/default/classes/{integration_class_name}",
                content,
            )
            files.append(GeneratedFile(
                path=f"force-app/main/default/classes/{integration_class_name}",
                content=content,
            ))

        # 9. Project config
        proj_ctx = dict(ctx)
        template = self._env.get_template("sfdx_project.json.j2")
        content = template.render(**proj_ctx)
        files.append(GeneratedFile(path="sfdx-project.json", content=content))

        # 10. Generated README with traceability
        readme_ctx = dict(ctx,
            models=models,
            services=[_pascal(s.name) for s in bundle.services],
            lwc_components=lwc_components,
        )
        template = self._env.get_template("GENERATED.md.j2")
        content = template.render(**readme_ctx)
        files.append(GeneratedFile(path="GENERATED.md", content=content))

        # 11. CI/CD pipeline
        template = self._env.get_template("github_actions_sf.yml.j2")
        content = template.render(**ctx)
        files.append(GeneratedFile(path=".github/workflows/deploy.yml", content=content))

        # 12. .gitignore for SFDX project
        files.append(GeneratedFile(path=".gitignore", content=(
            "# Salesforce DX\n"
            ".sfdx/\n"
            ".sf/\n"
            "node_modules/\n"
            "*.log\n"
            "auth_url.txt\n"
        )))

        # 13. Scratch org definition (for CI/CD)
        template = self._env.get_template("scratch_def.json.j2")
        content = template.render(**ctx)
        files.append(GeneratedFile(path="config/project-scratch-def.json", content=content))

        # 14. Validation rules from UML class validations
        for model_name, model_def in models.items():
            for field_def in model_def.get("fields", []):
                fname = field_def.get("name", "")
                # Generate required validation for non-nullable fields
                if field_def.get("required") and fname not in ("id", "created_at", "updated_at") and not field_def.get("primary_key"):
                    rule = {
                        "field": fname,
                        "rule_type": "required",
                        "description": f"{fname} is required per architecture constraint",
                    }
                    rule_name = f"{fname}_required"
                    vr_ctx = dict(ctx, model_name=model_name, rule_name=rule_name, rule=rule)
                    template = self._env.get_template("validation_rule.xml.j2")
                    content = template.render(**vr_ctx)
                    object_api_name = ctx["sf_object_api_name"](model_name)
                    files.append(GeneratedFile(
                        path=f"force-app/main/default/objects/{object_api_name}/validationRules/{rule_name}.validationRule-meta.xml",
                        content=content,
                    ))

        return GeneratedCodeBundle(
            solution_id=bundle.solution_id,
            bundle_id=bundle_id,
            language="salesforce-apex",
            spec_hash=bundle.spec_hash,
            files=files,
        )

    # ── SAP CAP generation ──

    _CDS_TYPE_MAP = {
        "string": "String(255)",
        "text": "String(5000)",
        "integer": "Integer",
        "int": "Integer",
        "number": "Decimal(10,2)",
        "float": "Decimal(10,2)",
        "decimal": "Decimal(10,2)",
        "boolean": "Boolean",
        "bool": "Boolean",
        "datetime": "Timestamp",
        "timestamp": "Timestamp",
        "date": "Date",
        "uuid": "UUID",
        "json": "String(5000)",
        "enum": "String(64)",
    }

    def _map_cds_type(self, field_type, field_format=None):
        """Map a field type + format to CDS type."""
        ft = (field_type or "string").lower()
        ff = (field_format or "").lower()
        if ff == "date-time":
            return "Timestamp"
        if ff == "date":
            return "Date"
        if ff == "uuid":
            return "UUID"
        return self._CDS_TYPE_MAP.get(ft, "String(255)")

    def _generate_sap_cap(self, bundle):
        """Generate a complete SAP CAP (BTP / OData V4) project from a ProductSpecBundle.

        Output structure:
          db/schema.cds                       — CDS data model
          srv/<entity>-service.cds            — Service definitions
          srv/<entity>-service.js             — Service handlers
          app/<entity>/annotations.cds        — Fiori Element annotations
          tests/<entity>.test.js              — Jest tests
          package.json, .cdsrc.json, mta.yaml, xs-security.json
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        bundle_id = f"cap_{now}"

        files = []
        confirmed = getattr(bundle, "confirmed_fields", {}) or {}
        getattr(bundle, "business_rules", {}) or {}

        sol_snake = _snake(bundle.solution_name)
        sol_pascal = _pascal(bundle.solution_name)
        sol_kebab = _kebab(bundle.solution_name)
        namespace = sol_snake

        ctx = {
            "solution_id": bundle.solution_id,
            "solution_name": bundle.solution_name,
            "bundle_id": bundle_id,
            "namespace": namespace,
            "sol_snake": sol_snake,
            "sol_pascal": sol_pascal,
            "sol_kebab": sol_kebab,
        }

        # Build model context with CDS types
        models = {}
        if confirmed:
            for name, field_defs in confirmed.items():
                pascal_name = _pascal(name)
                cds_fields = []
                associations = []
                for f in field_defs:
                    _g = f.get if isinstance(f, dict) else lambda k, d=None: getattr(f, k, d)
                    fname = _snake(_g("name", ""))
                    ftype = _g("type", "string")
                    fformat = _g("format")
                    fk = _g("foreign_key")

                    if fk:
                        target = _pascal(fname.replace("_id", ""))
                        associations.append({
                            "field_name": _snake(target),
                            "target": target,
                        })
                        continue

                    cds_fields.append({
                        "name": fname,
                        "type": ftype,
                        "format": fformat,
                        "cds_type": self._map_cds_type(ftype, fformat),
                        "required": _g("required", False),
                        "description": _g("description", ""),
                        "display_name": fname.replace("_", " ").title(),
                    })
                models[pascal_name] = {
                    "fields": cds_fields,
                    "associations": associations,
                }
        else:
            for svc in bundle.services:
                pascal_name = _pascal(svc.name)
                models[pascal_name] = {"fields": [], "associations": []}

        entity_list = [{"name": name} for name in models]

        # 1. schema.cds
        tpl = self._env.get_template("schema.cds.j2")
        files.append(GeneratedFile(
            path="db/schema.cds",
            content=tpl.render(**ctx, models=models),
        ))

        # 2-5. Per-entity: service.cds, handler.js, annotations.cds, test.js
        svc_tpl = self._env.get_template("service.cds.j2")
        handler_tpl = self._env.get_template("service_handler.js.j2")
        annot_tpl = self._env.get_template("annotations.cds.j2")
        test_tpl = self._env.get_template("test.js.j2")

        for entity_name, model_def in models.items():
            svc_name = f"{entity_name}Service"
            svc_kebab = _kebab(entity_name)
            read_scope = f"{sol_snake}.{entity_name}.Read"
            write_scope = f"{sol_snake}.{entity_name}.Write"

            ent_ctx = dict(ctx,
                entity_name=entity_name,
                service_name=svc_name,
                service_kebab=svc_kebab,
                read_scope=read_scope,
                write_scope=write_scope,
                model_def=model_def,
            )

            files.append(GeneratedFile(
                path=f"srv/{svc_kebab}-service.cds",
                content=svc_tpl.render(**ent_ctx),
            ))
            files.append(GeneratedFile(
                path=f"srv/{svc_kebab}-service.js",
                content=handler_tpl.render(**ent_ctx),
            ))
            files.append(GeneratedFile(
                path=f"app/{svc_kebab}/annotations.cds",
                content=annot_tpl.render(**ent_ctx),
            ))
            files.append(GeneratedFile(
                path=f"tests/{svc_kebab}.test.js",
                content=test_tpl.render(**ent_ctx),
            ))

        # 6. package.json
        tpl = self._env.get_template("package.json.j2")
        files.append(GeneratedFile(
            path="package.json",
            content=tpl.render(**ctx),
        ))

        # 7. .cdsrc.json
        tpl = self._env.get_template("cdsrc.json.j2")
        files.append(GeneratedFile(
            path=".cdsrc.json",
            content=tpl.render(**ctx),
        ))

        # 8. xs-security.json
        tpl = self._env.get_template("xs_security.json.j2")
        files.append(GeneratedFile(
            path="xs-security.json",
            content=tpl.render(**ctx, entities=entity_list),
        ))

        # 9. mta.yaml
        tpl = self._env.get_template("mta.yaml.j2")
        files.append(GeneratedFile(
            path="mta.yaml",
            content=tpl.render(**ctx),
        ))

        return GeneratedCodeBundle(
            solution_id=bundle.solution_id,
            bundle_id=bundle_id,
            language="sap-cap",
            spec_hash=bundle.spec_hash,
            files=files,
        )

    def _generate_sap_btp_integration(self, bundle):
        """Generate a SAP BTP Integration Suite iFlow descriptor from a ProductSpecBundle.

        Output structure:
          iflow.xml               — BTP Integration iFlow BPMN descriptor
          README.md               — Deployment instructions
        """
        from jinja2 import Environment, FileSystemLoader
        import os

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        bundle_id = f"btp_{now}"

        sol_snake = _snake(bundle.solution_name)
        sol_kebab = _kebab(bundle.solution_name)

        # Build field mappings from confirmed fields
        confirmed = getattr(bundle, "confirmed_fields", {}) or {}
        field_mappings = []
        for model_name, model_def in confirmed.items():
            if isinstance(model_def, dict):
                for field in model_def.get("fields", []):
                    field_mappings.append({
                        "source": field.get("name", ""),
                        "target": field.get("name", "") + "__c",
                    })

        # Resolve source/target systems from bundle metadata
        source_system = getattr(bundle, "source_system", None) or "SAP S/4HANA"
        target_system = getattr(bundle, "target_system", None) or "Salesforce"
        source_entity = list(confirmed.keys())[0] if confirmed else "BusinessObject"
        error_handling = getattr(bundle, "error_handling", None) or "StoreAndForward"

        btp_templates_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "..",
            "app", "modules", "solutions_product", "templates", "sap_btp_integration",
        )
        btp_env = Environment(
            loader=FileSystemLoader(os.path.abspath(btp_templates_dir)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

        ctx = {
            "solution_id": bundle.solution_id,
            "bundle_id": bundle_id,
            "solution_name": bundle.solution_name,
            "sol_snake": sol_snake,
            "sol_kebab": sol_kebab,
            "source_system": source_system,
            "target_system": target_system,
            "source_entity": source_entity,
            "field_mappings": field_mappings,
            "error_handling": error_handling,
        }

        files = []

        tpl = btp_env.get_template("iflow.xml.j2")
        files.append(GeneratedFile(
            path="iflow.xml",
            content=tpl.render(**ctx),
        ))

        readme_content = f"""# {bundle.solution_name} — SAP BTP Integration Suite iFlow

Generated by Entelim · solution_id={bundle.solution_id} · bundle_id={bundle_id}

## Overview

This iFlow integrates **{source_system}** ({source_entity}) with **{target_system}**.

## Deployment

1. Log in to SAP BTP Integration Suite tenant.
2. Navigate to **Design** → **Create** → **Import**.
3. Upload `iflow.xml`.
4. Configure credentials:
   - Source endpoint: add HTTP destination for {source_system}
   - Target endpoint: configure OAuth2 credentials for {target_system}
5. **Deploy** the iFlow.
6. Monitor in **Monitor** → **Message Processing**.

## Field Mappings

| Source ({source_entity}) | Target ({target_system}) |
|---|---|
""" + "\n".join(
            f"| {m['source']} | {m['target']} |"
            for m in field_mappings
        ) + "\n"

        files.append(GeneratedFile(path="README.md", content=readme_content))

        return GeneratedCodeBundle(
            solution_id=bundle.solution_id,
            bundle_id=bundle_id,
            language="sap-btp-integration",
            spec_hash=bundle.spec_hash,
            files=files,
        )
