---
page_family: module
module_label: "Architecture Review Board"
endpoint: arb.dashboard
grouped_sub_pages: [arb.reviews, arb.sessions, arch_decisions.list_decisions]
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-07, segment: S3}
capture_status: awaiting_capture
---

# Architecture Review Board

*Reviews, sessions and decisions, with the impact evidence already attached before the meeting
starts.*

## What this module does

Every change proposal your board reviews, alongside the sessions where it was discussed and the
decisions that came out of them. A decision here writes straight through to your change-request
system, so governance doesn't live in a separate record nobody trusts.

## Where you'll meet it

- [Take a change through the review board with the impact evidence attached](/enterprise-architecture/review-board)

## Related modules

- [Risk Register](/modules/risk-register)
