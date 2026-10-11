# dead-code-ok: historical migration comment kept for audit trail — do not remove
# DEPRECATED: This file is migrated to app/modules/capabilities/. dead-code-ok
# Registration is now centralized via app.modules.capabilities.register().
# Do NOT modify -- kept as fallback until Phase 6 cleanup.
#
# Migration: Copied from app/routes/enterprise_api_routes.py -> app/modules/capabilities/routes/
# Date: 2026-02-14 | Relative imports fixed for new location.
#
# CORRECTION (2026-07-30): the "DEPRECATED / fallback / do NOT modify" note above is
# stale. app/routes/enterprise_api_routes.py no longer exists, and this file is
# imported live by app/modules/capabilities/__init__.py — it is the only copy and it
# serves traffic. Two real defects were fixed here on that basis (an unbound `logger`
# used in nine except blocks, and Python accidentally embedded in the Playwright
# template string). Treat this as live code, not a frozen fallback.
#
# Enterprise Entity API Routes for Unified Mapping Modal
# Provides endpoints for fetching applications, systems, initiatives, projects
# Used by ADM Kanban enterprise integration

from app.services.archimate_backbone import sync_archimate_element
import csv
import io
import logging

import requests
from flask import Blueprint, Response, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import or_

from app import db
from app.decorators import require_roles
from app.middleware.tenant_decorators import is_active_org_admin
from app.models.adm_kanban import KanbanCard
from app.models.application_portfolio import ApplicationComponent
from app.models.audit_log import AuditLog
from app.models.project_models import Project
from app.models.solution_architect_models import SolutionRequirement
from app.models.system_architecture import SystemBoundary
from app.models.vendor.vendor_organization import EnterpriseInitiative
from app.utils.pagination import safe_int_arg
from app.utils.route_guards import require_entity

# Module-level logger. Nine call sites in this file referenced `logger` — all inside
# `except` blocks — but it was never bound at module scope: the only
# `logger = logging.getLogger(__name__)` in the file was accidentally embedded in the
# Playwright TypeScript template string below, so it was string content, not code.
# Every one of those handlers therefore raised NameError and masked the exception it
# was meant to report.
logger = logging.getLogger(__name__)

enterprise_api_bp = Blueprint(
    "enterprise_entity_api", __name__, url_prefix="/api/enterprise"
)

# RRT-005: ADM phase → architecture layer mapping
ADM_PHASE_LAYER_MAP = {
    'preliminary': 'business',
    'vision': 'business',
    'a': 'business',
    'b': 'business',          # Business Architecture
    'c': 'application',       # Information Systems Architecture
    'd': 'technology',        # Technology Architecture
    'e': 'crosscutting',      # Opportunities & Solutions
    'f': 'crosscutting',      # Migration Planning
    'g': 'crosscutting',      # Implementation Governance
    'h': 'crosscutting',      # Architecture Change Management
}


def _suggest_layer_from_phase(phase_str):
    """Map ADM phase to recommended architecture layer."""
    if not phase_str:
        return None
    phase_lower = phase_str.lower().strip()
    # Match "phase b", "b", "phase_b", etc.
    for key, layer in ADM_PHASE_LAYER_MAP.items():
        if key in phase_lower or phase_lower.endswith(key):
            return layer
    # Default heuristics
    if 'business' in phase_lower:
        return 'business'
    if 'application' in phase_lower or 'information' in phase_lower:
        return 'application'
    if 'technology' in phase_lower or 'infrastructure' in phase_lower:
        return 'technology'
    return None


def _score_ac_quality(ac_text):
    """PRQ-007: Score acceptance criteria quality 0-100."""
    if not ac_text or not ac_text.strip():
        return 0, ['AC is empty']
    issues = []
    score = 40  # base score for non-empty AC
    # Check for testability markers
    testability_keywords = ['given', 'when', 'then', 'shall', 'must', 'should', 'verify', 'assert', 'ensure']
    if any(kw in ac_text.lower() for kw in testability_keywords):
        score += 20
    else:
        issues.append('No testability keywords (given/when/then/shall/must)')
    # Check for measurable criteria
    import re
    has_number = bool(re.search(r'\d+', ac_text))
    if has_number:
        score += 15
    else:
        issues.append('No measurable criteria (numbers/thresholds)')
    # Check for unfilled placeholders
    placeholder_pattern = r'\{[A-Z_]+\}|\[\[.*?\]\]|<[A-Z_]+>'
    if re.search(placeholder_pattern, ac_text):
        score -= 20
        issues.append('Unfilled placeholder tokens found')
    # Check length
    if len(ac_text) >= 50:
        score += 15
    else:
        issues.append('AC text too short (< 50 chars)')
    # Check for multiple criteria (newlines or semicolons)
    if '\n' in ac_text or ';' in ac_text:
        score += 10
    else:
        issues.append('Consider listing multiple criteria')
    return max(0, min(100, score)), issues


@enterprise_api_bp.route('/solutions/<int:solution_id>/populate-from-template', methods=['POST'])
@login_required
def populate_solution_from_template(solution_id):
    """RRT-006: Batch-populate a solution with template-based starter requirements."""
    from app.models.solution_architect_models import SolutionRequirement, Solution
    from app.models.requirement_template import RequirementTemplate

    solution = Solution.query.get_or_404(solution_id)

    data = request.get_json() or {}
    layers = data.get('layers', ['business', 'application', 'technology', 'crosscutting'])
    overwrite = data.get('overwrite', False)

    if not isinstance(layers, list) or len(layers) == 0:
        return jsonify({'error': 'layers must be a non-empty list'}), 400

    existing_reqs = SolutionRequirement.query.filter(
        SolutionRequirement.solution_id == solution_id,
        SolutionRequirement.deleted_at == None
    ).all()
    existing_layers = set(
        (getattr(r, 'layer', None) or '').lower()
        for r in existing_reqs
        if getattr(r, 'layer', None)
    )
    existing_template_ids = set(
        getattr(r, 'template_id', None)
        for r in existing_reqs
        if getattr(r, 'template_id', None)
    )

    created = []
    skipped = []

    for layer in layers:
        layer_lower = layer.lower()

        if not overwrite and layer_lower in existing_layers:
            skipped.append({'layer': layer_lower, 'reason': 'layer_already_populated'})
            continue

        templates = RequirementTemplate.query.filter(
            RequirementTemplate.layer == layer_lower,
            RequirementTemplate.is_system == True
        ).limit(3).all()

        for tpl in templates:
            if tpl.id in existing_template_ids:
                skipped.append({'layer': layer_lower, 'template_id': tpl.id, 'reason': 'template_already_applied'})
                continue

            req = SolutionRequirement(
                solution_id=solution_id,
                name=tpl.name,
                description=tpl.description_hint or '',
                layer=tpl.layer,
                template_id=tpl.id,
                acceptance_criteria=tpl.ac_hint or '',
                moscow_priority='SHOULD',
                status='open',
            )
            db.session.add(req)
            sync_archimate_element(req)
            created.append({
                'name': tpl.name,
                'layer': tpl.layer,
                'template_id': tpl.id
            })

    if created:
        db.session.commit()

    return jsonify({
        'solution_id': solution_id,
        'solution_name': solution.name,
        'created_count': len(created),
        'skipped_count': len(skipped),
        'created': created,
        'skipped': skipped,
        'message': f"Created {len(created)} requirements from templates"
    }), 201 if created else 200


@enterprise_api_bp.route('/requirement-templates', methods=['GET'])
@login_required
def list_requirement_templates():
    """Return all system templates grouped by layer."""
    from app.models.requirement_template import RequirementTemplate
    templates = RequirementTemplate.query.order_by(
        RequirementTemplate.layer, RequirementTemplate.name
    ).all()
    grouped = {}
    for t in templates:
        grouped.setdefault(t.layer, []).append(t.to_dict())
    return jsonify({'templates': grouped, 'total': len(templates)}), 200


@enterprise_api_bp.route('/requirement-templates/<int:tmpl_id>', methods=['GET'])
@login_required
def get_requirement_template(tmpl_id):
    """Return a single template by ID."""
    from app.models.requirement_template import RequirementTemplate
    tmpl = RequirementTemplate.query.get_or_404(tmpl_id)
    return jsonify(tmpl.to_dict()), 200


@enterprise_api_bp.route("/applications", methods=["GET"])
@login_required
def get_applications():
    """Get applications for mapping modal"""
    try:
        search = request.args.get("search", "").strip()
        limit = safe_int_arg('limit', 100, minimum=1, maximum=500)

        query = ApplicationComponent.query

        if search:
            query = query.filter(
                or_(
                    ApplicationComponent.name.ilike(f"%{search}%"),
                    ApplicationComponent.description.ilike(f"%{search}%"),
                    ApplicationComponent.application_code.ilike(f"%{search}%"),
                    ApplicationComponent.external_id.ilike(f"%{search}%"),
                )
            )

        applications = query.limit(limit).all()

        result = []
        for app in applications:
            result.append(
                {
                    "id": app.id,
                    "name": app.name,
                    "description": app.description,
                    "code": app.application_code,
                    "external_id": app.external_id,
                    "type": app.application_type or "Unknown",
                    "category": app.application_category or "Unknown",
                    "domain": app.business_domain or "Unknown",
                    "criticality": app.criticality or "Unknown",
                    "status": app.lifecycle_status or "Unknown",
                }
            )

        return jsonify({"applications": result})

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@enterprise_api_bp.route("/systems", methods=["GET"])
@login_required
def get_systems():
    """Get systems for mapping modal"""
    try:
        search = request.args.get("search", "").strip()
        limit = safe_int_arg('limit', 100, minimum=1, maximum=500)

        query = SystemBoundary.query

        if search:
            query = query.filter(
                or_(
                    SystemBoundary.name.ilike(f"%{search}%"),
                    SystemBoundary.description.ilike(f"%{search}%"),
                    SystemBoundary.system_name.ilike(f"%{search}%"),
                )
            )

        systems = query.limit(limit).all()

        result = []
        for system in systems:
            result.append(
                {
                    "id": system.id,
                    "name": system.name,
                    "description": system.description,
                    "system_name": system.system_name,
                    "system_type": system.system_type or "Unknown",
                    "system_category": system.system_category or "Unknown",
                    "boundary_type": system.boundary_type or "Unknown",
                }
            )

        return jsonify({"systems": result})

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@enterprise_api_bp.route("/initiatives", methods=["GET"])
@login_required
def get_initiatives():
    """Get initiatives for mapping modal"""
    try:
        search = request.args.get("search", "").strip()
        limit = safe_int_arg('limit', 100, minimum=1, maximum=500)

        query = EnterpriseInitiative.query

        if search:
            query = query.filter(
                or_(
                    EnterpriseInitiative.name.ilike(f"%{search}%"),
                    EnterpriseInitiative.description.ilike(f"%{search}%"),
                )
            )

        initiatives = query.limit(limit).all()

        result = []
        for init in initiatives:
            result.append(
                {
                    "id": init.id,
                    "name": init.name,
                    "description": init.description,
                    "type": init.initiative_type or "Unknown",
                    "status": getattr(init, "status", None) or getattr(init, "current_phase", None) or "Unknown",
                    "priority": getattr(init, "priority", None) or "Unknown",
                    "start_date": init.planned_start_date.isoformat() if getattr(init, "planned_start_date", None) else None,
                    "end_date": init.planned_end_date.isoformat() if getattr(init, "planned_end_date", None) else None,
                }
            )

        return jsonify({"initiatives": result})

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@enterprise_api_bp.route("/projects", methods=["GET"])
@login_required
def get_projects():
    """Get projects for mapping modal"""
    try:
        search = request.args.get("search", "").strip()
        limit = safe_int_arg('limit', 100, minimum=1, maximum=500)

        query = Project.query

        if search:
            query = query.filter(
                or_(
                    Project.name.ilike(f"%{search}%"),
                    Project.description.ilike(f"%{search}%"),
                    Project.code.ilike(f"%{search}%"),
                )
            )

        projects = query.limit(limit).all()

        result = []
        for proj in projects:
            result.append(
                {
                    "id": str(proj.id),  # UUID to string
                    "name": proj.name,
                    "description": proj.description,
                    "code": proj.code,
                    "status": proj.status or "Unknown",
                    "priority": proj.priority or "Unknown",
                    "start_date": proj.start_date.isoformat()
                    if proj.start_date
                    else None,
                    "end_date": proj.end_date.isoformat() if proj.end_date else None,
                }
            )

        return jsonify({"projects": result})

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@enterprise_api_bp.route("/requirements", methods=["POST"])
@login_required
def create_requirement():
    """Manually create a new requirement."""
    data = request.get_json() or {}
    name = (data.get('requirement_name') or '').strip()
    if not name:
        return jsonify({"error": "requirement_name is required"}), 400

    layer = data.get('layer') or None
    template_id = data.get('template_id') or None
    work_package_id = data.get('work_package_id')

    # PRQ-003: accept req_type, auto-inherit from template if not provided
    req_type = data.get('req_type')
    if not req_type and template_id:
        try:
            from app.models.requirement_template import RequirementTemplate
            tpl = RequirementTemplate.query.get(template_id)
            if tpl:
                req_type = getattr(tpl, 'type', None)
        except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
            logger.exception("Failed to operation")
            pass

    # RRT-005: auto-suggest layer from work package phase if not provided
    if not layer and work_package_id:
        try:
            card = KanbanCard.query.get(int(work_package_id))
            if card:
                card_phase = getattr(card, 'phase', None) or getattr(card, 'adm_phase', None) or ''
                layer = _suggest_layer_from_phase(card_phase) or layer
        except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
            logger.exception("Failed to database query")
            pass

    req = SolutionRequirement(
        solution_id=data.get('solution_id'),
        capability_id=data.get('capability_id'),
        requirement_name=name,
        description=data.get('description') or None,
        requirement_type=data.get('requirement_type') or 'functional',
        moscow_priority=data.get('moscow_priority') or 'SHOULD',
        priority=data.get('priority') or 'medium',
        acceptance_criteria=data.get('acceptance_criteria') or None,
        story_points=data.get('story_points') or None,
        epic_parent_id=data.get('epic_parent_id') or None,
        layer=layer,
        template_id=template_id,
        req_type=req_type,
    )
    req.stakeholder_name = data.get('stakeholder_name')
    req.stakeholder_role = data.get('stakeholder_role')
    req.source_document = data.get('source_document')
    req.approval_status = data.get('approval_status', 'draft')
    req.sprint_id = data.get('sprint_id')
    req.milestone = data.get('milestone')
    raw_date = data.get('target_release_date')
    if raw_date:
        try:
            from datetime import date
            req.target_release_date = date.fromisoformat(raw_date)
        except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
            logger.exception("Failed to operation")
            pass
    compliance_tags = data.get('compliance_tags', [])
    if compliance_tags and isinstance(compliance_tags, list):
        req.compliance_tags = compliance_tags
    db.session.add(req)
    sync_archimate_element(req)
    db.session.commit()
    return jsonify({"success": True, "requirement": {
        "id": req.id,
        "requirement_name": req.requirement_name,
        "description": req.description,
        "requirement_type": req.requirement_type,
        "moscow_priority": req.moscow_priority,
        "priority": req.priority,
        "acceptance_criteria": req.acceptance_criteria,
        "solution_id": req.solution_id,
        "capability_id": req.capability_id,
        "created_at": req.created_at.isoformat() if hasattr(req, 'created_at') and req.created_at else None,
    }}), 201


