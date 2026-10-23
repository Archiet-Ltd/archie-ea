"""
flask seed-integration-patterns — seed the ARB-approved/conditional/blocked integration
patterns and the reference architectures of the pattern catalogue.

Idempotent: skips patterns that already exist by name. Safe to run multiple times.
Run after deploying to a new environment to populate the integration pattern catalogue.

Usage:
    flask seed-integration-patterns
    flask seed-integration-patterns --dry-run
"""
import click
from flask.cli import with_appcontext

from app import db

# ---------------------------------------------------------------------------
# Pattern definitions — 18 canonical enterprise integration patterns
# ---------------------------------------------------------------------------

INTEGRATION_PATTERNS = [
    # ------------------------------------------------------------------
    # Approved — SAP <-> Microsoft (vendor_key=CROSS_VENDOR)
    # ------------------------------------------------------------------
    {
        "name": "SAP BTP \u2192 Microsoft Teams (notification)",
        "vendor_key": "CROSS_VENDOR",
        "pattern_type": "middleware",
        "middleware": "SAP BTP Integration Suite",
        "source_system_hint": "SAP BTP",
        "target_system_hint": "Microsoft Teams",
        "protocol": "rest",
        "data_format": "json",
        "approval_status": "approved",
        "codegen_target": "sap-btp-integration",
        "description": "Outbound notification from SAP BTP to Microsoft Teams via webhook",
    },
    {
        "name": "SAP S/4 OData \u2192 Power BI (analytics)",
        "vendor_key": "CROSS_VENDOR",
        "pattern_type": "api",
        "middleware": "SAP OData connector",
        "source_system_hint": "SAP S/4HANA",
        "target_system_hint": "Power BI",
        "protocol": "odata",
        "data_format": "json",
        "approval_status": "approved",
        "codegen_target": "azure-logic-app",
    },
    {
        "name": "SAP ECC IDoc \u2192 Azure Service Bus (async event)",
        "vendor_key": "CROSS_VENDOR",
        "pattern_type": "event_driven",
        "middleware": "Azure Service Bus",
        "source_system_hint": "SAP ECC",
        "target_system_hint": "Azure Service Bus",
        "protocol": "idoc",
        "data_format": "xml",
        "approval_status": "approved",
        "codegen_target": "azure-logic-app",
    },
    {
        "name": "SAP BTP Event Mesh \u2192 Azure Event Hub (event bridge)",
        "vendor_key": "CROSS_VENDOR",
        "pattern_type": "event_driven",
        "middleware": "SAP Event Mesh",
        "source_system_hint": "SAP BTP",
        "target_system_hint": "Azure Event Hub",
        "protocol": "event",
        "data_format": "json",
        "approval_status": "approved",
        "codegen_target": "sap-btp-integration",
    },
    {
        "name": "SAP BTP CAP \u2192 D365 (bidirectional data sync)",
        "vendor_key": "CROSS_VENDOR",
        "pattern_type": "middleware",
        "middleware": "SAP BTP Integration Suite",
        "source_system_hint": "SAP BTP CAP",
        "target_system_hint": "Microsoft D365",
        "protocol": "rest",
        "data_format": "json",
        "approval_status": "approved",
        "codegen_target": "sap-btp-integration",
    },
    {
        "name": "SAP SuccessFactors \u2192 M365 (HR data sync)",
        "vendor_key": "CROSS_VENDOR",
        "pattern_type": "middleware",
        "middleware": "SAP BTP Integration Suite",
        "source_system_hint": "SAP SuccessFactors",
        "target_system_hint": "Microsoft 365",
        "protocol": "rest",
        "data_format": "json",
        "approval_status": "approved",
        "codegen_target": "sap-btp-integration",
    },
    {
        "name": "Power Platform \u2192 SAP via BTP (write-back)",
        "vendor_key": "CROSS_VENDOR",
        "pattern_type": "middleware",
        "middleware": "SAP BTP / OData",
        "source_system_hint": "Power Platform",
        "target_system_hint": "SAP S/4HANA",
        "protocol": "odata",
        "data_format": "json",
        "approval_status": "approved",
        "codegen_target": "azure-logic-app",
    },
    {
        "name": "Power Automate \u2192 SAP RFC (business process)",
        "vendor_key": "CROSS_VENDOR",
        "pattern_type": "middleware",
        "middleware": "SAP BTP Principal Propagation",
        "source_system_hint": "Power Automate",
        "target_system_hint": "SAP ECC",
        "protocol": "soap",
        "data_format": "xml",
        "approval_status": "approved",
        "codegen_target": "azure-logic-app",
    },
    # ------------------------------------------------------------------
    # Approved — Generic (vendor_key=GENERIC)
    # ------------------------------------------------------------------
    {
        "name": "REST API integration (synchronous)",
        "vendor_key": "GENERIC",
        "pattern_type": "api",
        "middleware": None,
        "source_system_hint": None,
        "target_system_hint": None,
        "protocol": "rest",
        "data_format": "json",
        "approval_status": "approved",
        "codegen_target": None,
    },
    {
        "name": "Async event via Azure Service Bus",
        "vendor_key": "GENERIC",
        "pattern_type": "event_driven",
        "middleware": "Azure Service Bus",
        "source_system_hint": None,
        "target_system_hint": None,
        "protocol": "event",
        "data_format": "json",
        "approval_status": "approved",
        "codegen_target": "azure-logic-app",
    },
    {
        "name": "Batch file transfer via Azure Blob",
        "vendor_key": "GENERIC",
        "pattern_type": "file",
        "middleware": "Azure Blob Storage",
        "source_system_hint": None,
        "target_system_hint": None,
        "protocol": "file",
        "data_format": "csv",
        "approval_status": "approved",
        "codegen_target": "azure-logic-app",
    },
    {
        "name": "Async event via SAP Event Mesh",
        "vendor_key": "GENERIC",
        "pattern_type": "event_driven",
        "middleware": "SAP Event Mesh",
        "source_system_hint": None,
        "target_system_hint": None,
        "protocol": "event",
        "data_format": "json",
        "approval_status": "approved",
        "codegen_target": "sap-btp-integration",
    },
    {
        "name": "MuleSoft API Gateway (enterprise ESB)",
        "vendor_key": "GENERIC",
        "pattern_type": "middleware",
        "middleware": "MuleSoft Anypoint",
        "source_system_hint": None,
        "target_system_hint": None,
        "protocol": "rest",
        "data_format": "json",
        "approval_status": "approved",
        "codegen_target": None,
    },
    {
        "name": "Azure Logic App workflow orchestration",
        "vendor_key": "GENERIC",
        "pattern_type": "middleware",
        "middleware": "Azure Logic Apps",
        "source_system_hint": None,
        "target_system_hint": None,
        "protocol": "rest",
        "data_format": "json",
        "approval_status": "approved",
        "codegen_target": "azure-logic-app",
    },
    # ------------------------------------------------------------------
    # Conditional (vendor_key=GENERIC)
    # ------------------------------------------------------------------
    {
        "name": "Direct SAP BAPI/RFC call (no middleware)",
        "vendor_key": "GENERIC",
        "pattern_type": "api",
        "middleware": None,
        "source_system_hint": "SAP",
        "target_system_hint": None,
        "protocol": "soap",
        "data_format": "xml",
        "approval_status": "conditional",
        "approval_notes": "Only for brownfield legacy; must document migration plan to BTP",
        "arb_conditions": ["migration_plan_required", "sunset_date_required"],
        "codegen_target": None,
    },
    {
        "name": "Custom middleware / iPaaS (non-standard)",
        "vendor_key": "GENERIC",
        "pattern_type": "middleware",
        "middleware": None,
        "source_system_hint": None,
        "target_system_hint": None,
        "protocol": None,
        "data_format": None,
        "approval_status": "conditional",
        "approval_notes": "Must document vendor, SLA, and exit strategy",
        "arb_conditions": [
            "vendor_documentation_required",
            "sla_required",
            "exit_strategy_required",
        ],
        "codegen_target": None,
    },
    {
        "name": "SAP BTP Open Connectors (third-party)",
        "vendor_key": "GENERIC",
        "pattern_type": "middleware",
        "middleware": "SAP BTP Open Connectors",
        "source_system_hint": None,
        "target_system_hint": None,
        "protocol": None,
        "data_format": None,
        "approval_status": "conditional",
        "approval_notes": "Must confirm connector is on approved connector list",
        "arb_conditions": ["approved_connector_list_check_required"],
        "codegen_target": None,
    },
    # ------------------------------------------------------------------
    # Blocked (vendor_key=GENERIC)
    # ------------------------------------------------------------------
    {
        "name": "Point-to-point file drop (no tracking)",
        "vendor_key": "GENERIC",
        "pattern_type": "file",
        "middleware": None,
        "source_system_hint": None,
        "target_system_hint": None,
        "protocol": None,
        "data_format": None,
        "approval_status": "blocked",
        "approval_notes": (
            "No audit trail, no retry, no error handling "
            "\u2014 forbidden by enterprise integration standards"
        ),
        "codegen_target": None,
    },
    # ------------------------------------------------------------------
    # Reference architectures (whole-solution patterns)
    # ------------------------------------------------------------------
    {
        "name": "Event-driven service reference architecture",
        "vendor_key": "GENERIC",
        "pattern_type": "event_driven",
        "middleware": "Event broker",
        "protocol": "event",
        "data_format": "json",
        "approval_status": "approved",
        "description": (
            "Services publish and consume business events through a managed broker, "
            "with an event store and a dead-letter queue. Suits order and shipment "
            "tracking and other near-real-time flows."
        ),
        "allowed_auth_methods": ["oauth2", "mtls", "sasl_ssl"],
        "requires_encryption": True,
        "allows_personal_data": True,
        "is_reference_architecture": True,
        "fit_context": {
            "data": ["internal", "confidential", "personal"],
            "latency": ["real_time", "near_real_time"],
            "hosting": ["cloud", "hybrid"],
        },
        "components": [
            {"name": "Event Broker", "type": "TechnologyService", "layer": "technology"},
            {"name": "Event Producer", "type": "ApplicationComponent", "layer": "application"},
            {"name": "Event Consumer", "type": "ApplicationComponent", "layer": "application"},
            {"name": "Event Store", "type": "DataObject", "layer": "application"},
            {"name": "Dead-Letter Queue", "type": "TechnologyService", "layer": "technology"},
        ],
        "applies_to_controls": [
            {"name": "Encryption in transit", "description": "Broker connections use TLS."},
            {"name": "Authenticated producers and consumers", "description": "OAuth 2.0, mutual TLS or SASL over TLS."},
            {"name": "No lost events", "description": "Failed deliveries go to the dead-letter queue for replay."},
            {"name": "Event audit trail", "description": "The event store keeps every published event."},
        ],
    },
    {
        "name": "Batch analytics reference architecture",
        "vendor_key": "GENERIC",
        "pattern_type": "batch",
        "middleware": "Scheduled pipeline",
        "protocol": "file",
        "data_format": "csv",
        "approval_status": "approved",
        "description": (
            "Scheduled extraction into a governed analytics store, transformed in "
            "batch and served to reporting. Suits overnight reporting and "
            "reconciliation."
        ),
        "allowed_auth_methods": ["oauth2", "mtls"],
        "requires_encryption": True,
        "allows_personal_data": False,
        "is_reference_architecture": True,
        "fit_context": {
            "data": ["public", "internal", "confidential"],
            "latency": ["batch"],
            "hosting": ["cloud", "on_premise", "hybrid"],
        },
        "components": [
            {"name": "Extraction Job", "type": "ApplicationProcess", "layer": "application"},
            {"name": "Analytics Store", "type": "DataObject", "layer": "application"},
            {"name": "Transformation Pipeline", "type": "ApplicationComponent", "layer": "application"},
            {"name": "Reporting Service", "type": "ApplicationService", "layer": "application"},
            {"name": "Batch Scheduler", "type": "SystemSoftware", "layer": "technology"},
        ],
        "applies_to_controls": [
            {"name": "Encryption at rest and in transit", "description": "Files and the analytics store are encrypted."},
            {"name": "No personal data", "description": "Personal data is excluded or masked before loading."},
            {"name": "Reconciled loads", "description": "Each run records row counts in and out."},
        ],
    },
]

