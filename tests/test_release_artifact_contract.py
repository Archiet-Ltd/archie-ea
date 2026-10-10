"""Contracts for build-once, digest-addressed production releases."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_SERVICES = {
    "database-bootstrap",
    "schema-deploy",
    "database-acl",
    "server",
    "worker",
}


def test_production_override_uses_one_immutable_image_without_source_mounts():
    source = (ROOT / "deploy" / "docker-compose.production.yml").read_text(
        encoding="utf-8"
    )

    assert source.count("image: ${ARCHIE_IMAGE:?") == len(APP_SERVICES)
    assert source.count("build: !reset null") == len(APP_SERVICES)
    assert source.count("volumes: !reset []") == len(APP_SERVICES)
    assert "./:/app" not in source
    assert "FLASK_CONFIG: production" in source
    assert "SECRET_KEY: ${SECRET_KEY:?SECRET_KEY must be set for production}" in source


def test_release_image_records_the_exact_source_revision():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG VCS_REF" in dockerfile
    assert "org.opencontainers.image.revision=$VCS_REF" in dockerfile
    assert "USER appuser" in dockerfile


def test_ci_builds_once_after_every_release_gate_and_exports_the_digest():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    image_job = workflow.split("\n  release-image:\n", 1)[1]
    for gate in (
        "secret-scan",
        "static-gates",
        "boot-health",
        "tests",
        "db-gates",
        "security-sast",
        "smoke",
        "browser-compatibility",
        "walkthrough",
        "dependency-audit",
    ):
        assert gate in image_job.split("steps:", 1)[0]

    assert image_job.count("docker/build-push-action@") == 1
    assert "push: true" in image_job
    assert "VCS_REF=${{ github.sha }}" in image_job
    assert "steps.build.outputs.digest" in image_job
    assert "release.json" in image_job
    assert "--profile email config --format json" in image_job
    assert 'export SECRET_KEY="release-compose-contract-only"' in image_job
    assert 'assert "build" not in service' in image_job
    assert 'volume.get("target") == "/app"' in image_job
    assert 'service["environment"]["FLASK_CONFIG"] == "production"' in image_job
    assert 'service["environment"]["SECRET_KEY"] == os.environ["SECRET_KEY"]' in image_job
    assert 'inherited_environment[role] in service["environment"]' in image_job
    assert "docker run --rm --entrypoint python" in image_job
    assert """--format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'""" in image_job
    assert '\\"org.opencontainers.image.revision\\"' not in image_job


def test_static_ci_installs_the_browser_required_by_the_js_syntax_gate():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    static_job = workflow.split("\n  static-gates:\n", 1)[1].split(
        "\n  boot-health:\n", 1
    )[0]

    assert "playwright install --with-deps chromium" in static_job


def test_host_deployer_accepts_only_digest_and_full_commit_inputs():
    script = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")

    assert "sha256:[0-9a-f]{64}" in script
    assert "[0-9a-f]{40}" in script
    assert "org.opencontainers.image.revision" in script
    assert "--no-build" in script
    assert "docker compose build" not in script
    assert "git checkout" not in script
    assert "PREVIOUS_IMAGE" in script
    assert "release.env" in script
    assert "logs --since 15m server" in script
    assert "DEPLOY_VERIFIED_SCRIPT" in script
    assert "IMAGE_PIPELINE_TOPOLOGY=1" in script
    assert "deploy_verified.sh" in script
    assert 'data.get("environment") == "production"' in script
    assert "PUBLIC_HEALTH_TIMEOUT" in script
    assert "wait_for_public_health" in script
    assert 'data.get("environment") == "production"' in script.split(
        "wait_for_public_health()", 1
    )[1]
    assert script.index('wait_for_public_health "$PUBLIC_BASE_URL"') < script.index(
        "scripts/post_deploy_verify.py"
    )


def test_operator_cutover_records_legacy_identity_before_checkout():
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    record = script.index("LEGACY_COMMIT=$(git rev-parse HEAD)")
    checkout = script.index('git checkout --detach "$EXPECTED_COMMIT"')
    invoke = script.index("./deploy/remote-cutover.sh")
    assert record < checkout < invoke