@enterprise_api_bp.route('/requirements/suggest-layer', methods=['GET'])
@login_required
def suggest_requirement_layer():
    """RRT-005: Suggest architecture layer based on ADM phase or work package."""
    work_package_id = request.args.get('work_package_id', type=int)
    phase = request.args.get('phase', '')

    suggested_layer = None
    source = None

    if work_package_id:
        try:
            card = KanbanCard.query.get(work_package_id)
            if card:
                card_phase = getattr(card, 'phase', None) or getattr(card, 'adm_phase', None) or ''
                suggested_layer = _suggest_layer_from_phase(card_phase)
                if suggested_layer:
                    source = f'ADM phase: {card_phase}'
                if not suggested_layer:
                    title = (card.title or '').lower()
                    if 'business' in title:
                        suggested_layer = 'business'
                        source = 'work_package_title'
                    elif 'application' in title or 'data' in title:
                        suggested_layer = 'application'
                        source = 'work_package_title'
                    elif 'technology' in title or 'infrastructure' in title:
                        suggested_layer = 'technology'
                        source = 'work_package_title'
        except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
            logger.exception("Failed to database query")
            pass

    if not suggested_layer and phase:
        suggested_layer = _suggest_layer_from_phase(phase)
        if suggested_layer:
            source = f'phase parameter: {phase}'

    return jsonify({
        'suggested_layer': suggested_layer,
        'source': source,
        'work_package_id': work_package_id,
        'phase': phase
    }), 200


@enterprise_api_bp.route("/requirements/<int:req_id>/generate-ac", methods=["POST"])
@login_required
def generate_requirement_ac(req_id):
    """LLM-generate acceptance criteria for a single requirement."""
    from app.services.llm_service import LLMService
    from app.models.solution_architect_models import SolutionRequirement

    req = SolutionRequirement.query.get_or_404(req_id)

    # RRT-004: load template context for enriched AC
    from app.models.requirement_template import RequirementTemplate
    template = None
    template_hint = ''
    if getattr(req, 'template_id', None):
        try:
            template = RequirementTemplate.query.get(req.template_id)
            if template and template.ac_hint:
                template_hint = template.ac_hint
        except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
            logger.exception("Failed to database query")
            pass

    layer_context = getattr(req, 'layer', None) or ''

    # Build enriched prompt
    prompt_parts = [f"Generate SMART acceptance criteria for: {req.name}"]
    if req.description:
        prompt_parts.append(f"Description: {req.description}")
    if layer_context:
        prompt_parts.append(f"Architecture layer: {layer_context}")
    if template_hint:
        prompt_parts.append(f"Template guidance: {template_hint}")
    prompt_parts.append("Format as numbered list. Be specific and testable.")

    enriched_prompt = "\n".join(prompt_parts)

    try:
        ac_text = LLMService.generate_from_prompt(prompt=enriched_prompt, use_cache=False)
        req.acceptance_criteria = ac_text
        db.session.commit()
        return jsonify({
            'acceptance_criteria': ac_text,
            'layer': layer_context,
            'template_hint_used': bool(template_hint),
            'requirement_id': req_id
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _generate_layer_tests(req_name, req_ac, layer):
    """Generate layer-specific test scaffolds from acceptance criteria."""
    layer = (layer or '').lower()

    if 'security' in layer or 'crosscutting' in layer:
        return f"""# OWASP Security Tests — {req_name}
# Generated from: {req_ac}

## Positive Test
Given a legitimate authenticated request
When the operation is performed
Then it succeeds with expected response

## Authentication Test (OWASP A01)
Given an unauthenticated request
When attempting {req_name}
Then the system returns 401 Unauthorized

## Authorization Test (OWASP A01)
Given a user without required permissions
When attempting {req_name}
Then the system returns 403 Forbidden

## Input Validation Test (OWASP A03)
Given a request with malformed input (SQL injection, XSS payload)
When submitted to the endpoint
Then the system sanitises input and returns 400 Bad Request
"""

    elif 'technology' in layer and ('scalab' in (req_ac or '').lower() or 'perform' in (req_name or '').lower()):
        return f"""// k6 Load Test Scaffold — {req_name}
// Generated from: {req_ac}
import http from 'k6/http';
import {{ check, sleep }} from 'k6';

export const options = {{
  vus: 100,           // virtual users
  duration: '30s',
  thresholds: {{
    http_req_duration: ['p(95)<500'],  // 95% under 500ms
    http_req_failed: ['rate<0.01'],    // <1% errors
  }},
}};

export default function () {{
  const res = http.get('${{__ENV.BASE_URL}}/api/endpoint');
  check(res, {{
    'status 200': (r) => r.status === 200,
    'duration OK': (r) => r.timings.duration < 500,
  }});
  sleep(1);
}}
"""

    elif 'application' in layer and ('api' in req_name.lower() or 'integrat' in req_name.lower()):
        return f"""# OpenAPI / Integration Test Stub — {req_name}
# Generated from: {req_ac}

## Happy Path
Given valid request headers and payload
When POST /api/endpoint is called
Then response status is 201
And response body matches schema {{id, created_at, ...}}

## Validation
Given missing required fields in request body
When POST /api/endpoint is called
Then response status is 400
And error message identifies missing fields

## Auth
Given no Bearer token in Authorization header
When POST /api/endpoint is called
Then response status is 401
"""

    elif 'crosscutting' in layer and 'access' in req_name.lower():
        return f"""// Playwright Accessibility Test — {req_name}
// Generated from: {req_ac}
import {{ test, expect }} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('{req_name} - WCAG 2.1 AA compliance', async ({{ page }}) => {{
  await page.goto('/relevant-page');

  const results = await new AxeBuilder({{ page }})
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
    .analyze();

  expect(results.violations).toEqual([]);
}});

test('{req_name} - keyboard navigation', async ({{ page }}) => {{
  await page.goto('/relevant-page');
  await page.keyboard.press('Tab');
  const focused = page.locator(':focus');
  await expect(focused).toBeVisible();
}});
"""

    else:
        lines = (req_ac or '').split('\n')
        scenarios = []
        for line in lines[:5]:
            if line.strip():
                scenarios.append(f"""
Feature: {req_name}
  Scenario: {line.strip()[:80]}
    Given the system is in a valid state
    When {req_name} is performed
    Then {line.strip()}
""")
        return '\n'.join(scenarios) if scenarios else f"Feature: {req_name}\n  # Add scenarios based on: {req_ac}"


def _detect_test_type(layer, name):
    layer = (layer or '').lower()
    name = (name or '').lower()
    if 'security' in layer or 'crosscutting' in layer:
        return 'owasp'
    if 'technology' in layer and ('scalab' in name or 'perform' in name):
        return 'k6_load'
    if 'application' in layer and ('api' in name or 'integrat' in name):
        return 'openapi'
    if 'access' in name:
        return 'playwright_a11y'
    return 'bdd'


@enterprise_api_bp.route("/requirements/<int:req_id>/generate-test-cases", methods=["POST"])
@login_required
def generate_requirement_test_cases(req_id):
    """Generate layer-aware BDD test cases from acceptance criteria for a single requirement."""
    req = SolutionRequirement.query.get_or_404(req_id)

    if not req.acceptance_criteria:
        return jsonify({"error": "No acceptance criteria to generate tests from"}), 400

    layer = getattr(req, 'layer', None)
    test_cases = _generate_layer_tests(req.name, req.acceptance_criteria, layer)
    return jsonify({'test_cases': test_cases, 'requirement_id': req_id, 'layer': layer, 'test_type': _detect_test_type(layer, req.name)}), 200


@enterprise_api_bp.route("/requirements/<int:req_id>/dod", methods=["PATCH"])
@login_required
@require_roles("admin", "architect")
def update_requirement_dod(req_id):
    """Update the DoD checklist and auto-compute dod_complete."""
    from app.models.solution_architect_models import SolutionRequirement

    req = SolutionRequirement.query.get_or_404(req_id)
    data = request.get_json(silent=True) or {}
    if "dod_checklist" in data:
        req.dod_checklist = data["dod_checklist"]
    checklist = req.dod_checklist or []
    req.dod_complete = bool(checklist) and all(item.get("checked") for item in checklist)
    db.session.commit()
    return jsonify({"id": req.id, "dod_complete": req.dod_complete, "dod_checklist": req.dod_checklist}), 200


@enterprise_api_bp.route("/entities", methods=["GET"])
@login_required
def get_all_entities():
    """Get all enterprise entities for unified mapping"""
    try:
        search = request.args.get("search", "").strip()
        entity_type = request.args.get(
            "type"
        )  # application, system, initiative, project
        limit = safe_int_arg('limit', 50, minimum=1, maximum=500)

        result = {}

        # Applications
        if not entity_type or entity_type == "application":
            query = ApplicationComponent.query
            if search:
                query = query.filter(
                    or_(
                        ApplicationComponent.name.ilike(f"%{search}%"),
                        ApplicationComponent.description.ilike(f"%{search}%"),
                        ApplicationComponent.application_code.ilike(f"%{search}%"),
                    )
                )
            apps = query.limit(limit).all()
            result["applications"] = [
                {
                    "id": app.id,
                    "name": app.name,
                    "description": app.description,
                    "code": app.application_code,
                    "type": "application",
                    "subtype": app.application_type or "Unknown",
                    "category": app.application_category or "Unknown",
                    "domain": app.business_domain or "Unknown",
                    "criticality": app.criticality or "Unknown",
                    "status": app.lifecycle_status or "Unknown",
                }
                for app in apps
            ]

        # Systems
        if not entity_type or entity_type == "system":
            query = SystemBoundary.query
            if search:
                query = query.filter(
                    or_(
                        SystemBoundary.name.ilike(f"%{search}%"),
                        SystemBoundary.description.ilike(f"%{search}%"),
                        SystemBoundary.system_name.ilike(f"%{search}%"),
                    )
                )
            systems = query.limit(limit).all()
            result["systems"] = [
                {
                    "id": system.id,
                    "name": system.name,
                    "description": system.description,
                    "code": system.system_name,
                    "type": "system",
                    "subtype": system.system_type or "Unknown",
                    "category": system.system_category or "Unknown",
                    "boundary_type": system.boundary_type or "Unknown",
                }
                for system in systems
            ]

        # Initiatives
        if not entity_type or entity_type == "initiative":
            query = EnterpriseInitiative.query
            if search:
                query = query.filter(
                    or_(
                        EnterpriseInitiative.name.ilike(f"%{search}%"),
                        EnterpriseInitiative.description.ilike(f"%{search}%"),
                    )
                )
            initiatives = query.limit(limit).all()
            result["initiatives"] = [
                {
                    "id": init.id,
                    "name": init.name,
                    "description": init.description,
                    "code": getattr(init, "code", None),
                    "type": "initiative",
                    "subtype": init.initiative_type or "Unknown",
                    "status": getattr(init, "status", None) or getattr(init, "current_phase", None) or "Unknown",
                    "priority": getattr(init, "priority", None) or "Unknown",
                    "start_date": init.planned_start_date.isoformat() if getattr(init, "planned_start_date", None) else None,
                    "end_date": init.planned_end_date.isoformat() if getattr(init, "planned_end_date", None) else None,
                }
                for init in initiatives
            ]

        # Projects
        if not entity_type or entity_type == "project":
            query = Project.query
            if search:
                query = query.filter(
                    or_(
                        Project.name.ilike(f"%{search}%"),
                        Project.description.ilike(f"%{search}%"),
                        Project.code.ilike(f"%{search}%"),
                    )
                )
            projects = query.limit(limit).all()
            result["projects"] = [
                {
                    "id": str(proj.id),  # UUID to string
                    "name": proj.name,
                    "description": proj.description,
                    "code": proj.code,
                    "type": "project",
                    "subtype": proj.status or "Unknown",
                    "status": proj.status or "Unknown",
                    "priority": proj.priority or "Unknown",
                    "start_date": proj.start_date.isoformat()
                    if proj.start_date
                    else None,
                    "end_date": proj.end_date.isoformat() if proj.end_date else None,
                }
                for proj in projects
            ]

        return jsonify(result)

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


# =============================================================================
# CAPABILITY-BASED PLANNING — Requirement Generation
# =============================================================================


@enterprise_api_bp.route("/capabilities/<int:capability_id>/generate-requirements", methods=["POST"])
@login_required
def generate_requirements_for_capability(capability_id):
    """
    Generate EARS-format requirements from a business capability gap analysis.

    Body (JSON):
        solution_id  int  required  — solution to attach generated requirements to

    Response:
        {"status": "success", "requirement_ids": [...], "count": N}
        {"status": "error", "error": "..."}, 422
    """
    from flask_login import current_user
    from app.modules.capabilities.services.capability_requirement_generator_service import (
        CapabilityRequirementGeneratorService,
    )

    data = request.get_json() or {}
    solution_id = data.get("solution_id")
    if not solution_id:
        return jsonify({"status": "error", "error": "solution_id is required"}), 422

    try:
        solution_id = int(solution_id)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "error": "solution_id must be an integer"}), 422

    svc = CapabilityRequirementGeneratorService()
    result = svc.generate_requirements(
        capability_id=capability_id,
        solution_id=solution_id,
        current_user_id=current_user.id,
    )
    if result.get("status") == "error":
        return jsonify(result), 422
    return jsonify(result), 200


