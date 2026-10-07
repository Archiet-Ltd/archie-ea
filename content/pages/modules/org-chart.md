---
page_family: module
module_label: "Org Chart & RACI"
endpoint: organization.index
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S2-02, segment: S2}
  - {id: UC-S4-04, segment: S4}
capture_status: live
cta: plans
---

# Org Chart & RACI

*Who owns what, honestly — including the systems that don't have an owner recorded, shown as
missing, not silently assigned to someone.*

## What is org chart software?

An org chart is the diagram of an organisation's reporting lines and structure: who reports to
whom, grouped by department or business unit, usually with headcount and role attached. Most
organisations still keep this in a slide deck or a spreadsheet that one person updates by hand,
which means it's out of date the moment a reporting line changes and nobody else can safely add
to it.

Org chart software is built to fix that: a live record of the structure instead of a static slide,
generated from the organisation's own data rather than redrawn by hand each time something
changes, and most commonly paired with a RACI matrix — the standard governance tool that maps each
person or role against the processes they're Responsible for, Accountable for, Consulted on or
Informed about, since "who reports to whom" and "who's accountable for what" are related but
different questions.

The harder, more honest version of both questions is what happens when a box in the chart or a
cell in the matrix has nobody in it. A chart that quietly assigns an owner where none really
exists, or a RACI cell left blank because nobody checked, hides exactly the gap an organisation
most needs to see: the system, process or team with no one accountable for it at all.

## What this module does

Entelim's Org Chart & RACI module builds that structure from the organisation's own actor and
relationship data, not a hand-maintained slide deck. Every ownership record in one place: who's
accountable for what, by organisation unit, with gaps shown as genuinely missing rather than
silently assigned to someone.

## Where you'll meet it

- [Which systems have no owner, and which owner is a single point of failure?](/scale-up/no-owner)
- [Which of my people is a single point of failure?](/services-ops/key-person-risk)

## Related modules

- [Applications](/modules/applications)
