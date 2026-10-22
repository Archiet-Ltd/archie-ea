"""Boot health — the gate that makes silent degradation loud.

Why this file exists
--------------------
``app/_bootstrap/blueprints.py`` registers every module behind ``try/except``
(~100 handlers) so one broken import degrades a single feature instead of taking
down the app. That resilience is deliberate and worth keeping, but on its own it
means a module can stop registering with **no failing test and no error** — the
first symptom is a Jinja ``BuildError`` 500 on every page whose chrome calls
``url_for()`` on one of the vanished endpoints. Because the sidebar renders on
every admin page, a single missing registration can 500 the whole product.

These tests close that loop. They are deliberately **database-free**:
``create_app()`` boots fine with no PostgreSQL (connection errors are caught and
logged), so this suite runs in CI without a database service and catches the
class of regression that the DB-backed tests never reach.

Kept strict on purpose. All four assertions passed on a clean tree when written
(3364 view functions, 0 registration failures, 0 unresolved endpoints across 533
templates) — so any failure here is a real regression, not pre-existing debt.
"""

import glob
import os
import re

import pytest

# Endpoint reference inside a Jinja url_for(...) with a literal 'blueprint.view'.
# Deliberately only matches string literals — url_for(some_var) is unresolvable
# statically and is out of scope.
URL_FOR_LITERAL = re.compile(r"url_for\(\s*'([a-zA-Z_][\w]*\.[\w]+)'")

# A Jinja comment never renders, so a url_for() named inside {# ... #} (usage
# examples in a macro file's header) cannot raise a BuildError and is not scanned.
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)

# The documented guard for non-fatally-registered blueprints (see DESIGN.md):
#   {% if 'value_stream.index' in flask.current_app.view_functions %}
# An endpoint named in such a guard is *allowed* to be absent — that is the whole
# point of the guard — so it is excluded from the strict assertions below.
VIEW_FUNCTIONS_GUARD = re.compile(r"'([a-zA-Z_][\w]*\.[\w]+)'\s+in\s+[\w.]*view_functions")

# Message fragments the registration helpers use when a module fails to load.
REGISTRATION_FAILURE_MARKERS = (
    "failed to register",
    "could not register",
    "skipped (non-fatal)",
    "blueprint failed",
)

# Warnings that legitimately reach app.logger during blueprint registration but
# are unrelated to it. Both are environment-dependent, not registration failures:
#   - pgvector check defers when there is no app context yet
#   - policy_integration probes for an optional module
# This is a ratchet: a NEW unexplained boot warning fails the test, so noise
# cannot quietly accumulate. Add to it only with a reason.
ALLOWLISTED_BOOT_WARNINGS = {
    "Deferring pgvector extension check - no app context",
    "Original tools module not found",
}

# Flask raises this when a blueprint gains a route after it has been registered.
# It fires only on the SECOND create_app() in a process, so it appears when this
# module runs after another test has already built an app — never on a real boot.
# Verified against production: all three SAP routes (/api/sap/import,
# /admin/sap-clean-core, /admin/sap-clean-core/<id>) are present in the live
# url_map, and the message appears zero times in the server log.
#
# Matched by signature rather than added to the exact-string allowlist above,
# because the message embeds the blueprint name and would otherwise have to be
# enumerated per blueprint — which would quietly grow into a blanket exemption.
#
# It is still a real fragility worth naming: app/modules/codegen/services/
# sap_importer.py attaches routes to the `codegen` blueprint after registration,
# so it works only because create_app() happens to run once per process. Anything
# that boots the app twice — a future in-process worker, a warm-reload dev server
# — silently loses those routes.
_REREGISTRATION_SIGNATURE = "can no longer be called on the blueprint"


def _is_second_boot_artifact(record) -> bool:
    return _REREGISTRATION_SIGNATURE in record["message"]

CORE_CHROME_TEMPLATES = [
    "app/templates/layouts/admin_base.html",
    "app/templates/components/admin_sidebar.html",
]


@pytest.fixture(scope="module")
def app():
    """Boot the real app once. No database required."""
    os.environ.setdefault("FLASK_CONFIG", "testing")
    from app import create_app

    return create_app("testing")


def _scan_templates(paths):
    """Return ({endpoint: {files}}, {guarded endpoints}) for *paths*."""
    referenced, guarded = {}, set()
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        guarded |= set(VIEW_FUNCTIONS_GUARD.findall(text))
        for endpoint in URL_FOR_LITERAL.findall(JINJA_COMMENT.sub("", text)):
            referenced.setdefault(endpoint, set()).add(path)
    return referenced, guarded


