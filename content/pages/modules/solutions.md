---
page_family: module
module_label: "Solutions"
endpoint: solution_design.list_solutions
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-07, segment: S3}
capture_status: awaiting_capture
---

# Solutions

*Every solution design in one list — the record the review board actually reviews.*

## What this module does

Solutions are what the Architecture Review Board evaluates: proposed changes, with their design and
context, tracked from proposal through to decision.

## Where you'll meet it

- [Take a change through the review board with the impact evidence attached](/enterprise-architecture/review-board)

## Related modules

- [Architecture Review Board](/modules/arb)
