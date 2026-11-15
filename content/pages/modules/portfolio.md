---
wave: 5
page_family: module
module_label: "Portfolio"
endpoint: portfolio.index
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-04, segment: S3}
  - {id: UC-S3-09, segment: S3}
capture_status: live
cta: plans
icon: layers
headline: Every transformation initiative, with budget, spend and health
summary: One list of initiatives with phase, health, approved budget, spend, variance, progress and sponsor, and a page for the detail behind each figure.
who_for:
- A portfolio manager who reports on the whole change portfolio.
- A programme sponsor who needs the detail behind one initiative.
questions:
- q: Is the programme on time and on budget?
  who: a portfolio manager
- q: Which initiatives serve no strategic objective?
  who: a CTO
- q: What benefits has this initiative delivered against its target?
  who: a programme sponsor
- q: What demand did this initiative come from?
  who: an enterprise architect
steps:
- name: Read the list
  text: Phase, health, budget, spend, variance, progress and sponsor, with totals.
- name: Filter
  text: Show the initiatives that serve no strategic objective.
- name: Open an initiative
  text: Benefits against baseline and target, assumptions, goals and work packages.
- name: Work the intake queue
  text: The module also holds the demand intake queue.
---

# Portfolio

*Every transformation initiative with its budget, spend, health and benefits in one list, and a
page per initiative for the detail behind each figure.*

## What this module does

The list shows each initiative's phase, health, approved budget, spend to date, variance against
budget, percentage complete, benefit count and sponsor, with totals across the portfolio. A figure
nobody recorded shows as a dash, never as zero. You can filter to the initiatives that serve no
strategic objective.

Open an initiative to see its benefits against baseline and target, record measurements, track
assumptions and their exposure, link the goals it serves, and see the work packages delivering it
with their percentage complete, plus the demand it came from. The module also holds the demand
intake queue. Work packages show progress only; cost and dates are not shown there.

## Where you'll meet it

- [Is the programme on time and on budget?](/use-cases/programme-tracking)
- [Which value streams are at risk?](/use-cases/value-streams-at-risk) (a separate page, not part of this module)

## Related modules

- [Business Case](/modules/business-case)
- [Investment Analysis](/modules/investment-analysis)
- [Roadmaps](/modules/roadmaps)
