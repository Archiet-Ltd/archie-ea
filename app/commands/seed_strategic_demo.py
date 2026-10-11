"""flask seed-strategic-demo — a demonstration data set for T-S1.

No existing seeder creates a value stream: `flask seed-demo-mappings` maps
entities that already exist and is pinned never to create one;
`flask seed-capabilities` creates capability catalogues and creates no value
stream and sets no per-tenant maturity. Without this command, the T-S1
answer is correct on day one but has nothing to show (design § 8, SR-S2) --
the demonstration fixture is T-S1's own acceptance item, not a general
seeding concern.

Writes, for one organisation only:

  * two value streams, each with two stages, through
    ``value_stream_service.create_value_stream`` / ``create_stage``;
  * six ``UnifiedCapability`` rows owned by that organisation, each with
    ``current_maturity_level`` and ``target_maturity_level`` set -- at least
    two below 3 and at least two at 3 or above, so the answer shows both a
    scored at-risk capability and a scored safe one;
  * eight mapping rows across the four stages, through
    ``value_stream_service.upsert_mapping_cell``, with ``support_type``,
    ``support_level`` and ``impact_level`` varied across the eight.

It creates no capability it did not create itself, never touches a row whose
``organization_id`` is null, and never touches another organisation's row.
All names are plainly invented. Elements and initiatives are not part of
this command; T-S3 and T-S4 extend it when they need them.

Idempotent: re-running it creates nothing new and changes nothing.
``--dry-run`` writes nothing at all -- no writer function below is even
called in that mode, so a value-stream/stage/mapping writer's own internal
commit can never fire under ``--dry-run``.

    flask --app manage seed-strategic-demo --org-id 7 --dry-run
    flask --app manage seed-strategic-demo --org-id 7
"""

from __future__ import annotations

import click
from flask.cli import with_appcontext

from app import db

# Two value streams, two stages each. Names and codes are plainly invented,
# in the style the repository's other demonstration fixtures use.
_VALUE_STREAMS = [
    {
        "code": "DEMO-VSR-FULFIL",
        "name": "Demonstration: Order Fulfilment",
        "stages": [
            {"name": "Demo Intake", "order": 1},
            {"name": "Demo Delivery", "order": 2},
        ],
    },
    {
        "code": "DEMO-VSR-SERVICE",
        "name": "Demonstration: Service Request Handling",
        "stages": [
            {"name": "Demo Triage", "order": 1},
            {"name": "Demo Resolution", "order": 2},
        ],
    },
]

# Six capabilities. Three below the default threshold of 3, three at or
# above it, so a seeded answer always shows both a scored at-risk capability
# and a scored safe one (T-S1 acceptance item 15).
_CAPABILITIES = [
    {"code": "DEMO-CAP-ORDER-CAPTURE", "name": "Demonstration: Order Capture", "current": 2, "target": 4},
    {"code": "DEMO-CAP-INVENTORY", "name": "Demonstration: Inventory Tracking", "current": 4, "target": 5},
    {"code": "DEMO-CAP-DELIVERY-SCHED", "name": "Demonstration: Delivery Scheduling", "current": 1, "target": 3},
    {"code": "DEMO-CAP-CASE-TRIAGE", "name": "Demonstration: Case Triage", "current": 3, "target": 4},
    {"code": "DEMO-CAP-ISSUE-RESOLUTION", "name": "Demonstration: Issue Resolution", "current": 5, "target": 5},
    {"code": "DEMO-CAP-KNOWLEDGE-BASE", "name": "Demonstration: Knowledge Base", "current": 2, "target": 3},
]

