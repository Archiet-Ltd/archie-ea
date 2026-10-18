"""flask seed-demo-mappings — connect a demonstration tenant's entities.

A tenant seeded with applications, capabilities and value streams but no
relationships between them renders as three disconnected lists. Every coverage
view is empty, the capability map has nothing to colour, and no rationalisation
story can be told because nothing supports anything.

This command writes those relationships. **The content below is sample data.**
It encodes what these products do in general (a WMS supports warehouse
management), not an assessment of any particular organisation's estate. That is
appropriate for a demonstration tenant whose applications are themselves sample
rows; it is not appropriate for a tenant holding real portfolio data, which is
why `--org-id` is mandatory and never guessed.

Two safety properties, both pinned by tests/test_seed_demo_mappings.py:

  * It stays inside the organisation it is given. `ApplicationCapabilityMapping`
    is a plain `db.Model` with an `organization_id` column but no `TenantMixin`,
    so nothing scopes it automatically, and this runs from the CLI where the
    tenant middleware is a no-op anyway. Every read and write names the org,
    including the unified rows, which carry no organisation column and are
    scoped through the mapped application and the capability's provenance.
  * It never invents an entity. Names are resolved against what is already in
    the tenant; anything unmatched is counted in `skipped` and reported, never
    created.

It also writes the unified mappings, the rows the Investment Map and the
capability screens read. Each resolvable (application, capability) pair gets
the old `ApplicationCapabilityMapping` row (kept for the readers that still use
it) and, where the capability has a unified row, a
`UnifiedApplicationCapabilityMapping` row. A capability with no unified row yet
is projected by the one canonical projection
(`app.models.business_capabilities._project_capability_row`, the function the
capability write listeners call), never by a copy of its SQL here; when no
unified row can be made, the old row is still written and the pair is reported
as not linked in the unified store.

Idempotent; safe to re-run.

    flask --app manage seed-demo-mappings --org-id 7 --dry-run
    flask --app manage seed-demo-mappings --org-id 7
"""

import click
from flask.cli import with_appcontext

from app import db

# application name -> the capabilities that product supports
APP_CAPABILITIES = {
    "SAP S/4HANA": [
        "Financial Accounting", "Cost Controlling", "Management Reporting",
        "Order Management", "Inventory Management", "Purchase Requisitioning",
    ],
    "Salesforce Sales Cloud": [
        "Customer Management", "Customer Data Management", "Customer Segmentation",
        "Quotation & Pricing", "Sales Forecasting", "Channel Partner Management",
    ],
    "Siemens Opcenter MES": [
        "Shop Floor Execution", "Production Scheduling", "Quality Management",
    ],
    "Blue Yonder WMS": [
        "Warehouse Management", "Inventory Management", "Transport & Distribution",
    ],
    "Coupa": [
        "Strategic Sourcing", "Supplier Management", "Contract Management",
        "Purchase Requisitioning",
    ],
    "Workday HCM": [
        "Talent Acquisition", "Learning & Development", "Payroll & Benefits",
    ],
    "UKG Dimensions": ["Workforce Scheduling", "Payroll & Benefits"],
    "ServiceNow ITSM": ["IT Service Management", "Incident Management"],
    "Power BI": ["Data & Analytics", "Management Reporting"],
    "Autodesk Vault": [
        "Product Lifecycle Management", "Formulation & Recipe Management",
    ],
    "Sphera EHS": [
        "Compliance Reporting", "Environmental Monitoring", "Incident Management",
    ],
    "Kofax Capture": ["Financial Accounting", "Purchase Requisitioning"],
    # Deliberately overlaps Blue Yonder WMS. A legacy tool duplicating a
    # strategic platform on the same two capabilities is what makes a
    # rationalisation conversation possible at all.
    "Tadley Despatch Tool": ["Warehouse Management", "Transport & Distribution"],
}

# (value stream, stage) -> the capabilities that stage exercises
STAGE_CAPABILITIES = {
    ("Order to Cash", "Capture Order"): ["Order Management", "Customer Data Management"],
    ("Order to Cash", "Validate Credit & Pricing"): ["Quotation & Pricing", "Treasury & Credit"],
    ("Order to Cash", "Schedule Production"): ["Production Scheduling"],
    ("Order to Cash", "Pick, Pack & Despatch"): ["Warehouse Management", "Transport & Distribution"],
    ("Order to Cash", "Invoice & Collect"): ["Financial Accounting", "Treasury & Credit"],
    ("Procure to Pay", "Identify Need"): ["Purchase Requisitioning"],
    ("Procure to Pay", "Source & Negotiate"): ["Strategic Sourcing", "Supplier Management"],
    ("Procure to Pay", "Raise Purchase Order"): ["Purchase Requisitioning", "Contract Management"],
    ("Procure to Pay", "Receive & Inspect"): ["Inventory Management", "Quality Management"],
    ("Procure to Pay", "Match & Pay"): ["Financial Accounting"],
    ("Plan to Produce", "Demand Forecast"): ["Demand Planning", "Sales Forecasting"],
    ("Plan to Produce", "Master Production Schedule"): ["Production Scheduling"],
    ("Plan to Produce", "Material Planning"): ["Inventory Management"],
    ("Plan to Produce", "Manufacture"): ["Shop Floor Execution"],
    ("Plan to Produce", "Quality Release"): ["Quality Management"],
}


