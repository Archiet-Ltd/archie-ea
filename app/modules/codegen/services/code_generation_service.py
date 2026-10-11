"""Code Generation Service — generates FastAPI project files from UML."""
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PROMPTS = {
    "models": """Generate SQLAlchemy 2.0 models for a FastAPI application.

## UML Class Diagram
{class_diagram_json}

## Config
- Python {python_version}
- Auth: {auth_type}

CRITICAL RULES — violations cause runtime crashes:
1. Python boolean literals ONLY: True and False (capital). NEVER true/false (JSON-style).
   - Correct: nullable=True, nullable=False, primary_key=True
   - Wrong: nullable=true, nullable=false
2. Every model MUST have exactly ONE primary key column: id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
   - NEVER use nullable=False on the id column — primary_key implies NOT NULL
3. __tablename__ MUST be the exact snake_case of the class name with NO extra pluralisation.
   - Class Foo → __tablename__ = "foos"
   - Class FooLog → __tablename__ = "foo_logs"  (one trailing s only)
   - NEVER: class FooLogs → __tablename__ = "foologss"  (double-s is wrong)
4. Every model class MUST include: __table_args__ = {"extend_existing": True}
   This prevents SQLAlchemy MetaData conflicts during test collection.
5. Generate ONE Python file per class. Do NOT put multiple classes in a single entities.py.
6. Schemas: from typing import Any, Dict, List, Optional  (import ALL common types)

Generate one Python file per class. Each file MUST start with: # FILE: app/models/<snake_case_name>.py
Include: imports, class definition with __tablename__ and __table_args__, all fields, relationships.
Also generate:
# FILE: app/models/__init__.py (importing all models)
# FILE: app/database.py (engine, AsyncSessionLocal, Base, get_db dependency, init_db)
# FILE: alembic.ini
# FILE: alembic/env.py""",

    "schemas": """Generate Pydantic v2 schemas for each model class.

## UML Class Diagram
{class_diagram_json}

CRITICAL RULES:
- Import ALL typing utilities: from typing import Any, Dict, List, Optional
- Never use a type without importing it first

Generate Create, Update, and Response schemas per model.
Each file MUST start with: # FILE: app/schemas/<snake_case_name>.py
Also generate: # FILE: app/schemas/__init__.py""",

    "routes": """Generate FastAPI route handlers with full CRUD operations.

## UML Sequence Diagram (API flows)
{sequence_diagram_json}

## UML Class Diagram (data shapes)
{class_diagram_json}

## Config
- Auth: {auth_type}

Generate one router file per resource. Each file MUST start with: # FILE: app/api/routes/<resource>.py
Include: GET list (paginated), GET by id, POST create, PUT update, DELETE.
Also generate:
# FILE: app/api/__init__.py (APIRouter with all sub-routers)
# FILE: app/main.py (FastAPI app, CORS, router inclusion, lifespan)""",

    "services": """Generate service layer classes with business logic.

## UML Component Diagram
{component_diagram_json}

## UML Sequence Diagram (method flows)
{sequence_diagram_json}

Generate one service file per component. Each file MUST start with: # FILE: app/services/<snake_case_name>.py
Services contain business logic called by routes. Use dependency injection via FastAPI Depends.""",

    "integrations": """Generate typed Protocol interfaces for external system integrations.

## External Application Interfaces
{external_interfaces_json}

Generate one file per external system. Each file MUST start with: # FILE: app/integrations/<snake_case_name>.py
Use Python Protocol classes with method signatures. Include docstrings with ArchiMate source references.""",

    "tests": """Generate pytest test files for all routes.

## Generated Route Files
{routes_summary_json}

## UML Class Diagram (test data shapes)
{class_diagram_json}

## Auth: {auth_type}

CRITICAL RULES:
- conftest.py MUST create a test user, call /auth/login, and store the token in an `auth_headers` fixture
- Every test that calls a protected endpoint MUST pass headers=auth_headers to the client
- Use pytest-asyncio with asyncio_mode = "auto" in pytest.ini
- Test DB MUST use sqlite+aiosqlite:///./test.db (NOT plain sqlite:///)
- conftest.py MUST override the DATABASE_URL env var before importing app: os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
- POST endpoint URLs end with the collection path (e.g. /api/resources), not /api/resources/create

Generate one test file per route file. Each file MUST start with: # FILE: tests/test_<resource>.py
Include: test_create, test_read, test_read_list, test_update, test_delete.
Use httpx.AsyncClient with FastAPI TestClient pattern.
Also generate:
# FILE: tests/conftest.py (app fixture, test DB, test client, auth_headers fixture)
# FILE: pytest.ini (asyncio_mode = auto, testpaths = tests)""",

    "infrastructure": """Generate Docker and infrastructure files.

## UML Deployment Diagram
{deployment_diagram_json}

## Config
- Python {python_version}
- Database: PostgreSQL

Generate these files (each MUST start with # FILE: <path>):
# FILE: Dockerfile
# FILE: docker-compose.yml
# FILE: .env.example
# FILE: requirements.txt
# FILE: pyproject.toml

Dockerfile: multi-stage build, non-root user, health check.
docker-compose: app + postgres services, volumes, health checks.
requirements.txt: fastapi, uvicorn[standard], sqlalchemy[asyncio], alembic, pydantic, psycopg2-binary, pytest, httpx.""",
}


