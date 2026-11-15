"""
Genome Validator — validates an Architectural Genome dict against the JSON Schema
and applies semantic checks that JSON Schema alone cannot express.

Usage:
    from app.modules.codegen.services.genome_validator import validate_genome
    errors = validate_genome(genome_dict)
    if errors:
        raise ValueError(f"Genome validation failed: {errors}")
"""
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA_MISSING_MESSAGE = (
    "The genome rules file is missing from this installation, so the genome cannot be checked. "
    "Choose Quick Generate instead, or ask your administrator to restore the installation and try again."
)

# Lazy-loaded schema
_SCHEMA: Optional[dict] = None
_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "agents" / "schemas" / "architectural_genome_schema.json"


def _load_schema() -> dict:
    """Load the genome JSON Schema from disk. Cached after first load."""
    global _SCHEMA
    if _SCHEMA is None:
        with open(_SCHEMA_PATH) as f:
            _SCHEMA = json.load(f)
    return _SCHEMA


def validate_genome(genome: dict) -> list[str]:
    """
    Validate an Architectural Genome dict.

    Returns a list of error strings. Empty list = valid genome.

    Performs two levels of validation:
    1. JSON Schema validation (structure, types, required fields)
    2. Semantic validation (cross-field consistency, business rules)
    """
    errors = []

    # Level 1: JSON Schema validation
    errors.extend(_validate_json_schema(genome))

    # Level 2: Semantic validation (only if schema passes basic checks)
    if not errors:
        errors.extend(_validate_semantics(genome))

    return errors


def _validate_json_schema(genome: dict) -> list[str]:
    """Validate genome against JSON Schema. Uses jsonschema if available, falls back to manual checks."""
    errors = []

    try:
        import jsonschema
        try:
            schema = _load_schema()
        except FileNotFoundError:
            logger.error("Genome schema missing at %s", _SCHEMA_PATH)
            return [SCHEMA_MISSING_MESSAGE]
        validator = jsonschema.Draft202012Validator(schema)
        for error in validator.iter_errors(genome):
            path = ".".join(str(p) for p in error.absolute_path) or "(root)"
            errors.append(f"Schema: {path} — {error.message}")
    except ImportError:
        # jsonschema not installed — fall back to manual required-field checks
        logger.warning("jsonschema not installed; using manual validation only")
        errors.extend(_manual_schema_check(genome))

    return errors


def _manual_schema_check(genome: dict) -> list[str]:
    """Manual schema validation when jsonschema library is not available."""
    errors = []

    # Required top-level fields
    for field in ["genome_version", "solution_id", "generated_at", "problem", "modules", "infrastructure", "security"]:
        if field not in genome:
            errors.append(f"Missing required field: {field}")

    # genome_version format
    version = genome.get("genome_version", "")
    if version and not all(part.isdigit() for part in version.split(".")):
        errors.append(f"genome_version must be semver: got '{version}'")

    # problem.statement required
    problem = genome.get("problem", {})
    if not problem.get("statement"):
        errors.append("problem.statement is required")

    # modules must not be empty
    modules = genome.get("modules", {})
    if not modules:
        errors.append("modules must contain at least one module")

    # Each module must have aggregate_root and entities
    for mod_key, mod_def in modules.items():
        if not isinstance(mod_def, dict):
            errors.append(f"modules.{mod_key} must be an object")
            continue
        if not mod_def.get("aggregate_root"):
            errors.append(f"modules.{mod_key}.aggregate_root is required")
        if not mod_def.get("entities"):
            errors.append(f"modules.{mod_key}.entities is required")

    # infrastructure.database and infrastructure.auth required
    infra = genome.get("infrastructure", {})
    if not infra.get("database"):
        errors.append("infrastructure.database is required")
    if not infra.get("auth"):
        errors.append("infrastructure.auth is required")

    # Validate field types
    for mod_key, mod_def in modules.items():
        if not isinstance(mod_def, dict):
            continue
        for entity_name, fields in mod_def.get("fields", {}).items():
            if not isinstance(fields, list):
                errors.append(f"modules.{mod_key}.fields.{entity_name} must be an array")
                continue
            for i, field in enumerate(fields):
                if not field.get("name"):
                    errors.append(f"modules.{mod_key}.fields.{entity_name}[{i}].name is required")
                if not field.get("type"):
                    errors.append(f"modules.{mod_key}.fields.{entity_name}[{i}].type is required")

    # Validate state machine transitions reference valid states
    for mod_key, mod_def in modules.items():
        if not isinstance(mod_def, dict):
            continue
        sm = mod_def.get("state_machine")
        if not sm:
            continue
        states = set(sm.get("states", []))
        if len(states) < 2:
            errors.append(f"modules.{mod_key}.state_machine.states must have at least 2 states")
        for i, trans in enumerate(sm.get("transitions", [])):
            froms = trans.get("from", [])
            if isinstance(froms, str):
                froms = [froms]
            for f in froms:
                if f not in states:
                    errors.append(
                        f"modules.{mod_key}.state_machine.transitions[{i}].from "
                        f"references unknown state '{f}'"
                    )
            to = trans.get("to")
            if to and to not in states:
                errors.append(
                    f"modules.{mod_key}.state_machine.transitions[{i}].to "
                    f"references unknown state '{to}'"
                )

    # Validate language — use STACK_REGISTRY as single source of truth
    from app.modules.codegen.stack_registry import validate_language
    lang = genome.get("language")
    if lang:
        errors.extend(validate_language(lang))

    return errors


