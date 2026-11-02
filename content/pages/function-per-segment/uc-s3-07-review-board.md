---
wave: 5
page_family: function-per-segment
use_case_id: UC-S3-07
segment_id: S3
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/architecture-review-board
icon: users-round
---

# Take a change through the review board with its evidence pinned to the decision

**For enterprise architecture teams running formal governance.**

In Entelim a solution is submitted to the Architecture Review Board from its own page, once its readiness checks pass.
The review then sits in the board's queue, pinned to a snapshot of the proposal as submitted. The board records one of
five outcomes with a written rationale, and that decision stays with the review for good. Impact analysis sits beside
the review, so the board can see what a change touches before it decides.

## The question

A change proposal reaches the board without the evidence the board needs. The meeting turns into a research session,
the decision is made on a document that has changed since people read it, and months later nobody can say what was
approved or on what conditions.

## What Entelim answers

The **Solutions** list is the record the board reviews. On a solution's governance panel the **Submit to ARB** button
stays disabled until the readiness checks are complete. The submitter must tick that they have reviewed the content,
confirm the cost provenance where an estimate exists, and attest that the architecture design, security impact and data
impact have been reviewed, each with an evidence note. A blocked submission lists the evidence still missing. A
successful one shows the review number and an "Open canonical review" link.

The **Architecture Review Board** page opens on a queue and the tiles Total reviews, Pending, Approval rate and Cycle
time. A dash replaces the rate and the cycle time until there are completed reviews to measure. A Review status chart
and links to the **Full review ledger** and **Sessions** sit alongside.

Each review has:

- a **Dossier**, drawn from the pinned record and not the live subject, so what the board reads is what was submitted
- a **Decision** panel showing the recorded decision (rationale, who recorded it, when) separately from the review's
  current status
- a **Conditions** section, where each condition can have evidence submitted, be verified or be waived with a reason
  and expiry
- history, comments and documents

The outcomes are **Approve**, **Approve with conditions**, **Return for evidence**, **Return for options** and
**Reject**. Each opens a form with a required rationale of up to 4,000 characters. Approve with conditions adds a
description for each condition, with an optional category and due date. The form states that the decision cannot be
edited or deleted afterwards. While a blocking condition is unresolved, the page says execution remains blocked.
A person who is not authorised to decide can open the review but sees the reason instead of the buttons.

**Sessions** schedules board meetings, with a name, date and time, duration, chair, optional secretary, location and
meeting link. **Decisions** keeps the decision register.

**Impact Analysis** is a separate screen. Choose an element, a layer filter and a change type (Full Replacement,
Upgrade / Version Change, Decommission / Retirement or Minor Modification) and select Analyze Impact. The report shows
Capabilities, Processes and TCO at Risk, the affected applications with owner, health and relationship, mitigation
strategy, and Save Scenario for Comparison.

## Who asks it

An enterprise architect or board secretary preparing the next session, and the chair who has to record decisions that
will stand up to an audit.

## How to get your answer

1. Complete the solution's phases and open its governance panel.
2. Tick the review confirmation, add the three attestations with evidence notes and choose **Submit to ARB**.
3. Open **Architecture Review Board** and find the review in the queue.
4. Run **Impact Analysis** for the elements the change touches, so the board can read what the change affects beside the review.
5. The deciding member opens the review, reads the dossier, and records the outcome with a rationale.
6. Track any conditions in the Conditions section until each is verified or waived.

## Doing this without Entelim

You circulate a slide deck, collect comments by email and minute the decision in a document. It works until the deck is
edited after the meeting or the conditions have to be chased.

## Limits

- Impact analysis is a separate screen that sits beside the review. It is run by a person and is not pinned to the decision.
- Only people authorised to decide see the decision buttons.
- A recorded decision cannot be edited or deleted.
- Submission depends on the solution's readiness. The button stays disabled until the checks are complete.
- A dash, not a zero, shows wherever there is nothing yet to measure.

## Frequently asked questions

### Does the board review the live solution or a snapshot?

A snapshot. The dossier is rendered from the record pinned at submission.

### Can the board approve with conditions?

Yes. Each condition blocks execution until its evidence is verified or a waiver is granted.

### Who can record a decision?

Authorised decision makers. Everyone else can read the review and sees why they cannot decide.

### Can I export the history of a review?

Yes. The history section has a link that downloads it as CSV.

## Related questions and modules

- [Architecture Review Board](/modules/arb)
- [Solutions](/modules/solutions)
- [What breaks if this service fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)
- [Which risks touch this part of the architecture?](/use-cases/risk-and-control-gaps)