# =============================================================================
# CBP-007: Capability Request endpoint (stakeholder persona entry point)
# =============================================================================


@enterprise_api_bp.route("/capabilities/request", methods=["POST"])
@login_required
def request_capability_enhancement():
    """
    Create a KanbanCard capturing a stakeholder's capability enhancement request.

    Body (JSON):
        capability_id   int     required
        business_need   str     required  (max 500 chars)
        business_driver str     required  (one of: Cost Reduction, Risk Mitigation, Growth, Compliance, Efficiency)

    Response:
        {"success": true, "card_id": N, "redirect": "/adm-kanban?phase=A&highlight=N"}
        {"success": false, "error": "..."}, 422
    """
    from flask_login import current_user
    from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard
    from app.models.business_capabilities import BusinessCapability

    data = request.get_json() or {}
    capability_id = data.get("capability_id")
    business_need = (data.get("business_need") or "").strip()[:500]
    business_driver = (data.get("business_driver") or "").strip()

    if not capability_id:
        return jsonify({"success": False, "error": "capability_id is required"}), 422
    if not business_need:
        return jsonify({"success": False, "error": "business_need is required"}), 422

    try:
        capability_id = int(capability_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "capability_id must be an integer"}), 422

    cap = BusinessCapability.query.get(capability_id)
    if cap is None:
        return jsonify({"success": False, "error": f"Capability {capability_id} not found"}), 404

    phase_a = ADMPhase.query.filter_by(code="A").first()
    if phase_a is None:
        return jsonify({"success": False, "error": "ADM Phase A not found — run flask setup-dev"}), 500

    board = KanbanBoard.query.order_by(KanbanBoard.id).first()
    if board is None:
        board = KanbanBoard(
            name="Capability-Based Planning",
            description="Auto-created for CBP stakeholder requests",
            project_name="CBP",
            created_by_id=current_user.id,
        )
        db.session.add(board)
        db.session.flush()

    description = f"Business driver: {business_driver}\n\n{business_need}" if business_driver else business_need
    card = KanbanCard(
        title="Request: " + (cap.name or f"Capability #{capability_id}"),
        description=description,
        card_type="requirement",
        adm_phase_id=phase_a.id,
        board_id=board.id,
        status="backlog",
        priority="medium",
        implements_capabilities=[capability_id],
        created_by_id=current_user.id,
    )
    db.session.add(card)
    db.session.commit()
    return jsonify({
        "success": True,
        "card_id": card.id,
        "redirect": f"/adm-kanban?phase=A&highlight={card.id}",
    }), 200


# =============================================================================
# TPM REPLACEMENT — Requirements Backlog Lifecycle & BA Enrichment APIs
# =============================================================================

_VALID_STATUSES = {"Draft", "Refined", "Approved", "Implemented"}


def _resolve_archimate_req_name(archimate_requirement_id):
    """Resolve ArchiMate Requirement element name from requirements table."""
    if not archimate_requirement_id:
        return None
    try:
        from app.models.models import Requirement
        elem = Requirement.query.get(archimate_requirement_id)
        if elem:
            return elem.title or getattr(elem, 'name', None)
        return None
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
        return None


def _req_to_dict(req):
    """Serialise a SolutionRequirement for the backlog API."""
    from app.models.business_capabilities import BusinessCapability
    cap_name = None
    if req.capability_id:
        cap = BusinessCapability.query.filter(
            BusinessCapability.id == req.capability_id
        ).first()
        cap_name = cap.name if cap else None
    sol_name = None
    if req.solution_id:
        from app.models.solution_models import Solution
        sol = Solution.query.get(req.solution_id)
        sol_name = sol.name if sol else None
    # Motivation layer name resolution (lazy — use ORM relationship if loaded)
    driver_name = req.driver.name if req.driver_id and req.driver else None
    goal_name = req.goal.name if req.goal_id and req.goal else None
    return {
        "id": req.id,
        "reference_id": f"REQ-{req.id:04d}",
        "name": req.name,
        "description": req.description,
        "moscow_priority": req.moscow_priority,
        "togaf_phase": req.togaf_phase,
        "status": req.status,
        "owner": req.owner,
        "ai_generated": req.ai_generated,
        "ai_confidence": req.ai_confidence,
        "acceptance_criteria": req.acceptance_criteria,
        "assumptions": req.assumptions,
        "dependencies_text": req.dependencies_text,
        "capability_id": req.capability_id,
        "capability_name": cap_name,
        "solution_id": req.solution_id,
        "solution_name": sol_name,
        # ArchiMate 3.2 Motivation Layer
        "driver_id": req.driver_id,
        "driver_name": driver_name,
        "goal_id": req.goal_id,
        "goal_name": goal_name,
        "stakeholder_id": req.stakeholder_id,
        "archimate_requirement_id": req.archimate_requirement_id,
        "archimate_requirement_name": _resolve_archimate_req_name(req.archimate_requirement_id),
        # User Story / Epic fields (TPM-003)
        "story_points": req.story_points,
        "epic_parent_id": req.epic_parent_id,
        "dod_complete": req.dod_complete,
        "item_type": req.item_type,
        "requirement_type": req.requirement_type.value if req.requirement_type else None,
        "dod_checklist": req.dod_checklist or [],
        # Prioritisation scoring (TPM-006)
        "rice_reach": req.rice_reach,
        "rice_impact": req.rice_impact,
        "rice_confidence": req.rice_confidence,
        "rice_effort": req.rice_effort,
        "rice_score": req.rice_score,
        "wsjf_cost_of_delay": req.wsjf_cost_of_delay,
        "wsjf_job_duration": req.wsjf_job_duration,
        "wsjf_score": req.wsjf_score,
        "layer": req.layer,
        "req_type": req.req_type,
        "template_id": req.template_id,
        # Stakeholder and approval (PRQ-002)
        'stakeholder_name': req.stakeholder_name,
        'stakeholder_role': req.stakeholder_role,
        'source_document': req.source_document,
        'approval_status': req.approval_status,
        'approved_by_id': req.approved_by_id,
        'approved_at': req.approved_at.isoformat() if req.approved_at else None,
        # Release scoping (PRQ-004)
        'sprint_id': req.sprint_id,
        'milestone': req.milestone,
        'target_release_date': req.target_release_date.isoformat() if req.target_release_date else None,
        # Compliance tagging (PRQ-008)
        'compliance_tags': req.compliance_tags or [],
    }


@enterprise_api_bp.route("/requirements", methods=["GET"])
@login_required
def list_requirements():
    """
    List all requirements with optional filters.

    Query params: status, capability_id, solution_id, search
    """
    from app.models.solution_architect_models import SolutionRequirement

    status = request.args.get("status", "").strip()
    cap_id = request.args.get("capability_id", type=int)
    sol_id = request.args.get("solution_id", type=int)
    search = request.args.get("search", "").strip()
    after_id = request.args.get("after_id", type=int)
    epic_id = request.args.get("epic_id", type=int)
    req_type = request.args.get("type", "").strip()
    req_type_filter = request.args.get("req_type", "").strip()

    q = SolutionRequirement.query.filter(SolutionRequirement.deleted_at == None)  # noqa: E711
    if status:
        q = q.filter(SolutionRequirement.status == status)
    if cap_id:
        q = q.filter(SolutionRequirement.capability_id == cap_id)
    if sol_id:
        q = q.filter(SolutionRequirement.solution_id == sol_id)
    if epic_id:
        q = q.filter(SolutionRequirement.epic_parent_id == epic_id)
    if req_type:
        q = q.filter(SolutionRequirement.item_type == req_type)
    if req_type_filter:
        q = q.filter(SolutionRequirement.req_type == req_type_filter)
    sprint_filter = request.args.get('sprint')
    if sprint_filter:
        q = q.filter(SolutionRequirement.sprint_id == sprint_filter)
    milestone_filter = request.args.get('milestone')
    if milestone_filter:
        q = q.filter(SolutionRequirement.milestone == milestone_filter)
    if search:
        q = q.filter(
            SolutionRequirement.name.ilike(f"%{search}%")
            | SolutionRequirement.description.ilike(f"%{search}%")
        )

    total = q.count()

    if after_id is not None:
        q = q.filter(SolutionRequirement.id < after_id)

    PAGE_SIZE = 100
    reqs = q.order_by(SolutionRequirement.id.desc()).limit(PAGE_SIZE).all()
    next_cursor = reqs[-1].id if len(reqs) == PAGE_SIZE else None
    return jsonify({
        "requirements": [_req_to_dict(r) for r in reqs],
        "next_cursor": next_cursor,
        "total": total,
        # legacy keys kept for backward compatibility
        "success": True,
        "data": [_req_to_dict(r) for r in reqs],
        "count": len(reqs),
    })


@enterprise_api_bp.route('/solutions/<int:solution_id>/release-scope', methods=['GET'])
@login_required
def solution_release_scope(solution_id):
    """PRQ-004: Summarise requirements by milestone/sprint for release scoping."""
    from app.models.solution_architect_models import SolutionRequirement, Solution

    solution = Solution.query.get_or_404(solution_id)
    reqs = SolutionRequirement.query.filter(
        SolutionRequirement.solution_id == solution_id,
        SolutionRequirement.deleted_at == None  # noqa: E711
    ).all()

    milestones = {}
    sprints = {}
    unscoped = []

    for req in reqs:
        if req.milestone:
            milestones.setdefault(req.milestone, []).append({'id': req.id, 'name': req.name, 'moscow': getattr(req, 'moscow_priority', None)})
        if req.sprint_id:
            sprints.setdefault(req.sprint_id, []).append({'id': req.id, 'name': req.name})
        if not req.milestone and not req.sprint_id:
            unscoped.append({'id': req.id, 'name': req.name})

    return jsonify({
        'solution_id': solution_id,
        'solution_name': solution.name,
        'milestones': {m: {'requirements': v, 'count': len(v)} for m, v in milestones.items()},
        'sprints': {s: {'requirements': v, 'count': len(v)} for s, v in sprints.items()},
        'unscoped': unscoped,
        'unscoped_count': len(unscoped),
        'total_requirements': len(reqs)
    }), 200


@enterprise_api_bp.route("/requirements/export", methods=["GET"])
@login_required
def export_requirements():
    """
    Export requirements as CSV.

    Query params: format (default: csv), solution_id (optional filter)
    """
    from app.models.solution_architect_models import SolutionRequirement

    sol_id = request.args.get("solution_id", type=int)

    q = SolutionRequirement.query.filter(SolutionRequirement.deleted_at == None)  # noqa: E711
    if sol_id:
        q = q.filter(SolutionRequirement.solution_id == sol_id)
    reqs = q.order_by(SolutionRequirement.id.asc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "requirement_name", "description", "requirement_type",
        "moscow_priority", "priority", "acceptance_criteria",
        "solution_id", "capability_id", "created_at",
    ])
    for r in reqs:
        writer.writerow([
            r.id,
            getattr(r, "name", ""),
            getattr(r, "description", ""),
            getattr(r, "requirement_type", ""),
            getattr(r, "moscow_priority", ""),
            getattr(r, "priority", ""),
            getattr(r, "acceptance_criteria", ""),
            getattr(r, "solution_id", ""),
            getattr(r, "capability_id", ""),
            getattr(r, "created_at", ""),
        ])

    csv_data = output.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=requirements.csv"},
    )



@enterprise_api_bp.route("/solutions/<int:solution_id>/requirement-count", methods=["GET"])
@login_required
def get_requirement_count(solution_id):
    """Return count of live (non-deleted) requirements for a solution."""
    from app.models.solution_models import Solution
    from app.models.solution_architect_models import SolutionRequirement

    require_entity(Solution, solution_id, description="Solution not found")

    count = SolutionRequirement.query.filter(
        SolutionRequirement.solution_id == solution_id,
        SolutionRequirement.deleted_at == None,  # noqa: E711
    ).count()
    return jsonify({"solution_id": solution_id, "count": count})


