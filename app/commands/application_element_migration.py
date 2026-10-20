"""
flask application-element migrate -- make every inventory row an Application (ADR-0014).

Today one element type, ApplicationComponent, stands for both the system an
organisation lists, owns and costs and the parts it is built from. This command
moves the first meaning to the new ``Application`` type:

1. Every inventory row (table application_components) that has a mirror element:
   the element's type becomes ``Application`` and
   ``custom_properties["entelim:migrated_from"] = "ApplicationComponent"``.
2. Every inventory row without a mirror: one is created as an ``Application``
   through the backbone helper, carrying the same marker.
3. ApplicationComponent elements that are not inventory mirrors stay components.
4. Relationships are never changed. Those the new rules flag (an Application
   composing an Application, a component containing an Application, an
   Application specialising a component) are listed per organisation.

It is a command, not a deploy-time migration, so it runs only when the lead runs
it, after the code that reads the new type is deployed. Do not run it before.

--dry-run   print per organisation what would change, write nothing.
--revert    put back exactly the elements carrying the marker: type returns to
            ApplicationComponent and the marker is removed. An element created
            by the migration for a row that had none stays, as a component,
            linked to its row (the state current code gives that row).
Both flags are idempotent: a second apply or revert changes nothing.

Every organisation is processed inside ``tenant_scope`` and every query also
carries an explicit organisation predicate (ADR 0013).

Usage:
    flask --app manage application-element migrate --dry-run
    flask --app manage application-element migrate --org-ids 1,2
    flask --app manage application-element migrate --revert --org-ids 1
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import click
from flask.cli import AppGroup, with_appcontext

from app import db

logger = logging.getLogger(__name__)

APPLICATION = "Application"
COMPONENT = "ApplicationComponent"
MARKER = "entelim:migrated_from"


def _organisation_ids(organization_ids: Optional[List[int]], revert: bool) -> List[int]:
    """The organisations to process: the given ones, or every one with something to do."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    if organization_ids:
        return sorted(set(organization_ids))
    ids = {
        row[0]
        for row in db.session.query(ApplicationComponent.organization_id).distinct().all()
        if row[0] is not None
    }
    if revert:
        ids |= {
            row[0]
            for row in db.session.query(ArchiMateElement.organization_id)
            .filter(ArchiMateElement.type == APPLICATION)
            .distinct()
            .all()
            if row[0] is not None
        }
    return sorted(ids)


def _with_marker(props: Optional[dict]) -> dict:
    new = dict(props or {})
    new[MARKER] = COMPONENT
    return new


def _without_marker(props: Optional[dict]) -> dict:
    new = dict(props or {})
    new.pop(MARKER, None)
    return new


def _attention(org_id: int, application_ids: set) -> tuple:
    """Relationships the narrowings now flag, and the demotion candidates.

    ``application_ids`` is every element that is (or will be) an Application in the
    organisation. Only pairs where both ends are application-kind and at least one end is
    an Application are examined; a pair is flagged when the type pair now forbids a
    relationship the parent pair allowed.
    """
    from app.config.archimate_relationship_matrix import is_valid_relationship
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    kind = {}
    for el_id, el_type in (
        db.session.query(ArchiMateElement.id, ArchiMateElement.type)
        .filter(ArchiMateElement.organization_id == org_id)
        .filter(ArchiMateElement.type.in_([APPLICATION, COMPONENT]))
        .all()
    ):
        kind[el_id] = APPLICATION if el_id in application_ids else el_type

    flagged: List[Dict] = []
    candidates = set()
    rels = (
        db.session.query(ArchiMateRelationship)
        .filter(ArchiMateRelationship.organization_id == org_id)
        .filter(
            ArchiMateRelationship.source_id.in_(kind.keys()),
            ArchiMateRelationship.target_id.in_(kind.keys()),
        )
        .order_by(ArchiMateRelationship.id)
        .all()
        if kind
        else []
    )
    for rel in rels:
        src, tgt = kind[rel.source_id], kind[rel.target_id]
        if APPLICATION not in (src, tgt):
            continue
        rtype = (rel.type or "").lower()
        if is_valid_relationship(src, tgt, rtype):
            continue
        if not is_valid_relationship(COMPONENT, COMPONENT, rtype):
            continue  # already invalid for components: not caused by this change
        if src == APPLICATION and tgt == APPLICATION and rtype == "composition":
            reason = "An Application may not compose another Application."
            candidates.add(rel.target_id)
        elif src == COMPONENT and tgt == APPLICATION:
            reason = "A component may not %s an Application." % (
                "compose" if rtype == "composition" else rtype
            )
        else:
            reason = "An Application may not %s a component." % rtype
        flagged.append(
            {
                "relationship_id": rel.id,
                "type": rtype,
                "source_id": rel.source_id,
                "target_id": rel.target_id,
                "reason": reason,
            }
        )
    # Application-kind composition parents also make an element a candidate for demotion.
    for rel in rels:
        if (rel.type or "").lower() == "composition" and kind[rel.target_id] == APPLICATION:
            candidates.add(rel.target_id)
    return flagged, sorted(candidates)