def _applications(org_id):
    from app.models.application_portfolio import ApplicationComponent

    rows = (
        db.session.query(ApplicationComponent)
        .filter(ApplicationComponent.organization_id == org_id)
        .all()
    )
    return {r.name: r for r in rows}


def _capabilities(org_id):
    from app.models.business_capabilities import BusinessCapability

    rows = (
        db.session.query(BusinessCapability)
        .filter(BusinessCapability.organization_id == org_id)
        .all()
    )
    return {r.name: r for r in rows}


def _stages(org_id):
    """(value stream name, stage name) -> stage, scoped through the value stream."""
    from app.models.unified_capability import ValueStream, ValueStreamStage

    rows = (
        db.session.query(ValueStreamStage, ValueStream.name)
        .join(ValueStream, ValueStreamStage.value_stream_id == ValueStream.id)
        .filter(ValueStream.organization_id == org_id)
        .all()
    )
    return {(vs_name, stage.name): stage for stage, vs_name in rows}


def _unified_capabilities_by_source(org_id, capability_ids):
    """business_capability.id -> its UnifiedCapability row, in this organisation.

    Keyed on the provenance the projection writes (`source_table` + `source_id`)
    and restricted to `org_id`, so a same-named capability in another
    organisation is never matched.
    """
    from app.commands.project_capabilities import SOURCE_TABLE
    from app.models.unified_capability import UnifiedCapability

    if not capability_ids:
        return {}
    rows = (
        db.session.query(UnifiedCapability)
        .filter(
            UnifiedCapability.source_table == SOURCE_TABLE,
            UnifiedCapability.source_id.in_([str(i) for i in capability_ids]),
            UnifiedCapability.organization_id == org_id,
        )
        .all()
    )
    return {int(row.source_id): row for row in rows}


def _project_missing_unified_rows(capabilities):
    """Project capabilities that have no unified row, through the one projection.

    `_project_capability_row` is what the BusinessCapability write listeners run:
    it refuses a row whose organisation or code moved since it was projected
    rather than overwrite another organisation's row, and it skips quietly on a
    database without the provenance index. This calls it; it does not repeat it.
    """
    from app.models.business_capabilities import _project_capability_row

    connection = db.session.connection()
    for capability in capabilities:
        _project_capability_row(connection, capability)


