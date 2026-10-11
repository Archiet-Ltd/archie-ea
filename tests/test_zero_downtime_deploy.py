"""docker-compose.yml must decouple server from the schema-deploy chain.

Before the zero-downtime deploy change, server depended on database-acl
(which transitively depended on schema-deploy), so the server waited for
the full schema chain before starting. After the change, server depends
only on postgres and redis, so the old server keeps serving while the
schema chain runs as a one-off.
"""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.yml"


def _load_compose():
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


def test_server_depends_on_postgres_not_database_acl():
    """Server must depend on postgres (healthy), not on database-acl."""
    compose = _load_compose()
    server = compose["services"]["server"]
    deps = server.get("depends_on", {})

    assert "postgres" in deps, "server must depend on postgres"
    assert "database-acl" not in deps, (
        "server must NOT depend on database-acl — "
        "the schema chain runs as a one-off before server is recreated, "
        "so the old server keeps serving throughout"
    )


def test_server_depends_on_redis():
    """Server must still depend on redis (started)."""
    compose = _load_compose()
    server = compose["services"]["server"]
    deps = server.get("depends_on", {})

    assert "redis" in deps, "server must depend on redis"


def test_worker_depends_on_postgres_not_database_acl():
    """Worker must also be decoupled from database-acl."""
    compose = _load_compose()
    worker = compose["services"]["worker"]
    deps = worker.get("depends_on", {})

    assert "postgres" in deps, "worker must depend on postgres"
    assert "database-acl" not in deps, (
        "worker must NOT depend on database-acl — same zero-downtime decoupling as server"
    )


def test_schema_deploy_chain_still_intact():
    """The schema chain (database-bootstrap -> schema-deploy -> database-acl)
    must still exist so it can be run as a one-off before server recreate."""
    compose = _load_compose()

    schema_deploy = compose["services"]["schema-deploy"]
    schema_deps = schema_deploy.get("depends_on", {})
    assert "database-bootstrap" in schema_deps, (
        "schema-deploy must still depend on database-bootstrap"
    )

    database_acl = compose["services"]["database-acl"]
    acl_deps = database_acl.get("depends_on", {})
    assert "schema-deploy" in acl_deps, (
        "database-acl must still depend on schema-deploy"
    )


def test_deploy_schema_has_test_delay_hook():
    """deploy-schema.sh must have the ZDD_TEST_SCHEMA_DELAY hook so the
    zero-downtime test script can simulate a slow schema step."""
    script = (ROOT / "scripts" / "database" / "deploy-schema.sh").read_text(
        encoding="utf-8"
    )
    assert "ZDD_TEST_SCHEMA_DELAY" in script, (
        "deploy-schema.sh must check ZDD_TEST_SCHEMA_DELAY for test hook"
    )


def test_runbook_exists():
    """The deploy-without-downtime runbook must exist."""
    runbook = ROOT / "docs" / "runbooks" / "deploy-without-downtime.md"
    assert runbook.exists(), "docs/runbooks/deploy-without-downtime.md must exist"
    content = runbook.read_text(encoding="utf-8")
    assert "backward-compatible" in content.lower(), (
        "runbook must document the backward compatibility rule"
    )
    assert "docker compose up --exit-code-from database-acl database-acl" in content, (
        "runbook must document the schema-chain-first command"
    )
    assert "docker compose up -d --force-recreate server" in content, (
        "runbook must document the server-recreate command"
    )


def test_test_script_exists_and_executable():
    """The zero-downtime test script must exist and be executable."""
    script = ROOT / "scripts" / "test_zero_downtime_deploy.sh"
    assert script.exists(), "scripts/test_zero_downtime_deploy.sh must exist"
    assert script.stat().st_mode & 0o111, (
        "test_zero_downtime_deploy.sh must be executable"
    )
    content = script.read_text(encoding="utf-8")
    assert "ZDD_TEST_SCHEMA_DELAY" in content, (
        "test script must pass ZDD_TEST_SCHEMA_DELAY to schema-deploy"
    )
    assert "test_successful_deploy" in content, (
        "test script must have a successful-deploy scenario"
    )
    assert "test_failing_schema_step" in content, (
        "test script must have a failing-schema-step scenario"
    )