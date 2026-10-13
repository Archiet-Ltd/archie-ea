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

## Case 2: the org admin cannot sign in either

Confirm the request out of band with the organisation's contact on record, and record the ticket number. Then work
out which of the two cases below applies from what the admin sees. Open `flask shell` on the production app for
any step that needs it.

### Case 2a: the identity provider works, but the admin is refused afterwards

The admin signs in at the identity provider and then sees "This account is linked to a different sign-in." Keep
single sign-on enabled.

1. Reset the admin's sign-in link:

   ```python
   from app.services.sso_service import SSOService

   SSOService().reset_member_sign_in_link(<org id>, <user id>, actor_id=None, reason="support ticket <n>")
   ```

2. The admin signs in through the identity provider again. That sign-in connects their account.
3. The admin repairs any other member through Case 1.

### Case 2b: single sign-on itself is broken for the organisation

Nobody can complete a single sign-on sign-in, for example because the identity provider was replaced, its
certificate changed, the temporary-identifier message appears, or the identity provider's page fails. The admin
needs another way in to fix the SSO settings page. Never disable single sign-on while the admin has no password:
with single sign-on disabled, an account without a password cannot sign in at all.

1. Check whether the admin has a password:

   ```python
   from app import db
   from app.models.user import User

   user = db.session.get(User, <user id>)
   user.password_hash is not None
   ```

   If this is `True`, go to step 4.
2. Give the admin a password that nobody knows, while single sign-on is still enabled. Do not print or record the
   value:

   ```python
   import secrets
   from app.modules.admin.services.admin_user_service import AdminUserService

   AdminUserService.set_user_password(user, secrets.token_urlsafe(32))
   ```

   While single sign-on is enabled this password does not change how the admin signs in.
3. Ask the admin to use "Forgot password?" on the sign-in page with their own address and set their own password
   from the e-mailed link. Then confirm the step-1 check again, after `db.session.refresh(user)`. This needs
   outgoing e-mail; if the server cannot send e-mail, stop here and keep single sign-on enabled.
4. Disable the organisation's single sign-on configuration:

   ```python
   from app import db
   from app.models.sso_config import SSOConfig

   config = SSOConfig.query.filter_by(organization_id=<org id>).first()
   config.enabled = False
   db.session.commit()
   ```

   Members with a password can now sign in with it.
5. The admin signs in with their password, fixes the configuration on the SSO settings page, and confirms it with
   "Test sign-in".
6. If the identity provider was replaced, or the organisation moved between OIDC and SAML, the admin uses "Reset
   single sign-on" on their own row on the Team page before going on.
7. The admin re-enables single sign-on from the SSO settings page, and repairs any other member through Case 1.

The admin keeps their password. While single sign-on is enabled it is not used at the sign-in form, and it is the
way back in if this happens again.

## What never to do

- Never edit `external_id` with SQL by hand.
- Never point a link at a value you typed.

Clearing the link and letting the next sign-in connect it again is the only supported path.
