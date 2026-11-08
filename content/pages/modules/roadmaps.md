---
wave: 3
page_family: module
module_label: "Roadmaps"
endpoint: main.capability_roadmap
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-13, segment: S3}
capture_status: live
cta: plans
icon: milestone
headline: One page for stages, gaps and the projects that close them
summary: The stages of your target-state plan, the gap register between where you are and where you are going, and the projects doing the closing.
who_for:
- An enterprise architect who owns the path to the target state.
- A CTO who needs one picture for the board.
questions:
- q: What is the path from today's estate to the target state?
  who: an enterprise architect
- q: Which gaps does each stage close?
  who: a CTO
- q: Which projects are doing the closing?
  who: a programme manager
- q: What is still unplanned?
  who: a portfolio manager
steps:
- name: Define the stages
  text: The plateaus your plan actually defines.
- name: Read the gaps
  text: The gap register between stages.
- name: See the projects
  text: The projects that close each gap.
- name: Share one page
  text: One view instead of three that drift apart.
---

# Roadmaps

*Stages, the gap timeline between them, and the projects that close each gap — on one page, not
scattered across a deck and a spreadsheet.*

## What this module does

A roadmap here isn't a list of dates on a slide. It's the stages your target-state plan actually
defines, the gap register between where you are and where you're going, and the projects doing the
closing — unified on one page instead of three separate views that drift out of sync with each
other.

## Where you'll meet it

- [Show the path from today's estate to the target state](/use-cases/target-state-roadmap)

## Related modules

- [Gap Analysis](/modules/gap-analysis)
- [Projects](/modules/projects)
- [Portfolio](/modules/portfolio)