def _migrate_organisation(org_id: int, dry_run: bool) -> Dict:
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_backbone import create_backbone_element

    rows = (
        db.session.query(ApplicationComponent)
        .filter(ApplicationComponent.organization_id == org_id)
        .order_by(ApplicationComponent.id)
        .all()
    )
    mirror_ids = [r.archimate_element_id for r in rows if r.archimate_element_id]
    mirrors = {}
    if mirror_ids:
        mirrors = {
            e.id: e
            for e in db.session.query(ArchiMateElement)
            .filter(ArchiMateElement.organization_id == org_id)
            .filter(ArchiMateElement.id.in_(mirror_ids))
            .all()
        }

    changed = 0
    created = 0
    application_ids = {e.id for e in mirrors.values() if e.type == APPLICATION}
    for row in rows:
        element = mirrors.get(row.archimate_element_id) if row.archimate_element_id else None
        if element is None:
            created += 1
            if not dry_run:
                element = create_backbone_element(
                    element_type=APPLICATION,
                    layer="Application",
                    name=row.name,
                    description=None,
                    organization_id=org_id,
                    provenance={MARKER: COMPONENT},
                )
                row.archimate_element_id = element.id
                application_ids.add(element.id)
        elif element.type == COMPONENT:
            changed += 1
            application_ids.add(element.id)
            if not dry_run:
                element.type = APPLICATION
                element.custom_properties = _with_marker(element.custom_properties)
        else:
            application_ids.add(element.id)

    components = (
        db.session.query(ArchiMateElement.id)
        .filter(ArchiMateElement.organization_id == org_id)
        .filter(ArchiMateElement.type == COMPONENT)
        .all()
    )
    left = len({c[0] for c in components} - application_ids)

    flagged, candidates = _attention(org_id, application_ids)
    if not dry_run:
        db.session.commit()
    return {
        "organization_id": org_id,
        "inventory_rows": len(rows),
        "elements_changed": changed,
        "elements_created": created,
        "left_as_components": left,
        "relationships_needing_attention": flagged,
        "demotion_candidates": candidates,
    }


def _revert_organisation(org_id: int, dry_run: bool) -> Dict:
    from app.models.archimate_core import ArchiMateElement

    elements = (
        db.session.query(ArchiMateElement)
        .filter(ArchiMateElement.organization_id == org_id)
        .filter(ArchiMateElement.type == APPLICATION)
        .all()
    )
    marked = [e for e in elements if (e.custom_properties or {}).get(MARKER) == COMPONENT]
    if not dry_run:
        for element in marked:
            element.type = COMPONENT
            element.custom_properties = _without_marker(element.custom_properties)
        db.session.commit()
    return {
        "organization_id": org_id,
        "inventory_rows": None,
        "elements_changed": len(marked),
        "elements_created": 0,
        "left_as_components": None,
        "relationships_needing_attention": [],
        "demotion_candidates": [],
    }


def migrate_application_elements(
    dry_run: bool = False,
    revert: bool = False,
    organization_ids: Optional[List[int]] = None,
) -> Dict:
    """Apply (or with ``revert`` undo) the migration, one organisation at a time."""
    from app.jobs.tenant_safe_job import tenant_scope

    results = []
    for org_id in _organisation_ids(organization_ids, revert):
        with tenant_scope(org_id):
            if revert:
                results.append(_revert_organisation(org_id, dry_run))
            else:
                results.append(_migrate_organisation(org_id, dry_run))
    return {"dry_run": dry_run, "revert": revert, "organisations": results}


application_element_cli = AppGroup(
    "application-element", help="Application element (ADR-0014) maintenance commands."
)


@application_element_cli.command("migrate")
@click.option("--dry-run", is_flag=True, help="Report what would change without writing.")
@click.option("--revert", is_flag=True, help="Undo a previous run: restore the marked elements.")
@click.option("--org-ids", default=None, help="Comma-separated organisation IDs to process.")
@click.option(
    "--all-organisations",
    "all_organisations",
    is_flag=True,
    help="Process every organisation. Required when --org-ids is not given.",
)
@with_appcontext
def migrate_command(dry_run, revert, org_ids, all_organisations):
    """Make every inventory row's mirror element an Application (or revert).

    Name the scope: --org-ids 1,2 or --all-organisations. With neither, nothing runs.
    """
    if bool(org_ids) == bool(all_organisations):
        click.echo(
            "Refusing to run: give exactly one of --org-ids (comma-separated) or "
            "--all-organisations. Nothing was read or written.",
            err=True,
        )
        raise SystemExit(2)
    click.echo(
        "application-element migrate"
        + (" --revert" if revert else "")
        + (" (dry-run)" if dry_run else "")
    )
    parsed = [int(x.strip()) for x in org_ids.split(",")] if org_ids else None
    result = migrate_application_elements(dry_run=dry_run, revert=revert, organization_ids=parsed)
    for org in result["organisations"]:
        click.echo(f"\n  Organisation {org['organization_id']}:")
        for key in (
            "inventory_rows",
            "elements_changed",
            "elements_created",
            "left_as_components",
        ):
            click.echo(f"    {key}: {org[key]}")
        flagged = org["relationships_needing_attention"]
        click.echo(f"    relationships_needing_attention: {len(flagged)}")
        for item in flagged:
            click.echo(
                f"      relationship {item['relationship_id']} ({item['type']}, "
                f"{item['source_id']} -> {item['target_id']}): {item['reason']}"
            )
        if org["demotion_candidates"]:
            click.echo(f"    demotion_candidates: {org['demotion_candidates']}")
    click.echo("\nDone.")


def init_app(app):
    """Register the application-element command group."""
    app.cli.add_command(application_element_cli)
