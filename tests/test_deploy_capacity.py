"""Execute the real deploy script; never connect to a Docker daemon."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
if BASH is None and os.name == "nt" and shutil.which("git"):
    BASH = str(Path(shutil.which("git")).resolve().parents[1] / "bin" / "bash.exe")


@pytest.fixture
def run_deploy(tmp_path):
    assert BASH and Path(BASH).is_file(), "deployment tests require Bash"

    def run(*, available="3250585", minimum=None, docker_root="/srv/docker storage",
             info_status="0", df_status="0"):
        verify_stub = tmp_path / "verify-stub.sh"
        verify_stub.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        verify_stub.chmod(0o755)
        env = os.environ.copy()
        env.pop("ARCHIE_DEPLOY_MIN_FREE_MIB", None)
        env.update(
            ARCHIE_REPO=tmp_path.as_posix(),
            ARCHIE_BACKUPS=(tmp_path / "backups").as_posix(),
            ARCHIE_RELEASE_STATE=(tmp_path / "state").as_posix(),
            DEPLOY_VERIFIED_SCRIPT=verify_stub.as_posix(),
            TEST_SCRIPT=(ROOT / "deploy" / "deploy.sh").as_posix(),
            TEST_LOG=(tmp_path / "commands.log").as_posix(),
            TEST_AVAILABLE=available,
            TEST_DOCKER_ROOT=docker_root,
            TEST_INFO_STATUS=info_status,
            TEST_DF_STATUS=df_status,
        )
        if minimum is not None:
            env["ARCHIE_DEPLOY_MIN_FREE_MIB"] = minimum
        # Shell functions intercept commands even on Windows, with no executables
        # or production services involved. Pull deliberately stops at the boundary.
        harness = r'''
docker() {
    printf 'docker %s\n' "$*" >> "$TEST_LOG"
    case "$1" in
        info) printf '%s\n' "$TEST_DOCKER_ROOT"; return "$TEST_INFO_STATUS" ;;
        pull) return 77 ;;
        *) return 98 ;;
    esac
}
df() {
    printf 'df %s\n' "$*" >> "$TEST_LOG"
    [ "${@: -1}" = "$TEST_DOCKER_ROOT" ] || return 96
    printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
    printf '/dev/data 99999999 1 %s 1%% /srv\n' "$TEST_AVAILABLE"
    return "$TEST_DF_STATUS"
}
flock() { return 0; }
source "$TEST_SCRIPT" "ghcr.io/anioko/archie@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
'''
        result = subprocess.run(
            [BASH, "-c", harness], env=env, capture_output=True, text=True,
            timeout=15,
        )
        log = tmp_path / "commands.log"
        return result, log.read_text() if log.exists() else ""

    return run


def test_low_docker_disk_aborts_before_pull_or_backup(run_deploy, tmp_path):
    result, commands = run_deploy()
    assert result.returncode == 1, result.stderr
    assert "insufficient" in result.stderr.lower()
    assert "docker pull" not in commands
    assert "docker compose" not in commands
    assert not list(tmp_path.rglob("*.sql.gz"))
    assert not (tmp_path / "state" / "release.env").exists()


@pytest.mark.parametrize("available", ["20971520", "35651584"])
def test_sufficient_docker_disk_reaches_pull(run_deploy, available):
    result, commands = run_deploy(available=available)
    assert result.returncode == 77, result.stderr
    assert "df -Pk -- /srv/docker storage" in commands
    assert commands.index("docker info") < commands.index("df ") < commands.index("docker pull")


def test_configured_headroom_is_enforced(run_deploy):
    result, commands = run_deploy(available="20971520", minimum="32768")
    assert result.returncode == 1, result.stderr
    assert "insufficient" in result.stderr.lower()
    assert "docker pull" not in commands


@pytest.mark.parametrize("minimum", ["", "0", "-1", "1.5", "abc", "020480", "999999999999999999999"])
def test_invalid_headroom_fails_closed(run_deploy, minimum):
    result, commands = run_deploy(available="35651584", minimum=minimum)
    assert result.returncode == 1, result.stderr
    assert "ARCHIE_DEPLOY_MIN_FREE_MIB" in result.stderr
    assert "docker pull" not in commands


@pytest.mark.parametrize("settings", [
    {"docker_root": ""}, {"docker_root": "relative/path"},
    {"info_status": "1"}, {"df_status": "1"},
    {"available": "unknown"}, {"available": "-1"},
])
def test_unmeasurable_docker_storage_fails_closed(run_deploy, settings):
    result, commands = run_deploy(**settings)
    assert result.returncode == 1, result.stderr
    assert "ABORT:" in result.stderr
    assert "docker pull" not in commands
