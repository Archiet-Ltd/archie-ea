---
wave: 5
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
icon: gavel
headline: Take every change through review, with the decision on the record
summary: Reviews, sessions and decisions in one place, each decision recorded against the exact version of the proposal it reviewed.
who_for:
- A chief architect or enterprise architecture lead who chairs the board and needs every decision on the record.
- A domain owner or security and risk representative who reviews proposals in a session.
- A solution architect who submits a change and wants to know where it stands.
questions:
- q: Is this proposed change allowed to go ahead?
  who: a chief architect chairing the board
- q: Which version of the proposal did we approve, and on what conditions?
  who: a security reviewer after the event
- q: Which reviews are still waiting for a decision?
  who: an enterprise architecture lead
- q: Why was this system built the way it was?
  who: a new architect joining the team
steps:
- name: Submit the change
  text: A decision brief, solution, architecture model or decision record goes to the board as a review.
- name: Pin the evidence
  text: The evidence is captured for the review cycle, so everyone reads the same version.
- name: Decide in session
  text: Approve, approve with conditions, reject, or return for more evidence. A person who submitted a review cannot also decide it.
- name: Keep the record
  text: The decision, its rationale and its conditions stay with the pinned evidence.
---

# Architecture Review Board

*Reviews, sessions and decisions, with each decision recorded against the exact version of the
proposal it reviewed.*

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
and the decisions that came out of them. A review covers a decision brief, a solution, an architecture
model or an architecture decision record. For every one of these, the evidence is captured and pinned
for the review cycle: a decision brief is pinned as a version, and a solution, an architecture model
or an architecture decision record as an evidence snapshot. The decision is recorded against that
pinned evidence with its rationale and any conditions attached, so governance doesn't live in a
separate record nobody trusts.

Decisions are approved, approved with conditions, rejected, or returned for more evidence or
options. A person who submitted a review cannot also decide it. The board's sessions, standards,
change requests and decision log sit alongside the reviews. The pinned evidence is the proposal's
readiness record; a separate impact analysis is not attached to the review.

## Where you'll meet it

- [Take a change through the review board](/use-cases/architecture-review-board)

## Related modules

- [Risk Register](/modules/risk-register)
