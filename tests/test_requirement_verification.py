"""A test verifies a requirement: the link, its last result, the coverage lists and the exchange file."""

import os
import uuid

import pytest

pytestmark = pytest.mark.journey

db_required = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set - these tests need PostgreSQL",
)


def _element(db_session, org_id, name, kind):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(name=name, type=kind, layer="motivation" if kind == "Requirement" else "technology",
                           organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _service():
    from app.modules.architecture.services import requirement_verification_service

    return requirement_verification_service


@db_required
def test_requirement_states_follow_the_last_result(app, db_session, make_org, tenant_ctx):
    svc = _service()
    org = make_org("verify-state")
    org_id = org.id
    noun = uuid.uuid4().hex[:6]
    with tenant_ctx(org_id):
        req = _element(db_session, org_id, "Req %s" % noun, "Requirement")
        req_id = req.id
        assert [r["id"] for r in svc.coverage(org_id)["unverified"]] == [req_id]

        first = svc.link_test(req_id, org_id, new_test_name="test_one_%s" % noun)
        second = svc.link_test(req_id, org_id, new_test_name="test_two_%s" % noun)
        assert svc.coverage(org_id)["unverified"] == []
        assert svc.requirement_view(req_id, org_id)["state"] == "not_run"

        svc.record_result(first.id, org_id, "passed", commit="abc1234")
        svc.record_result(second.id, org_id, "passed", commit="abc1234")
        assert svc.requirement_view(req_id, org_id)["state"] == "verified"
        assert svc.coverage(org_id)["failed"] == []

        # Two tests, one failing: the requirement counts as failing, and is not unverified.
        svc.record_result(second.id, org_id, "failed", commit="def5678")
        cover = svc.coverage(org_id)
        assert [r["id"] for r in cover["failed"]] == [req_id]
        assert cover["unverified"] == []
        view = svc.requirement_view(req_id, org_id)
        by_name = {t["name"]: t for t in view["tests"]}
        assert by_name["test_two_%s" % noun]["commit"] == "def5678"
        assert by_name["test_two_%s" % noun]["run_at"]


@db_required
def test_another_organisations_requirements_and_tests_never_appear(app, db_session, make_org, tenant_ctx):
    svc = _service()
    org_a, org_b = make_org("verify-iso-a"), make_org("verify-iso-b")
    a_id, b_id = org_a.id, org_b.id
    with tenant_ctx(a_id):
        req_a = _element(db_session, a_id, "Only A requirement", "Requirement")
        svc.link_test(req_a.id, a_id, new_test_name="Only A test")
        req_a_id = req_a.id
    with tenant_ctx(b_id):
        req_b = _element(db_session, b_id, "Only B requirement", "Requirement")
        svc.link_test(req_b.id, b_id, new_test_name="Only B test")
        names_b = {r["name"] for r in svc.coverage(b_id)["unverified"]}
        assert names_b == set()
        assert svc.coverage(b_id)["total"] == 1
        assert [c["name"] for c in svc.candidate_tests(req_b.id, b_id)] == []
        # B cannot link, read or record against A's records.
        with pytest.raises(svc.VerificationError):
            svc.link_test(req_a_id, b_id, new_test_name="Intruder")
        assert svc.requirement_view(req_a_id, b_id)["tests"] == []
    with tenant_ctx(a_id):
        tests = svc.requirement_view(req_a_id, a_id)["tests"]
        assert [t["name"] for t in tests] == ["Only A test"]
        with pytest.raises(svc.VerificationError):
            svc.record_result(tests[0]["id"], b_id, "failed")


@db_required
def test_a_link_to_a_test_that_is_not_an_artifact_is_refused(app, db_session, make_org, tenant_ctx):
    svc = _service()
    org = make_org("verify-kind")
    org_id = org.id
    with tenant_ctx(org_id):
        req = _element(db_session, org_id, "Req kind", "Requirement")
        other = _element(db_session, org_id, "Not a test", "Goal")
        with pytest.raises(svc.VerificationError):
            svc.link_test(req.id, org_id, test_id=other.id)
        with pytest.raises(svc.VerificationError):
            svc.link_test(other.id, org_id, new_test_name="x")


@db_required
def test_export_and_reimport_keep_the_verifies_link_and_the_result(app, db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateRelationship
    from app.modules.architecture.services.archimate_xml_export_service import export_to_xml
    from app.services.archimate_import_service import ArchiMateImportService

    svc = _service()
    org_a, org_b = make_org("verify-xchg-a"), make_org("verify-xchg-b")
    a_id, b_id = org_a.id, org_b.id
    with tenant_ctx(a_id):
        req = _element(db_session, a_id, "Exchange requirement", "Requirement")
        test = svc.link_test(req.id, a_id, new_test_name="Exchange test")
        svc.record_result(test.id, a_id, "failed", commit="cafe123")
        exported = export_to_xml()
    assert "entelim:specialization" in exported
    assert "verifies" in exported

    with tenant_ctx(b_id):
        ArchiMateImportService().import_xml(exported, strategy="skip_duplicates", organization_id=b_id)
        db_session.commit()
        req_b = next(r for r in svc.coverage(b_id)["failed"] if r["name"] == "Exchange requirement")
        view = svc.requirement_view(req_b["id"], b_id)
        assert view["state"] == "failing"
        (kept,) = view["tests"]
        assert (kept["name"], kept["result"], kept["commit"]) == ("Exchange test", "failed", "cafe123")
        assert kept["run_at"]
        link = db_session.execute(
            db_session.query(ArchiMateRelationship).filter_by(organization_id=b_id).statement
        ).scalars().all()
        assert [r.type for r in link] == ["realization"]
        assert link[0].custom_properties["entelim:specialization"] == "verifies"
