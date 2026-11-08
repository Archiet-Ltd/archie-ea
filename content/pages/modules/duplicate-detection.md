---
wave: 5
page_family: module
module_label: "Duplicate Detection"
endpoint: unified_duplicate.simple_dashboard
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S2-03, segment: S2}
capture_status: live
cta: plans
icon: copy
headline: Find two applications doing the same job before you go looking
summary: Scans your application list for functional overlap and surfaces candidates for the consolidation list.
who_for:
- A CIO or portfolio manager who suspects the same job is paid for twice.
- An enterprise architect preparing a consolidation.
questions:
- q: What are we paying for twice?
  who: a CIO before a budget round
- q: Which applications overlap in function?
  who: an enterprise architect
- q: Which candidate should go on the consolidation list?
  who: a portfolio manager
- q: Where do I act on what was found?
  who: an application owner
steps:
- name: Start from the inventory
  text: Detection reads the applications already in the portfolio.
- name: Run detection
  text: Candidates for overlap are surfaced automatically.
- name: Review the candidates
  text: Decide which pairs are really the same job.
- name: Act in Rationalization
  text: Detection finds; Rationalization is where you decide.
---

# Duplicate Detection

*The engine behind Rationalization — finds two applications doing the same job before you go looking
for them.*

## What this module does

Scans your application list for functional overlap and surfaces candidates automatically, feeding
straight into the Rationalization dashboard and a consolidation list. This is the detection step;
Rationalization is where you act on what it finds.

## Where you'll meet it

- [What are we paying for twice?](/use-cases/duplicate-software-spend)

## Related modules

- [Rationalization](/modules/rationalization)
- [Applications](/modules/applications)
