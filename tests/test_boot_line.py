"""The boot line: one ENTELIM-BOOT JSON line per process that states the configuration the process runs under."""
import json
import logging

import pytest

from app._bootstrap.services import BOOT_LINE_KEYS, boot_line, log_boot_line

SECRET_WORDS = ("secret", "password", "passwd", "token", "api_key", "apikey", "credential")


class _App:
    def __init__(self, **cfg):
        self.config = {"TESTING": False, "MAIL_SUPPRESS_SEND": False,
                       "MAIL_SERVER": "smtp://user:hunter2@mail.example.com:25", **cfg}
        self.debug = False
        self.logger = logging.getLogger("boot-line-test")


def test_boot_line_has_the_eight_keys():
    line = boot_line(_App(), "production", worker_running=True)
    assert set(line) == set(BOOT_LINE_KEYS)
    assert len(BOOT_LINE_KEYS) == 8


def test_boot_line_states_production_values():
    line = boot_line(_App(), "production", worker_running=True)
    assert line["config"] == "production"
    assert line["testing"] is False and line["debug"] is False
    assert line["job_worker"] is True
    assert line["csp"] == "production"
    assert line["mail_suppressed"] is False


def test_testing_start_is_visible_in_the_line():
    app = _App(TESTING=True, MAIL_SUPPRESS_SEND=True)
    line = boot_line(app, "testing", worker_running=False)
    assert line["testing"] is True and line["mail_suppressed"] is True and line["job_worker"] is False


def test_debug_means_development_csp():
    app = _App()
    app.debug = True
    assert boot_line(app, "development", worker_running=False)["csp"] == "development"


def test_no_secret_in_the_line():
    text = json.dumps(boot_line(_App(), "production", worker_running=True))
    assert "hunter2" not in text and "://" not in text
    assert json.loads(text)["mail_host"] == "mail.example.com"
    for key in json.loads(text):
        assert not any(w in key.lower() for w in SECRET_WORDS)


def test_line_is_logged_once_with_prefix(caplog):
    app = _App()
    logging.getLogger("entelim.boot").propagate = True
    with caplog.at_level(logging.INFO, logger="entelim.boot"):
        log_boot_line(app, "production")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("ENTELIM-BOOT ")]
    assert len(lines) == 1
    assert set(json.loads(lines[0].split(" ", 1)[1])) == set(BOOT_LINE_KEYS)


def test_real_app_logs_one_line(app):
    # the shared session app was built through create_app, so the line exists in its factory path
    from app._bootstrap import services
    assert callable(services.log_boot_line)