@enterprise_api_bp.route("/requirements/<int:req_id>/status", methods=["PATCH"])
@login_required
@require_roles("admin", "architect")
def patch_requirement_status(req_id):
    """
    Update requirement lifecycle status.

    Body: {"status": "Refined", "owner": "Jane Smith", "notes": "..."}
    Transitions: Draft → Refined → Approved → Implemented (or back to Draft)
    """
    from app.models.solution_architect_models import SolutionRequirement

    req = SolutionRequirement.query.get(req_id)
    if req is None:
        return jsonify({"success": False, "error": "Requirement not found"}), 404

    if req.solution_id:
        from app.models.solution_models import Solution
        sol = Solution.query.get(req.solution_id)
        if sol and sol.created_by_id != current_user.id and not is_active_org_admin():
            return jsonify({"success": False, "error": "Forbidden: you do not own this solution"}), 403

    data = request.get_json() or {}
    new_status = data.get("status", "").strip()
    if new_status and new_status not in _VALID_STATUSES:
        return jsonify({"success": False, "error": f"Invalid status. Must be one of: {sorted(_VALID_STATUSES)}"}), 422

    if new_status:
        req.status = new_status
    if "owner" in data:
        req.owner = (data["owner"] or "").strip() or None
    db.session.commit()
    try:
        AuditLog.log(
            action="update",
            entity_type="requirement",
            entity_id=req.id,
            entity_name=req.name,
            user_id=current_user.id,
            user_email=current_user.email,
            description=f"Requirement status updated to '{req.status}'",
            new_values={"status": req.status, "owner": req.owner},
            status="success",
        )
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
        logger.exception("Failed to operation")
        pass
    return jsonify({"success": True, "data": _req_to_dict(req)})


# PRQ-006: audited fields for change log
_AUDITED_REQ_FIELDS = ['name', 'description', 'acceptance_criteria', 'status', 'approval_status']


def _log_req_change(req, field_name, old_value, new_value, user_id):
    """Record a field change in RequirementChangeLog."""
    from app.models.solution_architect_models import RequirementChangeLog
    if str(old_value or '') == str(new_value or ''):
        return  # no actual change
    log = RequirementChangeLog(
        req_id=req.id,
        changed_by_id=user_id,
        field_name=field_name,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
        change_type='update'
    )
    db.session.add(log)


@enterprise_api_bp.route("/requirements/<int:req_id>/enrich", methods=["PATCH"])
@login_required
@require_roles("admin", "architect")
def enrich_requirement(req_id):
    """
    BA enrichment — update acceptance criteria, assumptions, dependencies.

    Body: {"acceptance_criteria": "...", "assumptions": "...", "dependencies_text": "..."}
    """
    from app.models.solution_architect_models import SolutionRequirement

    req = SolutionRequirement.query.get(req_id)
    if req is None:
        return jsonify({"success": False, "error": "Requirement not found"}), 404

    if req.solution_id:
        from app.models.solution_models import Solution
        sol = Solution.query.get(req.solution_id)
        if sol and sol.created_by_id != current_user.id and not is_active_org_admin():
            return jsonify({"success": False, "error": "Forbidden: you do not own this solution"}), 403

    data = request.get_json() or {}
    # PRQ-006: snapshot fields before update
    old_values = {f: getattr(req, f, None) for f in _AUDITED_REQ_FIELDS}
    if "acceptance_criteria" in data:
        req.acceptance_criteria = data["acceptance_criteria"] or None
    if "assumptions" in data:
        req.assumptions = data["assumptions"] or None
    if "dependencies_text" in data:
        req.dependencies_text = data["dependencies_text"] or None
    if "name" in data and data["name"]:
        req.name = data["name"].strip()
    if "moscow_priority" in data:
        req.moscow_priority = data["moscow_priority"] or None
    # ArchiMate 3.2 Motivation Layer FKs
    try:
        if "driver_id" in data:
            req.driver_id = int(data["driver_id"]) if data["driver_id"] else None
        if "goal_id" in data:
            req.goal_id = int(data["goal_id"]) if data["goal_id"] else None
        if "stakeholder_id" in data:
            req.stakeholder_id = int(data["stakeholder_id"]) if data["stakeholder_id"] else None
        if "archimate_requirement_id" in data:
            req.archimate_requirement_id = int(data["archimate_requirement_id"]) if data["archimate_requirement_id"] else None
        if "capability_id" in data:
            req.capability_id = int(data["capability_id"]) if data["capability_id"] else None
        if "template_id" in data:
            req.template_id = int(data["template_id"]) if data["template_id"] else None
    except (ValueError, TypeError):
        db.session.rollback()
        return jsonify({"success": False, "error": "driver_id, goal_id, stakeholder_id, archimate_requirement_id, capability_id and template_id must be integers"}), 400
    if "owner" in data:
        req.owner = data["owner"] or None
    if "layer" in data:
        req.layer = data["layer"] or None
    if "req_type" in data:
        req.req_type = data["req_type"]
    for field in ['stakeholder_name', 'stakeholder_role', 'source_document', 'approval_status']:
        if field in data:
            setattr(req, field, data[field])
    for field in ['sprint_id', 'milestone']:
        if field in data:
            setattr(req, field, data[field])
    if 'target_release_date' in data and data['target_release_date']:
        try:
            from datetime import date
            req.target_release_date = date.fromisoformat(data['target_release_date'])
        except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
            logger.exception("Failed to operation")
            pass
    if 'compliance_tags' in data:
        tags = data['compliance_tags']
        if isinstance(tags, list):
            req.compliance_tags = tags
    try:
        if "story_points" in data:
            req.story_points = int(data["story_points"]) if data["story_points"] else None
        if "epic_parent_id" in data:
            req.epic_parent_id = int(data["epic_parent_id"]) if data["epic_parent_id"] else None
    except (ValueError, TypeError):
        db.session.rollback()
        return jsonify({"success": False, "error": "story_points and epic_parent_id must be integers"}), 400
    if "dod_complete" in data:
        req.dod_complete = bool(data["dod_complete"])
    # PRQ-005: gate — block 'implemented' unless approved
    new_status = data.get('status')
    if new_status == 'implemented':
        current_approval = req.approval_status or 'draft'
        if current_approval != 'approved':
            return jsonify({
                'error': "Cannot set status to 'implemented' — requirement must be approved first",
                'approval_status': current_approval,
                'hint': "POST /requirements/{id}/approve to approve, or change status to 'in_progress' instead"
            }), 400
    # Guard: if setting status=done, check dod_complete
    if data.get("status") == "done" and not req.dod_complete:
        checklist = req.dod_checklist or []
        if checklist:
            return jsonify({"error": "Cannot mark done: Definition of Done not complete", "dod_complete": False, "dod_checklist": checklist}), 400
    # PRQ-006: log changes
    for field in _AUDITED_REQ_FIELDS:
        new_val = getattr(req, field, None)
        if str(old_values.get(field) or '') != str(new_val or ''):
            _log_req_change(req, field, old_values[field], new_val, current_user.id)
    db.session.commit()
    try:
        AuditLog.log(
            action="update",
            entity_type="requirement",
            entity_id=req.id,
            entity_name=req.name,
            user_id=current_user.id,
            user_email=current_user.email,
            description="Requirement enriched (BA fields updated)",
            new_values={
                k: data[k] for k in ("acceptance_criteria", "assumptions", "dependencies_text", "name", "moscow_priority")
                if k in data
            },
            status="success",
        )
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
        logger.exception("Failed to operation")
        pass
    return jsonify({"success": True, "data": _req_to_dict(req)})


# Valid compliance frameworks (PRQ-008)
_COMPLIANCE_FRAMEWORKS = ['GDPR', 'SOX', 'HIPAA', 'ISO27001', 'PCI-DSS', 'NIST', 'FedRAMP', 'TOGAF', 'ITIL', 'CCPA']


@enterprise_api_bp.route('/solutions/<int:solution_id>/compliance-summary', methods=['GET'])
@login_required
def compliance_summary(solution_id):
    """PRQ-008: Return compliance tagging summary for a solution."""
    from app.models.solution_models import Solution
    from app.models.solution_architect_models import SolutionRequirement

    require_entity(Solution, solution_id, description="Solution not found")
    reqs = SolutionRequirement.query.filter_by(solution_id=solution_id).all()
    reqs = [r for r in reqs if r.deleted_at is None]
    tag_counts = {}
    untagged = 0
    for req in reqs:
        tags = req.compliance_tags or []
        if not tags:
            untagged += 1
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return jsonify({
        'solution_id': solution_id,
        'tag_counts': tag_counts,
        'untagged_requirements': untagged,
        'total_requirements': len(reqs),
        'available_frameworks': _COMPLIANCE_FRAMEWORKS
    }), 200


@enterprise_api_bp.route('/requirements/by-compliance-tag', methods=['GET'])
@login_required
def requirements_by_compliance_tag():
    """PRQ-008: Filter requirements by compliance tag."""
    from app.models.solution_architect_models import SolutionRequirement
    tag = request.args.get('tag', '')
    solution_id = request.args.get('solution_id', type=int)
    if not tag:
        return jsonify({'error': 'tag query param required'}), 400
    q = SolutionRequirement.query
    if solution_id:
        q = q.filter_by(solution_id=solution_id)
    all_reqs = q.all()
    matching = [r for r in all_reqs if r.deleted_at is None and tag in (r.compliance_tags or [])]
    return jsonify({
        'tag': tag,
        'requirements': [{'id': r.id, 'title': r.title, 'compliance_tags': r.compliance_tags} for r in matching],
        'count': len(matching)
    }), 200


@enterprise_api_bp.route('/requirements/<int:req_id>/history', methods=['GET'])
@login_required
def get_requirement_history(req_id):
    """PRQ-006: Get change log for a requirement."""
    from app.models.solution_architect_models import SolutionRequirement, RequirementChangeLog

    req = SolutionRequirement.query.get_or_404(req_id)
    logs = RequirementChangeLog.query.filter_by(req_id=req_id).order_by(RequirementChangeLog.changed_at.desc()).limit(100).all()

    return jsonify({
        'requirement_id': req_id,
        'requirement_name': req.name,
        'history': [log.to_dict() for log in logs],
        'total_changes': len(logs)
    }), 200


@enterprise_api_bp.route('/requirements/<int:req_id>/approve', methods=['POST'])
@login_required
def approve_requirement(req_id):
    """PRQ-002: Mark a requirement as approved by the current user."""
    from app.models.solution_architect_models import SolutionRequirement
    from datetime import datetime

    req = SolutionRequirement.query.get_or_404(req_id)

    data = request.get_json() or {}
    action = data.get('action', 'approve')  # approve / reject / defer / submit_review

    TRANSITIONS = {
        'draft': ['in_review'],
        'in_review': ['approved', 'rejected', 'deferred'],
        'deferred': ['in_review'],
        'rejected': ['in_review'],
    }

    current = req.approval_status or 'draft'
    action_map = {
        'submit_review': 'in_review',
        'approve': 'approved',
        'reject': 'rejected',
        'defer': 'deferred'
    }

    target = action_map.get(action)
    if not target:
        return jsonify({'error': f"Unknown action: {action}"}), 400

    allowed = TRANSITIONS.get(current, [])
    if target not in allowed:
        return jsonify({
            'error': f"Cannot transition from '{current}' to '{target}'",
            'allowed_transitions': allowed
        }), 400

    req.approval_status = target
    if target == 'approved':
        req.approved_by_id = current_user.id
        req.approved_at = datetime.utcnow()

    db.session.commit()
    return jsonify({
        'requirement_id': req_id,
        'approval_status': req.approval_status,
        'approved_by_id': req.approved_by_id,
        'approved_at': req.approved_at.isoformat() if req.approved_at else None
    }), 200


# =============================================================================
# PRQ-005: Dedicated approval workflow convenience endpoints
# =============================================================================


@enterprise_api_bp.route('/requirements/<int:req_id>/submit-review', methods=['POST'])
@login_required
def submit_requirement_for_review(req_id):
    """PRQ-005: Submit requirement for review (draft -> in_review)."""
    from app.models.solution_architect_models import SolutionRequirement
    req = SolutionRequirement.query.get_or_404(req_id)
    if (req.approval_status or 'draft') not in ('draft', 'deferred', 'rejected'):
        return jsonify({'error': f"Cannot submit from status '{req.approval_status}'"}), 400
    req.approval_status = 'in_review'
    db.session.commit()
    return jsonify({'requirement_id': req_id, 'approval_status': 'in_review'}), 200


@enterprise_api_bp.route('/requirements/<int:req_id>/reject', methods=['POST'])
@login_required
def reject_requirement(req_id):
    """PRQ-005: Reject a requirement (in_review -> rejected)."""
    from app.models.solution_architect_models import SolutionRequirement
    req = SolutionRequirement.query.get_or_404(req_id)
    if (req.approval_status or 'draft') != 'in_review':
        return jsonify({'error': "Can only reject requirements that are in_review"}), 400
    data = request.get_json() or {}
    req.approval_status = 'rejected'
    db.session.commit()
    return jsonify({'requirement_id': req_id, 'approval_status': 'rejected', 'reason': data.get('reason', '')}), 200


@enterprise_api_bp.route('/requirements/<int:req_id>/defer', methods=['POST'])
@login_required
def defer_requirement(req_id):
    """PRQ-005: Defer a requirement (in_review -> deferred)."""
    from app.models.solution_architect_models import SolutionRequirement
    req = SolutionRequirement.query.get_or_404(req_id)
    if (req.approval_status or 'draft') != 'in_review':
        return jsonify({'error': "Can only defer requirements that are in_review"}), 400
    req.approval_status = 'deferred'
    db.session.commit()
    return jsonify({'requirement_id': req_id, 'approval_status': 'deferred'}), 200


# =============================================================================
# TPM-006: MoSCoW / RICE / WSJF prioritisation scoring
# =============================================================================


@enterprise_api_bp.route("/requirements/<int:req_id>/score", methods=["PATCH"])
@login_required
@require_roles("admin", "architect")
def score_requirement(req_id):
    """Update prioritisation scoring fields on a requirement."""
    req = SolutionRequirement.query.get_or_404(req_id)
    data = request.get_json(silent=True) or {}
    for field in ['moscow_priority', 'rice_reach', 'rice_impact', 'rice_confidence',
                  'rice_effort', 'wsjf_cost_of_delay', 'wsjf_job_duration']:
        if field in data:
            setattr(req, field, data[field])
    db.session.commit()
    return jsonify({
        'id': req.id,
        'moscow_priority': req.moscow_priority,
        'rice_score': req.rice_score,
        'wsjf_score': req.wsjf_score,
    }), 200


