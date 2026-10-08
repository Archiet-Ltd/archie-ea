"""
flask seed-charters — populate agent_charters from ARCHITECT_PERSONAS.

Creates version 1 charter records for every existing organisation, derived from
the persona charter definitions in architect_persona_charters.py.

Usage:
    flask --app manage seed-charters
    flask --app manage seed-charters --organization-id 1
"""
import click
from flask.cli import with_appcontext

from app import db
from app.models.agent_charter import AgentCharter
from app.models.organization import Organization
from app.modules.ai_chat.services.architect_persona_charters import (
    ARCHITECT_PERSONAS, CHARTERS,
)

# Tool categories for charter bounds
ALL_WRITE_TOOLS = sorted([
    "create_solution", "link_capability_to_solution", "link_application_to_capability",
    "create_archimate_element", "update_application_status", "submit_for_arb_review",
    "create_driver", "create_goal", "create_constraint", "create_requirement",
    "create_risk", "create_option", "mark_option_recommended",
    "link_application_to_solution", "link_vendor_product", "run_inference_engine",
    "generate_blueprint_narrative", "create_archimate_relationship",
    "update_solution_fields", "update_solution_phase", "propose_genome_patch",
    "create_adr", "record_capability_maturity", "score_rationalization",
    "merge_capabilities", "create_vendor", "extract_contract_from_document",
    "bulk_update_application_status", "create_contract", "create_programme",
    "upsert_license", "poll_infrastructure",
])

SOLUTION_DESIGN_TOOLS = sorted([
    "create_solution", "create_archimate_element", "run_inference_engine",
    "create_archimate_relationship", "create_driver", "create_goal",
    "create_constraint", "create_requirement", "create_risk", "create_option",
    "mark_option_recommended", "link_capability_to_solution",
    "link_application_to_solution", "link_vendor_product",
    "update_solution_fields", "update_solution_phase",
    "generate_blueprint_narrative", "create_adr", "propose_genome_patch",
])

