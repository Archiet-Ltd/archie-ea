"""Report, read only, what the chat's record rules change for each organisation.

    flask --app manage chat-records-report [--organization-id N]

Per organisation it lists: members whose chat write ability changes (they may write today through their platform
role, and the permission matrix does not let them create); Goal elements with no goals row; Goal elements holding
a date-like property, with the key names counted rather than assumed; change records still pending for a tool the
review does not settle; and relationships whose two ends belong to different organisations. Nothing is changed.
"""
import re

import click
from flask.cli import with_appcontext

from app import db

_DATE_KEY = re.compile(r"date|deadline|due|target|horizon|by_when", re.I)
_DATE_VALUE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def date_keys_of(properties):
    """The keys of an element's properties that look like a date, by name or by value."""
    found = []
    for key, value in (properties or {}).items():
        if key in ("ai_generated", "source_model"):
            continue
        if _DATE_KEY.search(str(key)) or (isinstance(value, str) and _DATE_VALUE.match(value.strip())):
            found.append(key)
    return found


def _members(org_id):
    from app.models.org_role import OrgRole
    from app.models.user import User

    ids = {u for (u,) in db.session.execute(db.select(User.id).where(User.organization_id == org_id))}
    ids |= {u for (u,) in db.session.execute(db.select(OrgRole.user_id).where(OrgRole.organization_id == org_id))}
    return list(db.session.execute(db.select(User).where(User.id.in_(ids)).order_by(User.id)).scalars()) if ids else []


def organisation_report(org_id):
    """One organisation's findings as plain data."""
    from app.core.auth.permissions import can, role_key
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.motivation import Goal
    from app.models.user import Permission
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

    changing = []
    for user in _members(org_id):
        if user.can(Permission.GENERAL) and not can(user, "create", "motivation_element", org_id):
            changing.append({"user_id": user.id, "email": user.email, "organisation_role": role_key(user, org_id)})

    goals = list(db.session.execute(db.select(ArchiMateElement).where(
        ArchiMateElement.organization_id == org_id, ArchiMateElement.type == "Goal")).scalars())
    with_row = {g for (g,) in db.session.execute(db.select(Goal.archimate_element_id).where(
        Goal.organization_id == org_id, Goal.archimate_element_id.isnot(None)))}
    key_counts = {}
    for element in goals:
        for key in date_keys_of(element.custom_properties):
            key_counts[key] = key_counts.get(key, 0) + 1

    pending = db.session.execute(db.select(AIChatCRUDApproval.id, AIChatCRUDApproval.entity_type).where(
        AIChatCRUDApproval.organization_id == org_id, AIChatCRUDApproval.status == ApprovalStatus.PENDING,
        AIChatCRUDApproval.operation_type == "tool_use")).all()
    unsettled = [{"id": i, "tool": t} for i, t in pending if t not in TOOL_SCHEMA_BY_NAME]

    other = db.aliased(ArchiMateElement)
    crossing = db.session.execute(
        db.select(db.func.count(ArchiMateRelationship.id))
        .join(ArchiMateElement, ArchiMateElement.id == ArchiMateRelationship.source_id)
        .join(other, other.id == ArchiMateRelationship.target_id)
        .where(ArchiMateRelationship.organization_id == org_id, ArchiMateElement.organization_id != other.organization_id)
    ).scalar_one()

    return {
        "organization_id": org_id,
        "members_whose_chat_write_ability_changes": changing,
        "goal_elements": len(goals),
        "goal_elements_with_no_goals_row": sum(1 for e in goals if e.id not in with_row),
        "goal_elements_with_a_date_like_property": sum(1 for e in goals if date_keys_of(e.custom_properties)),
        "date_like_property_keys": key_counts,
        "pending_records_for_tools_the_review_does_not_settle": unsettled,
        "relationships_with_ends_in_different_organisations": crossing,
    }


@click.command("chat-records-report")
@click.option("--organization-id", type=int, default=None, help="Report one organisation only.")
@with_appcontext
def chat_records_report(organization_id):
    """Read only: what the chat's record rules change, per organisation."""
    from app.models.organization import Organization

    query = db.select(Organization.id, Organization.name).order_by(Organization.id)
    if organization_id is not None:
        query = query.where(Organization.id == organization_id)
    for org_id, name in db.session.execute(query).all():
        db.session.expunge_all()  # one organisation at a time: no identity-map hit from the previous one
        report = organisation_report(org_id)
        click.echo("Organisation %d (%s)" % (org_id, name))
        changing = report["members_whose_chat_write_ability_changes"]
        click.echo("  members whose chat write ability changes: %d" % len(changing))
        for member in changing:
            click.echo("    - user %d, organisation role %s" % (member["user_id"], member["organisation_role"]))
        click.echo("  Goal elements: %d, with no goals row: %d, with a date-like property: %d" % (
            report["goal_elements"], report["goal_elements_with_no_goals_row"],
            report["goal_elements_with_a_date_like_property"]))
        for key, count in sorted(report["date_like_property_keys"].items()):
            click.echo("    - property %r on %d goal(s)" % (key, count))
        unsettled = report["pending_records_for_tools_the_review_does_not_settle"]
        click.echo("  pending records for tools the review does not settle: %d" % len(unsettled))
        for item in unsettled:
            click.echo("    - record %d (%s)" % (item["id"], item["tool"]))
        click.echo("  relationships with ends in different organisations: %d"
                   % report["relationships_with_ends_in_different_organisations"])


def init_app(app):
    app.cli.add_command(chat_records_report)