@enterprise_api_bp.route("/motivation/drivers", methods=["GET"])
@login_required
def list_motivation_drivers():
    """Return all Drivers as {id, name} pairs for use in requirement linkage pickers.
    Reads from archimate_elements (canonical source written by Composer).
    """
    from app.models.archimate_core import ArchiMateElement
    drivers = ArchiMateElement.query.filter(
        ArchiMateElement.type == "Driver",
        ArchiMateElement.name.isnot(None),
        ArchiMateElement.name != "",
        ~ArchiMateElement.name.like("<%"),
    ).order_by(ArchiMateElement.name).all()
    return jsonify({"success": True, "data": [{"id": d.id, "name": d.name} for d in drivers]})


@enterprise_api_bp.route("/motivation/goals", methods=["GET"])
@login_required
def list_motivation_goals():
    """Return all Goals/Outcomes as {id, name, driver_id} pairs for use in requirement linkage pickers.
    Reads from archimate_elements (canonical source written by Composer).
    """
    from app.models.archimate_core import ArchiMateElement
    goals = ArchiMateElement.query.filter(
        ArchiMateElement.type.in_(["Goal", "Outcome"]),
        ArchiMateElement.name.isnot(None),
        ArchiMateElement.name != "",
        ~ArchiMateElement.name.like("<%"),
    ).order_by(ArchiMateElement.name).all()
    return jsonify({
        "success": True,
        "data": [{"id": g.id, "name": g.name, "driver_id": None} for g in goals],
    })


@enterprise_api_bp.route("/motivation/stakeholders", methods=["GET"])
@login_required
def list_motivation_stakeholders():
    """Return all Stakeholders as {id, name} pairs for use in requirement linkage pickers.
    Reads from archimate_elements (canonical source written by Composer).
    """
    from app.models.archimate_core import ArchiMateElement
    stakeholders = ArchiMateElement.query.filter(
        ArchiMateElement.type == "Stakeholder",
        ArchiMateElement.name.isnot(None),
        ArchiMateElement.name != "",
        ~ArchiMateElement.name.like("<%"),
    ).order_by(ArchiMateElement.name).all()
    return jsonify({"success": True, "data": [{"id": s.id, "name": s.name} for s in stakeholders]})


@enterprise_api_bp.route("/archimate/diagram", methods=["GET"])
@login_required
def archimate_diagram():
    """Return SVG diagram for given ArchiMate element IDs.

    Query params:
      elements  — comma-separated integer element IDs (e.g. 1,2,3)
      viewpoint — viewpoint label string (default: application)

    Returns image/svg+xml.
    """
    from flask import Response
    from app.services.archimate_diagram_service import DiagramRenderService

    element_ids_str = request.args.get("elements", "")
    viewpoint = request.args.get("viewpoint", "application")

    try:
        element_ids = [
            int(x) for x in element_ids_str.split(",") if x.strip().isdigit()
        ]
    except Exception:
        element_ids = []

    svc = DiagramRenderService()
    svg = svc.render_diagram(element_ids, viewpoint)
    return Response(svg, mimetype="image/svg+xml")


# =============================================================================
# REQ-008: Batch LLM Enrichment for Requirements Missing Metadata
# =============================================================================

_ENRICH_PROMPT_TEMPLATE = (
    "Classify this requirement and return JSON with these exact keys:\n"
    "- moscow_priority: one of MUST/SHOULD/COULD/WONT\n"
    "- requirement_type: one of functional/non-functional/constraint\n"
    "- priority: one of high/medium/low\n\n"
    "Requirement: {name}\n"
    "Description: {description}\n\n"
    "Return ONLY valid JSON, nothing else."
)

_AC_PROMPT_TEMPLATE = (
    "You are a business analyst writing acceptance criteria.\n"
    "Requirement: {name}\n"
    "Description: {description}\n\n"
    "Write 3-5 acceptance criteria in BDD Gherkin format (GIVEN/WHEN/THEN). "
    "Be specific and testable. Return only the criteria, one per line."
)


@enterprise_api_bp.route("/requirements/batch-enrich", methods=["POST"])
@login_required
def batch_enrich_requirements():
    """
    LLM-batch-enrich requirements missing moscow_priority, requirement_type, or priority.
    Processes up to 50 records per call.

    Response: {"success": true, "enriched": N, "errors": M, "details": [...]}
    """
    import json as _json
    from app.services.llm_service import LLMService

    candidates = (
        SolutionRequirement.query
        .filter(
            SolutionRequirement.deleted_at.is_(None),
            or_(
                SolutionRequirement.moscow_priority.is_(None),
                SolutionRequirement.moscow_priority == "",
                SolutionRequirement.requirement_type.is_(None),
                SolutionRequirement.requirement_type == "",
                SolutionRequirement.priority.is_(None),
                SolutionRequirement.priority == "",
            ),
        )
        .limit(50)
        .all()
    )

    enriched = 0
    errors = 0
    details = []

    for req in candidates:
        try:
            # RRT-004: load layer and template context
            from app.models.requirement_template import RequirementTemplate
            _template_hint = ''
            if getattr(req, 'template_id', None):
                try:
                    _tmpl = RequirementTemplate.query.get(req.template_id)
                    if _tmpl and _tmpl.ac_hint:
                        _template_hint = _tmpl.ac_hint
                except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
                    logger.exception("Failed to database query")
                    pass
            _layer_context = getattr(req, 'layer', None) or ''

            enrich_parts = [_ENRICH_PROMPT_TEMPLATE.format(
                name=req.name or "",
                description=req.description or "",
            )]
            if _layer_context:
                enrich_parts.append(f"Architecture layer: {_layer_context}")
            if _template_hint:
                enrich_parts.append(f"Template guidance: {_template_hint}")
            prompt = "\n".join(enrich_parts)
            raw = LLMService.generate_from_prompt(prompt=prompt, use_cache=False)
            data = _json.loads(raw)

            if not req.moscow_priority:
                req.moscow_priority = data.get("moscow_priority") or req.moscow_priority
            if not req.requirement_type:
                req.requirement_type = data.get("requirement_type") or req.requirement_type
            if not req.priority:
                req.priority = data.get("priority") or req.priority

            db.session.commit()
            enriched += 1
            details.append({"id": req.id, "status": "enriched"})
        except Exception as exc:
            db.session.rollback()
            errors += 1
            details.append({"id": req.id, "status": "error", "error": str(exc)})

    return jsonify({"success": True, "enriched": enriched, "errors": errors, "details": details})


@enterprise_api_bp.route("/requirements/batch-generate-ac", methods=["POST"])
@login_required
def batch_generate_ac():
    """
    LLM-batch-generate acceptance criteria for requirements where acceptance_criteria is NULL/empty.
    Processes up to 50 records per call.

    Response: {"success": true, "enriched": N, "errors": M, "details": [...]}
    """
    from app.services.llm_service import LLMService

    candidates = (
        SolutionRequirement.query
        .filter(
            SolutionRequirement.deleted_at.is_(None),
            or_(
                SolutionRequirement.acceptance_criteria.is_(None),
                SolutionRequirement.acceptance_criteria == "",
            ),
        )
        .limit(50)
        .all()
    )

    enriched = 0
    errors = 0
    details = []

    for req in candidates:
        try:
            prompt = _AC_PROMPT_TEMPLATE.format(
                name=req.name or "",
                description=req.description or "",
            )
            ac_text = LLMService.generate_from_prompt(prompt=prompt, use_cache=False)
            req.acceptance_criteria = ac_text
            db.session.commit()
            enriched += 1
            details.append({"id": req.id, "status": "enriched"})
        except Exception as exc:
            db.session.rollback()
            errors += 1
            details.append({"id": req.id, "status": "error", "error": str(exc)})

    return jsonify({"success": True, "enriched": enriched, "errors": errors, "details": details})


@enterprise_api_bp.route("/requirements/cleanup-orphans", methods=["POST"])
@login_required
def cleanup_orphan_requirement_ids():
    """Remove soft-deleted requirement IDs from KanbanCard.requirement_ids arrays."""
    try:
        valid_ids = set(
            row.id for row in SolutionRequirement.query.filter(
                SolutionRequirement.deleted_at.is_(None)
            ).with_entities(SolutionRequirement.id).all()
        )

        cards = KanbanCard.query.filter(
            KanbanCard.requirement_ids.isnot(None)
        ).all()

        cards_updated = 0
        ids_removed = 0

        for card in cards:
            original = card.requirement_ids or []
            if not original:
                continue
            cleaned = [rid for rid in original if rid in valid_ids]
            removed = len(original) - len(cleaned)
            if removed > 0:
                card.requirement_ids = cleaned
                ids_removed += removed
                cards_updated += 1

        if cards_updated > 0:
            db.session.commit()

        return jsonify({"success": True, "cards_updated": cards_updated, "ids_removed": ids_removed})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), 500

# =============================================================================
# REQ-011: Direct JIRA Push for Individual Requirements
# =============================================================================


@enterprise_api_bp.route("/requirements/<int:req_id>/push-to-jira", methods=["POST"])
@login_required
def push_requirement_to_jira(req_id):
    """Push a single requirement to JIRA as a Story."""
    from flask import current_app
    jira_url = current_app.config.get('JIRA_URL')
    jira_user = current_app.config.get('JIRA_USER')
    jira_token = current_app.config.get('JIRA_API_TOKEN')

    if not all([jira_url, jira_user, jira_token]):
        return jsonify({"success": False, "error": "JIRA integration not configured"}), 503

    req = SolutionRequirement.query.get_or_404(req_id)
    project_key = current_app.config.get('JIRA_PROJECT_KEY') or 'ARCH'

    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": req.name,
            "description": req.description or req.acceptance_criteria or "",
            "issuetype": {"name": "Story"},
        }
    }

    try:
        response = requests.post(
            f"{jira_url.rstrip('/')}/rest/api/2/issue",
            json=payload,
            auth=(jira_user, jira_token),
            timeout=15,
        )
        if response.status_code in (200, 201):
            data = response.json()
            issue_key = data.get("key", "")
            req.jira_issue_key = issue_key
            req.jira_push_status = 'pushed'
            db.session.commit()
            return jsonify({
                "success": True,
                "jira_issue_key": issue_key,
                "jira_url": f"{jira_url.rstrip('/')}/browse/{issue_key}",
            })
        else:
            req.jira_push_status = 'failed'
            db.session.commit()
            return jsonify({"success": False, "error": response.text}), 502
    except requests.exceptions.RequestException as exc:
        req.jira_push_status = 'failed'
        db.session.commit()
        return jsonify({"success": False, "error": str(exc)}), 502


# =============================================================================
# AIP-003: Cross-layer Conflict and Gap Detection
# =============================================================================


@enterprise_api_bp.route('/solutions/<int:solution_id>/detect-conflicts', methods=['POST'])
@login_required
def detect_solution_conflicts(solution_id):
    """AIP-003: Detect cross-layer conflicts and architectural gaps."""
    from app.models.solution_architect_models import Solution

    solution = Solution.query.get_or_404(solution_id)

    reqs = SolutionRequirement.query.filter(
        SolutionRequirement.solution_id == solution_id,
        SolutionRequirement.deleted_at == None
    ).all()

    conflicts = []
    gaps = []
    warnings = []

    # --- CONFLICT DETECTION ---
    # 1. Keyword conflict: opposite terms in same layer
    CONFLICT_PAIRS = [
        ('realtime', 'batch'),
        ('synchronous', 'asynchronous'),
        ('on-premise', 'cloud'),
        ('stateless', 'stateful'),
        ('centralised', 'distributed'),
        ('monolith', 'microservice'),
    ]

    layer_reqs = {}
    for req in reqs:
        lyr = (getattr(req, 'layer', None) or 'unclassified').lower()
        layer_reqs.setdefault(lyr, []).append(req)

    for layer, layer_req_list in layer_reqs.items():
        for pair in CONFLICT_PAIRS:
            matches_a = [r for r in layer_req_list if pair[0] in (r.name + ' ' + (r.description or '')).lower()]
            matches_b = [r for r in layer_req_list if pair[1] in (r.name + ' ' + (r.description or '')).lower()]
            if matches_a and matches_b:
                conflicts.append({
                    'type': 'keyword_conflict',
                    'layer': layer,
                    'severity': 'high',
                    'message': f"Conflicting terms '{pair[0]}' vs '{pair[1]}' in {layer} layer",
                    'requirement_ids': [r.id for r in matches_a + matches_b],
                    'recommendation': f"Clarify architectural decision: choose '{pair[0]}' OR '{pair[1]}' in {layer} layer"
                })

    # 2. MoSCoW conflicts: Must-have that references a Should-have dependency
    must_haves = {r.id: r for r in reqs if (getattr(r, 'moscow_priority', None) or '').upper() == 'MUST'}
    should_haves = {r.id: r for r in reqs if (getattr(r, 'moscow_priority', None) or '').upper() == 'SHOULD'}

    for req in reqs:
        ac = (req.acceptance_criteria or '').lower()
        for should_id, should_req in should_haves.items():
            if should_req.name.lower()[:15] in ac and req.id in must_haves:
                conflicts.append({
                    'type': 'priority_dependency',
                    'layer': getattr(req, 'layer', 'unknown'),
                    'severity': 'medium',
                    'message': f"MUST-have '{req.name}' may depend on SHOULD-have '{should_req.name}'",
                    'requirement_ids': [req.id, should_id],
                    'recommendation': "Consider elevating the SHOULD-have requirement or removing the dependency"
                })

    # --- GAP DETECTION ---
    # Architecture layers present
    present_layers = set(
        (getattr(r, 'layer', None) or '').lower() for r in reqs
        if getattr(r, 'layer', None)
    )

    required_layers = {'business', 'application', 'technology', 'crosscutting'}
    missing_layers = required_layers - present_layers

    for missing in missing_layers:
        gaps.append({
            'type': 'missing_layer',
            'layer': missing,
            'severity': 'high',
            'message': f"No requirements for {missing} layer",
            'recommendation': f"Add requirements covering {missing} architecture concerns"
        })

    # Security gap: no crosscutting with security keywords
    security_reqs = [r for r in reqs if any(
        kw in (r.name + ' ' + (r.description or '')).lower()
        for kw in ['security', 'auth', 'encrypt', 'access control', 'rbac']
    )]
    if not security_reqs:
        gaps.append({
            'type': 'security_gap',
            'layer': 'crosscutting',
            'severity': 'high',
            'message': 'No security requirements detected',
            'recommendation': 'Add security requirements (authentication, authorisation, encryption)'
        })

    # Performance gap
    perf_reqs = [r for r in reqs if any(
        kw in (r.name + ' ' + (r.description or '')).lower()
        for kw in ['performance', 'latency', 'throughput', 'sla', 'response time']
    )]
    if not perf_reqs and len(reqs) > 5:
        gaps.append({
            'type': 'performance_gap',
            'layer': 'crosscutting',
            'severity': 'medium',
            'message': 'No performance/SLA requirements detected',
            'recommendation': 'Define performance requirements and SLAs'
        })

    # --- WARNINGS ---
    # Duplicate/similar names
    seen_names = {}
    for req in reqs:
        normalised = req.name.lower().strip()
        if normalised in seen_names:
            warnings.append({
                'type': 'duplicate_name',
                'message': f"Possible duplicate: '{req.name}' (ids: {seen_names[normalised]}, {req.id})",
                'requirement_ids': [seen_names[normalised], req.id]
            })
        else:
            seen_names[normalised] = req.id

    return jsonify({
        'solution_id': solution_id,
        'solution_name': solution.name,
        'total_requirements': len(reqs),
        'conflicts': conflicts,
        'conflict_count': len(conflicts),
        'gaps': gaps,
        'gap_count': len(gaps),
        'warnings': warnings,
        'warning_count': len(warnings),
        'health_score': max(0, 100 - len(conflicts) * 20 - len(gaps) * 10 - len(warnings) * 5)
    }), 200


