"""Views must pass every variable their template dereferences.

Flask's default Undefined renders as empty rather than raising, so a missing
kwarg is usually invisible - until the template does attribute access or applies
a filter, at which point it is a hard 500. Nothing in scripts/verify.py checks
this contract: `boot-health` verifies that endpoints resolve, not that a page
renders.

Five live 500s were found this way and fixed. Each is pinned below by rendering
the real template with exactly the keywords its view now passes, and failing only
on UndefinedError - so an unrelated rendering problem (a missing global, a
database-backed macro) does not turn this into a flaky test.
"""

from __future__ import annotations

import os

import pytest
from jinja2 import UndefinedError


class Stub:
    """A permissive stand-in for a model row.

    Attribute access always succeeds, so this test fails only when a *top-level
    context name* is missing - which is the contract under test. Mirroring every
    column of User or ArchitectureChangeRequest here would couple the test to the
    models and make it fail for reasons that are not the defect.
    """

    def __init__(self, **attrs):
        self.__dict__.update(attrs)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Stub()

    def __call__(self, *args, **kwargs):
        return Stub()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return True

    def __str__(self):
        return ""

    def __html__(self):
        return ""


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("FLASK_CONFIG", "testing")
    os.environ.setdefault("SECRET_KEY", "test-only-not-secret")
    from app import create_app

    application = create_app("testing")
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    return application


def _render(app, template, **kwargs):
    """Render *template*; re-raise only UndefinedError as a failure.

    Uses flask.render_template rather than jinja_env.get_template().render() so
    the app's context processors run - `current_user`, `config` and the design-
    system globals are injected there, and bypassing them produces UndefinedError
    for names the view is not supposed to pass.
    """
    from flask import render_template

    with app.test_request_context("/"):
        try:
            render_template(template, **kwargs)
        except UndefinedError as exc:
            pytest.fail(
                "%s raised UndefinedError with the kwargs its view passes: %s"
                % (template, exc)
            )
        except Exception:
            # Anything else (a macro needing a database row, a filter needing a
            # request) is out of scope here. The contract under test is only
            # "every dereferenced name was supplied".
            pass


def test_account_manage_renders_with_user_and_form(app):
    """account.change_password / change_email_request passed form= but not user=.

    account/manage.html does {{ '%s %s' % (user.first_name, user.last_name) }},
    so both 500'd - and the page is linked from components/admin_header.html,
    which layouts/admin_base.html includes on every authenticated page.
    """
    user = Stub(
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        username="ada",
        confirmed=True,
        is_authenticated=True,
    )
    _render(app, "account/manage.html", user=user, form=None)


def test_arb_change_request_detail_renders_with_cr(app):
    """arb.change_request_detail passed change_request=; the template reads cr."""
    cr = Stub(
        id=1,
        acr_reference="ACR-001",
        title="Example change",
        status="open",
        raised_at=None,
        description="",
        impact_level=None,
        disposition=None,
    )
    _render(app, "arb/change_request_detail.html", cr=cr)


def test_applications_edit_renders_with_application(app):
    """application_mgmt crud_routes passed app=; the template reads application."""
    application = Stub(
        id=1,
        name="Example App",
        description="",
        updated_at=None,
        deployment_status="production",
    )
    _render(
        app,
        "applications/edit.html",
        form=None,
        mode="edit",
        application=application,
        application_functions=[],
        application_processes=[],
        data_objects=[],
    )


@pytest.mark.parametrize(
    "module_path,needle",
    [
        ("app/modules/account/v2/routes/account_routes.py", "user=current_user, form=form"),
        ("app/modules/account/routes/account_routes.py", "user=current_user, form=form"),
    ],
)
def test_both_account_tiers_pass_user(module_path, needle):
    """Legacy and v2 are selected by a feature flag; both must be correct."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / module_path).read_text(encoding="utf-8")
    assert needle in text, "%s no longer passes user= to account/manage.html" % module_path


def test_custom_field_edit_does_not_render_a_wtforms_template_without_a_form():
    """The template needs a form object this route never builds, and no
    CustomField*Form class exists in the tree, so rendering it is a guaranteed
    500. custom_field_create() already redirects for the same reason."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "app/application_mgmt/custom_field_routes.py").read_text(encoding="utf-8")
    # Look for an actual render call, not the filename - which also appears in
    # the comment explaining why it is no longer rendered.
    rendered = [
        line
        for line in text.splitlines()
        if "custom_field_form.html" in line and not line.strip().startswith("#")
    ]
    assert not rendered, (
        "custom_field_routes renders a WTForms template again; build the form "
        "first, or keep redirecting as custom_field_create does"
    )


