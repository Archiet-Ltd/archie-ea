"""Operator deployment wrapper must preserve immutable artifact identity."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Scripts whose content this file tests.
_DEPLOY_SCHEMA = ROOT / "scripts" / "database" / "deploy-schema.sh"


def test_operator_wrapper_passes_digest_and_full_sha_to_host_deployer():
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "sha256:[0-9a-f]{64}" in script
    assert "[0-9a-f]{40}" in script
    assert "LEGACY_COMMIT=$(git rev-parse HEAD)" in script
    assert './deploy/remote-cutover.sh "$IMAGE_REF" "$EXPECTED_COMMIT" "$LEGACY_COMMIT"' in script
    assert "docker compose" not in script
    assert "git push" not in script
    assert "--no-build" in script


def test_first_immutable_cutover_has_a_verified_legacy_rollback():
    script = (ROOT / "deploy" / "remote-cutover.sh").read_text(encoding="utf-8")

    assert "sha256:[0-9a-f]{64}" in script
    assert "[0-9a-f]{40}" in script
    assert 'git cat-file -e "$LEGACY_COMMIT^{commit}"' in script
    assert 'git checkout --detach "$LEGACY_COMMIT"' in script
    assert "docker compose up -d --no-build --force-recreate server" in script
    assert 'curl -s -o /dev/null -m 10 -w' in script
    assert 'test -f "$RELEASE_FILE"' in script
    assert "verified legacy rollback" in script


def test_deploy_schema_runs_backfill_audit_trail():
    """scripts/database/deploy-schema.sh must invoke backfill-audit-trail.

    RUN-01: the command runs here because CREATE INDEX IF NOT EXISTS
    ix_soc2_audit_org_id requires table ownership, and the schema-deploy
    service connects as the deploy role that owns the tables.
    """
    script = _DEPLOY_SCHEMA.read_text(encoding="utf-8")
    assert 'flask --app manage backfill-audit-trail' in script
    assert 'WARN audit trail backfill skipped' in script
    assert 'RUN-01' in script
