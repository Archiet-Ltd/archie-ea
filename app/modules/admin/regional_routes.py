"""Regional settings: the locale and currency an organisation's dates, numbers and money are written in.

Blueprint: regional_bp | URL prefix: /admin | org admin or platform admin.

The two values are stored on the organisation (``locale`` and ``reporting_currency``) and read by the one
formatter, ``app/utils/locale_format.py``, on every screen.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user, login_required

from app import db
from app.middleware.tenant_decorators import require_org_or_platform_admin
from app.models.organization import Organization
from app.utils.locale_format import SUPPORTED_LOCALES, fmt_date, fmt_money, normalise_locale

regional_bp = Blueprint("regional", __name__)


def _organisation():
    org_id = getattr(current_user, "organization_id", None)
    if org_id is None:
        abort(403)
    require_org_or_platform_admin(org_id)
    org = db.session.get(Organization, org_id)
    if org is None:
        abort(404)
    return org


def _example(locale, currency):
    import datetime as _dt

    return (
        fmt_date(_dt.date(2026, 10, 25), "medium", locale),
        fmt_money(1234.56, currency, locale=locale),
    )


@regional_bp.route("/regional", methods=["GET", "POST"])
@login_required
def regional():
    from config import CurrencyConfig

    org = _organisation()
    error = None
    if request.method == "POST":
        locale = (request.form.get("locale") or "").strip()
        currency = (request.form.get("currency") or "").strip().upper()
        if locale not in SUPPORTED_LOCALES or not CurrencyConfig.is_supported(currency):
            error = _("Choose a language and region and a currency from the lists.")
        else:
            org.locale = locale
            org.reporting_currency = currency
            db.session.commit()
            flash(_("Regional settings saved."), "success")
            return redirect(url_for("regional.regional"))
    locale = normalise_locale(org.locale)
    currency = (org.reporting_currency or CurrencyConfig.DEFAULT_CURRENCY).upper()
    date_example, money_example = _example(locale, currency)
    return render_template(
        "admin/regional.html",
        locales=SUPPORTED_LOCALES,
        currencies=[(code, cfg["name"]) for code, cfg in CurrencyConfig.SUPPORTED_CURRENCIES.items()],
        locale=locale,
        currency=currency,
        date_example=date_example,
        money_example=money_example,
        error=error,
    ), (400 if error else 200)
