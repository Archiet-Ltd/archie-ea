"""Every setting the AI code reads from the environment reaches the server container.

The server's environment in docker-compose.yml is the only way a value in .env gets inside
the container. A setting the code reads but the file does not pass is silently the code's
default there, which on production meant the wrong provider and "No enabled LLM provider".
"""

import re
from pathlib import Path

import yaml

from app.modules.ai_chat.services.llm_service_impl import LLMService, PRICED_PROVIDERS
from app.modules.ai_chat.services.model_defaults import DEFAULT_MODELS

ROOT = Path(__file__).resolve().parent.parent
AI_CHAT = ROOT / "app" / "modules" / "ai_chat"

_READ = r"(?:os\.(?:environ\.get|getenv)|_env_number)\(\s*"
_LITERAL = re.compile(_READ + r"""["']([A-Z][A-Z0-9_]*)["']""")
_PREFIX = re.compile(_READ + r"""["']([A-Z][A-Z0-9_]*_)["']\s*\+""")
_SUFFIX = re.compile(r"""\.upper\(\)\s*\}?\s*(?:\+\s*)?["']?(_[A-Z][A-Z0-9_]*[A-Z0-9])["')]""")


def _sources():
    return [p.read_text(encoding="utf-8") for p in AI_CHAT.rglob("*.py") if "tests" not in p.parts]


def _providers():
    return sorted(set(LLMService.PROVIDERS) | set(PRICED_PROVIDERS) | set(DEFAULT_MODELS))


def _names_the_code_reads():
    texts = _sources()
    providers = [p.upper() for p in _providers()]
    names = set()
    for text in texts:
        names.update(n for n in _LITERAL.findall(text) if not n.endswith("_"))
        for prefix in _PREFIX.findall(text):
            names.update(prefix + p for p in providers)
        for suffix in re.findall(r"""\{[a-z_]*\.upper\(\)\}(_[A-Z][A-Z_]*[A-Z])\b""", text):
            names.update(p + suffix for p in providers)
        for suffix in re.findall(r"""\.upper\(\)\s*\+\s*["'](_[A-Z][A-Z_]*[A-Z])["']""", text):
            names.update(p + suffix for p in providers)
    return names


# Names the openai and anthropic packages read by themselves. Passing one empty (`${NAME:-}`) makes
# the package use "" as its address or credential, and every call fails. The platform reads its own
# `<PROVIDER>_API_BASE` / `AI_PROVIDER_BASE_URL_<PROVIDER>` instead.
SDK_OWNED_NAMES = (
    "OPENAI_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_API_VERSION",
    "OPENAI_API_TYPE",
    "ANTHROPIC_AUTH_TOKEN",
)


def _backup_key_names():
    """Names built as `<PROVIDER>_API_KEY_{i}` in a loop: found from the code, not listed by hand."""
    if not any(re.search(r"_API_KEY_\{i\}", text) for text in _sources()):
        return set()
    return {"%s_API_KEY_%d" % (p.upper(), i) for p in _providers() for i in range(1, 10)}


def _wanted():
    names = _names_the_code_reads() | _backup_key_names()
    return sorted(
        n for n in names
        if n not in SDK_OWNED_NAMES
        and (n.startswith("LLM_") or n.startswith("AI_PROVIDER") or n.endswith(("_API_KEY", "_API_BASE", "_BASE_URL", "_API_KEY_SECONDARY")) or re.search(r"_API_KEY_[1-9]$", n))
    )


def _server_environment():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["server"]["environment"]
    return env if isinstance(env, dict) else dict(item.split("=", 1) for item in env)


def test_the_scan_sees_the_settings_that_choose_and_limit_the_provider():
    names = _names_the_code_reads()
    for expected in (
        "LLM_PROVIDER_ORDER",
        "AI_PROVIDER_ORDER",
        "LLM_SPEND_CEILING_USD_DEEPSEEK",
        "LLM_REQUEST_CEILING_NVIDIA",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "AZURE_API_KEY",
    ):
        assert expected in names


def test_server_environment_passes_every_llm_setting_the_code_reads():
    passed = _server_environment()
    missing = [name for name in _wanted() if name not in passed]
    assert not missing, "docker-compose.yml server.environment does not pass: " + ", ".join(missing)


def test_each_llm_setting_is_passed_through_from_env_and_never_given_a_value():
    passed = _server_environment()
    for name in _wanted():
        assert passed.get(name) == "${%s:-}" % name, "%s must read ${%s:-}" % (name, name)


def test_no_name_an_sdk_reads_by_itself_reaches_the_container():
    passed = _server_environment()
    present = [name for name in SDK_OWNED_NAMES if name in passed]
    assert not present, "an empty value breaks the SDK; remove from server.environment: " + ", ".join(present)


def test_the_scan_sees_the_backup_keys():
    assert "DEEPSEEK_API_KEY_1" in _backup_key_names()
    assert "OPENAI_API_KEY_9" in _backup_key_names()


def test_a_provider_address_override_is_read_under_the_platform_name(monkeypatch):
    for name in SDK_OWNED_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_PROVIDER_BASE_URL_OPENAI", "https://gateway.example.test/v1")
    assert LLMService.base_url_for("openai") == "https://gateway.example.test/v1"
