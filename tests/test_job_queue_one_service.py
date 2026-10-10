"""There is one job queue service, and a job belongs to the organisation that queued it."""
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_both_import_paths_resolve_to_one_class_and_one_instance():
    from app.modules.admin.v2.services import job_queue_service_v2 as old
    from app.services import job_queue_service as canonical

    assert old.JobQueueService is canonical.JobQueueService
    assert old.get_job_queue_service is canonical.get_job_queue_service
    assert old.get_job_queue_service() is canonical.get_job_queue_service()


def test_no_caller_still_imports_the_redirected_module():
    importers = []
    for path in (REPO / "app").rglob("*.py"):
        if path.name == "job_queue_service_v2.py":
            continue
        if "job_queue_service_v2" in path.read_text(encoding="utf-8", errors="ignore"):
            importers.append(str(path.relative_to(REPO)))
    assert importers == []


def test_a_job_queued_for_one_organisation_is_not_listed_for_another(app, db_session, make_org):
    from app.services.job_queue_service import get_job_queue_service

    org_a, org_b = make_org("a"), make_org("b")
    service = get_job_queue_service()

    job_a = service.create_job("Scan A", "model_health_scan", organization_id=org_a.id)
    job_b = service.create_job("Scan B", "model_health_scan", organization_id=org_b.id)
    platform = service.create_job("Platform sync", "abacus_sync", {"sync_type": "full"})

    listed_a = {j.id for j in service.list_jobs(org_a.id)}
    listed_b = {j.id for j in service.list_jobs(org_b.id)}

    assert listed_a == {job_a.id}
    assert listed_b == {job_b.id}
    assert platform.id not in listed_a | listed_b
    assert job_a.payload["organization_id"] == org_a.id


def test_create_job_keeps_the_callers_payload(app, db_session, make_org):
    from app.services.job_queue_service import get_job_queue_service

    org = make_org("payload")
    payload = {"sync_type": "full"}
    job = get_job_queue_service().create_job("Sync", "abacus_sync", payload, organization_id=org.id)

    assert job.payload == {"sync_type": "full", "organization_id": org.id}
    assert payload == {"sync_type": "full"}
