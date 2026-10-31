"""The release preview launcher: its mail sink comes up and catches mail, and the server binds loopback."""
import os
import socket
import sys
import subprocess
import time
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parent.parent / "scripts" / "ops" / "release_preview.sh"
VENV_PYTHON = sys.executable


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _stop_sink(port):
    """Stop only the sink this test started, found by its own command line."""
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(a.endswith(b"mail_sink.py") for a in cmd) and str(port).encode() in cmd:
            os.kill(int(entry.name), 15)


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHON",)}
    env["HOME"] = str(tmp_path)
    env["RELEASE_PREVIEW_SINK_LOG"] = str(tmp_path / "sink.log")
    env.update(extra)
    return env


def test_check_sink_listens_and_captures_a_password_reset_mail(app, tmp_path):
    from flask_mail import Message

    from app.flask_email import _compose, _transmit

    port = _free_port()
    run = subprocess.run([str(LAUNCHER), "--check-sink", str(port)], env=_env(tmp_path, PYTHON=VENV_PYTHON),
                         capture_output=True, text=True, timeout=60)
    try:
        assert run.returncode == 0, run.stderr
        assert f"listening on 127.0.0.1:{port}" in run.stdout
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass

        class _User:
            def full_name(self):
                return "Preview Reader"

            email = "reader@example.com"

        with app.app_context():
            previous = {k: app.config.get(k) for k in ("MAIL_SERVER", "MAIL_PORT", "MAIL_USE_TLS", "MAIL_USE_SSL", "MAIL_DEFAULT_SENDER")}
            app.config.update(MAIL_SERVER="127.0.0.1", MAIL_PORT=port, MAIL_USE_TLS=False, MAIL_USE_SSL=False,
                               MAIL_DEFAULT_SENDER="Entelim Preview <preview@localhost>")
            state = app.extensions["mail"]
            old = (state.server, state.port, state.use_tls, state.use_ssl, state.suppress)
            state.server, state.port, state.use_tls, state.use_ssl, state.suppress = (
                "127.0.0.1", port, False, False, False)
            try:
                with app.test_request_context(base_url="http://localhost"):
                    msg = _compose(app, "reader@example.com", "Reset your password", "account/email/reset_password",
                                   user=_User(), reset_link="http://localhost/account/reset/abc123", expires_at=None)
                    delivered, error = _transmit(app, msg)
            finally:
                state.server, state.port, state.use_tls, state.use_ssl, state.suppress = old
                app.config.update(previous)
        assert delivered, error
        deadline = time.time() + 5
        text = ""
        while time.time() < deadline and "reset/abc123" not in text:
            time.sleep(0.1)
            text = (tmp_path / "sink.log").read_text()
        assert text.count("MESSAGE FOLLOWS") == 1
        assert "reader@example.com" in text and "reset/abc123" in text
    finally:
        _stop_sink(port)


def test_launcher_exits_non_zero_when_the_sink_does_not_come_up(tmp_path):
    broken = tmp_path / "python"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)
    run = subprocess.run([str(LAUNCHER), "--check-sink", str(_free_port())],
                         env=_env(tmp_path, PYTHON=str(broken)), capture_output=True, text=True, timeout=60)
    assert run.returncode != 0
    assert "did not come up" in run.stderr


@pytest.mark.parametrize("extra,expected", [([], "127.0.0.1:"), (["9.9.9.9"], "9.9.9.9:")])
def test_server_binds_loopback_unless_an_address_is_named(tmp_path, extra, expected):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "gunicorn"
    fake.write_text('#!/bin/sh\necho "bind $2"\n')
    fake.chmod(0o755)
    sink_port = _free_port()
    env = _env(tmp_path, PYTHON=VENV_PYTHON, SECRET_KEY="x", TRANSFORMATION_COMMAND_CAPABILITY_SECRET="y",
               RELEASE_PREVIEW_LOG=str(tmp_path / "server.log"), PATH=f"{bindir}:{os.environ['PATH']}")
    try:
        run = subprocess.run([str(LAUNCHER), "18080", "postgresql://x/y", str(sink_port), *extra],
                             env=env, capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr
        assert f"bind {expected}18080" in (tmp_path / "server.log").read_text()
    finally:
        _stop_sink(sink_port)