class CodeGenerationService:
    """Generates FastAPI project files from enriched UML diagrams."""

    @staticmethod
    def build_prompt(prompt_key: str, uml: Dict, config: Dict, extra_context: Optional[Dict] = None) -> str:
        """Build a generation prompt from UML data and config."""
        try:
            from app.models.ai_service import AIPromptTemplate
            db_key = f"solution_prompt_codegen_{prompt_key}"
            override = AIPromptTemplate.query.filter_by(name=db_key).first()
            template = override.system_prompt if override and override.system_prompt else PROMPTS.get(prompt_key, "")
        except Exception:
            template = PROMPTS.get(prompt_key, "")

        format_vars = {
            "class_diagram_json": json.dumps(uml.get("class_diagram", {}), indent=2),
            "sequence_diagram_json": json.dumps(uml.get("sequence_diagram", {}), indent=2),
            "component_diagram_json": json.dumps(uml.get("component_diagram", {}), indent=2),
            "deployment_diagram_json": json.dumps(uml.get("deployment_diagram", {}), indent=2),
            "python_version": config.get("python_version", "3.12"),
            "auth_type": config.get("auth", "none"),
            "external_interfaces_json": "[]",
            "routes_summary_json": "[]",
        }
        if extra_context:
            format_vars.update(extra_context)

        result = template
        for key, val in format_vars.items():
            result = result.replace("{" + key + "}", str(val))
        return result

    @staticmethod
    def parse_code_response(raw_text: str) -> Dict[str, str]:
        """Parse multi-file code response into {filepath: content} dict."""
        files = {}
        blocks = re.split(r'```\w*\n', raw_text)

        for block in blocks:
            block = block.rstrip('`').strip()
            if not block:
                continue

            file_match = re.match(r'^#\s*FILE:\s*(.+?)$', block, re.MULTILINE)
            if file_match:
                filepath = file_match.group(1).strip()
                content = block[file_match.end():].strip()
                files[filepath] = _sanitize_generated_file(filepath, content)

        return files

    @staticmethod
    def check_daily_limit(solution_id: int) -> bool:
        """Return True if generation is allowed, False if limit reached."""
        from app.modules.codegen.models import CodegenGeneration
        limit = int(os.environ.get("CODEGEN_DAILY_LIMIT", "5"))
        gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
        if not gen or not gen.config:
            return True
        today = datetime.utcnow().strftime("%Y-%m-%d")
        last_reset = (gen.config or {}).get("_daily_reset_date", "")
        if last_reset != today:
            return True
        gen_count = (gen.config or {}).get("_daily_gen_count", 0)
        return gen_count < limit

    @staticmethod
    def increment_daily_count(gen) -> None:
        """Increment the daily generation counter."""
        config = gen.config or {}
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if config.get("_daily_reset_date") != today:
            config["_daily_reset_date"] = today
            config["_daily_gen_count"] = 1
        else:
            config["_daily_gen_count"] = config.get("_daily_gen_count", 0) + 1
        gen.config = config

    @staticmethod
    def generate_all(uml: Dict, config: Dict, solution) -> Dict[str, Any]:
        """Generate all code files from UML. Returns {files: {path: content}, errors: []}."""
        from app.modules.ai_chat.services.llm_service import LLMService

        provider, model = LLMService._get_configured_provider()
        all_files = {}
        errors = []
        prompt_order = ["models", "schemas", "routes", "services", "integrations", "tests", "infrastructure"]

        for prompt_key in prompt_order:
            extra = {}
            if prompt_key == "tests" and all_files:
                route_files = {k: v[:200] for k, v in all_files.items() if "/routes/" in k}
                extra["routes_summary_json"] = json.dumps(list(route_files.keys()), indent=2)
            if prompt_key == "integrations":
                extra["external_interfaces_json"] = json.dumps(
                    [c for c in uml.get("component_diagram", {}).get("components", [])
                     if c.get("type") == "integration"], indent=2
                )

            prompt = CodeGenerationService.build_prompt(prompt_key, uml, config, extra)

            try:
                raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
                if not raw_text:
                    errors.append({"prompt": prompt_key, "error": "Empty response"})
                    continue

                files = CodeGenerationService.parse_code_response(raw_text)
                if files:
                    all_files.update(files)
                else:
                    raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
                    files = CodeGenerationService.parse_code_response(raw_text)
                    if files:
                        all_files.update(files)
                    else:
                        errors.append({"prompt": prompt_key, "error": "Failed to parse code"})
            except Exception as e:
                logger.error("Code generation failed for %s: %s", prompt_key, e)
                errors.append({"prompt": prompt_key, "error": str(e)})

        # JWIRE-004: Post-process UML to inject real ArchiMate element IDs for provenance.
        # The LLM prompt asks for source_element_id but output is unreliable (often stays 123 or ?).
        # Build a name→id lookup from SolutionArchiMateElement + ArchiMateElement and patch it in,
        # then add # ARCHIMATE_SOURCE: <id> at the top of generated model files.
        try:
            from app import db as _db_j4
            from app.models.solution_archimate_element import SolutionArchiMateElement as _SAE_j4
            from app.models.archimate_core import ArchiMateElement as _AE_j4

            if solution and getattr(solution, "id", None):
                ae_rows = (
                    _db_j4.session.query(_AE_j4.id, _AE_j4.name)
                    .join(_SAE_j4, _SAE_j4.element_id == _AE_j4.id)
                    .filter(_SAE_j4.solution_id == solution.id)
                    .all()
                )
                name_to_id: dict = {}
                if not ae_rows:
                    logger.warning(
                        "JWIRE-004: no ArchiMate elements linked to solution %s — "
                        "trace markers will be absent from generated files",
                        solution.id,
                    )
                for ae_id, ae_name in ae_rows:
                    _nm = ae_name.lower()
                    # Strip "data/dto/entity/model/record/schema" suffix so
                    # "TaskData" element matches generated file "task.py"
                    import re as _re_j4
                    _nm_stripped = _re_j4.sub(r"(data|dto|entity|model|record|schema)$", "", _nm).rstrip("_")
                    for key in (
                        _nm,
                        _nm.replace(" ", "_").replace("-", "_"),
                        _nm.replace(" ", "_").replace("-", "_").rstrip("s"),
                        # Strip all separators — catches "Comment Data" → "commentdata"
                        # matching generated file stems like commentdata.py
                        _nm.replace(" ", "").replace("_", "").replace("-", ""),
                        _nm_stripped,
                        _nm_stripped.replace(" ", "_").replace("-", "_"),
                    ):
                        name_to_id.setdefault(key, ae_id)

                for cls in uml.get("class_diagram", {}).get("classes", []):
                    existing = cls.get("source_element_id")
                    if existing and str(existing) not in ("?", "123"):
                        continue
                    cls_name = cls.get("name", "").lower()
                    table = cls.get("table_name", cls_name).lower()
                    for key in (cls_name, table, table.rstrip("s")):
                        if key in name_to_id:
                            cls["source_element_id"] = name_to_id[key]
                            break

                for path, content in list(all_files.items()):
                    if "/models/" in path and path.endswith(".py"):
                        model_stem = path.split("/")[-1].replace(".py", "").lower()
                        elem_id = (
                            name_to_id.get(model_stem)
                            or name_to_id.get(model_stem.rstrip("s"))
                        )
                        if elem_id and "# ARCHIMATE_SOURCE:" not in content:
                            all_files[path] = f"# ARCHIMATE_SOURCE: {elem_id}\n" + content
        except Exception as _j4_err:
            logger.error(
                "JWIRE-004 provenance injection failed for solution %s: %s",
                solution.id if solution else "unknown", _j4_err, exc_info=True,
            )

        # Documentation (Jinja template, not LLM)
        try:
            readme = _generate_readme(solution, uml, config, all_files)
            all_files["README.md"] = readme
            arch_md = _generate_architecture_md(uml)
            all_files["ARCHITECTURE.md"] = arch_md
        except Exception as e:
            logger.error("Documentation generation failed: %s", e)
            errors.append({"prompt": "documentation", "error": str(e)})

        # GAP-08: Syntax validation on Python files
        syntax_warnings = []
        for path, content in all_files.items():
            if path.endswith(".py"):
                err = _check_python_syntax(content, path)
                if err:
                    syntax_warnings.append({"file": path, "error": err})

        return {
            "files": all_files,
            "errors": errors,
            "groups_succeeded": len(prompt_order) - len([e for e in errors if e["prompt"] in prompt_order]),
            "groups_total": len(prompt_order),
            "syntax_warnings": syntax_warnings,
        }

    @staticmethod
    def enrich_deterministic_output(files: Dict[str, str], uml: Dict, config: Dict,
                                    solution_context: str = "",
                                    business_rules: list = None) -> Dict[str, Any]:
        """LLM enrichment layer for deterministic-generated code.

        Takes service and route files from the deterministic generator and improves them:
        - Fills TODO/pass stubs with real business logic derived from the UML
        - Adds docstrings to all public methods
        - Adds input validation at method entry points
        - ENFORCES business rules from the solution's motivation layer — constraints,
          goals, and quality attributes are injected as concrete validation code, not
          comments. This is the primary mechanism that makes generated code domain-correct
          rather than generic.

        Only touches business logic files (app/services/, app/api/routes/) —
        infrastructure, models, and test scaffolding are left as-is.

        Returns {"files": {path: enriched_content}, "errors": [...]}
        """
        from app.modules.ai_chat.services.llm_service import LLMService

        # Only enrich files that contain business logic — not infra or generated models
        enrichable = {
            path: content for path, content in files.items()
            if (path.startswith("app/services/") or path.startswith("app/api/routes/"))
            and path.endswith(".py")
            and len(content) < 8000  # skip very large files to stay within token budget
        }

        if not enrichable:
            return {"files": {}, "errors": []}

        # Compact UML summary — only what's needed for business logic decisions
        classes_summary = [
            {
                "name": c.get("name", ""),
                "fields": [f.get("name", "") for f in c.get("fields", [])[:6]],
                "description": c.get("description", ""),
            }
            for c in uml.get("class_diagram", {}).get("classes", [])[:10]
        ]
        flows_summary = [
            {
                "name": f.get("name", ""),
                "path": f.get("path", ""),
                "method": f.get("http_method", ""),
                "steps": [s.get("action", "") for s in f.get("steps", [])[:4]],
            }
            for f in uml.get("sequence_diagram", {}).get("flows", [])[:8]
        ]
        uml_summary = json.dumps({"classes": classes_summary, "flows": flows_summary}, indent=2)

        files_block = "\n\n".join(
            f"# FILE: {path}\n```python\n{content}\n```"
            for path, content in enrichable.items()
        )

        arch_context_block = (
            f"## Solution Architecture Context\n{solution_context}\n\n"
            if solution_context else ""
        )

        # Business rules block — must/should rules become enforcement code, not comments
        rules_block = ""
        if business_rules:
            must_rules = [r for r in business_rules if r.get("severity") == "must"]
            should_rules = [r for r in business_rules if r.get("severity") == "should"]
            if must_rules or should_rules:
                rules_lines = [
                    "## Business Rules — ENFORCE IN CODE (not as comments)\n",
                    "These rules come from the solution's formal constraint and goal model. "
                    "For every MUST rule, generate a concrete enforcement: a validator, a guard clause, "
                    "a pre-save check, or a route dependency. Raise HTTP 400/422 when violated. "
                    "Do NOT leave them as TODO comments or docstrings.\n",
                ]
                if must_rules:
                    rules_lines.append("### MUST enforce (raise HTTP 400/422 if violated):")
                    for r in must_rules[:10]:
                        name = r.get("name", "")
                        cond = (r.get("condition") or "")[:120]
                        src = r.get("source", "")
                        val = r.get("value", "") or r.get("target_value", "") or ""
                        val_str = f" [target: {val}]" if val else ""
                        rules_lines.append(f"- **{name}**{val_str}: {cond} (source: {src})")
                if should_rules:
                    rules_lines.append("\n### SHOULD enforce (log warning or return 400 if violated):")
                    for r in should_rules[:8]:
                        name = r.get("name", "")
                        cond = (r.get("condition") or "")[:100]
                        rules_lines.append(f"- **{name}**: {cond}")
                rules_lines.append(
                    "\nFor each MUST rule: write a helper function like `_validate_{rule_name_snake}(value)` "
                    "in the service file and call it in the relevant route handler before committing data."
                )
                rules_block = "\n".join(rules_lines) + "\n\n"

        prompt = (
            "You are improving scaffold code generated from an ArchiMate architecture model.\n\n"
            f"{arch_context_block}"
            f"{rules_block}"
            "## Architecture Context (UML summary)\n"
            f"{uml_summary}\n\n"
            "## Task\n"
            "For each file below:\n"
            "1. Replace any TODO comments or bare `pass` stubs with real business logic "
            "that reflects the UML flows and field names above\n"
            "2. For every MUST business rule above: generate a `_validate_<rule>()` function "
            "and call it in the relevant route/service method before writing to the database. "
            "Raise `HTTPException(status_code=422, detail='<rule name> violated: <reason>')` "
            "when the rule is violated\n"
            "3. Add a one-line docstring to every public class and method\n"
            "4. Add `if not <param>: raise ValueError(...)` validation at method entry "
            "where parameters are required\n"
            "5. Keep ALL existing import statements, function signatures, and class names "
            "exactly as they are — only fill in bodies\n\n"
            "Return ONLY the improved versions of the files listed below. "
            "Each file MUST begin with its path marker: # FILE: <path>\n\n"
            f"{files_block}"
        )

        try:
            provider, model = LLMService._get_configured_provider()
            raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
            if not raw_text:
                return {"files": {}, "errors": [{"prompt": "enrichment", "error": "Empty LLM response"}]}

            enriched = CodeGenerationService.parse_code_response(raw_text)
            # Safety: only accept files that were in the enrichable set — never add new files
            valid = {k: v for k, v in enriched.items() if k in enrichable}
            logger.info("LLM enrichment: %d/%d files improved", len(valid), len(enrichable))
            return {"files": valid, "errors": []}

        except Exception as exc:
            logger.warning("LLM enrichment failed (deterministic output kept): %s", exc)
            return {"files": {}, "errors": [{"prompt": "enrichment", "error": str(exc)}]}


