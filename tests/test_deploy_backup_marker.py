"""The capability tenancy cutover must see whichever backup the deploy took.

No PostgreSQL required: these exercise the shell-level marker/manifest
plumbing directly (scripts/database/backup_marker_to_manifest.sh,
deploy/write-backup-marker.sh) and scripts/database/deploy-schema.sh itself
with `flask` faked out, so the ~18 real `flask --app manage` steps never run
(see tests/test_schema_migrations.py for why that alone takes 20+ minutes).
The behavioural proof that the cutover, once it has a manifest, actually
clears every unclassified unified_capabilities row lives there instead.

Dump paths used here are synthetic Linux-style strings (the real marker
never names a path on the machine running these tests, only one inside a
Linux container), which also keeps manifest JSON free of the unescaped
backslashes a real Windows path would produce in this printf-built JSON --
a pre-existing property of the manifest format, not something these tests
are responsible for fixing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKUP_MARKER_TO_MANIFEST = ROOT / "scripts" / "database" / "backup_marker_to_manifest.sh"
WRITE_BACKUP_MARKER = ROOT / "deploy" / "write-backup-marker.sh"
# Relative, matching docker-compose.yml's literal `command: sh scripts/database/deploy-schema.sh`.
DEPLOY_SCHEMA_RELATIVE = "scripts/database/deploy-schema.sh"

BASH = shutil.which("bash")
if BASH is None and os.name == "nt" and shutil.which("git"):
    BASH = str(Path(shutil.which("git")).resolve().parents[1] / "bin/bash.exe")

# scripts/database/deploy-schema.sh writes its manifest and reports at these
# fixed paths (not test-isolated -- that is true in production too, but there
# each deploy runs in a fresh one-shot container, so it never collides with a
# prior run the way repeated local/CI test invocations against a shared /tmp
# would). Cleared before every real run of the script in these tests, via the
# same Bash process, so whatever "/tmp" resolves to on this platform is the
# one both the cleanup and the script itself agree on.
_DEPLOY_SCHEMA_TMP_ARTIFACTS = (
    "/tmp/cutover-capability-tenancy-manifest.json",
    "/tmp/cutover-capability-tenancy-report.json",
    "/tmp/project-capabilities-report.json",
)


def _run(*args, cwd=None, env=None, timeout=60):
    assert BASH and Path(BASH).is_file(), "these tests require Bash"
    return subprocess.run(
        [BASH, *[str(a) for a in args]],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
    )


def _run_deploy_schema(env):
    _run("-c", "rm -f " + " ".join(_DEPLOY_SCHEMA_TMP_ARTIFACTS), env=env)
    return _run(DEPLOY_SCHEMA_RELATIVE, cwd=ROOT, env=env)


# ------------------------------------------------ backup_marker_to_manifest.sh


def test_backup_marker_to_manifest_builds_manifest_from_a_real_marker(tmp_path):
    """Exactly the format deploy/archie-backup.sh (and write-backup-marker.sh)
    write: a line ending `file=<dump path>`."""
    marker = tmp_path / "LAST_SUCCESS"
    dump_path = "/var/backups/archie/archie.20261001T090000Z.dump"
    marker.write_text(
        f"2026-10-01T09:00:00Z size=4193280 objects=812 file={dump_path}\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"

    result = _run(BACKUP_MARKER_TO_MANIFEST, marker, manifest)

    assert result.returncode == 0, result.stderr
    assert manifest.is_file(), "manifest was not written"
    assert json.loads(manifest.read_text(encoding="utf-8")) == {"backup_path": dump_path}


def test_backup_marker_to_manifest_uses_the_last_file_line(tmp_path):
    """A marker overwritten mid-read or carrying a stale prior line must not
    name an old dump; only the last file= line counts."""
    marker = tmp_path / "LAST_SUCCESS"
    marker.write_text(
        "2026-09-30T03:00:00Z size=1 objects=1 file=/old/stale.dump\n"
        "2026-10-01T09:00:00Z size=4193280 objects=812 file=/new/fresh.dump\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"

    result = _run(BACKUP_MARKER_TO_MANIFEST, marker, manifest)

    assert result.returncode == 0, result.stderr
    assert json.loads(manifest.read_text(encoding="utf-8")) == {
        "backup_path": "/new/fresh.dump"
    }


def test_backup_marker_to_manifest_skips_when_marker_is_absent(tmp_path):
    marker = tmp_path / "does-not-exist" / "LAST_SUCCESS"
    manifest = tmp_path / "manifest.json"

    result = _run(BACKUP_MARKER_TO_MANIFEST, marker, manifest)

    assert result.returncode == 0, result.stderr
    assert not manifest.exists(), "no marker means no manifest, not an empty one"


def test_backup_marker_to_manifest_skips_when_marker_has_no_file_line(tmp_path):
    marker = tmp_path / "LAST_SUCCESS"
    marker.write_text("not a marker line\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"

    result = _run(BACKUP_MARKER_TO_MANIFEST, marker, manifest)

    assert result.returncode == 0, result.stderr
    assert not manifest.exists()


# --------------------------------------------------------- write-backup-marker.sh


def test_write_backup_marker_round_trips_through_backup_marker_to_manifest(tmp_path):
    """The one writer (write-backup-marker.sh) and the one reader
    (backup_marker_to_manifest.sh) must agree on the marker format without
    either side hand-copying it -- proves reuse, not just two scripts that
    happen to still match today."""
    marker = tmp_path / "LAST_SUCCESS"
    dump_path = "/var/backups/archie/archie.20261001T090000Z.dump"

    written = _run(WRITE_BACKUP_MARKER, marker, "4193280", "812", dump_path)
    assert written.returncode == 0, written.stderr
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8").rstrip("\n").endswith(
        f"size=4193280 objects=812 file={dump_path}"
    )

    manifest = tmp_path / "manifest.json"
    read = _run(BACKUP_MARKER_TO_MANIFEST, marker, manifest)
    assert read.returncode == 0, read.stderr
    assert json.loads(manifest.read_text(encoding="utf-8")) == {"backup_path": dump_path}


def test_archie_backup_sh_reuses_write_backup_marker_not_a_second_format():
    """Regression guard: archie-backup.sh must call the shared writer, not
    inline its own copy of the marker line a second time."""
    script = (ROOT / "deploy" / "archie-backup.sh").read_text(encoding="utf-8")
    assert "write-backup-marker.sh" in script
    assert '"$MARKER"' in script
    # The format string itself must live in exactly one place now.
    assert 'echo "$(ts) size=$SIZE objects=$OBJECTS file=$OUT" > "$MARKER"' not in script


# -------------------------------------------------------- deploy-schema.sh


@pytest.fixture
def fake_flask(tmp_path):
    """A `flask` on PATH that always succeeds, logs every invocation, and
    copies any --backup-manifest file it was given so the test can read its
    content without depending on how /tmp resolves across processes.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "flask-invocations.log"
    manifest_copy = tmp_path / "flask-saw-manifest.json"
    flask_path = bin_dir / "flask"
    flask_path.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$FAKE_FLASK_LOG"\n'
        'prev=""\n'
        'for arg in "$@"; do\n'
        '    if [ "$prev" = "--backup-manifest" ] && [ -f "$arg" ]; then\n'
        '        cp "$arg" "$FAKE_FLASK_MANIFEST_COPY"\n'
        "    fi\n"
        '    prev="$arg"\n'
        "done\n"
        "exit 0\n",
        encoding="utf-8", newline="\n",
    )
    os.chmod(flask_path, 0o755)

    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["FAKE_FLASK_LOG"] = str(log)
    env["FAKE_FLASK_MANIFEST_COPY"] = str(manifest_copy)
    return {"env": env, "log": log, "manifest_copy": manifest_copy}


