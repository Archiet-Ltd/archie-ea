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


# A raw snake_case key (an ARB review's reason, a governance gate's name) never
# reaches the UI beside its own label: known codes get a hand-written label,
# anything else is title-cased by title_keep_caps.
ENUM_LABEL_OVERRIDES = {
    "arb_review_is_legacy_generic": "Legacy review (predates typed ARB submission)",
    "arb_queue_empty": "No items in this queue",
    "arb_submission": "Architecture Review Board submission",
}


def title_keep_caps(value):
    """The one title-case helper for a stored label (also the humanize_key filter).

    A known key gets its hand-written label. Otherwise underscores read as
    spaces, a word with a capital after its first letter (SaaS, IoT, eCommerce)
    or an all-capital word of two to five letters (AI, SAML, API) stays as it
    was typed, and every other word is title-cased. The stored value is never
    changed.

    A hyphen is kept: the values people type for an application type or category
    (off-the-shelf, line-of-business) are hyphenated on purpose. Every key that goes
    through humanize_key is snake_case, so no caller needs a hyphen read as a space."""
    if value is None:
        return value
    text = str(value)
    if text in ENUM_LABEL_OVERRIDES:
        return ENUM_LABEL_OVERRIDES[text]

    def keep(word):
        if word.isalpha() and not word.isupper() and any(c.isupper() for c in word[1:]):
            return True
        return word.isalpha() and word.isupper() and 2 <= len(word) <= 5

    return " ".join(w if keep(w) else w.title() for w in text.replace("_", " ").split())


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
    def dash_filter(value, suffix="", missing="—"):
        """Render a figure that could not be computed as an em dash.

        DESIGN.md mandates the em dash for null display, and CLAUDE.md forbids
        substituting a plausible number: a printed `0` is indistinguishable
        from a measured zero, so a view whose query failed passes None and
        lets this filter say so. Jinja's `|default` will not do - it only
        fires on Undefined unless given a second argument.

        `suffix` is appended only when there is a real value, so
        `{{ coverage|dash('%') }}` renders an em dash rather than "-%".
        `missing` replaces the em dash where a sentence reads better, e.g.
        `{{ score|dash('/100', 'Not scored') }}`.

        Undefined is treated as None: a view that never passed the name did
        not measure it either, and Jinja would render it as empty.
        """
        from jinja2 import Undefined

        if value is None or isinstance(value, Undefined):
            return missing
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

    app.add_template_filter(title_keep_caps, "title_keep_caps")
    app.add_template_filter(title_keep_caps, "humanize_key")

    app.jinja_env.globals["ENUM_LABEL_OVERRIDES"] = ENUM_LABEL_OVERRIDES

    def index_for_role(role):
        return url_for(role.index)

    app.add_template_global(index_for_role)

    # ── S2-01: i18n formatting filters ─────────────────────────────
    # H-04: NOT registered as a Jinja filter. A second "format_currency"
    # existed here and SHADOWED the org-aware one in
    # _bootstrap/context_processors.py, silently defeating the single
    # currency source of truth: this one falls back to a hardcoded "USD"
    # and returns "" for a missing amount, which renders as a blank cell
    # rather than the em dash the never-invent-data rule requires.
    # Kept as a plain helper for any caller that passes an explicit
    # currency; the registered filter is the org-aware one.
    def format_currency_filter(value, currency=None):
        """Format a number as locale-aware currency.

        Usage: {{ 1234.56|format_currency }}
               {{ 1234.56|format_currency('EUR') }}

        Falls back to CurrencyConfig if no currency specified,
        then to plain formatting if Flask-Babel unavailable.
        """
        if value is None:
            return ""
        try:
            from flask_babel import format_currency as babel_fmt

            if not currency:
                try:
                    from config import CurrencyConfig
                    currency = CurrencyConfig.get_currency_config().get("code", "USD")
                except Exception:
                    currency = "USD"
            return babel_fmt(value, currency)
        except ImportError:
            # Fallback without Babel
            try:
                from config import CurrencyConfig
                symbol = CurrencyConfig.get_currency_config().get("symbol", "$")
            except Exception:
                symbol = "$"
            return f"{symbol}{value:,.2f}"

    @app.template_filter("format_datetime")
    def format_datetime_filter(value, fmt="medium"):
        """Format a datetime as locale-aware string.

        Usage: {{ item.created_at|format_datetime }}
               {{ item.created_at|format_datetime('short') }}
               {{ item.created_at|format_datetime('yyyy-MM-dd HH:mm') }}

        Falls back to strftime if Flask-Babel unavailable.
        """
        if value is None:
            return ""
        try:
            from flask_babel import format_datetime as babel_fmt
            return babel_fmt(value, fmt)
        except ImportError:
            try:
                if fmt == "short":
                    return value.strftime("%m/%d/%y %H:%M")
                elif fmt == "medium":
                    return value.strftime("%b %d, %Y %H:%M")
                elif fmt == "long":
                    return value.strftime("%B %d, %Y %H:%M:%S")
                elif fmt == "full":
                    return value.strftime("%A, %B %d, %Y %H:%M:%S %Z")
                else:
                    return value.strftime(str(fmt))
            except (AttributeError, ValueError):
                return str(value)

    @app.template_filter("format_number")
    def format_number_filter(value, decimals=None):
        """Format a number with locale-aware grouping.

        Usage: {{ 1234567|format_number }}
               {{ 3.14159|format_number(2) }}
        """
        if value is None:
            return ""
        try:
            from flask_babel import format_decimal
            if decimals is not None:
                fmt = f"#,##0.{'0' * decimals}"
                return format_decimal(value, format=fmt)
            return format_decimal(value)
        except ImportError:
            if decimals is not None:
                return f"{value:,.{decimals}f}"
            if isinstance(value, float):
                return f"{value:,.2f}"
            return f"{value:,}"

    @app.template_filter("format_percent")
    def format_percent_filter(value, decimals=1):
        """Format a decimal as locale-aware percentage.

        Usage: {{ 0.85|format_percent }}     → 85.0%
               {{ 0.85|format_percent(0) }}  → 85%
        """
        if value is None:
            return ""
        try:
            from flask_babel import format_percent as babel_pct
            return babel_pct(value)
        except ImportError:
            return f"{value * 100:,.{decimals}f}%"
