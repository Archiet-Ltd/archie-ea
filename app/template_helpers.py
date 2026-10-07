"""
Template helper functions for robust URL building and currency formatting
"""

from typing import Optional, Union

from flask import current_app, url_for
from werkzeug.routing import BuildError

from app.services.currency_service import CurrencyService


def safe_url_for(endpoint, **values):
    """Safely build URL with fallback for missing endpoints"""
    try:
        return url_for(endpoint, **values)
    except BuildError:
        # Return a fallback URL or # for missing endpoints
        return "#"


def safe_url_for_with_fallback(endpoint, fallback_url="#", **values):
    """Safely build URL with custom fallback"""
    try:
        return url_for(endpoint, **values)
    except BuildError:
        return fallback_url


def _get_show_archimate(user=None):
    """Read show_archimate_names from a user-like object, returning False
    when the attribute is absent or an error occurs."""
    if user is None:
        return False
    try:
        return bool(getattr(user, "show_archimate_names", False))
    except Exception:
        import logging
        _log = logging.getLogger(__name__)
        _log.warning("Could not read show_archimate_names from user %r", user, exc_info=True)
        return False


def _plain_name(element_type, user=None):
    """Return the plain-language display name for an ArchiMate element type.

    Module-level helper so tests can import it directly. The Jinja filter
    ``|plain_name`` delegates here.
    """
    from app.models.archimate_element_types import plain_name_for

    show_archimate = _get_show_archimate(user)
    if show_archimate:
        return element_type or "\u2014"
    return plain_name_for(element_type)


def _plain_layer(layer, user=None):
    """Return the plain-language display name for an ArchiMate layer.

    Module-level helper so tests can import it directly. The Jinja filter
    ``|plain_layer`` delegates here.
    """
    from app.models.archimate_element_types import plain_layer_name

    show_archimate = _get_show_archimate(user)
    if show_archimate:
        return layer or "\u2014"
    return plain_layer_name(layer)


