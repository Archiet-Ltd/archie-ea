"""Acceptance checks for the product name rename to Entelim.

The product is called Entelim everywhere a person reads. Three things pin
that down as regression barriers rather than a one-time sweep:

1. ``APP_NAME`` defaults to "Entelim" in the application config, so every
   page, email and document that reads the name from ``config.APP_NAME``
   renders it correctly on a fresh install.
2. The dotted wordmark ``A.R.C.H.I.E.`` no longer appears anywhere under
   ``app/`` — including comments and generated-code templates.
3. The assistant wordmark ``Archi`` does not appear as a standalone word in
   user-facing templates or static JavaScript; only ``ArchiMate`` (the
   external standard) may appear.

These mirror the repository-wide gates in scripts/verify.py so a regression
fails here first, in a targeted test run.
"""

import re

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"

STANDALONE_ARCHI = re.compile(r"(?<![A-Za-z])Archi(?![A-Za-z])")
STANDALONE_ARCHIE = re.compile(r"(?<![A-Za-z])Archie(?![A-Za-z])")


def test_app_name_defaults_to_entelim():
    import config as config_module

    cls = config_module.Config
    assert cls.APP_NAME == "Entelim"


def test_email_subject_prefix_derives_from_app_name():
    import config as config_module

    cls = config_module.Config
    assert cls.EMAIL_SUBJECT_PREFIX == "[Entelim]"


def test_no_dotted_wordmark_anywhere_in_app():
    offenders = []
    for path in APP_DIR.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "A.R.C.H.I.E." in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"A.R.C.H.I.E. still present in: {offenders}"


def test_no_standalone_archi_word_in_templates_or_static_js():
    suffixes = {".html", ".js"}
    offenders = []
    for path in APP_DIR.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if STANDALONE_ARCHI.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"'Archi' standalone still present in: {offenders}"


def test_no_archie_dot_ai_email_in_billing_page():
    billing = APP_DIR / "templates" / "admin" / "billing.html"
    text = billing.read_text(encoding="utf-8")
    assert "sales@archie.ai" not in text


def test_no_at_archie_in_slack_integration_page():
    slack = APP_DIR / "templates" / "admin" / "integrations_slack.html"
    text = slack.read_text(encoding="utf-8")
    assert "@archie" not in text


def test_no_at_archie_in_admin_index_page():
    index_page = APP_DIR / "templates" / "admin" / "index.html"
    text = index_page.read_text(encoding="utf-8")
    assert "@archie" not in text


def test_no_archie_trace_in_codegen_workbench():
    wb = APP_DIR / "templates" / "codegen" / "_wb_ide.html"
    text = wb.read_text(encoding="utf-8")
    assert "ARCHIE_TRACE" not in text


def test_account_flash_welcome_uses_app_name():
    """The registration flash message reads APP_NAME from config, not a hardcoded string."""
    account_routes = APP_DIR / "modules" / "account" / "routes" / "account_routes.py"
    text = account_routes.read_text(encoding="utf-8")
    assert "current_app.config['APP_NAME']" in text
    assert '"Welcome to Entelim!"' not in text


def test_account_v2_flash_welcome_uses_app_name():
    account_routes = APP_DIR / "modules" / "account" / "v2" / "routes" / "account_routes.py"
    text = account_routes.read_text(encoding="utf-8")
    assert "current_app.config['APP_NAME']" in text
    assert '"Welcome to Entelim!"' not in text


def test_onboarding_descriptions_use_app_name():
    dashboard_views = APP_DIR / "modules" / "dashboard" / "v2" / "routes" / "dashboard_views.py"
    text = dashboard_views.read_text(encoding="utf-8")
    assert "current_app.config['APP_NAME']" in text
    assert '"Bring your application portfolio into Entelim."' not in text
    assert "\"Bring in the colleagues who'll use Entelim with you.\"" not in text


def test_download_filenames_use_entelim_prefix():
    """User-visible download filenames must not carry the old archie prefix."""
    ai_chat_js = APP_DIR / "static" / "js" / "ai_chat" / "app.js"
    text = ai_chat_js.read_text(encoding="utf-8")
    assert "archie-chat-" not in text
    assert "entelim-chat-" in text

    workflows_html = APP_DIR / "templates" / "ea_workflows" / "instance_detail.html"
    text = workflows_html.read_text(encoding="utf-8")
    assert "archie-review-" not in text
    assert "entelim-review-" in text
# ---------------------------------------------------------------------------
# Root-level documents visible on GitHub
# ---------------------------------------------------------------------------

