---
page_family: module
module_label: "Applications"
endpoint: unified_applications.application_list
source: app/modules/modules_directory/routes.py + app/utils/role_access.py (_link calls, read 2026-09-23)
state: on_main
answers_use_cases:
  - {id: UC-S1-05, segment: S1}
  - {id: UC-S2-03, segment: S2}
  - {id: UC-S2-07, segment: S2}
  - {id: UC-S3-01, segment: S3}
  - {id: UC-S4-05, segment: S4}
capture_status: awaiting_capture
cta: plans
---

# Applications

*The single list of every application Entelim knows about, where it came from, who owns it, and what
it costs.*

## What application portfolio management is

An application portfolio is the complete list of every application your organisation runs, together
with four things about each one: who owns it, what it costs, what stage of its life it's in, and how
much risk it carries. Application portfolio management is keeping that list true and using it to
make decisions — what to consolidate, what to renew, and what to retire before it becomes a problem
instead of after.

Most organisations never get past the first part. The list exists, but it's split across a
spreadsheet, three people's memory, and whatever the last vendor renewal email said, and it goes
stale the week after anyone updates it. The cost of that gap is specific and findable: two teams
paying for software that does the same job, applications nobody has named an owner for, and
contracts or end-of-life dates that arrive as a surprise because nothing was watching them. It also
surfaces at the worst possible moment — when an audit, a security review, or an acquirer asks for the
current system landscape, and the honest answer is a diagram drawn months ago by someone who has
since left.

Done well, a portfolio holds up under three questions asked at once: who is accountable for this
application, what does it actually cost across licensing, maintenance, infrastructure and support,
and what happens if it goes away. That means ownership and cost attached to every application, not
just the ones someone remembered to document; renewal and retirement dates visible before they
become urgent; and duplicate spend visible on its own, not found by accident during a budget review.

## What this module does

Entelim's Applications module is where that list lives for real, not hypothetically. Every
application in your model — typed in or imported (spreadsheet, CSV, JSON, or an Archi / Open
Exchange model) — lives in one list, not scattered across a spreadsheet and three people's heads. It
links straight into the questions that read it: what you're paying for twice, and what breaks if
this application fails.

## Where you'll meet it

- [Show an investor what we run, in an afternoon](/startups/show-what-we-run)
- [What are we paying for twice?](/scale-up/duplicate-spend)
- [Give the acquirer a current architecture picture we didn't draw by hand](/scale-up/twin-map)
- [Import our existing Archi or Open Exchange model](/enterprise-architecture/import)
- [Which contracts renew soon, and what depends on them?](/services-ops/contract-renewals)

## Related modules

- [Vendors](/modules/vendors)
- [Rationalization](/modules/rationalization)
- [Procurement](/modules/procurement)
