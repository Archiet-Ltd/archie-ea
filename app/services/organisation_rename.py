"""The one way an organisation's name changes.

The platform administrator's edit, the confirmation of the company name read from a website, and the
organisation administrator's own settings form all come through ``rename_organisation``. The slug is
left alone so links and bookmarks keep working. Every screen reads ``Organization.name``; the copies
made of it elsewhere (public signals stored under the stand-in name, the payment customer) are
cleaned or updated here.
"""

import logging

from app import db

logger = logging.getLogger(__name__)

NAME_MAX = 200
BLANK_NAME = "Enter a name for the workspace."
TOO_LONG = "Use %d characters or fewer for the workspace name." % NAME_MAX


class InvalidOrganisationName(ValueError):
    """The name cannot be used; the message says what to fix, in words fit for the field."""


def clean_organisation_name(name):
    """The trimmed name, or InvalidOrganisationName when it is blank or longer than 200 characters."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise InvalidOrganisationName(BLANK_NAME)
    if len(cleaned) > NAME_MAX:
        raise InvalidOrganisationName(TOO_LONG)
    return cleaned


def _update_payment_customer(org, name):
    """Keep the name on the payment provider's customer record in step. Never blocks a rename."""
    try:
        from app.models.subscription import Subscription

        sub = Subscription.query.filter_by(organization_id=org.id).first()
        customer_id = getattr(sub, "stripe_customer_id", None)
        if not customer_id:
            return
        from app.services import billing_service

        api = billing_service._api()
        billing_service._call(api.Customer.modify, customer_id, name=name)
    except Exception as exc:  # noqa: BLE001 - the provider being absent or slow must not undo a rename
        logger.warning("Payment customer name not updated for organisation %s: %s", org.id, exc)


def rename_organisation(org, name, by_user=None):
    """Give ``org`` its new name, tidy what was stored under the old one and record the change.

    The caller commits. Returns the stored name; raises InvalidOrganisationName for a blank or too long name.
    """
    from flask import has_request_context, request

    from app.modules.organization.public_signals import tidy
    from app.shared.services.audit_service import audit_action

    new_name = clean_organisation_name(name)
    old_name = org.name
    org.name = new_name
    tidy(org.id, org)
    if old_name != new_name:
        _update_payment_customer(org, new_name)
        audit_action(
            action="organisation_renamed",
            entity_type="organizations",
            entity_id=org.id,
            entity_name=new_name,
            user_id=getattr(by_user, "id", None),
            user_email=getattr(by_user, "email", None),
            ip_address=request.remote_addr if has_request_context() else None,
            description='Workspace renamed from "%s" to "%s"' % (old_name, new_name),
            old_values={"name": old_name},
            new_values={"name": new_name},
        )
    return new_name