_ROOT_DOCS = {
    "ARCHITECT_QUICK_START.md": ROOT / "ARCHITECT_QUICK_START.md",
    "DESIGN.md": ROOT / "DESIGN.md",
    "CITATION.cff": ROOT / "CITATION.cff",
    "llms.txt": ROOT / "llms.txt",
    "package.json": ROOT / "package.json",
    "CONTRIBUTING.md": ROOT / "CONTRIBUTING.md",
    "COMMERCIAL-LICENSE.md": ROOT / "COMMERCIAL-LICENSE.md",
    "CLAUDE.md": ROOT / "CLAUDE.md",
}


def test_no_dotted_wordmark_in_root_docs():
    offenders = []
    for name, path in _ROOT_DOCS.items():
        text = path.read_text(encoding="utf-8")
        if "A.R.C.H.I.E." in text:
            offenders.append(name)
    assert offenders == [], f"A.R.C.H.I.E. still present in root docs: {offenders}"


def test_no_archie_word_in_root_docs():
    """Root docs must not contain the old product name 'Archie' (with 'e').

    'Archi' without the trailing 'e' is the external Archi desktop tool
    (archimatetool.com) and is a legitimate reference, not the old brand.
    'Archiet' is a separate product (spec-driven code generation) and is
    also legitimate.
    """
    offenders = []
    for name, path in _ROOT_DOCS.items():
        text = path.read_text(encoding="utf-8")
        if STANDALONE_ARCHIE.search(text):
            offenders.append(name)
    assert offenders == [], f"'Archie' still present in root docs: {offenders}"


def test_demo_script_no_old_brand():
    path = ROOT / "scripts" / "demo" / "record_demo.py"
    text = path.read_text(encoding="utf-8")
    assert "A.R.C.H.I.E." not in text
    assert "ARCHIE" not in text


def test_web_search_user_agent_no_old_brand():
    path = APP_DIR / "services" / "web_search_service.py"
    text = path.read_text(encoding="utf-8")
    assert "ARCHIE-EA-Platform" not in text
    assert "Entelim-Platform" in text


def test_gunicorn_conf_no_old_brand():
    path = ROOT / "gunicorn.conf.py"
    text = path.read_text(encoding="utf-8")
    assert "A.R.C.H.I.E." not in text


def test_start_server_bat_no_old_brand():
    path = ROOT / "start-server.bat"
    text = path.read_text(encoding="utf-8")
    assert "A.R.C.H.I.E." not in text

# ---------------------------------------------------------------------------
# docs/ directory files modified in the rename
# ---------------------------------------------------------------------------

_DOCS_FILES = [
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "application-rationalization.md",
    ROOT / "docs" / "archimate-3-2-cheat-sheet.md",
    ROOT / "docs" / "architecture-review-board.md",
    ROOT / "docs" / "demo-script.md",
    ROOT / "docs" / "open-source-enterprise-architecture-tools.md",
    ROOT / "docs" / "togaf-adm-with-archimate.md",
]


def test_no_dotted_wordmark_in_docs_dir():
    offenders = []
    for path in _DOCS_FILES:
        text = path.read_text(encoding="utf-8")
        if "A.R.C.H.I.E." in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"A.R.C.H.I.E. still present in docs/: {offenders}"


def test_no_archie_word_in_docs_dir():
    offenders = []
    for path in _DOCS_FILES:
        text = path.read_text(encoding="utf-8")
        if STANDALONE_ARCHIE.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"'Archie' still present in docs/: {offenders}"


# ---------------------------------------------------------------------------
# Codegen artefacts — publisher prefix and HTTP headers
# ---------------------------------------------------------------------------


def test_power_platform_publisher_prefix_is_ent():
    from types import SimpleNamespace

    from app.modules.codegen.routes._helpers import (
        _generate_power_platform_solution,
    )

    sol = SimpleNamespace(id=1, name="Test Solution", blueprint_version=1)
    files = _generate_power_platform_solution(sol, {}, {})
    manifest = files["pac-manifest.json"]
    assert '"prefix": "ent"' in manifest
    assert '"prefix": "arc"' not in manifest


def test_sap_btp_iflow_headers_use_entelim_prefix():
    from types import SimpleNamespace

    from app.modules.codegen.routes._helpers import (
        _generate_sap_btp_integration,
    )

    sol = SimpleNamespace(id=1, name="Test Solution", blueprint_version=1)
    files = _generate_sap_btp_integration(sol, {}, {})
    iflow = files["iflow/main.iflw"]
    assert "X-Entelim-Solution" in iflow
    assert "X-Entelim-Generated" in iflow
    assert "X-ARCHIE-Solution" not in iflow
    assert "X-ARCHIE-Generated" not in iflow


# ---------------------------------------------------------------------------
# Deploy artefacts
# ---------------------------------------------------------------------------


def test_caddyfile_503_page_no_old_brand():
    path = ROOT / "deploy" / "Caddyfile.proxy"
    text = path.read_text(encoding="utf-8")
    assert "Archie" not in text
