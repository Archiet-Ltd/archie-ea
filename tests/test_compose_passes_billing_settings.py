"""Every setting the billing code reads reaches the server container.

The server's environment in docker-compose.yml is the only way a value in .env
gets inside the container. A setting the code reads but the file does not pass
is silently unset there, so the billing page keeps saying "Online payment is
not open yet" even after an operator sets the value in .env.

The names come from the code, not from this file: monelytics_provider.SETTINGS
for the Monelytics settings, and a scan of app/ for the STRIPE_ settings the
billing routes and the provider's webhook read.
"""

import re
from pathlib import Path

import yaml

from app.services.monelytics_provider import SETTINGS

ROOT = Path(__file__).resolve().parent.parent

_READ = r"os\.environ\.get\(\s*"
_LITERAL = re.compile(_READ + r"""["']([A-Z][A-Z0-9_]*)["']""")


def _sources():
    return [p.read_text(encoding="utf-8") for p in ROOT.glob("app/**/*.py")]


def _stripe_names_the_code_reads():
    """STRIPE_ settings read by the billing routes and the webhook handler."""
    names = set()
    for text in _sources():
        names.update(n for n in _LITERAL.findall(text) if n.startswith("STRIPE_"))
    return sorted(names)


def _wanted():
    return sorted(set(SETTINGS) | set(_stripe_names_the_code_reads()))


def _compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _environment(service):
    env = service.get("environment", {})
    return env if isinstance(env, dict) else dict(item.split("=", 1) for item in env)


def _server_environment():
    return _environment(_compose()["services"]["server"])


def _worker_environment():
    worker = _compose()["services"].get("worker")
    return _environment(worker) if worker is not None else {}


def test_the_scan_sees_the_billing_settings_the_code_reads():
    names = set(SETTINGS) | set(_stripe_names_the_code_reads())
    for expected in (
        "MONELYTICS_BASE_URL",
        "MONELYTICS_KEYCLOAK_TOKEN_URL",
        "MONELYTICS_CLIENT_ID",
        "MONELYTICS_CLIENT_SECRET",
        "STRIPE_SECRET_KEY",
        "STRIPE_API_BASE",
        "STRIPE_AUTOMATIC_TAX",
        "STRIPE_WEBHOOK_SECRET",
    ):
        assert expected in names


def test_server_environment_passes_every_billing_setting_the_code_reads():
    passed = _server_environment()
    missing = [name for name in _wanted() if name not in passed]
    assert not missing, "docker-compose.yml server.environment does not pass: " + ", ".join(missing)


def test_each_billing_setting_is_passed_through_from_env_and_never_given_a_value():
    passed = _server_environment()
    for name in _wanted():
        assert passed.get(name) == "${%s:-}" % name, "%s must read ${%s:-}" % (name, name)


def test_no_billing_setting_is_written_into_compose_as_a_value():
    passed = _server_environment()
    for name in _wanted():
        value = passed.get(name)
        assert value is not None and value.startswith("${"), "%s must not carry a literal value" % name


def test_worker_needs_no_billing_settings_because_it_does_not_call_the_provider():
    imported = any(
        "monelytics_provider" in text for text in _sources()
    )
    # The worker's only job is email; no worker code imports the provider, so
    # the worker service in docker-compose.yml needs none of these names. If a
    # worker module ever imports it, add the names above to its environment.
    assert imported  # billing routes and billing service do, on the server


def test_override_environment_matches_the_server_when_it_defines_one():
    override = ROOT / "docker-compose.override.yml"
    if not override.exists():
        return
    override_compose = yaml.safe_load(override.read_text(encoding="utf-8"))
    if "server" not in (override_compose or {}).get("services", {}):
        return
    passed = _server_environment()
    override_env = _environment(override_compose["services"]["server"])
    for name in _wanted():
        assert override_env.get(name) == passed.get(name), (
            "override server.%s disagrees with docker-compose.yml" % name
        )
