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


def _wanted():
    names = _names_the_code_reads()
    return sorted(n for n in names if n.startswith("LLM_") or n.startswith("AI_PROVIDER") or n.endswith(("_API_KEY", "_API_BASE", "_BASE_URL", "_API_KEY_SECONDARY")))


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