# Eight mapping rows across the four stages, support fields varied across
# the eight so the payload's curated fields are visibly populated rather
# than uniform.
_MAPPINGS = [
    {
        "value_stream_code": "DEMO-VSR-FULFIL", "stage_name": "Demo Intake",
        "capability_code": "DEMO-CAP-ORDER-CAPTURE",
        "support_type": "primary", "support_level": 5, "impact_level": "critical",
    },
    {
        "value_stream_code": "DEMO-VSR-FULFIL", "stage_name": "Demo Intake",
        "capability_code": "DEMO-CAP-INVENTORY",
        "support_type": "secondary", "support_level": 3, "impact_level": "medium",
    },
    {
        "value_stream_code": "DEMO-VSR-FULFIL", "stage_name": "Demo Delivery",
        "capability_code": "DEMO-CAP-DELIVERY-SCHED",
        "support_type": "primary", "support_level": 4, "impact_level": "high",
    },
    {
        "value_stream_code": "DEMO-VSR-FULFIL", "stage_name": "Demo Delivery",
        "capability_code": "DEMO-CAP-INVENTORY",
        "support_type": "supporting", "support_level": 2, "impact_level": "low",
    },
    {
        "value_stream_code": "DEMO-VSR-SERVICE", "stage_name": "Demo Triage",
        "capability_code": "DEMO-CAP-CASE-TRIAGE",
        "support_type": "primary", "support_level": 5, "impact_level": "high",
    },
    {
        "value_stream_code": "DEMO-VSR-SERVICE", "stage_name": "Demo Triage",
        "capability_code": "DEMO-CAP-KNOWLEDGE-BASE",
        "support_type": "secondary", "support_level": 3, "impact_level": "medium",
    },
    {
        "value_stream_code": "DEMO-VSR-SERVICE", "stage_name": "Demo Resolution",
        "capability_code": "DEMO-CAP-ISSUE-RESOLUTION",
        "support_type": "primary", "support_level": 4, "impact_level": "critical",
    },
    {
        "value_stream_code": "DEMO-VSR-SERVICE", "stage_name": "Demo Resolution",
        "capability_code": "DEMO-CAP-KNOWLEDGE-BASE",
        "support_type": "supporting", "support_level": 1, "impact_level": "low",
    },
]


