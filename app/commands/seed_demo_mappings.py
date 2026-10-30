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
    tenant middleware is a no-op anyway. Every read and write names the org.
  * It never invents an entity. Names are resolved against what is already in
    the tenant; anything unmatched is counted in `skipped` and reported, never
    created.

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
    # The Lantern Quay demonstration tenant's own value stream (seeded by
    # `flask seed-demo-company`, _VALUE_STREAM in seed_demo_company.py) links
    # each of these same stages to a capability already, but through
    # UnifiedCapability via CapabilityValueStreamMapping -- a different table
    # from the one vendor discovery's value-streams tab resolves through
    # (value_stream_stage_capabilities). These four lines project the same,
    # already-decided stage/capability pairing into that table, by the real
    # business-capability name each stage already carries, rather than
    # inventing a second assignment.
    ("Calibration Request to Certificate", "Receive Request"): ["Order Fulfilment"],
    ("Calibration Request to Certificate", "Schedule Engineer"): [
        "Field Service Management", "Workforce Planning",
    ],
    ("Calibration Request to Certificate", "Calibrate and Test"): [
        "Sensor Calibration", "Quality Assurance",
    ],
    ("Calibration Request to Certificate", "Certify and Report"): [
        "Regulatory Compliance", "Water Quality Monitoring",
    ],
}

# Vendor products and the (real, already-seeded) demo capabilities each
# covers, so vendor discovery (app/application_mgmt/vendor_analysis_routes.py,
# apqc_process_api_endpoints.py) finds matches on a freshly seeded demo tenant
# instead of demoing empty. Coverage figures are illustrative placeholders for
# a demonstration tenant, same spirit as APP_CAPABILITIES/STAGE_CAPABILITIES
# above. Vendor organisations and products are shared reference data (no
# `organization_id` -- see VendorOrganization's own docstring), so these are
# created once, globally, the first time any tenant is seeded; only the
# capability-coverage assessment below is written per organisation.
#
# (vendor org name, vendor org type, product name, product family,
#  [(capability name, coverage %, maturity level 1-5, fit score 0-100), ...],
#  [APQC process_code, ...])
VENDOR_PRODUCTS = [
    (
        "SAP SE", "software_vendor", "SAP S/4HANA Cloud", "ERP",
        [
            ("Inventory Control", 85, 4, 82),
            ("Order Fulfilment", 80, 4, 78),
            ("Supplier Management", 75, 4, 74),
        ],
        ["3.1", "7.1"],
    ),
    (
        "SAP SE", "software_vendor", "SAP Ariba", "PROCUREMENT",
        [
            ("Supplier Management", 88, 4, 85),
            ("Regulatory Compliance", 70, 3, 68),
        ],
        ["3.2"],
    ),
    (
        "ServiceNow", "software_vendor", "ServiceNow Field Service Management", "FSM",
        [
            ("Field Service Management", 90, 5, 88),
            ("Workforce Planning", 80, 4, 78),
            ("Incident Response", 82, 4, 80),
        ],
        ["6.7.1"],
    ),
    (
        "Trimble Inc.", "software_vendor", "Trimble Field Service Platform", "FSM",
        [
            ("Sensor Calibration", 84, 4, 82),
            ("Asset Lifecycle Management", 79, 4, 77),
        ],
        ["8.3"],
    ),
    (
        "Siemens AG", "software_vendor", "Siemens Opcenter Quality", "QMS",
        [
            ("Quality Assurance", 86, 4, 84),
            ("Regulatory Compliance", 81, 4, 79),
        ],
        ["3.3"],
    ),
    (
        "Microsoft Corporation", "software_vendor", "Microsoft Power Platform", "ANALYTICS",
        [
            ("Data Analytics", 77, 3, 75),
            ("Demand Forecasting", 70, 3, 68),
        ],
        [],
    ),
]


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