# Columns copied from a spec when present (the rest are named explicitly below).
_OPTIONAL_FIELDS = (
    "allowed_auth_methods",
    "requires_encryption",
    "allows_personal_data",
    "is_reference_architecture",
    "components",
    "applies_to_controls",
    "fit_context",
)


@click.command("seed-integration-patterns")
@click.option("--dry-run", is_flag=True, help="Print what would be inserted without writing.")
@with_appcontext
def seed_integration_patterns(dry_run):
    """Seed the ARB integration patterns and reference architectures (idempotent)."""
    from app.models.integration_pattern import IntegrationPattern

    inserted = 0
    skipped = 0

    for spec in INTEGRATION_PATTERNS:
        existing = IntegrationPattern.query.filter_by(name=spec["name"]).first()
        if existing:
            skipped += 1
            if dry_run:
                click.echo(f"  [SKIP] {spec['name']} (id={existing.id})")
            continue

        if dry_run:
            click.echo(f"  [INSERT] {spec['name']} — status={spec.get('approval_status', 'approved')}")
            inserted += 1
            continue

        pattern = IntegrationPattern(
            name=spec["name"],
            vendor_key=spec["vendor_key"],
            pattern_type=spec["pattern_type"],
            middleware=spec.get("middleware"),
            source_system_hint=spec.get("source_system_hint"),
            target_system_hint=spec.get("target_system_hint"),
            protocol=spec.get("protocol"),
            data_format=spec.get("data_format"),
            approval_status=spec.get("approval_status", "approved"),
            approval_notes=spec.get("approval_notes"),
            arb_conditions=spec.get("arb_conditions"),
            codegen_target=spec.get("codegen_target"),
            description=spec.get("description"),
            documentation_url=spec.get("documentation_url"),
            **{field: spec[field] for field in _OPTIONAL_FIELDS if field in spec},
        )
        db.session.add(pattern)
        inserted += 1

    if not dry_run and inserted > 0:
        db.session.commit()

    click.echo(f"\nDone — inserted: {inserted}, skipped (already exist): {skipped}")
    if dry_run:
        click.echo("(dry run — no changes written)")


def init_app(app):
    """Register seed-integration-patterns CLI command."""
    app.cli.add_command(seed_integration_patterns)
