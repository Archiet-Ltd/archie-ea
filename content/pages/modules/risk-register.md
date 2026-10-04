---
page_family: module
module_label: "Risk Register"
endpoint: risk.risk_register
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S2-08, segment: S2}
  - {id: UC-S3-08, segment: S3}
capture_status: live
cta: plans
---

# Risk Register

*Every recorded risk, traced over derived connections to show its real blast radius — not just the
one component it was logged against.*

## What is enterprise risk management?

Enterprise risk management is the practice of recording, scoring and tracking every risk that
could get in the way of an organisation's objectives in one place, instead of each team keeping
its own list. The COSO and ISO 31000 frameworks most risk and compliance functions already work
from describe the same basic loop: log the risk, score how likely it is and how much damage it
would do, assign someone to own it, and score it again once a mitigation is in place — the
inherent risk before that work starts, the residual risk after.

A buyer searching for enterprise risk management software is typically looking for whatever makes
that loop survive contact with a real organisation: one register instead of several spreadsheets,
a scoring method that means the same thing wherever it's applied, a record of how a score moved
over time rather than just its latest value, and a way to see at a glance which risks carry the
most exposure right now.

What a static register can't do is keep up with the fact that a risk rarely stays confined to the
one thing it was originally logged against — the system, process or programme it threatens shifts
as the architecture around it changes, and a document frozen at the moment it was written has no
way to notice.

## What this module does

Entelim's Risk Register module runs that register in software, not a spreadsheet: each risk
carries a single score for how likely it is and how much damage it would do, a status, an owner
and a mitigation plan. Ask which risks matter, and the answer doesn't stop at the element a risk
was originally logged against — it follows the same connection chain that answers what breaks, so a
risk shows up everywhere its impact would actually reach.

## Where you'll meet it

- [Which risks sit on our revenue-critical path?](/scale-up/risk-blast-radius)
- [Which risks touch this part of the architecture?](/enterprise-architecture/risk-and-controls)

## Related modules

- [Compliance Frameworks](/modules/compliance-frameworks)
- [Applications](/modules/applications)
