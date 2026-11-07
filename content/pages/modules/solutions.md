---
wave: 5
page_family: module
module_label: "Solutions"
endpoint: solution_design.list_solutions
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-07, segment: S3}
capture_status: live
cta: plans
icon: puzzle
headline: Every solution design in one list, ready for review
summary: Solutions are the proposed changes the Architecture Review Board evaluates, with their design and context, tracked from proposal to decision.
who_for:
- A solution architect who tracks designs.
- An ARB member who needs to see what is up for review.
questions:
- q: Which solutions are waiting for review?
  who: an ARB member
- q: Where does my design stand?
  who: a solution architect
- q: What was decided on this solution?
  who: an enterprise architect
- q: What context did this proposal come with?
  who: a reviewer
steps:
- name: Record the solution
  text: Its design and context.
- name: Track progress
  text: From proposal through to decision.
- name: Take it to review
  text: The review board evaluates the solution.
- name: Read the decision
  text: The outcome stays with the solution.
---

# Solutions

*Every solution design in one list — the record the review board actually reviews.*

## What this module does

Solutions are what the Architecture Review Board evaluates: proposed changes, with their design and
context, tracked from proposal through to decision.

## Where you'll meet it

- [Take a change through the review board with its evidence pinned to the decision](/use-cases/architecture-review-board)

## Related modules

- [Architecture Review Board](/modules/arb)
