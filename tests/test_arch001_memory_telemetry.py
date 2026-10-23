"""ARCH-001 (S1): memory telemetry, docker-compose ceiling arithmetic, and the
ARCH-111 vendored font all get a regression test rather than just a comment.

These are static/config checks — no app fixture, no DB — because the thing
being protected is gunicorn.conf.py's hooks (which run inside a gunicorn
worker process we cannot spin up in a unit test) and the repo's own
declared config files.
"""
import importlib.util
import os
import re
import sys
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_gunicorn_conf():
    """Import gunicorn.conf.py as a plain module (it is not a package)."""
    path = os.path.join(ROOT, "gunicorn.conf.py")
    spec = importlib.util.spec_from_file_location("gunicorn_conf_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rss_mb_returns_a_positive_float_for_the_current_process():
    """_rss_mb() must actually measure something, not just not-crash."""
    conf = _load_gunicorn_conf()
    rss = conf._rss_mb()
    assert rss is not None
    assert rss > 0


def test_post_fork_logs_worker_boot_rss():
    """post_fork must emit an info log carrying 'rss_mb' so a boot-time
    measurement exists in the logs — the whole point of ARCH-001 telemetry
    is that a recurrence is diagnosable from logs, not re-inferred from
    dmesg + arithmetic like the 07/17/18 Aug incidents were."""
    conf = _load_gunicorn_conf()
    server = MagicMock()
    worker = MagicMock(pid=12345)
    conf.post_fork(server, worker)
    rss_calls = [
        call for call in server.log.info.call_args_list
        if call.args and "rss_mb" in call.args[0]
    ]
    assert rss_calls, "post_fork did not log worker boot RSS"


def test_pre_request_logs_rss_on_the_last_request_before_recycle():
    """pre_request must log RSS when the worker is about to hit
    max_requests, and must NOT log on an ordinary request far from the
    threshold (no log-spam every request)."""
    conf = _load_gunicorn_conf()

    worker = MagicMock(pid=999, max_requests=750, nr=749)  # last request before recycle
    conf.pre_request(worker, MagicMock())
    recycle_calls = [
        call for call in worker.log.info.call_args_list
        if call.args and "recycle" in call.args[0]
    ]
    assert recycle_calls, "pre_request did not log RSS at the max_requests boundary"

    worker_mid = MagicMock(pid=999, max_requests=750, nr=5)  # nowhere near recycle
    conf.pre_request(worker_mid, MagicMock())
    assert not worker_mid.log.info.called, "pre_request should not log on an ordinary request"


def test_worker_exit_does_not_attempt_to_measure_a_dead_process():
    """worker_exit runs in the MASTER process per gunicorn's own hook
    contract, so it must not call _rss_mb() (which would silently measure
    the master, not the worker that exited) — regression guard for that
    exact mistake."""
    conf = _load_gunicorn_conf()
    server = MagicMock()
    worker = MagicMock(pid=1)
    conf.worker_exit(server, worker)
    for call in server.log.info.call_args_list:
        assert "rss_mb" not in (call.args[0] if call.args else ""), (
            "worker_exit must not log RSS — it runs in the master process, "
            "not the worker, and would measure the wrong process"
        )


def test_docker_compose_server_mem_limit_arithmetic_fits_the_host():
    """The comment above mem_limit claims specific numbers; assert the
    compose file actually declares them and that they sum to <= host RAM,
    so the arithmetic can't drift out of sync with the file again."""
    compose_path = os.path.join(ROOT, "docker-compose.yml")
    text = open(compose_path, encoding="utf-8").read()

    # Anchor on the top-level (2-space indented) service key, not any mention
    # of the name elsewhere (depends_on:, DATABASE_URL host, etc. all contain
    # "postgres:"/"redis:" too).
    limits = {
        name: int(m.group(1))
        for name, m in (
            (n, re.search(rf"\n  {n}:\n.*?mem_limit:\s*(\d+)m", text, re.S))
            for n in ("server", "postgres", "adminer", "redis")
        )
        if m
    }
    assert limits == {"server": 3072, "postgres": 1536, "adminer": 64, "redis": 160}
    assert sum(limits.values()) <= 7942, "sum of container ceilings must not exceed host RAM"

    assert "GUNICORN_WORKERS: \"3\"" in text, "the worker trim from 9ead4e9 must not be reverted"
    assert "GUNICORN_MAX_REQUESTS: \"750\"" in text, "the max_requests trim must not be reverted"
    assert "docker compose exec server flask" in text, (
        "the never-run-flask-CLI-in-prod warning must be present as a comment"
    )


def test_arch111_inter_font_is_vendored_not_loaded_from_a_cdn():
    """ARCH-111: Inter must be vendored locally (files + manifest entries),
    and shadcn_tokens.css must reference it via a relative @font-face src,
    never an absolute https:// URL (that would be an air-gap violation)."""
    vendor_dir = os.path.join(ROOT, "app", "static", "vendor")
    for weight in (400, 500, 600, 700):
        fname = f"inter-{weight}.woff2"
        path = os.path.join(vendor_dir, fname)
        assert os.path.exists(path), f"{fname} is not vendored"
        assert os.path.getsize(path) > 1000, f"{fname} looks truncated"

    manifest = open(os.path.join(vendor_dir, "VENDOR_MANIFEST.txt"), encoding="utf-8").read()
    for weight in (400, 500, 600, 700):
        assert f"inter-{weight}.woff2 |" in manifest

    assert os.path.exists(os.path.join(vendor_dir, "inter-LICENSE.txt"))
    license_text = open(os.path.join(vendor_dir, "inter-LICENSE.txt"), encoding="utf-8").read()
    assert "SIL Open Font License" in license_text

    tokens_css = open(
        os.path.join(ROOT, "app", "static", "css", "shadcn_tokens.css"), encoding="utf-8"
    ).read()
    assert "@font-face" in tokens_css
    assert "font-family: 'Inter'" in tokens_css
    font_face_block = tokens_css[tokens_css.index("@font-face"):]
    assert "https://" not in font_face_block.split("--font-sans")[0], (
        "font must be loaded from a vendored relative path, not a remote URL"
    )
    # System-stack fallback must still be present so a missing/slow font
    # file never blocks text from painting.
    assert "system-ui" in tokens_css
    assert "font-display: swap" in tokens_css


def test_vendor_assets_script_declares_inter_and_verifies_clean():
    """scripts/vendor_assets.py --verify must pass with the new entries —
    run it in-process so this test fails loudly if it ever drifts, rather
    than relying on a human remembering to run it before every deploy."""
    sys.path.insert(0, ROOT)
    try:
        import importlib
        va_path = os.path.join(ROOT, "scripts", "vendor_assets.py")
        spec = importlib.util.spec_from_file_location("vendor_assets_under_test", va_path)
        va = importlib.util.module_from_spec(spec)
        cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            spec.loader.exec_module(va)
            assert all(f"inter-{w}.woff2" in va.ASSETS for w in (400, 500, 600, 700))
            assert va.verify() == 0
        finally:
            os.chdir(cwd)
    finally:
        sys.path.pop(0)
