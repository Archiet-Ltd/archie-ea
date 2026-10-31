"""Region and currency: how the organisation writes dates, numbers and amounts of money.

One card on the account page and one writer, registered on both account blueprints so the two cannot differ.
The values live on the organisation (``settings["locale"]`` and ``reporting_currency``); the formatting is
``app/utils/locale_format.py``.
"""
import logging
from datetime import datetime

from flask import flash, g, redirect, request, url_for
from flask_login import current_user

from app.middleware.tenant_decorators import is_platform_admin, org_admin_required
from app.utils import locale_format

_log = logging.getLogger(__name__)


def region_card():
    """What the Region and currency card needs, or None when the signed-in person may not change it."""
    from app.services.rbac_service import rbac_service

    org_id = getattr(g, "current_org_id", None)
    if not org_id or not (is_platform_admin(current_user) or rbac_service.is_org_admin(current_user, org_id)):
        return None
    return {
        "locales": locale_format.locale_options(), "currencies": list(locale_format.CURRENCIES),
        "locale": locale_format.current_locale(), "currency": locale_format.current_currency(),
        "examples": {"money": locale_format.fmt_money(1234.56),
                     "date": locale_format.fmt_date(datetime(2026, 10, 25))},
    }


def add_routes(bp) -> None:
    @bp.route("/manage/region", methods=["POST"])
    @org_admin_required
    def save_region():
        """Set the organisation's locale and reporting currency."""
        from app import db
        from app.models.organization import Organization

        locale = request.form.get("locale", "")
        currency = request.form.get("currency", "").upper()
        if locale not in locale_format.LOCALES or currency not in dict(locale_format.CURRENCIES):
            flash("Choose one of the listed languages and currencies.", "error")
            return redirect(url_for("account.manage"))
        org = db.session.get(Organization, g.current_org_id)
        try:
            org.settings = {**(org.settings or {}), "locale": locale}  # a new dict, so the JSON change is seen
            org.reporting_currency = currency
            db.session.commit()
            flash("Region and currency saved.", "success")
        except Exception as exc:
            _log.error("Failed to save region for organisation %s: %s", g.current_org_id, exc)
            db.session.rollback()
            flash("Could not save the region and currency. Please try again.", "error")
        return redirect(url_for("account.manage"))
