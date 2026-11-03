"""A business process's own ArchiMate element has its dated create entry, in its own organisation."""
from app import db


def _create_entries(org_id, element_id):
    return db.session.execute(db.text(
        "SELECT count(*) FROM soc2_audit_log WHERE organization_id = :o AND action = 'create' "
        "AND table_name = 'archimate_elements' AND record_id = :r"), {"o": org_id, "r": element_id}).scalar()


def test_a_new_process_element_is_recorded_in_its_own_organisations_history(app, db_session, make_org):
    from app.models.process_data import BusinessProcess

    first, second = make_org(), make_org()
    made = []
    for org in (first, second):
        process = BusinessProcess(name="Order to cash", organization_id=org.id, level=2, process_type="core",
                                  status="active", bpmn_type="process")
        db_session.add(process)
        db_session.flush()
        made.append((org.id, process.archimate_element_id))

    for org_id, element_id in made:
        assert element_id
        assert _create_entries(org_id, element_id) == 1
    other_org, _ = made[1]
    assert _create_entries(other_org, made[0][1]) == 0