def register_template_filters(app):
    """Register all template filters with the Flask app"""

    @app.template_filter("currency")
    def currency_filter(amount: Union[int, float, str], currency_code: Optional[str] = None) -> str:
        """
        Format amount with proper currency symbol and formatting

        Usage in templates:
        {{ amount | currency }}
        {{ amount | currency('USD') }}
        """
        if currency_code == "":
            currency_code = None
        service = CurrencyService(app)
        return service.format_currency(amount, currency_code)

    @app.template_filter("currency_symbol")
    def currency_symbol_filter(currency_code: Optional[str] = None) -> str:
        """
        Get just the currency symbol

        Usage in templates:
        {{ '' | currency_symbol }}
        {{ '' | currency_symbol('USD') }}
        """
        if currency_code == "":
            currency_code = None
        service = CurrencyService(app)
        return service.get_currency_symbol(currency_code)

    @app.template_filter("currency_name")
    def currency_name_filter(currency_code: Optional[str] = None) -> str:
        """
        Get the full currency name

        Usage in templates:
        {{ '' | currency_name }}
        {{ '' | currency_name('USD') }}
        """
        if currency_code == "":
            currency_code = None
        service = CurrencyService(app)
        return service.get_currency_name(currency_code)

    @app.template_filter("currency_api")
    def currency_api_filter(
        amount: Union[int, float, str], currency_code: Optional[str] = None
    ) -> dict:
        """
        Format currency data for API responses

        Usage in templates:
        {{ amount | currency_api }}
        {{ amount | currency_api('USD') }}
        """
        if currency_code == "":
            currency_code = None
        service = CurrencyService(app)
        return service.format_for_api(amount, currency_code)

    @app.template_filter("currency_clean")
    def currency_clean_filter(amount_string: str, currency_code: Optional[str] = None) -> float:
        """
        Parse numeric amount from a formatted currency string

        Usage in templates:
        {{ "$1,234.56" | currency_clean('USD') }}
        """
        if currency_code == "":
            currency_code = None
        service = CurrencyService(app)
        return service.parse_amount_from_string(amount_string, currency_code)

    try:
        from app.modules.interface_register.services.size_bands import effort_band

        @app.template_filter("effort_band")
        def effort_band_filter(estimated_effort_hours):
            """Convert estimated effort hours to a T-shirt size band."""
            return effort_band(estimated_effort_hours)
    except Exception:
        # A failure anywhere in interface_register must not prevent every
        # other filter registered by this function from registering --
        # importing this module transitively pulls in the whole
        # interface_register package (routes -> models). Losing this one
        # filter degrades one feature; losing the whole function 500s every
        # page using |currency, |number_format, etc.
        app.logger.warning(
            "effort_band template filter failed to register", exc_info=True
        )

    @app.template_filter("number_format")
    def number_format_filter(number: Union[int, float], decimal_places: int = 2) -> str:
        """
        Format number with thousands separator and decimal places

        Usage in templates:
        {{ number | number_format }}
        {{ number | number_format(3) }}
        """
        try:
            if isinstance(number, str):
                number = float(number)
            return f"{number:,.{decimal_places}f}"
        except (ValueError, TypeError):
            return str(number)

    @app.template_filter("percent")
    def percent_filter(number: Union[int, float], decimal_places: int = 1) -> str:
        """
        Format number as percentage

        Usage in templates:
        {{ 0.1234 | percent }}
        {{ 0.1234 | percent(2) }}
        """
        try:
            if isinstance(number, str):
                number = float(number)
            return f"{number * 100:.{decimal_places}f}%"
        except (ValueError, TypeError):
            return str(number)

    @app.template_filter("ordinal")
    def ordinal_filter(number: Union[int, str]) -> str:
        """
        Convert number to ordinal (1st, 2nd, 3rd, etc.)

        Usage in templates:
        {{ 1 | ordinal }}  # -> "1st"
        {{ 2 | ordinal }}  # -> "2nd"
        {{ 3 | ordinal }}  # -> "3rd"
        """
        try:
            if isinstance(number, str):
                number = int(number)

            if 10 <= number % 100 <= 20:
                suffix = "th"
            else:
                suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")

            return f"{number}{suffix}"
        except (ValueError, TypeError):
            return str(number)

    @app.template_filter("file_size")
    def file_size_filter(size_bytes: Union[int, float]) -> str:
        """
        Format bytes in human readable file size

        Usage in templates:
        {{ 1024 | file_size }}  # -> "1.0 KB"
        {{ 1048576 | file_size }}  # -> "1.0 MB"
        """
        try:
            if isinstance(size_bytes, str):
                size_bytes = float(size_bytes)

            if size_bytes == 0:
                return "0 B"

            size_names = ["B", "KB", "MB", "GB", "TB"]
            i = 0
            while size_bytes >= 1024.0 and i < len(size_names) - 1:
                size_bytes /= 1024.0
                i += 1

            return f"{size_bytes:.1f} {size_names[i]}"
        except (ValueError, TypeError):
            return str(size_bytes)

    @app.template_filter("truncate_words")
    def truncate_words_filter(text: str, num_words: int = 50, suffix: str = "...") -> str:
        """
        Truncate text to specified number of words

        Usage in templates:
        {{ long_text | truncate_words(20) }}
        {{ long_text | truncate_words(20, " [more]") }}
        """
        if not text:
            return text

        words = text.split()
        if len(words) <= num_words:
            return text

        return " ".join(words[:num_words]) + suffix

    @app.template_filter("slugify")
    def slugify_filter(text: str) -> str:
        """
        Convert text to URL-friendly slug

        Usage in templates:
        {{ "Hello World!" | slugify }}  # -> "hello-world"
        """
        import re

        # Convert to lowercase and replace spaces with hyphens
        slug = re.sub(r"[^\w\s-]", "", text.lower())
        slug = re.sub(r"[\s-]+", "-", slug)
        return slug.strip("-")

    @app.template_filter("plain_name")
    def plain_name_filter(element_type, user=None):
        """Return the plain-language display name for an ArchiMate element type.

        When the current user has ``show_archimate_names`` enabled, the
        original PascalCase name is returned unchanged. Otherwise the
        plain-language name from PLAIN_LANGUAGE_NAMES is used.

        Usage in templates:
            {{ element.element_type | plain_name }}
            {{ element.element_type | plain_name(current_user) }}

        The ``user`` argument is optional; when omitted or None the filter
        defaults to plain names (the default setting is off).
        """
        return _plain_name(element_type, user)

    @app.template_filter("plain_layer")
    def plain_layer_filter(layer, user=None):
        """Return the plain-language display name for an ArchiMate layer.

        Same behaviour as ``plain_name``: respects the user's
        ``show_archimate_names`` setting.

        Usage in templates:
            {{ layer | plain_layer }}
            {{ layer | plain_layer(current_user) }}
        """
        return _plain_layer(layer, user)

    # Global template functions
    @app.context_processor
    def currency_context():
        """Make currency functions available globally in templates"""
        service = CurrencyService(app)

        return {
            "currency_service": service,
            "get_currency_symbol": service.get_currency_symbol,
            "get_currency_name": service.get_currency_name,
            "get_supported_currencies": service.get_supported_currencies,
            "get_supported_currency_codes": service.get_supported_currency_codes,
            "is_supported_currency": service.is_supported_currency,
            "default_currency": current_app.config.get("DEFAULT_CURRENCY", "GBP"),
        }

    @app.context_processor
    def plain_language_context():
        """Make plain-language vocabulary and user setting available to JS.

        Only injected when a user is signed in — public pages (landing,
        login, password reset) never render these values, so building
        ~3 KB of vocabulary data on every unauthenticated request is wasted
        work. Returned as plain Python objects, not pre-serialised JSON: the
        template renders them with ``|tojson|safe``, which does the
        escaping Jinja's autoescape otherwise can't see through a bare
        ``|safe`` (see tests/test_template_escaping.py).
        """
        from flask_login import current_user

        try:
            if not (
                current_user
                and hasattr(current_user, "is_authenticated")
                and current_user.is_authenticated
            ):
                return {
                    "plain_language_names": {},
                    "plain_layer_names": {},
                    "show_archimate_names": False,
                }
        except Exception:
            return {
                "plain_language_names": {},
                "plain_layer_names": {},
                "show_archimate_names": False,
            }

        from app.models.archimate_element_types import PLAIN_LANGUAGE_NAMES, PLAIN_LAYER_NAMES

        show_archimate = _get_show_archimate(current_user)

        return {
            "plain_language_names": PLAIN_LANGUAGE_NAMES,
            "plain_layer_names": PLAIN_LAYER_NAMES,
            "show_archimate_names": show_archimate,
        }


# Alias for backward compatibility
register_currency_filters = register_template_filters