def seed_strategic_demo(org_id: int, dry_run: bool = False) -> dict:
    """Write (or, under ``--dry-run``, only count) the T-S1 demonstration
    data set for *org_id*. Returns counts: ``value_streams_created``,
    ``stages_created``, ``capabilities_created``, ``mappings_created``,
    ``already_present``.

    Runs inside ``tenant_scope(org_id)`` (constraint: the command's whole
    write surface is one tenant). Every lookup and every write below names
    ``org_id`` explicitly rather than relying only on the ambient tenant
    context, matching this repository's belt-and-braces tenancy pattern.
    """
    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.unified_capability import (
        CapabilityValueStreamMapping,
        UnifiedCapability,
        ValueStream,
        ValueStreamStage,
    )
    from app.modules.capabilities.services import value_stream_service

    stats = {
        "value_streams_created": 0,
        "stages_created": 0,
        "capabilities_created": 0,
        "mappings_created": 0,
        "already_present": 0,
    }

    with tenant_scope(org_id):
        # -- value streams -------------------------------------------------
        vs_by_code = {}
        for vs_spec in _VALUE_STREAMS:
            existing = ValueStream.query.filter_by(
                organization_id=org_id, code=vs_spec["code"]
            ).first()
            if existing is not None:
                vs_by_code[vs_spec["code"]] = existing
                stats["already_present"] += 1
                continue
            if dry_run:
                stats["value_streams_created"] += 1
                continue
            vs = value_stream_service.create_value_stream(
                {
                    "name": vs_spec["name"],
                    "code": vs_spec["code"],
                    "value_stream_type": "customer_facing",
                }
            )
            vs_by_code[vs_spec["code"]] = vs
            stats["value_streams_created"] += 1

        # -- stages ----------------------------------------------------------
        stage_by_key = {}
        for vs_spec in _VALUE_STREAMS:
            vs = vs_by_code.get(vs_spec["code"])
            for stage_spec in vs_spec["stages"]:
                if vs is None:
                    # dry-run: the parent value stream was only counted, not
                    # created, so there is no id to check a stage against.
                    stats["stages_created"] += 1
                    continue
                existing_stage = ValueStreamStage.query.filter_by(
                    value_stream_id=vs.id, name=stage_spec["name"]
                ).first()
                if existing_stage is not None:
                    stage_by_key[(vs_spec["code"], stage_spec["name"])] = existing_stage
                    stats["already_present"] += 1
                    continue
                if dry_run:
                    stats["stages_created"] += 1
                    continue
                stage = value_stream_service.create_stage(
                    vs.id,
                    {"name": stage_spec["name"], "stage_order": stage_spec["order"]},
                )
                stage_by_key[(vs_spec["code"], stage_spec["name"])] = stage
                stats["stages_created"] += 1

        # -- capabilities ------------------------------------------------
        cap_by_code = {}
        for cap_spec in _CAPABILITIES:
            existing_cap = UnifiedCapability.query.filter_by(
                organization_id=org_id, code=cap_spec["code"]
            ).first()
            if existing_cap is not None:
                cap_by_code[cap_spec["code"]] = existing_cap
                stats["already_present"] += 1
                continue
            if dry_run:
                stats["capabilities_created"] += 1
                continue
            cap = UnifiedCapability(
                name=cap_spec["name"],
                code=cap_spec["code"],
                organization_id=org_id,
                scope="tenant",
                level=1,
                current_maturity_level=cap_spec["current"],
                target_maturity_level=cap_spec["target"],
            )
            db.session.add(cap)
            db.session.flush()
            cap_by_code[cap_spec["code"]] = cap
            stats["capabilities_created"] += 1

        # -- mappings ------------------------------------------------------
        for mapping_spec in _MAPPINGS:
            vs = vs_by_code.get(mapping_spec["value_stream_code"])
            stage = stage_by_key.get(
                (mapping_spec["value_stream_code"], mapping_spec["stage_name"])
            )
            cap = cap_by_code.get(mapping_spec["capability_code"])

            if vs is None or stage is None or cap is None:
                # dry-run on a FRESH organisation: nothing above was actually
                # created, so there is nothing concrete to look an existing
                # mapping up against -- counted only. This branch is not
                # reachable outside dry-run, because a real run always
                # resolves all three from the rows it just created above.
                stats["mappings_created"] += 1
                continue

            # Look up the existing row BEFORE branching on dry_run: on an
            # already-seeded organisation, vs/stage/cap above are all
            # resolved from EXISTING rows even under --dry-run, so the
            # lookup below is real and "would create" must not claim work
            # that is already done.
            existing_mapping = CapabilityValueStreamMapping.query.filter_by(
                capability_id=cap.id,
                value_stream_id=vs.id,
                value_stream_stage_id=stage.id,
            ).first()
            if (
                existing_mapping is not None
                and existing_mapping.support_type == mapping_spec["support_type"]
                and existing_mapping.support_level == mapping_spec["support_level"]
                and existing_mapping.impact_level == mapping_spec["impact_level"]
            ):
                # Already present with the same values -- skip the upsert
                # call entirely so a re-run does not even touch updated_at
                # (constraint: re-running changes nothing).
                stats["already_present"] += 1
                continue

            if dry_run:
                # Absent, or present with different values: real work this
                # run would do, reported honestly, but not written.
                stats["mappings_created"] += 1
                continue

            value_stream_service.upsert_mapping_cell(
                cap.id,
                vs.id,
                stage.id,
                {
                    "support_type": mapping_spec["support_type"],
                    "support_level": mapping_spec["support_level"],
                    "impact_level": mapping_spec["impact_level"],
                },
            )
            stats["mappings_created"] += 1

        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()

    return stats


@click.command("seed-strategic-demo")
@click.option(
    "--org-id", type=int, required=True,
    help="Organisation to seed. Mandatory -- this writes sample data.",
)
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@with_appcontext
def seed_strategic_demo_command(org_id, dry_run):
    """Write the T-S1 demonstration data set for one organisation."""
    from app.models.organization import Organization

    org = db.session.get(Organization, org_id)
    if org is None:
        raise click.ClickException(f"No organization with id={org_id}.")

    click.echo(f"  organisation {org_id} ({org.name})")

    stats = seed_strategic_demo(org_id, dry_run=dry_run)

    verb = "would create" if dry_run else "created"
    click.echo(
        f"  {verb} {stats['value_streams_created']} value stream(s), "
        f"{stats['stages_created']} stage(s), "
        f"{stats['capabilities_created']} capability row(s), "
        f"{stats['mappings_created']} mapping row(s); "
        f"{stats['already_present']} already present."
    )


def init_app(app):
    app.cli.add_command(seed_strategic_demo_command)
