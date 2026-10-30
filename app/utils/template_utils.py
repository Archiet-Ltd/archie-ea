"""
Template utilities for Flask application
"""

import re

from markupsafe import Markup, escape


# ── Error humanization ──────────────────────────────────────────────
WORKFLOW_ERROR_PATTERNS = {
    'database': (
        ['sqlalchemy', 'integrityerror', 'operationalerror', 'databaseerror',
         'psycopg', 'sqlite'],
        "Database operation failed during workflow execution.",
    ),
    'validation': (
        ['valueerror', 'typeerror', 'keyerror', 'validation', 'missing required',
         'invalid'],
        "Invalid input encountered during workflow execution.",
    ),
    'file': (
        ['filenotfounderror', 'ioerror', 'csv', 'parse error', 'encoding'],
        "File processing failed during workflow execution.",
    ),
    'network': (
        ['connectionerror', 'timeouterror', 'network', 'unreachable'],
        "Network operation failed during workflow execution.",
    ),
    'permission': (
        ['permissionerror', 'access denied', 'forbidden', 'unauthorized'],
        "Access denied during workflow execution.",
    ),
}

_EXCEPTION_MARKERS = re.compile(
    r'Traceback|Error\b|Exception\b|^\w+Error:|^\w+Exception:',
    re.MULTILINE,
)


def humanize_error(error_msg):
    """Convert raw error messages into user-friendly HTML.

    - User-authored messages (rejections, cancellations) pass through unchanged.
    - Technical exceptions get a friendly summary with collapsible raw details.
    """
    if not error_msg:
        return Markup('')

    msg = str(error_msg)

    # Pass-through: user-authored messages
    if msg.startswith('Rejected by ') or msg.startswith('Cancelled by '):
        return Markup(escape(msg))

    # Check for technical markers
    lower = msg.lower()
    if not _EXCEPTION_MARKERS.search(msg):
        # No exception patterns — likely already human-friendly
        return Markup(escape(msg))

    # Classify the error
    friendly = "An unexpected error occurred during workflow execution."
    for _category, (keywords, summary) in WORKFLOW_ERROR_PATTERNS.items():
        if any(kw in lower for kw in keywords):
            friendly = summary
            break

    escaped_raw = escape(msg)
    return Markup(
        f'<span class="block">{escape(friendly)}'
        f' You can retry or contact support.</span>'
        f'<details class="mt-2 text-xs">'
        f'<summary class="cursor-pointer text-destructive/60 '
        f'hover:text-destructive/80">Technical details</summary>'
        f'<pre class="mt-1 p-2 rounded bg-muted text-muted-foreground '
        f'overflow-x-auto whitespace-pre-wrap break-words text-[11px]">'
        f'{escaped_raw}</pre></details>'
    )


def register_template_utils(app):
    """Register template utilities with Flask app."""
    _register(app)