def _validate_semantics(genome: dict) -> list[str]:
    """Semantic validation that JSON Schema cannot express."""
    errors = []
    modules = genome.get("modules", {})

    # 0. State machine transitions must reference valid states
    for mod_key, mod_def in modules.items():
        sm = mod_def.get("state_machine")
        if not sm:
            continue
        states = set(sm.get("states", []))
        if len(states) < 2:
            errors.append(f"modules.{mod_key}.state_machine.states must have at least 2 states")
        for i, trans in enumerate(sm.get("transitions", [])):
            froms = trans.get("from", [])
            if isinstance(froms, str):
                froms = [froms]
            for f in froms:
                if f not in states:
                    errors.append(
                        f"modules.{mod_key}.state_machine.transitions[{i}].from "
                        f"references unknown state '{f}'"
                    )
            to = trans.get("to")
            if to and to not in states:
                errors.append(
                    f"modules.{mod_key}.state_machine.transitions[{i}].to "
                    f"references unknown state '{to}'"
                )

    # 1. Aggregate root must be in entities list
    for mod_key, mod_def in modules.items():
        root = mod_def.get("aggregate_root")
        entities = mod_def.get("entities", [])
        if root and root not in entities:
            errors.append(
                f"modules.{mod_key}: aggregate_root '{root}' is not in entities list {entities}"
            )

    # 2. Foreign keys must reference entities that exist somewhere in the genome
    all_entity_names = set()
    for mod_def in modules.values():
        all_entity_names.update(mod_def.get("entities", []))

    for mod_key, mod_def in modules.items():
        for entity_name, fields in mod_def.get("fields", {}).items():
            for field in fields:
                fk = field.get("foreign_key")
                if fk:
                    ref_entity = fk.split(".")[0]
                    if ref_entity not in all_entity_names:
                        errors.append(
                            f"modules.{mod_key}.fields.{entity_name}.{field['name']}: "
                            f"foreign_key references unknown entity '{ref_entity}'"
                        )

    # 3. State machine effects that reference operations should have those operations defined
    for mod_key, mod_def in modules.items():
        sm = mod_def.get("state_machine")
        if not sm:
            continue
        operations = set(mod_def.get("operations", {}).keys())
        for trans in sm.get("transitions", []):
            effects = trans.get("effect", [])
            if isinstance(effects, str):
                effects = [effects]
            # Effects are free-form strings, but if they look like function calls, check operations
            for effect in effects:
                # Extract function name if it looks like "function_name(args)"
                if "(" in effect:
                    func_name = effect.split("(")[0].strip()
                    # Only warn, don't error — effects can call external functions
                    if func_name and operations and func_name not in operations:
                        logger.debug(
                            "modules.%s: state machine effect '%s' is not a defined operation "
                            "(this may be intentional for external effects)",
                            mod_key, func_name,
                        )

    # 4. Mobile offline_entities must reference existing modules
    mobile = genome.get("mobile", {})
    offline = mobile.get("offline", {})
    for entity_ref in offline.get("offline_entities", []):
        if entity_ref not in modules:
            errors.append(
                f"mobile.offline.offline_entities: '{entity_ref}' is not a defined module"
            )

    # 5. Push notification triggers should reference state machines that exist
    push = mobile.get("push_notifications", {})
    for channel in push.get("channels", []):
        trigger = channel.get("trigger", "")
        # Trigger format: "module.field -> state" or "event_name"
        if "->" in trigger:
            parts = trigger.split("->")[0].strip().split(".")
            if len(parts) >= 1:
                module_ref = parts[0]
                if module_ref not in modules:
                    errors.append(
                        f"mobile.push_notifications.channels[{channel.get('id')}]: "
                        f"trigger references unknown module '{module_ref}'"
                    )

    # 6. Sensitive field paths must reference entities that exist
    for mod_key, mod_def in modules.items():
        for sf in mod_def.get("sensitive_fields", []):
            field_path = sf.get("field", "")
            if "." in field_path:
                entity_part = field_path.split(".")[0]
                if entity_part not in mod_def.get("entities", []):
                    errors.append(
                        f"modules.{mod_key}.sensitive_fields: "
                        f"field '{field_path}' references unknown entity '{entity_part}'"
                    )

    # 7. Identity provider roles should be consistent with operation authorization
    idp_roles = set(genome.get("identity_provider", {}).get("roles", []))
    if idp_roles:
        for mod_key, mod_def in modules.items():
            for op_name, op_def in mod_def.get("operations", {}).items():
                for role in op_def.get("authorization", []):
                    if role not in idp_roles:
                        errors.append(
                            f"modules.{mod_key}.operations.{op_name}: "
                            f"authorization role '{role}' is not in identity_provider.roles"
                        )

    return errors