def _sanitize_generated_file(filepath: str, content: str) -> str:
    """Post-process LLM output to fix known systematic generation bugs.

    Applied to every file immediately after parsing — before storage or syntax check.
    Safe to apply unconditionally: each fix only activates when the bad pattern is present.
    """
    if not filepath.endswith(".py"):
        return content

    lines = content.splitlines()
    out = []
    for line in lines:
        stripped = line.strip()

        # Fix 1: nullable=false/true → Python booleans (LLM outputs JSON booleans)
        if "nullable=false" in line or "nullable=true" in line:
            line = re.sub(r'\bnullable=false\b', 'nullable=False', line)
            line = re.sub(r'\bnullable=true\b', 'nullable=True', line)

        # Fix 2: id column must have primary_key=True (never nullable=False/True alone)
        if (stripped.startswith('id = Column(') or stripped.startswith('id=Column(')) and \
                'primary_key' not in line and 'nullable=' in line:
            line = re.sub(r',\s*nullable=(?:False|True)', ', primary_key=True', line, count=1)

        # Fix 3: double-s __tablename__ (e.g. "foologss" → "foologes" won't happen,
        # but "networkpolicyconfigurationss" → strip one trailing s)
        if '__tablename__' in line:
            line = re.sub(
                r'(__tablename__\s*=\s*")(\w+ss)(")',
                lambda m: m.group(1) + m.group(2)[:-1] + m.group(3),
                line,
            )

        # Fix 4: schemas/models.py — ensure Any is in typing imports when used
        # (handled at file level below)

        out.append(line)

    result = '\n'.join(out)

    # Fix 5: schemas files using Any/Dict/List without importing them
    if filepath.startswith('app/schemas/') and 'from typing import' in result:
        result = re.sub(
            r'from typing import ([^\n]+)',
            lambda m: 'from typing import ' + _ensure_typing_imports(m.group(1), result),
            result,
            count=1,
        )

    # Fix 6: model files missing __table_args__ extend_existing
    if filepath.startswith('app/models/') and 'Base):' in result and 'extend_existing' not in result:
        result = re.sub(
            r'(__tablename__\s*=\s*"[^"]+"\s*\n)',
            r'\1    __table_args__ = {"extend_existing": True}\n',
            result,
        )

    return result