def _register(app):
    """Inline registration of core template utilities."""
    from flask import url_for

    @app.template_test()
    def equalto(value, other):
        return value == other

    @app.template_global()
    def is_hidden_field(field):
        from wtforms.fields import HiddenField
        return isinstance(field, HiddenField)

    import builtins as _builtins

    @app.template_global("getattr")
    def template_getattr(obj, name, default=None):
        if obj is None:
            return default
        try:
            return _builtins.getattr(obj, name, default)
        except (AttributeError, TypeError):
            return default

    @app.template_filter("safe_attr")
    def safe_attr_filter(obj, attr_name, default=None):
        if obj is None:
            return default
        try:
            return _builtins.getattr(obj, attr_name, default)
        except (AttributeError, TypeError):
            return default

    @app.template_filter("dash")
    def dash_filter(value, suffix=""):
        """Render a figure that could not be computed as an em dash.

        DESIGN.md mandates the em dash for null display, and CLAUDE.md forbids
        substituting a plausible number: a printed `0` is indistinguishable
        from a measured zero, so a view whose query failed passes None and
        lets this filter say so. Jinja's `|default` will not do - it only
        fires on Undefined unless given a second argument.

        `suffix` is appended only when there is a real value, so
        `{{ coverage|dash('%') }}` renders an em dash rather than "-%".

        Undefined is treated as None: a view that never passed the name did
        not measure it either, and Jinja would render it as empty.
        """
        from jinja2 import Undefined

        if value is None or isinstance(value, Undefined):
            return "—"
        return "%s%s" % (value, suffix)

    @app.template_filter("safe_ratio_pct")
    def safe_ratio_pct_filter(numerator, denominator, decimals=1):
        """Render numerator/denominator as a percentage, or an em dash when
        denominator is 0/None -- the M1 fix for zero-denominator screens
        (policy-monitoring, rationalization scorecard, product-roadmap) that
        previously rendered a misleading 0%/100% for "no records yet".

        Usage: {{ passed|safe_ratio_pct(total) }}  -> "83.3%" or "—"
        """
        value = safe_ratio(numerator, denominator, decimals=decimals)
        return "—" if value is None else f"{value}%"

    def safe_ratio(numerator, denominator, decimals=1):
        """Python-side counterpart of `safe_ratio_pct` for use outside
        templates (route/service code building a context dict). Returns
        None -- never 0 -- when denominator is falsy, so callers pass the
        None straight to the template and let `dash`/`safe_ratio_pct` render
        the em dash rather than inventing a percentage over zero records.
        """
        if not denominator:
            return None
        try:
            return round((numerator / denominator) * 100, decimals)
        except (TypeError, ZeroDivisionError):
            return None

    app.jinja_env.globals["safe_ratio"] = safe_ratio

    # H4/L1: a shared enum-to-label mapping so a raw snake_case key (an ARB
    # review's `reason`, a governance gate's `gate_name`, ...) never reaches
    # the UI beside its own human label -- one helper instead of the
    # `|replace('_',' ')|title` one-offs scattered through arb templates.
    # Known codes get a hand-written label; anything else falls back to the
    # same title-casing those one-offs already did, so this is a strict
    # improvement, never a regression.
    ENUM_LABEL_OVERRIDES = {
        "arb_review_is_legacy_generic": "Legacy review (predates typed ARB submission)",
        "arb_queue_empty": "No items in this queue",
        "arb_submission": "Architecture Review Board submission",
    }

    @app.template_filter("humanize_key")
    def humanize_key_filter(value):
        """Turn a raw snake_case/enum key into a human label.

        Usage: {{ review.reason|humanize_key }}
               {{ gate_name|humanize_key }}
        """
        if value is None:
            return None
        text = str(value)
        if text in ENUM_LABEL_OVERRIDES:
            return ENUM_LABEL_OVERRIDES[text]
        return text.replace("_", " ").replace("-", " ").strip().title()

    app.jinja_env.globals["ENUM_LABEL_OVERRIDES"] = ENUM_LABEL_OVERRIDES

    def index_for_role(role):
        return url_for(role.index)

    app.add_template_global(index_for_role)

    # ── S2-01: i18n formatting filters ─────────────────────────────
    # The one currency formatter is fmt_money (app/utils/locale_format.py), registered as ``format_currency`` in
    # _bootstrap/context_processors.py; no second copy lives here.

    @app.template_filter("format_datetime")
    def format_datetime_filter(value, fmt=None):
        """Format a datetime as locale-aware string.

        Usage: {{ item.created_at|format_datetime }}
               {{ item.created_at|format_datetime('short') }}
               {{ item.created_at|format_datetime('yyyy-MM-dd HH:mm') }}

        Falls back to strftime if Flask-Babel unavailable.
        """
        from app.utils.locale_format import fmt_datetime_as

        text = fmt_datetime_as(value, fmt)
        return "" if value is None else (str(value) if text is None else text)

    @app.template_filter("format_number")
    def format_number_filter(value, decimals=None):
        """Format a number with locale-aware grouping.

        Usage: {{ 1234567|format_number }}
               {{ 3.14159|format_number(2) }}
        """
        from app.utils.locale_format import fmt_number

        text = fmt_number(value, decimals)
        return "" if value is None else (str(value) if text is None else text)

    @app.template_filter("format_percent")
    def format_percent_filter(value, decimals=1):
        """Format a decimal as locale-aware percentage.

        Usage: {{ 0.85|format_percent }}     → 85.0%
               {{ 0.85|format_percent(0) }}  → 85%
        """
        from app.utils.locale_format import fmt_percent

        text = fmt_percent(value, decimals)
        return "" if value is None else (str(value) if text is None else text)
