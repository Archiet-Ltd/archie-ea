---
wave: 5
page_family: function-per-segment
use_case_id: UC-S3-09
segment_id: S3
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/programme-tracking
icon: list-checks
---

# Is the programme on time and on budget, and what does each project touch?

**For programme managers and the enterprise architecture teams supporting them.**

Entelim answers this on two levels. For the work itself, the programme question on Ask shows each work package on a system or capability with its status, percentage complete, owner, any overdue flag and its cost variance, plus how many connections it reaches. For the programme as a whole, the Portfolio screen shows each initiative's phase, health, budget against spend, completion and sponsor.

## The question

A transformation programme has dates, a budget and a list of projects. Whether it is on track, and what each project is touching along the way, usually lives in three tools that do not talk to each other. The status report says amber. Nobody can say which systems the amber work package sits on.

## What Entelim answers

**Ask, programme question.** Choose "What's changing at ___, and is it on track?" and pick a system, capability or other element. You get one card for each work package recorded on that element:

- the work package name and its status
- the percentage complete, with "overdue" beside it when it is late
- the owner, or "No owner recorded"
- the cost position: "Cost variance:" with a percentage where the package has a positive estimated cost, "Not costed" where it has none, and "Restricted to roles with budget authority" for people who may not see money
- a line such as "3 connections reached", counting what the work package touches through the model

Where nothing is recorded, the page says "No work packages recorded against this element."

**Work Packages.** A table of every work package with Status, Priority, Progress and Due Date. Search by name, choose 10, 25, 50 or 100 rows a page, and edit a row's name, summary, status (Planned, In Progress, Completed, Blocked or On Hold), priority, progress and due date.

**Transformation programmes.** One card per programme with its description, a type label, a count of workstreams (or of member solutions) and its status. **New transformation programme** starts another.

**Portfolio.** Tiles for Initiatives, Approved budget, Spent to date and Health, counted as red, amber, green and unrated. The table shows Initiative, Phase, Health, Budget, Spent, Variance, Complete, Benefits and Sponsor. A link lists the initiatives serving no objective. Anything not recorded shows as a dash.

**ADM Kanban.** Architecture work laid out across the TOGAF phases, with cards for solutions, review-board items, waves, gaps and assessments, and links to Gap Analysis, Plateau Roadmap and Roadmap.

## Who asks it

A programme lead before a steering committee, or an architect asked by the programme office which of their systems is about to change and how late that change is.

## How to get your answer

1. Record each work package against the system or capability it changes, with an owner, an estimated cost and a due date.
2. Keep status and progress current on the Work Packages screen.
3. Open **Ask**, choose the programme question and pick the system.
4. Read each card for status, overdue flag, owner and cost position.
5. Open **Portfolio** for budget, spend and health at initiative level, and **Transformation programmes** for the programme structure.

## Doing this without Entelim

A project plan, a finance extract and an architecture register, joined by hand before each steering meeting. It works until one of them moves.

## Limits

- The Ask card shows percentage variance, not the estimated and actual amounts. It does not show dates; the Work Packages screen carries the due date.
- A work package appears on Ask only if it was recorded on the element you pick.
- Cost variance appears only where an estimated cost above zero is recorded. Otherwise the card says "Not costed" rather than showing zero.
- Portfolio initiatives are not created from that screen; they arrive through an import or load.
- The ADM Kanban is a view of architecture work. Its cards are not work packages.

## Frequently asked questions

### What if a work package has no cost?

The card says "Not costed". It never shows 0% as if the package were on budget.

### Does it show the budget for the whole programme?

Portfolio does, per initiative: Budget, Spent and Variance. Totals appear when at least one initiative carries a figure.

### Can everyone see the cost lines?

No. Roles without budget authority see "Restricted to roles with budget authority" in place of the figure.

### Can I see what a work package touches?

You see a count on the Ask card. Open the system on the twin map for the connections themselves.

## Related questions and modules

- [Portfolio](/modules/portfolio)
- [Projects](/modules/projects)
- [Show the path from today's estate to the target state](/use-cases/target-state-roadmap)
- [Take a change through the review board with its evidence pinned to the decision](/use-cases/architecture-review-board)
