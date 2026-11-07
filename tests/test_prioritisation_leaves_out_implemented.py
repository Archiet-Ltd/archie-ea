"""Prioritisation ranks every backlog requirement not yet implemented, and only the caller's own."""
from app.models.solution_architect_models import RequirementType, SolutionRequirement
from app.services import prioritisation_service


def _backlog(db_session, org, name, status):
    row = SolutionRequirement(
        name=name, description=name, status=status, moscow_priority="MUST",
        requirement_type=RequirementType.FUNCTIONAL, organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def test_implemented_requirements_are_left_out_and_other_organisations_do_not_leak(db_session, make_org, tenant_ctx):
    ours, theirs = make_org("ours"), make_org("theirs")
    for status in ("Draft", "Refined", "Approved", "Implemented"):
        _backlog(db_session, ours, "Ours %s" % status, status)
    _backlog(db_session, theirs, "Theirs Implemented", "Implemented")
    _backlog(db_session, theirs, "Theirs Draft", "Draft")

    with tenant_ctx(ours.id):
        titles = {item["title"] for item in prioritisation_service.get_backlog_prioritised(method="moscow")}
    assert titles == {"Ours Draft", "Ours Refined", "Ours Approved"}

    with tenant_ctx(theirs.id):
        titles = {item["title"] for item in prioritisation_service.get_backlog_prioritised(method="moscow")}
    assert titles == {"Theirs Draft"}
