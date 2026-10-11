---
page_family: site
title: "Security"
page_role: "The security page — real, verifiable practices only, no certification claims."
source: app/services/public_pages.py (bleach sanitisation), app/models/user.py (password hashing),
  scripts/verify.py (tenant-scoping and raw-sql-tenancy gates), SECURITY.md
---

# Security

## Your data, your infrastructure, if you choose

Entelim is licensed under AGPL-3.0. Self-hosted, your data never leaves your own infrastructure
unless you connect something that does.

## Tenant isolation is enforced, not just documented

Every organisation's data is scoped to that organisation. Database queries that touch
organisation-owned data without an organisation check are blocked automatically before a change can
ship, by an automated check that runs on every commit — not a policy someone has to remember to
follow.

## Passwords and sessions

Passwords are salted and hashed; Entelim never stores or logs a plain-text password. Sign-in uses a
server-side session cookie; an optional "remember me" cookie keeps you signed in between visits, and
is rejected if it's tampered with.

## Input handling

Public content pages are rendered from Markdown and sanitised before they reach the browser: only a
fixed, known-safe set of HTML tags and attributes is allowed through, so a page can never inject a
script or an unsafe link. Forms are protected against cross-site request forgery, and sign-in and
sign-up are rate-limited against automated abuse.

## Reporting a problem

If you find a security issue, please report it privately through [contact](/contact) rather than
disclosing it publicly, so it can be fixed first.

## What we don't claim

Entelim does not currently hold a third-party security certification (SOC 2, ISO 27001, or
similar). If your organisation needs one as part of a commercial or hosted agreement, raise it when
you [get in touch](/contact).
