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

A database model built to hold organisation-specific data carries a marker that tells Entelim's data
layer to filter it. Every read, update and insert against that data is scoped to the signed-in
user's own organisation automatically — not a check a developer has to remember to add to each
query.

An automated check also runs on every commit. It stops a new database read or write from shipping
if it would let one organisation's query reach another's data without going through that automatic
filter. Once a gap like that is closed, the check won't let it reopen: the bar it holds the code to
only ever gets tighter.

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