def _invocations(fake_flask):
    if not fake_flask["log"].exists():
        return []
    return [line for line in fake_flask["log"].read_text(encoding="utf-8").splitlines() if line]


def test_deploy_schema_finds_the_marker_through_the_override_and_builds_the_manifest(
    tmp_path, fake_flask
):
    """End to end through the real script: ARCHIE_BACKUP_MARKER overrides the
    marker path, deploy-schema.sh builds the manifest from it, and calls
    cutover-capability-tenancy --apply with that manifest.
    """
    dump_path = "/var/backups/archie/archie.20261001T090000Z.dump"
    marker = tmp_path / "custom-marker-location" / "LAST_SUCCESS"
    marker.parent.mkdir()
    written = _run(WRITE_BACKUP_MARKER, marker, "4193280", "812", dump_path)
    assert written.returncode == 0, written.stderr

    env = dict(fake_flask["env"])
    env["ARCHIE_BACKUP_MARKER"] = str(marker)

    result = _run_deploy_schema(env)

    assert result.returncode == 0, result.stderr
    assert "cutover-capability-tenancy --apply succeeded" in result.stdout

    invocations = _invocations(fake_flask)
    assert any("cutover-capability-tenancy" in line and "--apply" in line for line in invocations), (
        invocations
    )
    assert fake_flask["manifest_copy"].is_file(), "cutover never received a manifest"
    assert json.loads(fake_flask["manifest_copy"].read_text(encoding="utf-8")) == {
        "backup_path": dump_path
    }


def test_deploy_schema_refuses_the_cutover_when_no_marker_exists(tmp_path, fake_flask):
    """No marker -> no manifest -> the cutover step is never even attempted,
    and the deploy still finishes (non-fatal, same convention as every
    backfill-* step above it)."""
    env = dict(fake_flask["env"])
    env["ARCHIE_BACKUP_MARKER"] = str(tmp_path / "nothing-has-ever-written-here")

    result = _run_deploy_schema(env)

    assert result.returncode == 0, result.stderr
    assert "WARN capability tenancy cutover skipped" in result.stderr
    assert "cutover-capability-tenancy --apply succeeded" not in result.stdout

    invocations = _invocations(fake_flask)
    assert not any("cutover-capability-tenancy" in line for line in invocations), invocations
