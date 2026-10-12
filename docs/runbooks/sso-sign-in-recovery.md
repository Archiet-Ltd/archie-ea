# Single sign-on sign-in recovery

For support and the operator. Use this when a member of an organisation that signs in through its own identity
provider can no longer sign in.

## Symptom

The member sees one of these messages after signing in at their organisation's identity provider:

- "This account is linked to a different sign-in. Contact your administrator."
- "Your identity provider sent a temporary sign-in identifier that changes every time. Ask your administrator to have
  it send a persistent NameID or a subject-id attribute."

The first means the account is connected to a sign-in identity that the identity provider no longer sends, for
example after the organisation changed identity provider, moved from OIDC to SAML, or renamed the member at the
identity provider. The second means the identity provider is configured to send a temporary identifier.

## Case 1: the org admin can sign in

1. For the temporary-identifier message, the org admin first changes the identity provider's NameID setting to a
   persistent NameID, or adds a subject-id or pairwise-id attribute, and confirms it with "Test sign-in" on the SSO
   settings page.
2. The org admin opens Team, finds the member, and uses "Reset single sign-on" on that member's row.
3. The member signs in through the organisation's identity provider again. That sign-in connects their account.

Every reset is recorded in the audit log as `sso_link_reset`, with the organisation, the admin who made it and the
reason.

## Case 2: the org admin is locked out too

1. Confirm the request out of band with the organisation's contact on record, and record the ticket number.
2. Open `flask shell` on the production app.
3. Disable the organisation's SSO configuration:

   ```python
   from app import db
   from app.models.sso_config import SSOConfig

   config = SSOConfig.query.filter_by(organization_id=<org id>).first()
   config.enabled = False
   db.session.commit()
   ```

   Members with a password can now sign in with it.
4. If the org admin has no password, reset the org admin's sign-in link:

   ```python
   from app.services.sso_service import SSOService

   SSOService().reset_member_sign_in_link(<org id>, <user id>, actor_id=None, reason="support ticket <n>")
   ```

5. The org admin fixes the identity provider, tests it with "Test sign-in" on the SSO settings page, and re-enables
   SSO from the settings page.

## What never to do

- Never edit `external_id` with SQL by hand.
- Never point a link at a value you typed.

Clearing the link and letting the next sign-in connect it again is the only supported path.