def _ensure_typing_imports(current_imports: str, file_content: str) -> str:
    """Return expanded typing import list that covers all types used in the file."""
    needed = set(x.strip() for x in current_imports.split(','))
    for typ in ('Any', 'Dict', 'List', 'Optional', 'Union', 'Tuple'):
        if re.search(r'\b' + typ + r'\b', file_content) and typ not in needed:
            needed.add(typ)
    return ', '.join(sorted(needed))


def _check_python_syntax(content: str, path: str) -> str:
    """Return None if syntax OK, error string if broken (GAP-08)."""
    try:
        compile(content, path, "exec")
        return None
    except SyntaxError as e:
        return f"Line {e.lineno}: {e.msg}"


def _generate_readme(solution, uml, config, files):
    """Generate README.md using Jinja template, incorporating blueprint architectural decisions."""
    narratives = getattr(solution, "section_narratives", None) or {}
    _language = config.get("language", "python-fastapi")
    _port_map = {"python-flask": "5000", "python-fastapi": "8000", "go-chi": "8080",
                 "java-spring-boot": "8080", "react-shadcn": "3000"}
    _port = _port_map.get(_language, "8000")
    try:
        from flask import current_app
        template = current_app.jinja_env.get_template("codegen/readme.md.jinja")
        return template.render(
            solution_name=solution.name or "Untitled",
            description=getattr(solution, "description", "") or getattr(solution, "problem_statement", "") or "",
            python_version=config.get("python_version", "3.12"),
            auth_type=config.get("auth", "none"),
            language=_language,
            port=_port,
            classes=uml.get("class_diagram", {}).get("classes", []),
            flows=uml.get("sequence_diagram", {}).get("flows", []),
            file_list=sorted(files.keys()),
            business_domain=getattr(solution, "business_domain", "") or "",
            solution_type=getattr(solution, "solution_type", "") or "",
            complexity_level=getattr(solution, "complexity_level", "") or "",
            security_narrative=narratives.get("security_viewpoint", ""),
            nfr_narrative=narratives.get("nfr_satisfaction", ""),
            deployment_narrative=narratives.get("deployment_view", ""),
            blueprint_version=getattr(solution, "blueprint_version", 1) or 1,
        )
    except Exception as e:
        logger.warning("Jinja README template failed, using fallback: %s", e)
        classes = uml.get("class_diagram", {}).get("classes", [])
        uml.get("sequence_diagram", {}).get("flows", [])
        lines = [f"# {solution.name or 'Untitled'}", "", "Generated by Entelim", ""]
        for c in classes:
            lines.append(f"- **{c.get('name', '')}** — {c.get('description', '')}")
        return "\n".join(lines)


def _generate_architecture_md(uml):
    """Generate ARCHITECTURE.md traceability manifest."""
    lines = ["# Architecture Traceability", "", "Maps generated files to ArchiMate source elements.", ""]
    classes = uml.get("class_diagram", {}).get("classes", [])
    for cls in classes:
        source_id = cls.get("source_element_id", "?")
        table = cls.get("table_name", cls["name"].lower())
        lines.append(f"## {cls['name']} (ArchiMate #{source_id})")
        lines.append(f"- Model: `app/models/{table}.py`")
        lines.append(f"- Schema: `app/schemas/{table}.py`")
        lines.append(f"- Description: {cls.get('description', '')}")
        lines.append("")
    return "\n".join(lines)