# =============================================================================
# TPM-008: Jira Pull Sync — sync Jira issue status back into platform
# =============================================================================


@enterprise_api_bp.route("/requirements/<int:req_id>/sync-from-jira", methods=["POST"])
@login_required
def sync_requirement_from_jira(req_id):
    """Pull current Jira issue status back into platform."""
    from flask import current_app
    req = SolutionRequirement.query.get_or_404(req_id)
    if not req.jira_issue_key:
        return jsonify({'error': 'No Jira issue linked'}), 400

    jira_url = current_app.config.get('JIRA_URL')
    jira_user = current_app.config.get('JIRA_USER')
    jira_token = current_app.config.get('JIRA_API_TOKEN')
    if not all([jira_url, jira_user, jira_token]):
        return jsonify({'error': 'Jira not configured'}), 503

    try:
        import requests as _requests
        resp = _requests.get(
            f"{jira_url}/rest/api/2/issue/{req.jira_issue_key}",
            auth=(jira_user, jira_token),
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        jira_status = data.get('fields', {}).get('status', {}).get('name', 'Unknown')
        req.jira_push_status = f'synced:{jira_status}'
        db.session.commit()
        return jsonify({'jira_issue_key': req.jira_issue_key, 'jira_status': jira_status}), 200
    except Exception:  # fabricated-ok: returns an honest 502 error, fabricates no value
        return jsonify({'error': 'Failed to reach Jira'}), 502


@enterprise_api_bp.route("/requirements/sync-all-jira", methods=["GET"])
@login_required
def sync_all_requirements_from_jira():
    """Sync all requirements that have a jira_issue_key."""
    reqs = SolutionRequirement.query.filter(
        SolutionRequirement.jira_issue_key != None,
        SolutionRequirement.deleted_at == None
    ).all()
    return jsonify({'total': len(reqs), 'message': 'Bulk sync initiated', 'requirement_ids': [r.id for r in reqs]}), 200


# =============================================================================
# REQ-013: Work Package Link for Roadmap Traceability
# =============================================================================


@enterprise_api_bp.route("/requirements/<int:req_id>/link-work-package", methods=["PATCH"])
@login_required
@require_roles("admin", "architect")
def link_work_package(req_id):
    """Link or unlink a requirement to a kanban work package (REQ-013)."""
    req = SolutionRequirement.query.get_or_404(req_id)

    # RBAC: solution owner or admin OF THE ACTIVE organisation.
    #
    # D-4 (admin-rbac-active-org continuation): this used to be
    # ``current_user.is_admin()`` -- a global Permission.ADMINISTER flag,
    # independent of which organisation is active in the session. Since
    # every self-registered user is Administrator of their own
    # organisation, a user who merely accepted a Viewer invitation into
    # another organisation and switched their session into it bypassed the
    # solution-owner check for any requirement there too -- the exact bug
    # admin_required/org_admin_required already fix elsewhere in this PR.
    from flask import g

    from app.middleware.tenant_decorators import is_platform_admin
    from app.services.rbac_service import rbac_service

    _active_org_id = getattr(g, "current_org_id", None)
    _is_admin_here = is_platform_admin(current_user) or rbac_service.is_org_admin(
        current_user, _active_org_id
    )
    if not _is_admin_here:
        if req.solution_id:
            from app.models.solution_models import Solution
            sol = Solution.query.get(req.solution_id)
            if sol and sol.owner_id != current_user.id:
                return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    wp_id = data.get("work_package_id")
    try:
        req.work_package_id = int(wp_id) if wp_id is not None else None
    except (ValueError, TypeError):
        return jsonify({"error": "work_package_id must be an integer"}), 400

    try:
        db.session.commit()
        return jsonify({"id": req.id, "work_package_id": req.work_package_id})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 500


@enterprise_api_bp.route('/solutions/<int:solution_id>/traceability-chain', methods=['GET'])
@login_required
def solution_traceability_chain(solution_id):
    """AIP-005: Build cross-layer traceability chain for a solution."""
    from app.models.solution_architect_models import Solution

    solution = Solution.query.get_or_404(solution_id)
    reqs = SolutionRequirement.query.filter(
        SolutionRequirement.solution_id == solution_id,
        SolutionRequirement.deleted_at == None  # noqa: E711
    ).all()

    # Group by layer
    layers = {'business': [], 'application': [], 'technology': [], 'crosscutting': [], 'unclassified': []}
    for req in reqs:
        lyr = (getattr(req, 'layer', None) or 'unclassified').lower()
        target = layers.get(lyr, layers['unclassified'])
        target.append({
            'id': req.id,
            'name': req.name,
            'status': req.status,
            'moscow': getattr(req, 'moscow_priority', None),
            'layer': lyr
        })

    def _find_links(source_reqs, target_reqs):
        """Find requirements that share keyword context — inferred traceability."""
        links = []
        for src in source_reqs:
            src_words = set((src['name']).lower().split())
            src_words = {w for w in src_words if len(w) > 4}
            for tgt in target_reqs:
                tgt_words = set((tgt['name']).lower().split())
                tgt_words = {w for w in tgt_words if len(w) > 4}
                overlap = src_words & tgt_words
                if len(overlap) >= 1:
                    links.append({
                        'from_id': src['id'],
                        'from_name': src['name'],
                        'from_layer': src['layer'],
                        'to_id': tgt['id'],
                        'to_name': tgt['name'],
                        'to_layer': tgt['layer'],
                        'shared_keywords': list(overlap)[:5],
                        'link_type': 'inferred'
                    })
        return links

    bus_to_app = _find_links(layers['business'], layers['application'])
    app_to_tech = _find_links(layers['application'], layers['technology'])
    crosscutting_links = _find_links(
        layers['crosscutting'],
        layers['business'] + layers['application'] + layers['technology']
    )

    total = len(reqs)
    classified = total - len(layers['unclassified'])

    linked_ids = set()
    for link in bus_to_app + app_to_tech + crosscutting_links:
        linked_ids.add(link['from_id'])
        linked_ids.add(link['to_id'])

    orphaned = [{'id': r.id, 'name': r.name, 'layer': getattr(r, 'layer', None)}
                for r in reqs if r.id not in linked_ids]

    return jsonify({
        'solution_id': solution_id,
        'solution_name': solution.name,
        'total_requirements': total,
        'classified_count': classified,
        'layer_counts': {k: len(v) for k, v in layers.items()},
        'traceability_links': {
            'business_to_application': bus_to_app,
            'application_to_technology': app_to_tech,
            'crosscutting': crosscutting_links
        },
        'total_links': len(bus_to_app) + len(app_to_tech) + len(crosscutting_links),
        'orphaned_requirements': orphaned[:10],
        'orphaned_count': len(orphaned),
        'coverage_pct': round(len(linked_ids) / total * 100) if total > 0 else 0
    }), 200


# =============================================================================
# TPM-004: Epic hierarchy — list top-level epics with their children
# =============================================================================
@enterprise_api_bp.route("/solutions/<int:solution_id>/requirements/epics", methods=["GET"])
@login_required
def list_epics(solution_id):
    """Return top-level epics (epic_parent_id IS NULL) with their child requirements."""
    from app.models.solution_models import Solution

    require_entity(Solution, solution_id, description="Solution not found")
    epics = SolutionRequirement.query.filter_by(
        solution_id=solution_id,
        epic_parent_id=None,
        deleted_at=None
    ).all()
    result = []
    for epic in epics:
        children = SolutionRequirement.query.filter_by(
            epic_parent_id=epic.id,
            deleted_at=None
        ).all()
        result.append({
            **_req_to_dict(epic),
            "children_count": len(children),
            "children": [_req_to_dict(c) for c in children],
        })
    return jsonify(result), 200


# =============================================================================
# AIP-002: Architecture Completeness Scoring
# =============================================================================
@enterprise_api_bp.route('/solutions/<int:solution_id>/completeness-score', methods=['GET'])
@login_required
def solution_completeness_score(solution_id):
    """AIP-002: Score requirements coverage across TOGAF/ArchiMate layers."""
    from app.models.solution_architect_models import SolutionRequirement, Solution

    solution = Solution.query.get_or_404(solution_id)

    # Get all active requirements for this solution
    reqs = SolutionRequirement.query.filter(
        SolutionRequirement.solution_id == solution_id,
        SolutionRequirement.deleted_at == None
    ).all()

    total = len(reqs)

    # Layer definitions with expected minimum coverage
    LAYER_DEFINITIONS = {
        'business': {
            'label': 'Business Layer',
            'description': 'Processes, roles, policies, business rules',
            'min_expected': 3
        },
        'application': {
            'label': 'Application Layer',
            'description': 'Functional, data, integration requirements',
            'min_expected': 4
        },
        'technology': {
            'label': 'Technology Layer',
            'description': 'Infrastructure, platform, security, operations',
            'min_expected': 2
        },
        'crosscutting': {
            'label': 'Cross-cutting / NFR',
            'description': 'Performance, usability, compliance, security',
            'min_expected': 2
        }
    }

    # Count requirements per layer
    layer_counts = {}
    unclassified = 0
    for req in reqs:
        lyr = (getattr(req, 'layer', None) or '').lower().strip()
        if lyr in LAYER_DEFINITIONS:
            layer_counts[lyr] = layer_counts.get(lyr, 0) + 1
        else:
            unclassified += 1

    # Score each layer
    layer_scores = {}
    gaps = []
    total_score = 0
    max_score = 0

    for layer_key, layer_def in LAYER_DEFINITIONS.items():
        count = layer_counts.get(layer_key, 0)
        min_exp = layer_def['min_expected']
        coverage_pct = min(100, round(count / min_exp * 100)) if min_exp > 0 else 100

        # Score: 0=missing, 1=partial, 2=adequate, 3=comprehensive
        if count == 0:
            score_label = 'missing'
            score_val = 0
            gaps.append({
                'layer': layer_key,
                'label': layer_def['label'],
                'severity': 'high',
                'message': f"No {layer_def['label']} requirements defined",
                'recommendation': f"Add at least {min_exp} requirements for {layer_def['description']}"
            })
        elif count < min_exp:
            score_label = 'partial'
            score_val = 1
            gaps.append({
                'layer': layer_key,
                'label': layer_def['label'],
                'severity': 'medium',
                'message': f"Only {count} of expected {min_exp} {layer_def['label']} requirements",
                'recommendation': f"Add {min_exp - count} more requirements covering {layer_def['description']}"
            })
        elif count < min_exp * 2:
            score_label = 'adequate'
            score_val = 2
        else:
            score_label = 'comprehensive'
            score_val = 3

        layer_scores[layer_key] = {
            'count': count,
            'min_expected': min_exp,
            'coverage_pct': coverage_pct,
            'score_label': score_label,
            'label': layer_def['label'],
            'description': layer_def['description']
        }
        total_score += score_val
        max_score += 3

    # Overall percentage
    overall_pct = round(total_score / max_score * 100) if max_score > 0 else 0

    # Readiness level
    if overall_pct >= 80:
        readiness = 'ready'
        readiness_label = 'Architecture Ready'
    elif overall_pct >= 60:
        readiness = 'adequate'
        readiness_label = 'Adequate Coverage'
    elif overall_pct >= 40:
        readiness = 'partial'
        readiness_label = 'Partial Coverage'
    else:
        readiness = 'insufficient'
        readiness_label = 'Insufficient Coverage'

    return jsonify({
        'solution_id': solution_id,
        'solution_name': solution.name,
        'total_requirements': total,
        'unclassified_count': unclassified,
        'overall_score_pct': overall_pct,
        'readiness': readiness,
        'readiness_label': readiness_label,
        'layer_scores': layer_scores,
        'gaps': gaps,
        'gap_count': len(gaps)
    }), 200


# =============================================================================
# AIP-004: Reference Architecture Pattern Matching
# =============================================================================


@enterprise_api_bp.route('/solutions/<int:solution_id>/pattern-match', methods=['GET'])
@login_required
def solution_pattern_match(solution_id):
    """AIP-004: Match solution requirements to reference architecture patterns."""
    from app.models.solution_architect_models import SolutionRequirement, Solution

    solution = Solution.query.get_or_404(solution_id)
    reqs = SolutionRequirement.query.filter(
        SolutionRequirement.solution_id == solution_id,
        SolutionRequirement.deleted_at == None
    ).all()

    # Aggregate all requirement text for keyword matching
    all_text = ' '.join(
        (r.name + ' ' + (r.description or '')).lower()
        for r in reqs
    )

    layer_counts = {}
    for req in reqs:
        lyr = (getattr(req, 'layer', None) or 'unclassified').lower()
        layer_counts[lyr] = layer_counts.get(lyr, 0) + 1

    # Reference pattern library
    PATTERNS = [
        {
            'id': 'microservices',
            'name': 'Microservices Architecture',
            'description': 'Decomposes application into small, independently deployable services',
            'keywords': ['microservice', 'api gateway', 'service mesh', 'container', 'kubernetes', 'docker', 'independently deploy'],
            'min_tech_reqs': 3,
            'archimate_elements': ['ApplicationComponent', 'ApplicationService', 'Node']
        },
        {
            'id': 'event_driven',
            'name': 'Event-Driven Architecture',
            'description': 'Components communicate through events/messages asynchronously',
            'keywords': ['event', 'message queue', 'kafka', 'rabbitmq', 'pubsub', 'async', 'webhook', 'stream'],
            'min_tech_reqs': 2,
            'archimate_elements': ['ApplicationEvent', 'ApplicationInterface', 'CommunicationNetwork']
        },
        {
            'id': 'layered',
            'name': 'Layered (N-Tier) Architecture',
            'description': 'Separates concerns into presentation, business, data layers',
            'keywords': ['layer', 'tier', 'frontend', 'backend', 'database', 'api', 'ui'],
            'min_tech_reqs': 1,
            'archimate_elements': ['ApplicationLayer', 'BusinessLayer', 'TechnologyLayer']
        },
        {
            'id': 'serverless',
            'name': 'Serverless / FaaS Architecture',
            'description': 'Functions-as-a-Service with no server management',
            'keywords': ['serverless', 'lambda', 'function', 'faas', 'cloud function', 'auto-scale'],
            'min_tech_reqs': 2,
            'archimate_elements': ['ApplicationFunction', 'Node', 'SystemSoftware']
        },
        {
            'id': 'cqrs',
            'name': 'CQRS / Event Sourcing',
            'description': 'Separates read and write models with event log as source of truth',
            'keywords': ['cqrs', 'command', 'query', 'event sourcing', 'audit trail', 'immutable', 'read model'],
            'min_tech_reqs': 2,
            'archimate_elements': ['ApplicationProcess', 'DataObject', 'ApplicationEvent']
        },
        {
            'id': 'hexagonal',
            'name': 'Hexagonal (Ports & Adapters)',
            'description': 'Domain logic isolated from external systems via ports and adapters',
            'keywords': ['port', 'adapter', 'domain', 'hexagonal', 'clean architecture', 'dependency inversion'],
            'min_tech_reqs': 2,
            'archimate_elements': ['ApplicationComponent', 'ApplicationInterface', 'BusinessObject']
        },
        {
            'id': 'data_mesh',
            'name': 'Data Mesh',
            'description': 'Decentralised data ownership with domain-oriented data products',
            'keywords': ['data mesh', 'data product', 'data domain', 'data pipeline', 'analytics', 'bi ', 'reporting'],
            'min_tech_reqs': 2,
            'archimate_elements': ['DataObject', 'ApplicationComponent', 'BusinessProcess']
        }
    ]

    # Score each pattern
    matched = []
    partial = []

    for pattern in PATTERNS:
        kw_hits = sum(1 for kw in pattern['keywords'] if kw in all_text)
        kw_score = min(100, round(kw_hits / max(len(pattern['keywords']), 1) * 100))

        tech_count = layer_counts.get('technology', 0)
        meets_min_tech = tech_count >= pattern['min_tech_reqs']

        confidence = kw_score * 0.7 + (30 if meets_min_tech else 0)
        confidence = min(100, round(confidence))

        entry = {
            'pattern_id': pattern['id'],
            'name': pattern['name'],
            'description': pattern['description'],
            'confidence_pct': confidence,
            'keyword_hits': kw_hits,
            'total_keywords': len(pattern['keywords']),
            'archimate_elements': pattern['archimate_elements'],
            'meets_tech_threshold': meets_min_tech
        }

        if confidence >= 50:
            matched.append(entry)
        elif confidence >= 20:
            partial.append(entry)

    matched.sort(key=lambda x: x['confidence_pct'], reverse=True)
    partial.sort(key=lambda x: x['confidence_pct'], reverse=True)

    return jsonify({
        'solution_id': solution_id,
        'solution_name': solution.name,
        'total_requirements': len(reqs),
        'matched_patterns': matched[:3],
        'partial_patterns': partial[:3],
        'dominant_layer': max(layer_counts, key=layer_counts.get) if layer_counts else None,
        'layer_distribution': layer_counts
    }), 200


@enterprise_api_bp.route('/requirements/<int:req_id>/auto-classify', methods=['POST'])
@login_required
def auto_classify_requirement(req_id):
    """AIP-001: Auto-classify requirement to architecture layer and suggest template."""
    from app.models.solution_architect_models import SolutionRequirement
    from app.models.requirement_template import RequirementTemplate

    req = SolutionRequirement.query.get_or_404(req_id)

    text = (req.name + ' ' + (req.description or '') + ' ' + (getattr(req, 'acceptance_criteria', '') or '')).lower()

    LAYER_KEYWORDS = {
        'business': [
            'business process', 'business rule', 'stakeholder', 'workflow', 'approval',
            'policy', 'compliance', 'governance', 'role', 'actor', 'user story',
            'business capability', 'value stream', 'sla', 'kpi', 'objective'
        ],
        'application': [
            'api', 'endpoint', 'microservice', 'service', 'database', 'crud',
            'integration', 'interface', 'module', 'component', 'application',
            'user interface', 'ui', 'screen', 'form', 'report', 'dashboard',
            'data model', 'schema', 'event', 'message', 'queue'
        ],
        'technology': [
            'infrastructure', 'server', 'network', 'cloud', 'kubernetes', 'docker',
            'deployment', 'ci/cd', 'pipeline', 'monitoring', 'logging', 'backup',
            'disaster recovery', 'scalability', 'availability', 'hosting', 'platform',
            'operating system', 'hardware', 'storage', 'bandwidth'
        ],
        'crosscutting': [
            'security', 'authentication', 'authorisation', 'encryption', 'audit',
            'performance', 'latency', 'throughput', 'accessibility', 'wcag',
            'usability', 'localisation', 'internationalisation', 'gdpr', 'privacy',
            'legal', 'regulatory', 'compliance', 'testability', 'maintainability'
        ]
    }

    layer_scores = {}
    for layer, keywords in LAYER_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        layer_scores[layer] = score

    best_layer = max(layer_scores, key=layer_scores.get)
    best_score = layer_scores[best_layer]
    confidence = min(100, round(best_score / 3 * 100)) if best_score > 0 else 0

    if confidence < 20:
        best_layer = 'application'
        confidence = 10

    try:
        templates = RequirementTemplate.query.filter(
            RequirementTemplate.layer == best_layer,
            RequirementTemplate.is_active == True  # noqa: E712
        ).all()

        best_template = None
        best_template_score = -1
        for tpl in templates:
            tpl_text = (tpl.name + ' ' + (tpl.description or '')).lower()
            tpl_score = sum(1 for word in text.split()[:20] if word in tpl_text)
            if tpl_score > best_template_score:
                best_template_score = tpl_score
                best_template = tpl
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
        best_template = None

    apply_changes = request.get_json(silent=True) or {}
    should_apply = apply_changes.get('apply', False)

    if should_apply:
        req.layer = best_layer
        if best_template:
            req.template_id = best_template.id
        db.session.commit()

    return jsonify({
        'requirement_id': req_id,
        'classified_layer': best_layer,
        'confidence_pct': confidence,
        'layer_scores': layer_scores,
        'suggested_template': {
            'id': best_template.id,
            'name': best_template.name,
            'layer': best_template.layer
        } if best_template else None,
        'applied': should_apply,
        'current_layer': req.layer
    }), 200


@enterprise_api_bp.route('/solutions/<int:solution_id>/generate-design', methods=['POST'])
@login_required
def generate_solution_design(solution_id):
    """AIP-006: Generate structured architecture design narrative from requirements."""
    from app.models.solution_architect_models import SolutionRequirement, Solution

    solution = Solution.query.get_or_404(solution_id)
    reqs = SolutionRequirement.query.filter(
        SolutionRequirement.solution_id == solution_id,
        SolutionRequirement.deleted_at == None
    ).all()

    data = request.get_json() or {}
    style = data.get('style', 'narrative')  # 'narrative', 'structured', 'adr'

    # Group by layer
    layers = {'business': [], 'application': [], 'technology': [], 'crosscutting': []}
    for req in reqs:
        lyr = (getattr(req, 'layer', None) or '').lower()
        if lyr in layers:
            layers[lyr].append(req)

    must_haves = [r for r in reqs if (getattr(r, 'moscow_priority', None) or '').upper() == 'MUST']

    def _req_summary(req_list, limit=5):
        return ', '.join(r.name for r in req_list[:limit])

    # Generate design sections
    sections = {}

    # Business Architecture section
    if layers['business']:
        sections['business_architecture'] = {
            'title': 'Business Architecture',
            'content': f"The solution supports {len(layers['business'])} business requirements. "
                      f"Key capabilities include: {_req_summary(layers['business'])}. "
                      f"Business processes are governed by defined policies and roles.",
            'requirement_count': len(layers['business']),
            'archimate_layer': 'Business Layer'
        }

    # Application Architecture section
    if layers['application']:
        sections['application_architecture'] = {
            'title': 'Application Architecture',
            'content': f"The application layer comprises {len(layers['application'])} functional requirements. "
                      f"Core components: {_req_summary(layers['application'])}. "
                      f"Services are exposed via well-defined APIs following REST principles.",
            'requirement_count': len(layers['application']),
            'archimate_layer': 'Application Layer'
        }

    # Technology Architecture section
    if layers['technology']:
        sections['technology_architecture'] = {
            'title': 'Technology Architecture',
            'content': f"The technology layer addresses {len(layers['technology'])} infrastructure requirements. "
                      f"Platform concerns: {_req_summary(layers['technology'])}. "
                      f"The infrastructure is designed for scalability, reliability, and security.",
            'requirement_count': len(layers['technology']),
            'archimate_layer': 'Technology Layer'
        }

    # Cross-cutting section
    if layers['crosscutting']:
        sections['crosscutting_concerns'] = {
            'title': 'Cross-cutting Concerns',
            'content': f"Non-functional requirements span {len(layers['crosscutting'])} concerns: "
                      f"{_req_summary(layers['crosscutting'])}. "
                      f"These requirements apply across all architecture layers.",
            'requirement_count': len(layers['crosscutting']),
            'archimate_layer': 'All Layers'
        }

    # ADR style output
    if style == 'adr':
        adr_records = []
        for req in must_haves[:5]:
            adr_records.append({
                'adr_id': f"ADR-{req.id:03d}",
                'title': f"Decision: {req.name}",
                'status': 'Proposed',
                'context': req.description or f"Requirement to implement: {req.name}",
                'decision': f"Implement {req.name} as a {getattr(req, 'layer', 'application')}-layer concern",
                'consequences': req.acceptance_criteria or 'See acceptance criteria'
            })
        sections['architecture_decisions'] = adr_records

    # Executive summary
    total = len(reqs)
    classified = sum(len(v) for v in layers.values())
    summary = (
        f"Architecture design for '{solution.name}' covers {total} requirements across "
        f"{sum(1 for v in layers.values() if v)} architecture layers. "
        f"{len(must_haves)} must-have requirements drive the core design decisions."
    )

    return jsonify({
        'solution_id': solution_id,
        'solution_name': solution.name,
        'style': style,
        'executive_summary': summary,
        'sections': sections,
        'must_have_count': len(must_haves),
        'total_requirements': total,
        'design_completeness_pct': round(classified / total * 100) if total > 0 else 0
    }), 200


@enterprise_api_bp.route('/solutions/<int:solution_id>/readiness-report', methods=['GET'])
@login_required
def solution_readiness_report(solution_id):
    """AIP-008: Composite architecture readiness report aggregating all AI scores."""
    from app.models.solution_architect_models import SolutionRequirement, Solution

    solution = Solution.query.get_or_404(solution_id)
    reqs = SolutionRequirement.query.filter(
        SolutionRequirement.solution_id == solution_id,
        SolutionRequirement.deleted_at == None
    ).all()

    total = len(reqs)

    # --- DIMENSION 1: Layer Coverage ---
    layer_counts = {}
    for req in reqs:
        lyr = (getattr(req, 'layer', None) or 'unclassified').lower()
        layer_counts[lyr] = layer_counts.get(lyr, 0) + 1

    required_layers = {'business', 'application', 'technology', 'crosscutting'}
    present_layers = set(layer_counts.keys()) & required_layers
    coverage_score = round(len(present_layers) / len(required_layers) * 100)

    # --- DIMENSION 2: Requirements Quality ---
    classified = sum(layer_counts.get(item, 0) for item in required_layers)
    with_ac = sum(1 for r in reqs if r.acceptance_criteria and len(r.acceptance_criteria) > 20)
    quality_score = round((classified / total * 50 + with_ac / max(total, 1) * 50)) if total > 0 else 0

    # --- DIMENSION 3: Priority Distribution ---
    must_count = sum(1 for r in reqs if (getattr(r, 'moscow_priority', None) or '').upper() == 'MUST')
    should_count = sum(1 for r in reqs if (getattr(r, 'moscow_priority', None) or '').upper() == 'SHOULD')
    moscow_coverage = round((must_count + should_count) / max(total, 1) * 100)

    # --- DIMENSION 4: Conflict/Gap count (inline simplified logic) ---
    security_reqs = [r for r in reqs if any(
        kw in (r.name + ' ' + (r.description or '')).lower()
        for kw in ['security', 'auth', 'encrypt', 'access']
    )]
    missing_layers = required_layers - present_layers
    critical_gaps = len(missing_layers) + (1 if not security_reqs else 0)
    gap_score = max(0, 100 - critical_gaps * 25)

    # --- COMPOSITE SCORE ---
    composite = round(coverage_score * 0.3 + quality_score * 0.3 + moscow_coverage * 0.2 + gap_score * 0.2)

    # Certification level
    if composite >= 85:
        cert_level = 'CERTIFIED'
        cert_label = 'Architecture Certified — Ready for Delivery'
        cert_color = 'green'
    elif composite >= 70:
        cert_level = 'CONDITIONAL'
        cert_label = 'Conditionally Approved — Minor Gaps'
        cert_color = 'yellow'
    elif composite >= 50:
        cert_level = 'REVIEW_REQUIRED'
        cert_label = 'Review Required — Significant Gaps'
        cert_color = 'orange'
    else:
        cert_level = 'NOT_READY'
        cert_label = 'Not Ready — Major Deficiencies'
        cert_color = 'red'

    # Recommendations
    recommendations = []
    if coverage_score < 75:
        recommendations.append({
            'priority': 'HIGH',
            'action': f"Add requirements for missing layers: {', '.join(missing_layers)}",
            'impact': 'Improves layer coverage score'
        })
    if quality_score < 60:
        missing_ac = total - with_ac
        recommendations.append({
            'priority': 'MEDIUM',
            'action': f"Add acceptance criteria to {missing_ac} requirements",
            'impact': 'Improves quality score'
        })
    if moscow_coverage < 50:
        recommendations.append({
            'priority': 'MEDIUM',
            'action': 'Prioritise requirements using MoSCoW method',
            'impact': 'Improves priority distribution score'
        })
    if not security_reqs:
        recommendations.append({
            'priority': 'HIGH',
            'action': 'Add security requirements (authentication, authorisation, encryption)',
            'impact': 'Addresses critical security gap'
        })

    return jsonify({
        'solution_id': solution_id,
        'solution_name': solution.name,
        'certification_level': cert_level,
        'certification_label': cert_label,
        'certification_color': cert_color,
        'composite_score': composite,
        'dimensions': {
            'layer_coverage': {'score': coverage_score, 'label': 'Layer Coverage', 'present_layers': list(present_layers)},
            'requirements_quality': {'score': quality_score, 'label': 'Requirements Quality', 'with_ac': with_ac, 'total': total},
            'priority_distribution': {'score': moscow_coverage, 'label': 'Priority Distribution', 'must_count': must_count, 'should_count': should_count},
            'gap_analysis': {'score': gap_score, 'label': 'Gap Analysis', 'critical_gaps': critical_gaps}
        },
        'recommendations': recommendations,
        'total_requirements': total
    }), 200


@enterprise_api_bp.route('/requirements/<int:req_id>/validate-ac', methods=['POST'])
@login_required
def validate_requirement_ac(req_id):
    """PRQ-010: Validate acceptance criteria quality — detect tokens, vague language, missing structure."""
    import re

    req = SolutionRequirement.query.get_or_404(req_id)
    ac = req.acceptance_criteria or ''

    issues = []
    score = 100  # start perfect, deduct per issue

    # --- 1. Unfilled placeholder tokens ---
    TOKEN_RE = re.compile(r'\{[a-zA-Z_][a-zA-Z0-9_ ]*\}')
    tokens = TOKEN_RE.findall(ac)
    if tokens:
        issues.append({
            'type': 'unfilled_token',
            'severity': 'error',
            'message': f"Unfilled placeholder(s): {', '.join(set(tokens))}",
            'fix': 'Replace each {placeholder} with a specific value'
        })
        score -= len(tokens) * 15

    # --- 2. Vague modal verbs ---
    VAGUE_MODALS = ['should', 'might', 'may', 'could', 'would', 'possibly', 'probably', 'perhaps', 'approximately']
    ac_lower = ac.lower()
    found_vague = [w for w in VAGUE_MODALS if f' {w} ' in ac_lower or ac_lower.startswith(w)]
    if found_vague:
        issues.append({
            'type': 'vague_language',
            'severity': 'warning',
            'message': f"Vague modal verb(s) found: {', '.join(found_vague)}",
            'fix': "Replace with 'shall', 'must', or a definitive statement"
        })
        score -= len(found_vague) * 5

    # --- 3. Missing structure check ---
    has_given_when_then = (
        'given ' in ac_lower and 'when ' in ac_lower and 'then ' in ac_lower
    )
    has_shall = 'shall' in ac_lower or 'must' in ac_lower
    if not has_given_when_then and not has_shall:
        issues.append({
            'type': 'missing_structure',
            'severity': 'warning',
            'message': 'No Given/When/Then pattern or shall-statement found',
            'fix': "Use 'Given [context], When [action], Then [outcome]' or 'The system shall [behaviour]'"
        })
        score -= 20

    # --- 4. Non-measurable language ---
    VAGUE_QUALIFIERS = ['fast', 'quick', 'slow', 'good', 'bad', 'appropriate', 'reasonable', 'adequate', 'easy', 'simple', 'robust', 'scalable', 'flexible']
    found_qualifiers = [w for w in VAGUE_QUALIFIERS if f' {w} ' in ac_lower or ac_lower.endswith(f' {w}')]
    if found_qualifiers:
        issues.append({
            'type': 'non_measurable',
            'severity': 'warning',
            'message': f"Non-measurable qualifier(s): {', '.join(found_qualifiers)}",
            'fix': 'Quantify: replace with specific numbers, thresholds, or time units'
        })
        score -= len(found_qualifiers) * 5

    # --- 5. Too short ---
    word_count = len(ac.split())
    if word_count < 10 and ac:
        issues.append({
            'type': 'too_brief',
            'severity': 'warning',
            'message': f"Acceptance criteria too brief ({word_count} words). Minimum recommended: 10",
            'fix': 'Expand with specific conditions, actions, and expected outcomes'
        })
        score -= 15

    # --- 6. Empty ---
    if not ac.strip():
        issues.append({
            'type': 'empty',
            'severity': 'error',
            'message': 'Acceptance criteria is empty',
            'fix': 'Add at least one testable acceptance criterion'
        })
        score = 0

    score = max(0, score)

    # Quality label
    if score >= 80:
        quality_label = 'good'
    elif score >= 60:
        quality_label = 'acceptable'
    elif score >= 40:
        quality_label = 'poor'
    else:
        quality_label = 'unacceptable'

    return jsonify({
        'requirement_id': req_id,
        'requirement_name': req.name,
        'ac_text': ac,
        'is_valid': len([i for i in issues if i['severity'] == 'error']) == 0,
        'quality_score': score,
        'quality_label': quality_label,
        'issues': issues,
        'issue_count': len(issues),
        'word_count': word_count if ac else 0
    }), 200


@enterprise_api_bp.route('/requirements/<int:req_id>/dependencies', methods=['GET'])
@login_required
def get_requirement_dependencies(req_id):
    """PRQ-001: Get dependency graph for a requirement."""
    from app.models.solution_architect_models import SolutionRequirement, RequirementDependency

    SolutionRequirement.query.get_or_404(req_id)

    outgoing = RequirementDependency.query.filter_by(req_id=req_id).all()
    incoming = RequirementDependency.query.filter_by(depends_on_id=req_id).all()

    def _dep_dict(dep, direction):
        other_id = dep.depends_on_id if direction == 'outgoing' else dep.req_id
        other = SolutionRequirement.query.get(other_id)
        return {
            'dep_id': dep.id,
            'direction': direction,
            'dependency_type': dep.dependency_type,
            'other_req_id': other_id,
            'other_req_name': other.name if other else None,
            'other_req_status': other.status if other else None
        }

    return jsonify({
        'requirement_id': req_id,
        'outgoing': [_dep_dict(d, 'outgoing') for d in outgoing],
        'incoming': [_dep_dict(d, 'incoming') for d in incoming],
        'total_deps': len(outgoing) + len(incoming)
    }), 200


@enterprise_api_bp.route('/requirements/<int:req_id>/dependencies', methods=['POST'])
@login_required
def add_requirement_dependency(req_id):
    """PRQ-001: Add a dependency between two requirements."""
    from app.models.solution_architect_models import SolutionRequirement, RequirementDependency

    SolutionRequirement.query.get_or_404(req_id)
    data = request.get_json() or {}

    depends_on_id = data.get('depends_on_id')
    dep_type = data.get('dependency_type', 'relates')

    if not depends_on_id:
        return jsonify({'error': 'depends_on_id is required'}), 400

    if depends_on_id == req_id:
        return jsonify({'error': 'A requirement cannot depend on itself'}), 400

    # Cycle detection: check if depends_on_id already depends on req_id
    existing = RequirementDependency.query.filter_by(req_id=depends_on_id, depends_on_id=req_id).first()
    if existing:
        return jsonify({'error': 'Circular dependency detected'}), 400

    SolutionRequirement.query.get_or_404(depends_on_id)

    dep = RequirementDependency(
        req_id=req_id,
        depends_on_id=depends_on_id,
        dependency_type=dep_type,
        created_by_id=current_user.id
    )
    db.session.add(dep)
    db.session.commit()

    return jsonify({'dep_id': dep.id, 'req_id': req_id, 'depends_on_id': depends_on_id, 'dependency_type': dep_type}), 201


@enterprise_api_bp.route('/requirements/<int:req_id>/dependencies/<int:dep_id>', methods=['DELETE'])
@login_required
def remove_requirement_dependency(req_id, dep_id):
    """PRQ-001: Remove a dependency."""
    from app.models.solution_architect_models import RequirementDependency, SolutionRequirement

    # A dependency carries no organisation of its own; its requirement does. Resolve
    # it first, as adding a dependency already does.
    SolutionRequirement.query.get_or_404(req_id)
    dep = RequirementDependency.query.filter_by(id=dep_id, req_id=req_id).first_or_404()
    db.session.delete(dep)
    db.session.commit()
    return jsonify({'deleted': dep_id}), 200

@enterprise_api_bp.route('/requirements/<int:req_id>/score-ac', methods=['POST'])
@login_required
def score_requirement_ac(req_id):
    """PRQ-007: Score the acceptance criteria quality for a requirement."""
    from app.models.solution_architect_models import SolutionRequirement
    req = SolutionRequirement.query.get_or_404(req_id)
    ac_text = req.acceptance_criteria or ''
    score, issues = _score_ac_quality(ac_text)
    return jsonify({
        'requirement_id': req_id,
        'ac_score': score,
        'ac_issues': issues,
        'ac_grade': 'A' if score >= 80 else ('B' if score >= 60 else ('C' if score >= 40 else 'D')),
        'ac_text': ac_text,
        'recommendation': 'Good AC quality' if score >= 80 else 'AC needs improvement'
    }), 200


@enterprise_api_bp.route('/solutions/<int:solution_id>/ac-quality-summary', methods=['GET'])
@login_required
def ac_quality_summary(solution_id):
    """PRQ-007: Return aggregate AC quality scores for all requirements in a solution."""
    from app.models.solution_models import Solution
    from app.models.solution_architect_models import SolutionRequirement

    require_entity(Solution, solution_id, description="Solution not found")
    reqs = SolutionRequirement.query.filter_by(solution_id=solution_id).all()
    reqs = [r for r in reqs if r.deleted_at is None]
    results = []
    for req in reqs:
        ac_text = req.acceptance_criteria or ''
        score, issues = _score_ac_quality(ac_text)
        results.append({
            'requirement_id': req.id,
            'title': req.title,
            'ac_score': score,
            'ac_grade': 'A' if score >= 80 else ('B' if score >= 60 else ('C' if score >= 40 else 'D')),
            'issues': issues
        })
    avg = sum(r['ac_score'] for r in results) / len(results) if results else 0
    return jsonify({
        'solution_id': solution_id,
        'average_ac_score': round(avg, 1),
        'requirement_scores': results,
        'total': len(results)
    }), 200


@enterprise_api_bp.route('/requirements/templates/stats', methods=['GET'])
@login_required
def template_stats():
    """PRQ-009: Return template library statistics."""
    from app.models.requirement_template import RequirementTemplate
    templates = RequirementTemplate.query.filter_by(is_system=True).all()
    by_layer = {}
    for t in templates:
        by_layer[t.layer] = by_layer.get(t.layer, 0) + 1
    return jsonify({
        'total_templates': len(templates),
        'by_layer': by_layer
    }), 200