def _assert_endpoints_resolve(app, paths, label):
    referenced, guarded = _scan_templates(paths)
    assert referenced, f"{label}: scanned {len(paths)} file(s) but found no url_for() literals — the scanner is broken, not the app"

    missing = {
        endpoint: sorted(files)
        for endpoint, files in referenced.items()
        if endpoint not in app.view_functions and endpoint not in guarded
    }
    assert not missing, (
        f"{label}: {len(missing)} endpoint(s) referenced by url_for() are not registered. "
        "Each will raise a BuildError 500 when its template renders. "
        "Either fix the registration or wrap the link in the documented guard "
        "({% if 'ep' in flask.current_app.view_functions %}). Missing: "
        + "; ".join(f"{ep} (in {files[0]})" for ep, files in sorted(missing.items()))
    )


def test_app_boots_with_no_database(app):
    """create_app() must succeed and register a substantial route table.

    The floor is a smoke threshold, not a target — it catches "half the app
    silently failed to register", which a bare no-exception check would miss.
    """
    assert len(app.view_functions) > 2000, (
        f"only {len(app.view_functions)} view functions registered — expected >2000. "
        "A large block of blueprints failed to register."
    )


def test_no_blueprint_registration_failures(app):
    """No module may fail to register.

    Reads the capture installed by ``init_blueprints``. This is the assertion the
    ~100 swallowed exceptions were previously invisible to.
    """
    failures = app.extensions.get("blueprint_registration_failures")
    assert failures is not None, (
        "app.extensions['blueprint_registration_failures'] is missing — the capture in "
        "app/_bootstrap/blueprints.py:init_blueprints was removed or bypassed."
    )

    registration_failures = [
        record
        for record in failures
        if any(marker in record["message"].lower() for marker in REGISTRATION_FAILURE_MARKERS)
        and not _is_second_boot_artifact(record)
    ]
    assert not registration_failures, "blueprint registration failed:\n" + "\n".join(
        f"  [{r['level']}] {r['logger']}: {r['message']}" for r in registration_failures
    )


def test_boot_warnings_are_allowlisted(app):
    """Ratchet: no new unexplained warnings during blueprint registration."""
    failures = app.extensions.get("blueprint_registration_failures") or []
    unexpected = [
        r
        for r in failures
        if r["message"] not in ALLOWLISTED_BOOT_WARNINGS and not _is_second_boot_artifact(r)
    ]
    assert not unexpected, (
        "new warning(s) during blueprint registration. If benign, add the message to "
        "ALLOWLISTED_BOOT_WARNINGS with a reason; otherwise fix the cause:\n"
        + "\n".join(f"  [{r['level']}] {r['logger']}: {r['message']}" for r in unexpected)
    )


def test_critical_endpoints_registered(app):
    """The endpoints app/__init__.py declares as required must exist."""
    from app import REQUIRED_ENDPOINTS

    missing = [ep for ep in REQUIRED_ENDPOINTS if ep not in app.view_functions]
    assert not missing, (
        f"REQUIRED_ENDPOINTS missing: {missing}. These are referenced by core chrome; "
        "url_for() will fail at runtime on every page that renders it."
    )


def test_core_chrome_endpoints_resolve(app):
    """Every unguarded url_for() in the base layout and sidebar must resolve.

    Highest-severity case: this chrome renders on every admin page, so one
    unresolved endpoint here is a site-wide 500, not a single broken page.
    """
    _assert_endpoints_resolve(app, CORE_CHROME_TEMPLATES, "core chrome")


def test_all_template_endpoints_resolve(app):
    """Every unguarded url_for() literal in every template must resolve."""
    templates = glob.glob("app/templates/**/*.html", recursive=True)
    assert len(templates) > 400, (
        f"found only {len(templates)} templates — tests must run from the repo root"
    )
    _assert_endpoints_resolve(app, templates, "all templates")


def test_url_for_inside_a_jinja_comment_is_not_scanned(tmp_path):
    """A usage example in a {# #} comment is not a reference; real calls still are."""
    template = tmp_path / "t.html"
    template.write_text(
        """{# example: {{ url_for('ghost.commented') }}
   spans lines #}
<a href="{{ url_for('ghost.live') }}">x</a>
""",
        encoding="utf-8",
    )
    referenced, _ = _scan_templates([str(template)])
    assert set(referenced) == {"ghost.live"}
