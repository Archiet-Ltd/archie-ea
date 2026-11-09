"""Every record type saves from its Edit page: a blank date is no date, dates open as a date input expects,
and a gap's Edit page applies its architecture state and opens on the rating every screen shows."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _editor(db_session, make_org, client, login_as, label):
    from tests.test_work_package_writer import _user

    org = make_org(label)
    login_as(client, _user(db_session, org, label.replace("-", ""), role="enterprise_architect"))
    return org


def _edit_config(html):
    found = re.search(r"__APP_CONFIG__\.editingElement\s*=\s*(\{.*?\});", html, re.S)
    assert found, "the Edit page carries no record to open the form on"
    return json.loads(found.group(1))


def _record(db_session, org, kind, target):
    from app.models.implementation_migration import Plateau, WorkPackage

    model = WorkPackage if kind == "WorkPackage" else Plateau
    row = model(organization_id=org.id, name="%s %s" % (kind, uuid.uuid4().hex[:6]), description="Old words",
                target_date=target)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.mark.parametrize("kind", ["WorkPackage", "Plateau"])
def test_a_record_with_no_target_date_saves_a_description_only_change(db_session, make_org, client, login_as, kind):
    org = _editor(db_session, make_org, client, login_as, "blank-date-%s" % kind.lower())
    row = _record(db_session, org, kind, None)
    url = "/architecture/implementation/%s/%d/edit" % (kind, row.id)

    reply = client.post(url, json={"name": row.name, "description": "New words", "target_date": ""})
    assert reply.status_code == 200 and reply.get_json()["success"] is True
    db_session.expire_all()
    stored = type(row).query.filter_by(id=row.id).one()
    assert stored.description == "New words" and stored.target_date is None

    reply = client.post(url, data={"name": row.name, "description": "Form words", "target_date": ""})
    assert reply.status_code == 302
    db_session.expire_all()
    stored = type(row).query.filter_by(id=row.id).one()
    assert stored.description == "Form words" and stored.target_date is None


@pytest.mark.parametrize("kind", ["WorkPackage", "Plateau"])
def test_a_target_date_opens_as_a_date_input_expects_and_is_kept(db_session, make_org, client, login_as, kind):
    org = _editor(db_session, make_org, client, login_as, "iso-date-%s" % kind.lower())
    row = _record(db_session, org, kind, date(2030, 3, 5))
    url = "/architecture/implementation/%s/%d/edit" % (kind, row.id)

    opened = _edit_config(client.get(url).get_data(as_text=True))
    assert opened["target_date"] == "2030-03-05"

    assert client.post(url, json={"name": row.name, "description": "Kept", "target_date": opened["target_date"]}).status_code == 200
    db_session.expire_all()
    assert type(row).query.filter_by(id=row.id).one().target_date == date(2030, 3, 5)


def test_the_edit_page_closes_on_escape_like_cancel():
    source = open("app/static/js/archimate_crud/dashboard.js", encoding="utf-8").read()
    assert re.search(r"if \(this\.editPageDeepLink\) \{\s*//[^\n]*\n\s*document\.addEventListener\('keydown'.*?'Escape'.*?cancelForm\(\)", source, re.S)


def _gap_with_element(db_session, org, **fields):
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import Gap

    element = ArchiMateElement(organization_id=org.id, name="Gap element", type="Gap", layer="implementation")
    db_session.add(element)
    db_session.flush()
    gap = Gap(organization_id=org.id, name="Gap %s" % uuid.uuid4().hex[:6], gap_type="coverage",
              archimate_element_id=element.id, **fields)
    db_session.add(gap)
    db_session.flush()
    return gap, element


def test_a_gap_save_applies_the_architecture_state_to_its_linked_element(db_session, make_org, client, login_as):
    from app.models.archimate_core import ArchiMateElement

    org = _editor(db_session, make_org, client, login_as, "gap-state")
    gap, element = _gap_with_element(db_session, org, priority="medium", severity="medium")

    reply = client.post("/architecture/implementation/Gap/%d/edit" % gap.id,
                        json={"name": gap.name, "priority": "medium", "architecture_state": "Target"})
    assert reply.status_code == 200
    db_session.expire_all()
    assert ArchiMateElement.query.filter_by(id=element.id).one().togaf_plateau == "Target"


def test_a_gap_whose_severity_and_priority_disagree_opens_on_its_rating_and_lowers_in_one_save(
    db_session, make_org, client, login_as
):
    from app.models.implementation_migration import Gap

    org = _editor(db_session, make_org, client, login_as, "gap-open")
    gap, _ = _gap_with_element(db_session, org, priority="medium", severity="critical")
    url = "/architecture/implementation/Gap/%d/edit" % gap.id

    assert _edit_config(client.get(url).get_data(as_text=True))["priority"] == "critical"

    assert client.post(url, json={"name": gap.name, "priority": "medium"}).status_code == 200
    db_session.expire_all()
    stored = Gap.query.filter_by(id=gap.id).one()
    assert (stored.priority, stored.severity) == ("medium", "medium")


def test_gap_summary_by_phase_counts_on_postgresql(db_session, make_org):
    from app.services.gap_register_service import gap_summary_by_phase

    org = make_org("summary-phase")
    before = gap_summary_by_phase()
    _gap_with_element(db_session, org, priority="critical", severity="low")
    after = gap_summary_by_phase()

    assert after["implementation"].get("critical", 0) == before["implementation"].get("critical", 0) + 1
    assert after["total"] == before["total"] + 1


def test_gap_id_reads_stay_inside_the_organisation(db_session, make_org, tenant_ctx):
    from app.modules.architecture.services.gap_archimate_service import GapArchiMateService
    from app.modules.architecture.services.gap_resolution_service import GapResolutionService

    mine, theirs = make_org("read-mine"), make_org("read-theirs")
    foreign, _ = _gap_with_element(db_session, theirs, priority="high", severity="high")
    foreign_id = foreign.id
    db_session.expire_all()
    with tenant_ctx(mine.id):
        db_session.expunge_all()
        assert GapArchiMateService().get_work_packages_for_gap(foreign_id) == []
        for read in (GapResolutionService().get_gap_resolution_progress,
                     GapResolutionService().create_work_packages_from_gap):
            with pytest.raises(ValueError):
                read(foreign_id)
