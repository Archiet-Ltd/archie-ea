---
page_family: module
module_label: "Risk Register"
endpoint: risk.risk_register
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S2-08, segment: S2}
  - {id: UC-S3-08, segment: S3}
capture_status: awaiting_capture
---

# Risk Register

*Every recorded risk, traced over derived connections to show its real blast radius — not just the
one component it was logged against.*

## What this module does

Ask which risks matter, and the answer doesn't stop at the element a risk was originally logged
against — it follows the same connection chain that answers what breaks, so a risk shows up
everywhere its impact would actually reach. Enterprise teams get a compliance-frameworks filter
alongside it for control-gap questions.

## Where you'll meet it

- [Which risks sit on our revenue-critical path?](/scale-up/risk-blast-radius)
- [Which risks and control gaps touch this goal?](/enterprise-architecture/risk-and-controls)

## Related modules

- [Compliance Frameworks](/modules/compliance-frameworks)
- [Applications](/modules/applications)