def compute_quality_score(genome: dict) -> dict:
    """
    Compute the Quality Genome Score (0-100) from genome content.

    Categories:
      Security (25): auth, mfa, encryption, api_keys, rate_limiting
      Reliability (20): health_checks, state_machines, validation, error_handling
      Observability (20): logging, tracing, metrics, alerting
      Testing (20): unit, integration, load, security
      Operations (15): ci_cd, iac, multi_env

    Returns: {"total": int, "breakdown": {category: {score, max, details}}}
    """
    security = genome.get("security", {})
    infra = genome.get("infrastructure", {})
    deployment = genome.get("deployment", {})
    modules = genome.get("modules", {})
    idp = genome.get("identity_provider", {})

    breakdown = {}

    # Security (25 points)
    sec_score = 0
    sec_details = []
    if idp.get("type") == "oidc":
        sec_score += 5
        sec_details.append("OIDC auth: +5")
    elif idp.get("type") == "jwt-local":
        sec_score += 3
        sec_details.append("JWT local auth: +3")
    if security.get("mfa") == "required_for_all":
        sec_score += 5
        sec_details.append("MFA all users: +5")
    elif security.get("mfa") == "required_for_admin":
        sec_score += 3
        sec_details.append("MFA admin only: +3")
    if security.get("encryption_at_rest"):
        sec_score += 5
        sec_details.append("Encryption at rest: +5")
    if security.get("api_keys"):
        sec_score += 5
        sec_details.append("API key management: +5")
    if security.get("rate_limiting"):
        sec_score += 5
        sec_details.append("Rate limiting: +5")
    breakdown["security"] = {"score": min(sec_score, 25), "max": 25, "details": sec_details}

    # Reliability (20 points)
    rel_score = 5  # Health checks always generated
    rel_details = ["Health checks: +5"]
    has_sm = any(mod.get("state_machine") for mod in modules.values())
    if has_sm:
        rel_score += 5
        rel_details.append("State machines: +5")
    has_validation = any(
        any(f.get("required") or f.get("max_length") or f.get("enum_values")
            for fields in mod.get("fields", {}).values()
            for f in fields)
        for mod in modules.values()
    )
    if has_validation:
        rel_score += 5
        rel_details.append("Field validation: +5")
    rel_score += 5  # Error handling always generated
    rel_details.append("Error handling: +5")
    breakdown["reliability"] = {"score": min(rel_score, 20), "max": 20, "details": rel_details}

    # Observability (20 points)
    obs_score = 0
    obs_details = []
    if infra.get("observability") == "opentelemetry":
        obs_score += 10  # Tracing + metrics
        obs_details.append("OpenTelemetry (tracing + metrics): +10")
    obs_score += 5  # Structured logging always generated
    obs_details.append("Structured logging: +5")
    obs_score += 5  # Alerting rules always generated
    obs_details.append("Alerting rules: +5")
    breakdown["observability"] = {"score": min(obs_score, 20), "max": 20, "details": obs_details}

    # Testing (20 points)
    test_score = 15  # Unit + integration + security tests always generated
    test_details = ["Unit tests: +5", "Integration tests: +5", "Security tests: +5"]
    # Load tests if SLAs defined
    has_slas = any(
        mod.get("operations", {}).get(op, {}).get("cache")
        for mod in modules.values()
        for op in mod.get("operations", {})
    )
    if has_slas or len(modules) >= 3:
        test_score += 5
        test_details.append("Load tests: +5")
    breakdown["testing"] = {"score": min(test_score, 20), "max": 20, "details": test_details}

    # Operations (15 points)
    ops_score = 0
    ops_details = []
    ci_cd = deployment.get("ci_cd", {})
    if ci_cd.get("provider") and ci_cd["provider"] != "none":
        ops_score += 5
        ops_details.append(f"CI/CD ({ci_cd['provider']}): +5")
    if deployment.get("target") in ("docker_compose", "kubernetes"):
        ops_score += 5
        ops_details.append(f"IaC ({deployment['target']}): +5")
    if len(deployment.get("environments", [])) >= 2:
        ops_score += 5
        ops_details.append("Multi-environment: +5")
    breakdown["operations"] = {"score": min(ops_score, 15), "max": 15, "details": ops_details}

    total = sum(cat["score"] for cat in breakdown.values())

    return {"total": total, "breakdown": breakdown}
