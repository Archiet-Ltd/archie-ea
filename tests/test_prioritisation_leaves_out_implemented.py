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


def test_a_backlog_requirement_with_no_ranked_copy_is_ranked_when_the_board_is_read(db_session, make_org, client, login_as):
    from app.models.models import Requirement
    from app.models.user import User

    ours, theirs = make_org("board-ours"), make_org("board-theirs")
    mine = _backlog(db_session, ours, "Ours written before ranking", "Draft")
    other = _backlog(db_session, theirs, "Theirs written before ranking", "Draft")
    # Put both back to the state of a row written before the ranked copy existed.
    for row in (mine, other):
        ranked_id = row.archimate_requirement_id
        db_session.query(SolutionRequirement).filter(SolutionRequirement.id == row.id).update(
            {"archimate_requirement_id": None}, synchronize_session=False)
        db_session.query(Requirement).filter(Requirement.id == ranked_id).delete(synchronize_session=False)
    db_session.expire_all()
    reader = User(email="board-reader@example.com", first_name="B", last_name="R", organization_id=ours.id,
                  confirmed=True)
    reader.password = "Passw0rd!Passw0rd"
    db_session.add(reader)
    db_session.flush()

    login_as(client, reader)
    titles = {item["title"] for item in client.get("/api/backlog/prioritised").get_json()}
    assert "Ours written before ranking" in titles
    assert "Theirs written before ranking" not in titles
    assert db_session.get(type(other), other.id).archimate_requirement_id is None
