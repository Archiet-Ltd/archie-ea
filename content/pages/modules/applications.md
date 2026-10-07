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
capture_status: live
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

## What is application dependency mapping?

An application dependency is a connection where one application, service, or piece of infrastructure
relies on another to function — for data, for authentication, for a shared capability it doesn't own
itself. Application dependency mapping is the practice of recording those relationships in one place,
so that for any application you can see what it depends on and what depends on it, instead of
reconstructing the answer from memory every time someone asks.

It matters because the question usually surfaces at the worst moment: an outage, where the fix
depends on knowing what else touches the failing system; a migration or decommission, where retiring
one application safely means knowing everything that still relies on it; or an auditor asking for the
current dependency picture rather than the one somebody drew by hand last year. A mapping tool answers
the question by holding those relationships as structured data instead of a diagram, so the view it
produces — what feeds this, what this feeds, how far the effect reaches — can be explored and stays
current as the model changes, rather than going stale the day after it's drawn.

In Entelim, that mapping runs through the same architecture model the rest of the module uses. Once an
application is linked to its ArchiMate element, its fact sheet shows what it depends on and what
depends on it, with a link into an interactive graph centred on that element — the blast radius you'd
otherwise only find out by breaking something. The same dependency model is what a DORA Register of
Information actually needs behind it: building one for an audit and building one you use day to day
turn out to be the same piece of work.

## What this module does

Entelim's Applications module is where that list lives for real, not hypothetically. Every
application in your model — typed in or imported (spreadsheet, CSV, JSON, or an Archi / Open
Exchange model) — lives in one list, not scattered across a spreadsheet and three people's heads. It
links straight into the questions that read it: what you're paying for twice, and what breaks if
this application fails.

## Where you'll meet it

- [Show an investor what we run, in an afternoon](/use-cases/show-investors-what-we-run)
- [What are we paying for twice?](/use-cases/duplicate-software-spend)
- [Give the acquirer a current architecture picture we didn't draw by hand](/use-cases/architecture-map-for-due-diligence)
- [Import our existing Archi or Open Exchange model](/use-cases/import-archimate-model)
- [Which contracts renew soon, and what depends on them?](/use-cases/contract-renewals)

## Related modules

- [Vendors](/modules/vendors)
- [Rationalization](/modules/rationalization)
- [Procurement](/modules/procurement)
