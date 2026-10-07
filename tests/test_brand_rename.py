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
        # The external Archi desktop tool's file format label is a legitimate
        # reference, not the old product name.
        text = text.replace("Archi (.archimate)", "")
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
    """The registration welcome reads APP_NAME from config, not a hardcoded string.

    Both account tiers register through one shared view in mail_views.py.
    """
    mail_views = APP_DIR / "modules" / "account" / "routes" / "mail_views.py"
    text = mail_views.read_text(encoding="utf-8")
    assert "current_app.config['APP_NAME']" in text
    assert '"Welcome to Entelim!"' not in text


def test_account_v2_flash_welcome_uses_app_name():
    for tier in ("routes", "v2/routes"):
        account_routes = APP_DIR / "modules" / "account" / tier / "account_routes.py"
        text = account_routes.read_text(encoding="utf-8")
        assert "mail_views.register_view()" in text
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


# ---------------------------------------------------------------------------
# GitHub issue templates — user-facing text in the public repository
# ---------------------------------------------------------------------------


def test_github_issue_template_no_old_product_name():
    """The bug report template asks for the product version; it must use the
    current product name, not the old one."""
    path = ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.md"
    text = path.read_text(encoding="utf-8")
    assert STANDALONE_ARCHIE.search(text) is None, (
        "'Archie' still present in .github/ISSUE_TEMPLATE/bug_report.md"
    )


# ---------------------------------------------------------------------------
# Codegen templates — environment variable names in generated output
# ---------------------------------------------------------------------------

_SOLUTIONS_TEMPLATE_DIR = APP_DIR / "modules" / "solutions_product" / "templates"

# Every .j2 file under solutions_product/templates that the review identified
# as carrying ARCHIE_* environment variable names in generated output.
_CODEGEN_TEMPLATES_WITH_ARCHIE_ENV = [
    _SOLUTIONS_TEMPLATE_DIR / "python_fastapi" / "health_reporter.py.j2",
    _SOLUTIONS_TEMPLATE_DIR / "python_fastapi" / "github_actions.yml.j2",
    _SOLUTIONS_TEMPLATE_DIR / "python_fastapi" / "gitlab_ci.yml.j2",
    _SOLUTIONS_TEMPLATE_DIR / "go_chi" / "github_actions.yml.j2",
    _SOLUTIONS_TEMPLATE_DIR / "go_chi" / "gitlab_ci.yml.j2",
]

_ARCHIE_ENV_VAR = re.compile(r"ARCHIE_[A-Z_]+")


def test_codegen_templates_no_archie_env_vars():
    """Generated code must not reference ARCHIE_* environment variables.

    These names ship to customers in generated projects and carry the old
    brand. The Entelim application's own environment is unchanged; this is
    about the output that codegen produces for external projects.
    """
    offenders = []
    for path in _CODEGEN_TEMPLATES_WITH_ARCHIE_ENV:
        text = path.read_text(encoding="utf-8")
        matches = _ARCHIE_ENV_VAR.findall(text)
        if matches:
            offenders.append(
                "{}: {}".format(
                    path.relative_to(ROOT),
                    ", ".join(sorted(set(matches))),
                )
            )
    assert offenders == [], (
        "ARCHIE_* env vars still present in codegen templates:\n  "
        + "\n  ".join(offenders)
    )
