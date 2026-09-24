---
page_family: module
module_label: "Projects"
endpoint: enterprise.work_packages
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-09, segment: S3}
capture_status: awaiting_capture
---

# Projects

*Dates, status, and estimated versus actual cost for every project — with cost shown as genuinely
absent when it hasn't been entered, not defaulted to zero.*

## What this module does

Every project tied to a wider initiative, with what it's meant to touch, whether it's on schedule,
and what it's actually costing against what was estimated. A project with no cost recorded shows
plainly that nothing has been entered, never a silent zero that makes an underfunded initiative look
on-budget.

## Where you'll meet it

- [Is the programme on time and on budget?](/enterprise-architecture/programme-tracking)

## Related modules

- [Portfolio](/modules/portfolio)
- [Roadmap](/modules/roadmaps)