def seed_demo_mappings(org_id, dry_run=False):
    """Link a tenant's applications, capabilities and value-stream stages.

    Returns counts: app_capability_created, unified_app_capability_created,
    stage_capability_created, already_present, skipped.
    """
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    # An association db.Table, not a mapped class — writes go through Core.
    from app.models.relationship_tables import value_stream_stage_capabilities

    apps = _applications(org_id)
    caps = _capabilities(org_id)
    stages = _stages(org_id)

    stats = {
        "app_capability_created": 0,
        "unified_app_capability_created": 0,
        "stage_capability_created": 0,
        "already_present": 0,
        "skipped": 0,
    }

    # Resolve each capability to its unified row, projecting the ones that have
    # none through the canonical projection. A capability that still has no
    # unified row afterwards keeps its old mapping row below.
    from app.models.business_capabilities import _provenance_index_available

    unified = _unified_capabilities_by_source(org_id, [c.id for c in caps.values()])
    unprojected = [c for c in caps.values() if c.id not in unified]
    would_project_ids = set()
    if unprojected and _provenance_index_available(db.session.connection()):
        if dry_run:
            would_project_ids = {c.id for c in unprojected}
        else:
            _project_missing_unified_rows(unprojected)
            unified = _unified_capabilities_by_source(
                org_id, [c.id for c in caps.values()]
            )

    # tenant-scoping-ok: scoped via the FK parent BusinessCapability (caps,
    # already filtered to org_id above), not ACM.organization_id -- that
    # column is NULL on every row in production, so a predicate on it would
    # treat every mapping as "not yet present" and duplicate it on re-seed.
    # See e622d36 / rationalization_scoring_service.py.
    _cap_ids = {c.id for c in caps.values()}
    existing_app_caps = {
        (m.application_component_id, m.business_capability_id)
        for m in db.session.query(ApplicationCapabilityMapping)
        .filter(ApplicationCapabilityMapping.business_capability_id.in_(_cap_ids))
        .all()
    }

    app_ids = [a.id for a in apps.values()] or [-1]
    unified_ids = [u.id for u in unified.values()] or [-1]
    # tenant-scoping-ok: the unified mapping table has no organization_id; the
    # read is scoped through both parents -- this organisation's application ids
    # and its unified capability ids, each restricted to org_id above.
    existing_unified = {
        (m.application_component_id, m.unified_capability_id)
        for m in db.session.query(UnifiedApplicationCapabilityMapping)
        .filter(
            UnifiedApplicationCapabilityMapping.application_component_id.in_(app_ids),
            UnifiedApplicationCapabilityMapping.unified_capability_id.in_(unified_ids),
        )
        .all()
    }

    for app_name, capability_names in APP_CAPABILITIES.items():
        app = apps.get(app_name)
        for capability_name in capability_names:
            capability = caps.get(capability_name)
            if app is None or capability is None:
                stats["skipped"] += 1
                continue
            unified_capability = unified.get(capability.id)
            wants_unified = (
                unified_capability is not None or capability.id in would_project_ids
            )
            legacy_present = (app.id, capability.id) in existing_app_caps
            unified_present = unified_capability is not None and (
                app.id,
                unified_capability.id,
            ) in existing_unified
            if legacy_present and (unified_present or not wants_unified):
                stats["already_present"] += 1
                continue
            if not legacy_present:
                if not dry_run:
                    db.session.add(
                        ApplicationCapabilityMapping(
                            organization_id=org_id,
                            application_component_id=app.id,
                            business_capability_id=capability.id,
                            support_level="primary",
                            is_active=True,
                        )
                    )
                stats["app_capability_created"] += 1
                existing_app_caps.add((app.id, capability.id))
            if wants_unified and not unified_present:
                # Defaults match the capability screen's own writer: partial
                # support, 50% coverage, enables.
                if not dry_run and unified_capability is not None:
                    db.session.add(
                        UnifiedApplicationCapabilityMapping(
                            application_component_id=app.id,
                            unified_capability_id=unified_capability.id,
                            support_level="partial",
                            coverage_percentage=50,
                            relationship_type="enables",
                            notes="Sample link added for the demonstration organisation.",
                        )
                    )
                stats["unified_app_capability_created"] += 1
                if unified_capability is not None:
                    existing_unified.add((app.id, unified_capability.id))

    stage_ids = [stage.id for stage in stages.values()] or [-1]
    existing_stage_caps = {
        (row.value_stream_stage_id, row.business_capability_id)
        for row in db.session.execute(
            value_stream_stage_capabilities.select().where(
                value_stream_stage_capabilities.c.value_stream_stage_id.in_(stage_ids)
            )
        )
    }

    for (vs_name, stage_name), capability_names in STAGE_CAPABILITIES.items():
        stage = stages.get((vs_name, stage_name))
        for capability_name in capability_names:
            capability = caps.get(capability_name)
            if stage is None or capability is None:
                stats["skipped"] += 1
                continue
            if (stage.id, capability.id) in existing_stage_caps:
                stats["already_present"] += 1
                continue
            if not dry_run:
                db.session.execute(
                    value_stream_stage_capabilities.insert().values(
                        value_stream_stage_id=stage.id,
                        business_capability_id=capability.id,
                        capability_role="enabling",
                        importance="high",
                    )
                )
            stats["stage_capability_created"] += 1
            existing_stage_caps.add((stage.id, capability.id))

    if dry_run:
        # Discard anything pending, but KEEP the counts: the whole point of a
        # dry run is to report what would change. Zeroing them here made the
        # command print "would link 0" for a tenant it would in fact have linked
        # 46 mappings into — a preview that is worse than no preview, because it
        # reads as "nothing to do".
        db.session.rollback()
    else:
        db.session.commit()
    return stats


@click.command("seed-demo-mappings")
@click.option("--org-id", type=int, required=True,
              help="Organisation to seed. Mandatory — this writes sample data.")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@with_appcontext
def seed_demo_mappings_command(org_id, dry_run):
    """Link a demonstration tenant's applications, capabilities and value streams."""
    from app.models.organization import Organization

    org = db.session.get(Organization, org_id)
    if org is None:
        raise click.ClickException(f"No organization with id={org_id}.")

    click.echo(f"  organisation {org_id} ({org.name})")

    # Run inside a request context carrying the tenant. Writing an
    # ApplicationCapabilityMapping fires the ArchiMate relationship sync, which
    # inserts into `archimate_relationships` — a NOT NULL organization_id whose
    # column default reads `g.current_org_id`. From a bare CLI there is no
    # request context and no g, so with more than one organisation present the
    # default returns None and the whole seed aborts on the constraint.
    # The lookups in seed_demo_mappings still name the organisation explicitly;
    # this context is for the listeners downstream, not a substitute for them.
    from flask import current_app

    from app.jobs.tenant_safe_job import tenant_scope

    with current_app.test_request_context("/"), tenant_scope(org_id):
        stats = seed_demo_mappings(org_id, dry_run=dry_run)
    verb = "would link" if dry_run else "linked"
    click.echo(
        f"  {verb} {stats['app_capability_created']} application-capability, "
        f"{stats['unified_app_capability_created']} unified and "
        f"{stats['stage_capability_created']} stage-capability mapping(s); "
        f"{stats['already_present']} already present; {stats['skipped']} skipped "
        "(name not present in this organisation)."
    )


def init_app(app):
    app.cli.add_command(seed_demo_mappings_command)
