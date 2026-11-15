---
wave: 5
page_family: connector
connector_group: "sign-in"
state: held
capture_status: awaiting_capture
cta: plans
description: "Entelim signs people in with your own identity provider over OpenID Connect or SAML 2.0, set up by your admin and included from the Team plan."
source: app/services/sso_service.py, app/services/saml_service.py, app/modules/auth/sso_routes.py,
  app/models/sso_config.py, app/templates/public/pricing.html, PR 527 (feat/seo-geo-nav-plumbing,
  app/services/public_pages.py CONNECTOR_GROUPS), read 11 October 2026
---

# Single sign-on for enterprise architecture software

Entelim supports SAML single sign-on, alongside OpenID Connect, so your people sign in with the
identity your organisation already manages rather than a separate Entelim password. Your
administrator sets this up once, on the single sign-on settings screen, and from then on anyone
whose email address matches the domain you enter is sent straight to your identity provider at
login. Single sign-on is included from the Team plan, with SAML 2.0 added on Enterprise.

## OpenID Connect and SAML, in plain terms

OpenID Connect is a sign-in standard built on OAuth: your identity provider confirms who someone is
and hands Entelim a signed token saying so, without Entelim ever seeing a password. SAML 2.0 does
the same job in an older, still widely used format: a signed XML message in place of a token. An
organisation configures one or the other, and Entelim verifies every response before anyone is
signed in.

## What it does

| Flow | Direction | Trigger |
|---|---|---|
| OpenID Connect sign-in | Your identity provider confirms the person; Entelim creates or continues the matching account | Automatic, whenever someone with a matching email address signs in |
| SAML 2.0 sign-in | The same confirmation, carried in a signed SAML assertion rather than an OIDC token | Automatic, same trigger |
| Test sign-in | The same round trip against your identity provider, without creating or signing in any account | Manual, run by an administrator from the single sign-on settings screen |

## Who uses it

Anyone in your organisation with an email address on a domain your administrator has entered is
sent to your identity provider automatically; they do nothing differently to sign in, and there is
no separate Entelim password for them to set or remember. Setting up and switching on single
sign-on is an administrator's job, done once on the settings screen. An administrator who signs in
this way still completes a one-time code afterwards, the same as after a password sign-in.
[Security architects](/roles/security-architects) typically own this screen and the certificate
behind it.

## Set it up

1. Open the single sign-on settings screen and choose OpenID Connect or SAML 2.0.
2. Enter the email domain, or domains, that should be sent to your identity provider.
3. For OpenID Connect, enter your identity provider's discovery address and the client ID and
   secret it issued. For SAML 2.0, enter your identity provider's sign-in address and its signing
   certificate instead.
4. Register the callback address Entelim shows you with your identity provider.
5. Run a test sign-in. It completes a real round trip against your identity provider and shows you
   what came back, without creating or signing in any account, so you can check it before anyone
   else is affected.
6. Switch single sign-on on and save.

## What you see afterwards

Once single sign-on is on, signing in with a matching email address goes straight to your identity
provider; there is no Entelim password in that path to remember or reset. The first time someone
signs in this way, Entelim creates their account automatically inside your organisation, with their
name taken from what your identity provider sends. If that email address already has an account in
your organisation, signing in this way continues under the same account rather than creating a
second one, and nothing else about what they see in Entelim changes because of how they signed in.

## Security and permissions

Only an organisation administrator can see or change single sign-on settings. Your identity
provider's signing certificate is entered by that administrator, out of band, and every SAML
response is checked against the certificate you entered, never against a certificate carried inside
the response itself, so a forged response cannot supply its own trust anchor. One email domain can
belong to only one organisation's configuration: if an administrator tries to add a domain another
organisation has already claimed, the save is refused. If an email address already belongs to an
account in a different organisation, a sign-in attempt through your configuration is refused rather
than taking that account over. Read more about how Entelim protects your data on
[Security](/security).

## Limits

An organisation configures one identity provider at a time, OpenID Connect or SAML 2.0, not both
together. Entelim does not read group membership from your identity provider and does not remove
anyone automatically when they leave it; remove their access directly in Entelim instead. Single
sign-on only covers the email domains your administrator adds: anyone on a different domain signs
in with an Entelim password as usual.

## Frequently asked questions

### Do you support SAML?

Yes. Entelim verifies every SAML 2.0 response against the signing certificate your administrator
enters, and supports OpenID Connect as the other option.

### Which identity providers work?

Any identity provider that speaks standard OpenID Connect or SAML 2.0. Entelim talks to the
protocol rather than to a particular vendor, so there is nothing provider-specific to install.

### Can we enforce SSO for everyone?

Anyone whose email address is on a domain your administrator has added is sent to your identity
provider automatically, with no password offered instead. Add every domain your people sign in with
to cover everyone in your organisation.

### What happens to existing accounts?

An existing account under the same email address in your organisation continues under single
sign-on rather than being duplicated. An email address that already belongs to a different
organisation's account cannot sign in through your configuration; that attempt is refused.

### Which plan includes SSO?

OpenID Connect single sign-on is included from the Team plan. SAML 2.0 is included on Enterprise.
[See Entelim pricing](/pricing).

## Related connectors and modules

- [Connectors](/connectors)
- [Security](/security)
- [Security architects](/roles/security-architects)
- [Team annual plan with onboarding](/team-annual-onboarding)

## Plan

Single sign-on is included from the Team plan, with SAML 2.0 added on Enterprise.
[See Entelim pricing](/pricing).
