"""Candidate diagnostics survive container replacement during failed deploys."""

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
def deployment(tmp_path):
    assert BASH and Path(BASH).is_file(), "deployment tests require Bash"
    state = tmp_path / "state"
    state.mkdir()
    previous = "ARCHIE_IMAGE=ghcr.io/anioko/archie@sha256:" + "c" * 64 + "\nARCHIE_COMMIT=" + "b" * 40 + "\n"
    (state / "release.env").write_text(previous)
    live_log = tmp_path / "container.log"

    def run(*, log_status="0", product_status="0", message="ERROR: candidate-only diagnostic"):
        live_log.write_text(message + "\n")
        verify_stub = tmp_path / "verify-stub.sh"
        verify_stub.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        verify_stub.chmod(0o755)
        env = os.environ.copy()
        env.update(
            ARCHIE_REPO=tmp_path.as_posix(),
            ARCHIE_BACKUPS=(tmp_path / "backups").as_posix(),
            ARCHIE_RELEASE_STATE=state.as_posix(),
            DEPLOY_VERIFIED_SCRIPT=verify_stub.as_posix(),
            ARCHIE_DEPLOY_MIN_FREE_MIB="20480",
            TEST_SCRIPT=(ROOT / "deploy" / "deploy.sh").as_posix(),
            TEST_LOG=(tmp_path / "commands.log").as_posix(),
            TEST_LIVE_LOG=live_log.as_posix(),
            TEST_LOG_STATUS=log_status,
            TEST_PRODUCT_STATUS=product_status,
        )
        harness = r'''
docker() {
    printf 'docker %s\n' "$*" >> "$TEST_LOG"
    case "$1" in
        info) printf '/var/lib/docker\n' ;;
        pull) return 0 ;;
        inspect|image)
            if [[ "$*" = *org.opencontainers.image.revision* ]]; then
                printf 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n'
            else printf 'sha256:fake-image-id\n'; fi ;;
        compose)
            case " $* " in
                *' logs '*) cat "$TEST_LIVE_LOG"; return "$TEST_LOG_STATUS" ;;
                *' ps '*) printf 'candidate-id\n' ;;
                *' exec '*) printf 'database backup\n' ;;
                *' up '*)
                    if [[ "$ARCHIE_IMAGE" = *@sha256:cccc* ]]; then
                        printf 'replacement container has no candidate evidence\n' > "$TEST_LIVE_LOG"
                    fi ;;
                *' config '*) return 0 ;;
                *) return 98 ;;
            esac ;;
        *) return 98 ;;
    esac
}
df() { printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/root 99999999 1 35651584 1%% /\n'; }
flock() { return 0; }
curl() { printf '{"status":"healthy","environment":"production"}\n'; }
python3() {
    if [ "$1" = '-c' ]; then cat > /dev/null; return 0; fi
    return "$TEST_PRODUCT_STATUS"
}
source "$TEST_SCRIPT" "ghcr.io/anioko/archie@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
'''
        result = subprocess.run([BASH, "-c", harness], env=env, capture_output=True, text=True, timeout=30)
        return result, (tmp_path / "commands.log").read_text(), previous

    return run


@pytest.mark.parametrize("settings", [{}, {"product_status": "1"}])
def test_failed_candidate_logs_survive_verified_rollback(deployment, tmp_path, settings):
    result, commands, previous = deployment(**settings)
    assert result.returncode == 1, result.stderr
    assert "verified rollback" in result.stderr
    assert "candidate-only diagnostic" not in result.stdout + result.stderr
    assert "candidate-only diagnostic" not in (tmp_path / "container.log").read_text()
    artifacts = list((tmp_path / "state").glob("candidate-*.log"))
    assert len(artifacts) == 1
    assert "ERROR: candidate-only diagnostic" in artifacts[0].read_text()
    assert artifacts[0].name in result.stderr
    assert commands.count("logs --since 15m server") == 1
    assert (tmp_path / "state" / "release.env").read_text() == previous
    if os.name != "nt":
        assert artifacts[0].stat().st_mode & 0o777 == 0o600


def test_log_retrieval_failure_cannot_publish_release(deployment, tmp_path):
    result, commands, previous = deployment(log_status="1", message="partial diagnostics without matching errors")
    assert result.returncode == 1, result.stderr
    assert "verified rollback" in result.stderr
    assert "could not capture" in result.stderr.lower()
    assert (tmp_path / "state" / "release.env").read_text() == previous
    assert commands.count("logs --since 15m server") == 1
    artifacts = list((tmp_path / "state").glob("candidate-*.log"))
    assert len(artifacts) == 1
    assert artifacts[0].name in result.stderr


def test_clean_captured_logs_allow_release(deployment, tmp_path):
    result, commands, _ = deployment(message="INFO: candidate ready")
    assert result.returncode == 0, result.stderr
    assert "deployed and identity-verified" in result.stdout
    assert "deploy_verified.sh" in result.stdout
    assert commands.count("logs --since 15m server") == 1
    artifacts = list((tmp_path / "state").glob("candidate-*.log"))
    assert len(artifacts) == 1
    assert "INFO: candidate ready" in artifacts[0].read_text()
