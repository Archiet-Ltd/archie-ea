---
page_family: module
module_label: "Architecture Review Board"
endpoint: arb.dashboard
grouped_sub_pages: [arb.reviews, arb.sessions, arch_decisions.list_decisions]
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-07, segment: S3}
capture_status: live
cta: plans
---

# Architecture Review Board

*Reviews, sessions and decisions, with the impact evidence already attached before the meeting
starts.*

## What is an architecture review board?

An architecture review board (ARB) is the governance body that decides whether a proposed change
to an organisation's technology and systems landscape is allowed to go ahead. TOGAF's Architecture
Development Method treats this as the core of architecture governance: before a new system, a
platform change, an integration or a significant redesign is built, it goes to the board as a
change proposal, together with the impact analysis — what it touches, what it risks breaking, what
compliance questions it raises, and how it fits the organisation's existing standards and target
architecture.

The board is usually the chief architect or enterprise architecture lead, the senior technical
owners of the domains affected, and representatives from security, risk and compliance. It reviews
each proposal in a session, asks the questions a single team can't answer for itself, and reaches
one of a small number of decisions: approve, approve with conditions, reject, or defer pending more
evidence. That decision is recorded, becomes part of the architecture's decision record, and from
then on is the standard other teams are measured against.

An architecture review board exists because the alternative — every team choosing its own
technology, integration pattern and design on its own — is how organisations end up with several
systems doing the same job, integrations that contradict each other, and nobody able to explain why
a critical system was built the way it was. The board is the one place a change is checked against
the bigger picture before it ships, not after.

## What this module does

Entelim's Architecture Review Board module runs that process in software, not in someone's inbox.
It holds every change proposal your board reviews, alongside the sessions where it was discussed
and the decisions that came out of them. A decision here is locked to the exact proposal it reviewed — a
decision brief, a solution, an architecture model, or an architecture decision record — along with
its rationale and any conditions attached, so governance doesn't live in a separate record nobody
trusts.

## Where you'll meet it

- [Take a change through the review board with the impact evidence attached](/enterprise-architecture/review-board)

## Related modules

- [Risk Register](/modules/risk-register)
