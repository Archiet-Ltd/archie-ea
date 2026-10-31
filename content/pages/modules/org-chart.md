---
wave: 5
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
icon: users
---

# Org Chart & RACI

*The structure of your organisation as modelled Business Actors, and a RACI matrix that maps people
and roles to your capabilities.*

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

Entelim's Org Chart & RACI module builds the structure from the organisation's own modelled
data, not a hand-maintained slide deck. The org chart is a tree of Business Actors — departments,
teams, business units and partners — joined by composition and aggregation relationships. Where no
such relationships exist yet, actors are listed grouped by type instead, and that grouped list
shows each actor's headcount where one is recorded; the tree shows names and types. Neither shows
individual reporting lines.

The Enterprise RACI is a matrix with actors, roles and users down the side and your top-level
capabilities across the top. Each cell holds Responsible, Accountable, Consulted or Informed, and
you can add a stakeholder, or create a new Business Actor, from the matrix itself. A cell nobody has
filled stays empty; the matrix does not flag it as a gap or count the capabilities without an
accountable owner. A third page, Workforce Transition, tracks role changes behind a transformation.

Owners of individual systems are recorded in each application's Ownership section, and capabilities
with no recorded owner are listed on their own page for CTOs and portfolio managers.

## Where you'll meet it

- [Which systems have no owner, and which owner is a single point of failure?](/use-cases/systems-with-no-owner)
- [Which of my people is a single point of failure?](/use-cases/key-person-risk)

## Related modules

- [Applications](/modules/applications)