# ─────────────────────────────────────────────────────────────────────
# Encoding contract: a template Jinja cannot decode has no tags at all.
#
# the strategic roadmap template (since merged into genome/roadmap.html) shipped as UTF-16LE with
# no BOM. Jinja reads templates as UTF-8, so every second byte was a NUL
# and not one `{%` matched: the page did not extend a layout, no block
# was defined, and the route emitted ~59 KB of literal template text to
# the browser. `template-syntax` cannot catch it - a template with zero
# tags parses perfectly - which is why it survived. These tests assert
# the bytes AND the parsed tags, because either alone is satisfiable by
# a file that renders nothing.
# ─────────────────────────────────────────────────────────────────────

_ROADMAP_TEMPLATE = "genome/roadmap.html"


def _template_bytes(app, name):
    _src, filename, _uptodate = app.jinja_env.loader.get_source(app.jinja_env, name)
    from pathlib import Path

    return Path(filename).read_bytes()


def test_every_template_is_decodable_utf8(app):
    """A NUL byte or a non-UTF-8 sequence anywhere in the tree is this bug."""
    from pathlib import Path

    root = Path(app.root_path) / "templates"
    broken = []
    for path in sorted(root.rglob("*.html")):
        raw = path.read_bytes()
        if b"\x00" in raw:
            broken.append("%s: NUL bytes (UTF-16?)" % path.relative_to(root).as_posix())
            continue
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            broken.append("%s: not UTF-8 (%s)" % (path.relative_to(root).as_posix(), exc))
    assert not broken, (
        "templates Jinja cannot decode - it reads UTF-8, so these render as "
        "literal markup with no tags:\n  " + "\n  ".join(broken)
    )


def test_enhanced_roadmap_parses_its_jinja_tags(app):
    """The tags must exist after parsing, not merely appear in the bytes."""
    from jinja2 import nodes

    raw = _template_bytes(app, _ROADMAP_TEMPLATE)
    assert b"\x00" not in raw, "%s is UTF-16 again" % _ROADMAP_TEMPLATE
    source = raw.decode("utf-8")

    ast = app.jinja_env.parse(source)
    extends = list(ast.find_all(nodes.Extends))
    blocks = {b.name for b in ast.find_all(nodes.Block)}
    assert extends, "%s no longer extends a layout" % _ROADMAP_TEMPLATE
    assert extends[0].template.value == "layouts/admin_base.html"
    assert {"title", "content"} <= blocks, (
        "%s lost its blocks - it would render as literal text" % _ROADMAP_TEMPLATE
    )


def test_enhanced_roadmap_renders_with_its_view_kwargs(app):
    """Exactly the keywords app/modules/genome/routes/roadmap_routes.py passes when
    the board could not be built."""
    html = _render_none_safe(
        app,
        _ROADMAP_TEMPLATE,
        board=None,
        plateaus=[],
        error="The roadmap could not be built.",
        lens="strategic",
        tabs=[("Strategic", "/genome/roadmap/", True)],
        crumbs=[("Enterprise", None)],
        critical_paths=[],
        clear_url=None,
    )
    if html is None:
        pytest.skip("layout needs request-scoped state unavailable here")
    assert "{% block" not in html and "{% extends" not in html, (
        "%s emitted its own tags as text - it is mis-encoded again"
        % _ROADMAP_TEMPLATE
    )
    # The failure is shown to the person, not drawn as an empty roadmap.
    assert "The roadmap could not be built." in html, (
        "%s content block did not render" % _ROADMAP_TEMPLATE
    )
    assert "roadmapApp()" not in html, (
        "%s adopted the generic roadmapApp name that alpine-architecture.js "
        "already registers; the page would bind to the wrong component"
        % _ROADMAP_TEMPLATE
    )


# ─────────────────────────────────────────────────────────────────────
# Error-path contract: a view whose query failed passes None for every
# figure (CLAUDE.md "never invent data"), so the template must render an
# em dash rather than crash. `none >= 80` and `"%.1f"|format(none)` are
# TypeErrors in Jinja exactly as in Python, and a page that 500s on None
# is worse than one showing a fabricated 0 - so TypeError is a failure
# here, not the "out of scope" catch-all that _render tolerates.
# ─────────────────────────────────────────────────────────────────────


