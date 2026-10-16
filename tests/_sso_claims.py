"""Test helper: record an organisation's proven claim on its single sign-on email domains."""

import uuid
from datetime import datetime


def verify_domains(db_session, org, email_domain):
    from app.models.sso_config import split_email_domains
    from app.models.sso_domain_claim import SSODomainClaim

    for domain in split_email_domains(email_domain):
        db_session.add(
            SSODomainClaim(
                organization_id=org.id, domain=domain, token=uuid.uuid4().hex, verified_at=datetime.utcnow()
            )
        )
    db_session.flush()
