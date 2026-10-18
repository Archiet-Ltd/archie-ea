"""Release qualification must execute the adversarial tests excluded by smoke."""

from pathlib import Path

import yaml
import ast


def test_ci_executes_adversarial_probes_and_retains_their_results():
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["smoke-adversarial"]["steps"]
    commands = [step.get("run", "") for step in steps]
    adversarial = [command for command in commands
                   if "test_adversarial_probes.py" in command and "-m adversarial" in command]
    assert len(adversarial) == 1
    assert "--junitxml=tests/smoke/_artifacts/chromium-adversarial.xml" in adversarial[0]


def test_adversarial_request_failures_are_not_silently_ignored():
    source = (Path(__file__).resolve().parents[1] / "tests/smoke/test_adversarial_probes.py").read_text(encoding="utf-8")
    silent_handlers = [handler for handler in ast.walk(ast.parse(source))
                       if isinstance(handler, ast.ExceptHandler)
                       and len(handler.body) == 1
                       and isinstance(handler.body[0], ast.Continue)]
    assert not silent_handlers, "Request failures must be retained as failed probes"


def test_identity_changing_routes_are_not_crawled_mid_persona():
    from tests.smoke.test_adversarial_probes import _keeps_probe_identity

    for path in ["/account/logout", "/auth/sign-out", "/admin/impersonate/12", "/admin/switch-organization/2"]:
        assert not _keeps_probe_identity(path)
    assert _keeps_probe_identity("/applications/")