def _render_none_safe(app, template, **kwargs):
    """Render *template* with a view's error-path kwargs.

    Fails on UndefinedError (a name the view forgot) and on TypeError /
    ValueError (arithmetic, formatting or a comparison against None).
    Everything else - a macro wanting a database row, an unresolvable
    url_for for a blueprint that is not registered in this config - is
    out of scope, as in _render above.
    """
    from flask import render_template

    with app.test_request_context("/"):
        try:
            return render_template(template, **kwargs)
        except UndefinedError as exc:
            pytest.fail("%s: UndefinedError on the error path: %s" % (template, exc))
        except (TypeError, ValueError) as exc:
            pytest.fail(
                "%s: %s on the error path - a figure is None and the template "
                "compares, formats or does arithmetic on it: %s"
                % (template, type(exc).__name__, exc)
            )
        except Exception:
            return None


# Exactly the keywords each error handler now passes. Keep these in step
# with the handlers; that is the point of the test.
_ERROR_PATH_CONTEXTS = [
    (
        "capability_analysis/unmapped_capabilities.html",
        dict(
            unmapped_capabilities=[],
            total_capabilities=None,
            mapped_capabilities=None,
            unmapped_count=None,
            mapping_coverage=None,
            domain_stats=[],
            priority_breakdown=[],
            load_error="x",
        ),
    ),
    (
        "hybrid_mapping/dashboard.html",
        dict(
            stats=None,
            app_mappings=[],
            product_mappings=[],
            archimate_mappings=[],
            unmapped_caps=[],
            unmapped_products=[],
            unmapped_archimate=[],
            load_error="x",
        ),
    ),
    (
        "business_capability/overview.html",
        dict(
            classified_capabilities=None,
            total_capabilities=None,
            classified_count=None,
            load_error="x",
        ),
    ),
    (
        "ea_workflows/phase_viewpoint.html",
        dict(
            phase_code="phase_a",
            phase_name="Phase A",
            viewpoint_name="",
            primary_layer="",
            archimate_concern="",
            input_types=[],
            derived_types=[],
            elements=[],
            element_count=None,
            relationship_count=None,
            load_error="x",
        ),
    ),
    (
        "enterprise/enterprise_dashboard.html",
        dict(
            data_models_count=None,
            solutions_count=None,
            software_modules_count=None,
            gaps_count=None,
            load_error="x",
        ),
    ),
    (
        "enterprise/software_architecture_dashboard.html",
        dict(
            component_count=None,
            service_count=None,
            interface_count=None,
            dependency_count=None,
            components=[],
            load_error="x",
        ),
    ),
    (
        "enterprise/data_architecture_dashboard.html",
        dict(
            data_stack=[],
            data_cap_count=None,
            conceptual_count=None,
            logical_count=None,
            physical_count=None,
            data_lineage_count=None,
            archimate_data_count=None,
            archimate_rel_count=None,
            load_error="x",
        ),
    ),
    (
        "capability_maturity/search.html",
        dict(capabilities=[], domains=[], total_count=None, load_error="x"),
    ),
    (
        "integration/dashboard.html",
        dict(definitions=[], active_instances=[], stats=None, load_error="x"),
    ),
    (
        "industry_apqc/dashboard.html",
        dict(frameworks=None, framework_stats=None, error="boom", load_error="x"),
    ),
    (
        "vendors/list.html",
        dict(
            vendors=[],
            stats=None,
            vendor_type_filter=None,
            domain_filter=None,
            contract_status_filter=None,
            search_query="",
            pagination=None,
            per_page=25,
            domain_choices=[],
            get_domain_label=lambda *a, **k: "",
            get_domain_color_classes=lambda *a, **k: "",
            VENDOR_DOMAINS=[],
            load_error="x",
        ),
    ),
]


@pytest.mark.parametrize(
    "template,context",
    _ERROR_PATH_CONTEXTS,
    ids=[t for t, _ in _ERROR_PATH_CONTEXTS],
)
def test_error_path_renders_with_none_figures(app, template, context):
    """Every figure None must render, and must not print the word None."""
    html = _render_none_safe(app, template, **context)
    if html is not None:
        assert ">None<" not in html, (
            "%s printed the literal 'None' - use |dash so a missing figure "
            "reads as an em dash" % template
        )


def test_dash_filter_distinguishes_missing_from_zero(app):
    """0 and None must not look alike: that is the whole rule."""
    env = app.jinja_env
    assert env.filters["dash"](None) == "\u2014"
    assert env.filters["dash"](0) == "0"
    assert env.filters["dash"](None, "%") == "\u2014"
    assert env.filters["dash"](42, "%") == "42%"
    from jinja2 import Undefined

    assert env.filters["dash"](Undefined(name="missing")) == "\u2014"