def _seed_vendor_products(org_id, caps, dry_run=False):
    """Vendor organisations, products, capability-coverage and APQC-process
    coverage for VENDOR_PRODUCTS.

    Vendor organisations and products are shared reference data (no
    `organization_id`), so they are created once, globally, by whichever
    tenant seeds first, and left alone (matched by name) on every later run
    or tenant. The capability-coverage rows (VendorProductCapability) ARE
    tenant-scoped and are written for every org this command runs against,
    against that org's own capability ids. APQC-process coverage
    (VendorProductAPQCMapping) is global like the vendor/product rows it
    hangs off, and is written once.

    Returns counts: vendor_product_created, vendor_capability_created,
    vendor_apqc_mapping_created.
    """
    from app.models.apqc_process import APQCProcess
    from app.models.vendor.vendor_organization import (
        VendorOrganization, VendorProduct, VendorProductCapability,
    )
    from app.models.vendor_product_apqc_mapping import VendorProductAPQCMapping

    stats = {
        "vendor_product_created": 0,
        "vendor_capability_created": 0,
        "vendor_apqc_mapping_created": 0,
    }

    orgs_by_name = {o.name: o for o in db.session.query(VendorOrganization)}
    products_by_key = {
        (p.vendor_organization_id, p.name): p for p in db.session.query(VendorProduct)
    }
    existing_coverage = {
        (row.vendor_product_id, row.business_capability_id)
        for row in db.session.query(VendorProductCapability).filter(
            VendorProductCapability.business_capability_id.in_(
                [c.id for c in caps.values()] or [-1]
            )
        )
    }
    apqc_id_by_code = {row.process_code: row.id for row in db.session.query(APQCProcess)}
    existing_apqc_mappings = {
        (row.vendor_product_id, row.apqc_process_id)
        for row in db.session.query(VendorProductAPQCMapping)
    }

    for org_name, org_type, product_name, family, coverage, apqc_codes in VENDOR_PRODUCTS:
        # `org`/`product` stay None on a dry run against data that does not
        # exist yet -- deliberately: nothing below can already exist for a
        # product that was never created, so every coverage/APQC row for it
        # counts as new, the same "report what would change" guarantee
        # seed_demo_mappings gives for app/stage capabilities.
        org = orgs_by_name.get(org_name)
        if org is None and not dry_run:
            org = VendorOrganization(
                name=org_name, display_name=org_name, vendor_type=org_type,
            )
            db.session.add(org)
            db.session.flush()
            orgs_by_name[org_name] = org

        product = products_by_key.get((org.id, product_name)) if org is not None else None
        if product is None:
            stats["vendor_product_created"] += 1
            if not dry_run:
                product = VendorProduct(
                    vendor_organization_id=org.id, name=product_name,
                    product_family_name=family,
                )
                db.session.add(product)
                db.session.flush()
                products_by_key[(org.id, product_name)] = product

        for capability_name, coverage_pct, maturity_level, fit_score in coverage:
            capability = caps.get(capability_name)
            if capability is None:
                continue
            key = (product.id, capability.id) if product is not None else None
            if key is not None and key in existing_coverage:
                continue
            stats["vendor_capability_created"] += 1
            if not dry_run:
                db.session.add(
                    VendorProductCapability(
                        organization_id=org_id,
                        vendor_product_id=product.id,
                        business_capability_id=capability.id,
                        coverage_percentage=coverage_pct,
                        maturity_level=maturity_level,
                        fit_score=fit_score,
                    )
                )
                existing_coverage.add((product.id, capability.id))

        for process_code in apqc_codes:
            apqc_process_id = apqc_id_by_code.get(process_code)
            if apqc_process_id is None:
                continue
            key = (product.id, apqc_process_id) if product is not None else None
            if key is not None and key in existing_apqc_mappings:
                continue
            stats["vendor_apqc_mapping_created"] += 1
            if not dry_run:
                db.session.add(
                    VendorProductAPQCMapping(
                        vendor_product_id=product.id,
                        apqc_process_id=apqc_process_id,
                        coverage_percentage=coverage[0][1] if coverage else 50,
                    )
                )
                existing_apqc_mappings.add((product.id, apqc_process_id))

    return stats


def seed_demo_mappings(org_id, dry_run=False):
    """Link a tenant's applications, capabilities, value-stream stages and
    vendor-product capability coverage.

    Returns counts: app_capability_created, stage_capability_created,
    vendor_product_created, vendor_capability_created,
    vendor_apqc_mapping_created, already_present, skipped.
    """
    from app.models.application_capability import ApplicationCapabilityMapping
    # An association db.Table, not a mapped class — writes go through Core.
    from app.models.relationship_tables import value_stream_stage_capabilities

    apps = _applications(org_id)
    caps = _capabilities(org_id)
    stages = _stages(org_id)

    stats = {
        "app_capability_created": 0,
        "stage_capability_created": 0,
        "vendor_product_created": 0,
        "vendor_capability_created": 0,
        "vendor_apqc_mapping_created": 0,
        "already_present": 0,
        "skipped": 0,
    }

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

    for app_name, capability_names in APP_CAPABILITIES.items():
        app = apps.get(app_name)
        for capability_name in capability_names:
            capability = caps.get(capability_name)
            if app is None or capability is None:
                stats["skipped"] += 1
                continue
            if (app.id, capability.id) in existing_app_caps:
                stats["already_present"] += 1
                continue
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

    vendor_stats = _seed_vendor_products(org_id, caps, dry_run=dry_run)
    stats["vendor_product_created"] += vendor_stats["vendor_product_created"]
    stats["vendor_capability_created"] += vendor_stats["vendor_capability_created"]
    stats["vendor_apqc_mapping_created"] += vendor_stats["vendor_apqc_mapping_created"]

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
        f"{stats['stage_capability_created']} stage-capability and "
        f"{stats['vendor_capability_created']} vendor-capability mapping(s), "
        f"{stats['vendor_product_created']} vendor product(s) and "
        f"{stats['vendor_apqc_mapping_created']} vendor-process mapping(s); "
        f"{stats['already_present']} already present; {stats['skipped']} skipped "
        "(name not present in this organisation)."
    )


def init_app(app):
    app.cli.add_command(seed_demo_mappings_command)
