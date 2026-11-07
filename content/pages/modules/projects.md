---
wave: 5
page_family: module
module_label: "Projects"
endpoint: enterprise.work_packages
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-09, segment: S3}
capture_status: live
cta: plans
icon: folder-kanban
headline: Dates, status and cost for every project
summary: Each project tied to a wider initiative, with whether it is on schedule and what it costs against what was estimated. A missing cost shows as missing, never as zero.
who_for:
- A programme manager tracking delivery.
- A portfolio manager who needs cost shown honestly.
questions:
- q: Is this project on schedule?
  who: a programme manager
- q: What is it costing against the estimate?
  who: a portfolio manager
- q: Which initiative does this project belong to?
  who: a sponsor
- q: Which projects have no cost recorded?
  who: a finance lead
steps:
- name: Open the work packages
  text: Every project tied to its initiative.
- name: Read the status
  text: Dates and whether it is on schedule.
- name: Compare the cost
  text: Estimated against actual.
- name: Spot the gaps
  text: A project with no cost recorded says so plainly.
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

- [Is the programme on time and on budget?](/use-cases/programme-tracking)

## Related modules

- [Portfolio](/modules/portfolio)
- [Roadmap](/modules/roadmaps)
