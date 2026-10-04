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

## What this module does

Every ownership record in one place: who's accountable for what, by organisation unit, with gaps
shown as genuinely missing rather than silently assigned to someone.

## Where you'll meet it

- [Which systems have no owner, and which owner is a single point of failure?](/scale-up/no-owner)
- [Which of my people is a single point of failure?](/services-ops/key-person-risk)

## Related modules

- [Applications](/modules/applications)
