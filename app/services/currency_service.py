"""
Currency Service - Centralized currency formatting and management

This service provides a unified way to handle currency formatting,
symbol display, and currency conversion across the application.
"""

from typing import Optional, Union

from flask import current_app

from config import CurrencyConfig


class CurrencyService:
    """Service for handling currency formatting and management"""

    def __init__(self, app=None):
        self.app = app
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize the service with Flask app"""
        app.config.setdefault("CURRENCY_CONFIG", CurrencyConfig)
        app.config.setdefault("DEFAULT_CURRENCY", CurrencyConfig.DEFAULT_CURRENCY)

    def format_currency(
        self, amount: Union[int, float, str], currency_code: Optional[str] = None
    ) -> str:
        """
        Format amount with proper currency symbol and formatting

        Args:
            amount: The monetary amount to format
            currency_code: Currency code (defaults to app default)

        Returns:
            Formatted currency string with symbol

        Raises:
            ValueError: If currency_code is not supported
        """
        if currency_code is None:
            currency_code = current_app.config.get("DEFAULT_CURRENCY", "GBP")

        # Convert string to float if needed
        if isinstance(amount, str):
            try:
                amount = float(amount)
            except ValueError:
                raise ValueError(f"Invalid amount: {amount}")

        # Get currency configuration
        currency_config = CurrencyConfig.get_currency_config(currency_code)

        # Format the amount with proper decimal places
        formatted_amount = f"{amount:,.{currency_config['decimal_places']}f}"

        # Apply thousands separator if needed (replacing default comma)
        if currency_config["thousands_separator"] != ",":
            formatted_amount = formatted_amount.replace(",", currency_config["thousands_separator"])

        # Add symbol in correct position
        if currency_config["position"] == "prefix":
            return f"{currency_config['symbol']}{formatted_amount}"
        else:
            return f"{formatted_amount}{currency_config['symbol']}"

    def get_currency_symbol(self, currency_code: Optional[str] = None) -> str:
        """
        Get just the currency symbol

        Args:
            currency_code: Currency code (defaults to app default)

        Returns:
            Currency symbol string
        """
        if currency_code is None:
            currency_code = current_app.config.get("DEFAULT_CURRENCY", "GBP")

        currency_config = CurrencyConfig.get_currency_config(currency_code)
        return currency_config["symbol"]

    def get_currency_name(self, currency_code: Optional[str] = None) -> str:
        """
        Get the full currency name

        Args:
            currency_code: Currency code (defaults to app default)

        Returns:
            Currency name string
        """
        if currency_code is None:
            currency_code = current_app.config.get("DEFAULT_CURRENCY", "GBP")

        currency_config = CurrencyConfig.get_currency_config(currency_code)
        return currency_config["name"]

    def is_supported_currency(self, currency_code: str) -> bool:
        """
        Check if a currency code is supported

        Args:
            currency_code: Currency code to check

        Returns:
            True if supported, False otherwise
        """
        return CurrencyConfig.is_supported(currency_code)

    def get_supported_currencies(self) -> dict:
        """
        Get all supported currencies with their details

        Returns:
            Dictionary of supported currencies
        """
        return CurrencyConfig.SUPPORTED_CURRENCIES.copy()

    def get_supported_currency_codes(self) -> list:
        """
        Get list of all supported currency codes

        Returns:
            List of currency codes
        """
        return CurrencyConfig.get_all_supported_codes()

    def parse_amount_from_string(
        self, amount_string: str, currency_code: Optional[str] = None
    ) -> float:
        """
        Parse numeric amount from a formatted currency string

        Args:
            amount_string: Formatted currency string (e.g., "£1,234.56")
            currency_code: Currency code for parsing context

        Returns:
            Numeric amount as float

        Raises:
            ValueError: If amount cannot be parsed
        """
        if currency_code is None:
            currency_code = current_app.config.get("DEFAULT_CURRENCY", "GBP")

        currency_config = CurrencyConfig.get_currency_config(currency_code)

        # Remove currency symbol
        clean_string = amount_string.replace(currency_config["symbol"], "")

        # Remove thousands separator
        clean_string = clean_string.replace(currency_config["thousands_separator"], "")

        # Convert to float
        try:
            return float(clean_string)
        except ValueError:
            raise ValueError(f"Cannot parse amount from: {amount_string}")

    def format_for_api(
        self, amount: Union[int, float, str], currency_code: Optional[str] = None
    ) -> dict:
        """
        Format currency data for API responses

        Args:
            amount: The monetary amount
            currency_code: Currency code

        Returns:
            Dictionary with formatted currency data
        """
        if currency_code is None:
            currency_code = current_app.config.get("DEFAULT_CURRENCY", "GBP")

        # Convert to float for consistency
        if isinstance(amount, str):
            try:
                amount = float(amount)
            except ValueError:
                raise ValueError(f"Invalid amount: {amount}")

        return {
            "amount": amount,
            "currency_code": currency_code,
            "formatted_amount": self.format_currency(amount, currency_code),
            "symbol": self.get_currency_symbol(currency_code),
            "name": self.get_currency_name(currency_code),
        }

    def validate_currency_code(self, currency_code: str) -> bool:
        """
        Validate currency code format and support

        Args:
            currency_code: Currency code to validate

        Returns:
            True if valid and supported
        """
        if not isinstance(currency_code, str):
            return False

        if len(currency_code) != 3:
            return False

        return self.is_supported_currency(currency_code)


# Global service instance
currency_service = CurrencyService()


# --- Dated exchange rates ----------------------------------------------------
# Rates live in the platform reference table ``exchange_rates``. Nothing here
# converts silently: a pair with no rate on file answers None, and a total that
# needs that pair answers "missing" rather than a sum.

from dataclasses import dataclass, field  # noqa: E402
from datetime import date  # noqa: E402
from decimal import Decimal  # noqa: E402
from typing import Iterable, List  # noqa: E402


def get_exchange_rate(from_currency: str, to_currency: str, on_date: Optional[date] = None):
    """Units of ``to_currency`` per one ``from_currency`` in effect on ``on_date``.

    The rate in effect is the latest one dated on or before ``on_date`` (today
    when omitted). A recorded opposite pair is inverted. Returns ``None`` when
    neither direction has a rate.
    """
    from app import db
    from app.models.cost_fact import ExchangeRate

    source, target = (from_currency or "").upper(), (to_currency or "").upper()
    if not source or not target:
        return None
    if source == target:
        return Decimal(1)
    on_date = on_date or date.today()

    def _latest(frm, to):
        return db.session.execute(
            db.select(ExchangeRate)
            .where(ExchangeRate.from_currency == frm, ExchangeRate.to_currency == to,
                   ExchangeRate.effective_date <= on_date)
            .order_by(ExchangeRate.effective_date.desc())
            .limit(1)
        ).scalar_one_or_none()

    direct = _latest(source, target)
    inverse = _latest(target, source)
    # Prefer the more recent of the two; a direct rate wins a tie.
    if direct is not None and (inverse is None or direct.effective_date >= inverse.effective_date):
        return Decimal(direct.rate)
    if inverse is not None and Decimal(inverse.rate) != 0:
        return Decimal(1) / Decimal(inverse.rate)
    return None


def record_exchange_rate(actor, from_currency: str, to_currency: str, rate, effective_date: date,
                         source: Optional[str] = None):
    """Write a dated rate. Only a platform administrator may; anyone else is refused."""
    from app import db
    from app.middleware.tenant_decorators import is_platform_admin
    from app.models.cost_fact import ExchangeRate

    if actor is None or not is_platform_admin(actor):
        raise PermissionError("exchange rates are written by the platform, not by an organisation")
    rate = Decimal(str(rate))
    if rate <= 0:
        raise ValueError("an exchange rate must be positive")
    frm, to = from_currency.upper(), to_currency.upper()
    row = db.session.execute(
        db.select(ExchangeRate).where(
            ExchangeRate.from_currency == frm, ExchangeRate.to_currency == to,
            ExchangeRate.effective_date == effective_date)
    ).scalar_one_or_none()
    if row is None:
        row = ExchangeRate(from_currency=frm, to_currency=to, effective_date=effective_date)
        db.session.add(row)
    row.rate = rate
    row.source = source
    db.session.flush()
    return row


@dataclass
class ConvertedTotal:
    """A total in ``currency``. ``amount`` is None when any fact lacked a rate."""

    currency: str
    amount: Optional[Decimal]
    missing: List[tuple] = field(default_factory=list)  # (from_currency, period date) with no rate

    @property
    def is_missing(self) -> bool:
        return self.amount is None


def convert_total(facts: Iterable, reporting_currency: str) -> ConvertedTotal:
    """Sum cost facts in ``reporting_currency`` at each fact's period rate.

    A fact in another currency is converted at the rate in effect at the end of
    its period. If any fact has no rate the whole total is missing (``amount``
    None) and ``missing`` names each pair; no partial sum is ever returned.
    """
    reporting = reporting_currency.upper()
    total = Decimal(0)
    missing: List[tuple] = []
    count = 0
    for fact in facts:
        count += 1
        as_of = fact.period_end or fact.period_start
        rate = get_exchange_rate(fact.currency, reporting, as_of)
        if rate is None:
            pair = (fact.currency, as_of)
            if pair not in missing:
                missing.append(pair)
            continue
        total += Decimal(fact.amount) * rate
    if missing or count == 0:
        return ConvertedTotal(currency=reporting, amount=None, missing=missing)
    return ConvertedTotal(currency=reporting, amount=total)
