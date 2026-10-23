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


def test_list_jobs_narrows_by_task_and_status_and_names_the_callers_own_organisation(
        app, db_session, make_org):
    from app.models.job import JobStatus
    from app.services.job_queue_service import JobQueueService, get_job_queue_service

    org, other = make_org("narrow"), make_org("narrow-other")
    service = get_job_queue_service()
    scan = service.create_job("Scan", "model_health_scan", organization_id=org.id)
    done = service.create_job("Old scan", "model_health_scan", organization_id=org.id)
    service.update_job_status(done.id, JobStatus.COMPLETED.value)
    service.create_job("Sync", "abacus_sync", organization_id=org.id)
    service.create_job("Other scan", "model_health_scan", organization_id=other.id)

    pending = service.list_jobs(
        org.id, task="model_health_scan",
        statuses=[JobStatus.PENDING.value, JobStatus.IN_PROGRESS.value])

    assert [j.id for j in pending] == [scan.id]
    assert "caller's own organisation" in JobQueueService.list_jobs.__doc__


def test_drift_route_asks_the_one_service_not_its_own_payload_loop():
    source = (REPO / "app/modules/genome/routes/drift_routes.py").read_text(encoding="utf-8")
    assert 'payload.get("organization_id")' not in source
    assert "list_jobs(" in source


def test_a_scan_is_enqueued_once_per_organisation_while_one_is_pending(app, db_session, make_org):
    from app.models.job import Job
    from app.modules.genome.routes.drift_routes import _enqueue_model_health_scan_if_not_pending
    from app.services.job_queue_service import get_job_queue_service

    org, other = make_org("drift"), make_org("drift-other")
    other_job = get_job_queue_service().create_job(
        "Scan", "model_health_scan", organization_id=other.id)

    assert _enqueue_model_health_scan_if_not_pending(org.id) is True
    assert _enqueue_model_health_scan_if_not_pending(org.id) is False
    assert _enqueue_model_health_scan_if_not_pending(other.id) is False

    mine = [j for j in Job.query.filter_by(task="model_health_scan").all()
            if (j.payload or {}).get("organization_id") == org.id]
    assert len(mine) == 1 and other_job.id not in {j.id for j in mine}


def test_a_pending_scan_older_than_fifty_newer_jobs_is_still_found(app, db_session, make_org):
    from app.models.job import Job
    from app.modules.genome.routes.drift_routes import _enqueue_model_health_scan_if_not_pending
    from app.services.job_queue_service import get_job_queue_service

    org = make_org("drift-old")
    service = get_job_queue_service()
    service.create_job("Scan", "model_health_scan", organization_id=org.id)
    for n in range(55):
        service.create_job(f"Sync {n}", "abacus_sync", organization_id=org.id)

    assert _enqueue_model_health_scan_if_not_pending(org.id) is False
    assert Job.query.filter_by(task="model_health_scan").count() == 1


def test_an_unreadable_credential_shows_the_plain_reconnect_sentence():
    """A wrong or rotated key reaches the worker as CredentialUnreadable or as the
    raw InvalidToken the key-unwrap raises before build_connector can catch it.
    Both become the one sentence the connect page already shows, never a bare
    'InvalidToken:' and never a key."""
    from cryptography.fernet import InvalidToken

    from app.modules.codegen.services.credential_encryption import CredentialUnreadable
    from app.services.connector_framework import CREDENTIAL_UNREADABLE_MESSAGE
    from app.services.job_queue_service import JobQueueService

    assert JobQueueService._format_job_error(InvalidToken()) == CREDENTIAL_UNREADABLE_MESSAGE
    assert (
        JobQueueService._format_job_error(CredentialUnreadable("org 7 is unreadable"))
        == CREDENTIAL_UNREADABLE_MESSAGE
    )
    # An ordinary failure is still reported with its type and message.
    assert JobQueueService._format_job_error(ValueError("no connector")) == "ValueError: no connector"


def test_a_failed_first_sync_on_an_invalid_token_sets_the_reconnect_sentence(
        app, db_session, make_org, monkeypatch):
    """When the GitHub first-sync job fails because the token cannot be decrypted,
    the job's error is the plain reconnect sentence — what the connect page shows —
    not the raw exception, and no key appears in it."""
    from cryptography.fernet import InvalidToken

    from app.connectors import github as gh
    from app.services.connector_framework import CREDENTIAL_UNREADABLE_MESSAGE
    from app.services.job_queue_service import get_job_queue_service

    org = make_org("invalid-token")
    service = get_job_queue_service()
    service.app = app
    job = service.create_job(
        "GitHub: read repositories", gh.FIRST_SYNC_TASK,
        payload={"connector_id": "github"}, organization_id=org.id)

    # The decrypt path raises the raw InvalidToken that today reaches the worker
    # from master.decrypt during key-unwrap, bypassing build_connector's catch.
    def _raise_invalid_token(org_id, connector_id):
        raise InvalidToken()

    monkeypatch.setattr(gh, "run_first_sync", _raise_invalid_token)

    service._process_job(job)

    refreshed = service.get_job(job.id)
    assert refreshed.status == "failed"
    assert refreshed.error == CREDENTIAL_UNREADABLE_MESSAGE
    assert "InvalidToken" not in (refreshed.error or "")