# Per-persona charter bounds: (purpose, readable_entities, proposable, forbidden)
_CHARTER_BOUNDS = {
    "enterprise_architect": (
        "Landscape steward — sense, rationalise and steer the enterprise portfolio.",
        ["application", "capability", "solution", "programme", "risk", "arb_item", "archimate_element", "vendor", "contract", "adr"],
        "all",
        sorted(["submit_for_arb_review", "generate_blueprint_narrative", "poll_infrastructure", "create_vendor", "create_contract", "upsert_license"]),
    ),
    "solutions_architect": (
        "Design partner — produce ArchiMate 3.2-sound solution designs that pass ARB.",
        ["application", "capability", "solution", "archimate_element", "vendor_product", "integration_pattern", "adr"],
        SOLUTION_DESIGN_TOOLS,
        sorted(["record_capability_maturity", "score_rationalization", "merge_capabilities", "update_application_status", "create_programme"]),
    ),
    "technology_architect": (
        "Conformance reviewer — verify designs against technical policy.",
        ["application", "capability", "solution", "archimate_element", "integration_pattern", "vendor_product"],
        sorted(["run_inference_engine", "create_archimate_relationship", "update_solution_fields", "poll_infrastructure", "create_archimate_element"]),
        sorted(["create_solution", "submit_for_arb_review", "create_adr", "record_capability_maturity", "merge_capabilities"]),
    ),
    "data_architect": (
        "Data-layer steward — canonical entities, classified data, traceable lineage.",
        ["application", "archimate_element", "data_object", "solution"],
        sorted(["create_archimate_element", "infer_schema", "create_archimate_relationship"]),
        sorted(["create_solution", "submit_for_arb_review", "update_application_status", "create_programme"]),
    ),
    "security_architect": (
        "Trust-boundary steward — ensure every solution can answer who can reach what.",
        ["application", "archimate_element", "risk", "solution", "arb_item"],
        [],
        ALL_WRITE_TOOLS,
    ),
    "business_architect": (
        "Capability-to-strategy translator — connect strategy to the capability model.",
        ["application", "capability", "solution", "archimate_element"],
        sorted(["create_solution", "link_capability_to_solution", "record_capability_maturity", "create_driver", "create_goal"]),
        sorted(["submit_for_arb_review", "update_application_status", "run_inference_engine", "merge_capabilities"]),
    ),
    "arb_member": (
        "Governance pre-brief — give the board a fast, evidence-based pre-brief.",
        ["application", "capability", "solution", "arb_item", "archimate_element", "adr", "risk"],
        [],
        ALL_WRITE_TOOLS,
    ),
    "portfolio_manager": (
        "TIME rationalization lead — keep the application portfolio moving to target.",
        ["application", "capability", "solution"],
        sorted(["score_rationalization", "propose_rationalization", "merge_capabilities", "bulk_update_application_status"]),
        sorted(["create_solution", "create_archimate_element", "submit_for_arb_review", "create_adr", "create_programme"]),
    ),
    "cto": (
        "Executive briefing — the CTO/CIO view, verdict-first.",
        ["application", "capability", "solution", "arb_item", "risk", "programme"],
        [],
        ALL_WRITE_TOOLS,
    ),
    "procurement": (
        "Commercial steward — contracts, licences, spend legible and governed.",
        ["vendor", "contract", "licence", "vendor_product"],
        sorted(["create_vendor", "create_contract", "upsert_license", "extract_contract_from_document", "link_vendor_product"]),
        sorted(["create_solution", "submit_for_arb_review", "create_archimate_element", "update_application_status", "create_programme"]),
    ),
    "application_manager": (
        "Application steward — keep owned applications healthy and correctly lifecycled.",
        ["application", "solution"],
        sorted(["update_application_status", "bulk_update_application_status", "score_rationalization"]),
        sorted(["create_solution", "submit_for_arb_review", "create_archimate_element", "merge_capabilities", "create_programme"]),
    ),
    "application_architect": (
        "Application-design steward — keep apps well-designed and correctly bounded.",
        ["application", "archimate_element", "solution"],
        sorted(["create_archimate_element", "run_inference_engine", "create_archimate_relationship", "update_application_status"]),
        sorted(["create_solution", "submit_for_arb_review", "merge_capabilities", "create_programme"]),
    ),
    "integration_architect": (
        "Interface and data-flow steward — governed patterns, traceable flows.",
        ["application", "archimate_element", "integration_pattern", "solution"],
        sorted(["create_archimate_element", "create_archimate_relationship", "run_inference_engine"]),
        sorted(["create_solution", "submit_for_arb_review", "update_application_status", "merge_capabilities", "create_programme"]),
    ),
    "systems_architect": (
        "Infrastructure and resilience steward — DR/BC coverage, deployment models.",
        ["application", "archimate_element", "solution"],
        sorted(["poll_infrastructure", "create_archimate_element", "create_archimate_relationship"]),
        sorted(["create_solution", "submit_for_arb_review", "merge_capabilities", "create_programme"]),
    ),
    "business_analyst": (
        "Requirements and process steward — traceability to capabilities and processes.",
        ["application", "capability", "archimate_element", "requirement", "solution"],
        sorted(["create_requirement", "create_archimate_element", "create_driver", "create_goal", "create_constraint"]),
        sorted(["submit_for_arb_review", "merge_capabilities", "update_application_status", "create_programme"]),
    ),
    "product_analyst": (
        "Product-capability alignment steward — features, roadmap, customer journeys.",
        ["application", "capability", "archimate_element", "solution"],
        [],
        ALL_WRITE_TOOLS,
    ),
    "platform_admin": (
        "Platform operator — user/role provisioning, tenant config, integrations, audit.",
        ["application", "capability", "solution", "user", "organization", "integration"],
        [],
        ALL_WRITE_TOOLS,
    ),
}


@click.command("seed-charters")
@click.option("--organization-id", type=int, default=None, help="Seed only this organisation. Default: all.")
@with_appcontext
def seed_charters(organization_id):
    """Populate agent_charters from ARCHITECT_PERSONAS for every organisation."""
    if organization_id is not None:
        orgs = Organization.query.filter_by(id=organization_id).all()
    else:
        orgs = Organization.query.all()
    if not orgs:
        click.echo("No organisations found to seed.")
        return

    created = 0
    skipped = 0
    for org in orgs:
        for persona in ARCHITECT_PERSONAS:
            # Skip if already exists
            existing = AgentCharter.query.filter_by(
                persona=persona, version=1, organization_id=org.id
            ).first()
            if existing:
                skipped += 1
                continue
            charter_text = CHARTERS.get(persona, "")
            bounds = _CHARTER_BOUNDS.get(persona)
            if bounds is None:
                continue
            purpose, readable, proposable, forbidden = bounds
            charter = AgentCharter(
                organization_id=org.id,
                persona=persona,
                version=1,
                purpose=purpose,
                readable_entities=readable,
                proposable_actions=proposable,
                forbidden_actions=forbidden,
                charter_text=charter_text,
            )
            db.session.add(charter)
            created += 1
    db.session.commit()
    click.echo(f"Seeded {created} charter(s) across {len(orgs)} organisation(s), skipped {skipped}.")


def register_seed_charters(app):
    """Register the seed-charters CLI command."""
    app.cli.add_command(seed_charters)


# Auto-register when imported.
try:
    from flask import current_app

    if current_app:
        current_app.cli.add_command(seed_charters)
except (RuntimeError, Exception):
    # Not in an app context yet — caller will register manually.
    pass