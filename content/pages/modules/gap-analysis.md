---
wave: 5
page_family: module
module_label: "Gap Analysis"
endpoint: enterprise.gap_analysis
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-13, segment: S3}
capture_status: live
cta: plans
icon: git-compare
headline: Keep one register of what is missing between today and the target
summary: Each gap named, tracked and linked to the projects closing it. The roadmap's gap timeline is built from this register.
who_for:
- An enterprise architect who owns the target state.
- A programme manager who needs to know which projects close which gap.
questions:
- q: What is missing between today's estate and the target state?
  who: an enterprise architect
- q: Which project closes this gap?
  who: a programme manager
- q: Which gaps are still open?
  who: a CTO
- q: Do the roadmap and the register agree?
  who: a tech lead
steps:
- name: Record the gaps
  text: Name each gap between today and the target.
- name: Link the work
  text: Connect each gap to the projects closing it.
- name: Track it
  text: See which gaps are open and which are closed.
- name: Read the roadmap
  text: The roadmap reads the same register, so the two never disagree.
---

# Gap Analysis

*The register of what's missing between today's estate and the target state — the record the
Roadmaps page's gap timeline is built from.*

## What this module does

Where Roadmaps shows the stage-by-stage picture, Gap Analysis is the underlying register: each gap
named, tracked, and linked to the projects closing it. One source both the standard roadmap view and
the tech-lead view read from, so they never disagree about what's actually missing.

## Where you'll meet it

- [Show the path from today's estate to the target state](/use-cases/target-state-roadmap)

## Related modules

- [Roadmaps](/modules/roadmaps)
- [Projects](/modules/projects)
