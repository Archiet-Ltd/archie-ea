---
wave: 3
page_family: function-per-segment
use_case_id: UC-S3-13
segment_id: S3
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/target-state-roadmap
icon: flag
---

# Show the path from today's estate to the target state, stage by stage

**For enterprise architecture teams building a transformation plan the whole organisation can follow.**

The Enterprise Capability Roadmap puts your plateaus, your gaps and the work packages that close them on one page. Plateaus are the stages between today's estate and the target. Each work package can be linked to a gap and to the plateau it lands in. A separate Gap Analysis screen lists the recorded gap records and flags the ones that no work package addresses.

## The question

A target-state architecture means nothing without the path to it. The stages, the gaps between them and the work that closes each gap usually sit in three documents: a slide for the stages, a spreadsheet for the gaps and a project plan for the work. The moment one changes, the other two are wrong, and nobody can say which gaps have no work against them.

## What Entelim answers

**Enterprise Capability Roadmap.** The page is titled "Enterprise Capability Roadmap" with the subtitle "ArchiMate 3.2 Implementation & Migration — Plateaus, Gaps, and Work Packages". Three buttons sit at the top: **Detect Gaps**, **Add Plateau** and **Add Gap**.

- Six tiles count Capability Gaps Detected, Plateaus, Work Packages, AI Generated, Capabilities and Domains.
- **Plateaus — Implementation Timeline** shows one card per plateau with its name, its description (or "No description"), its target date (or "No target date") and a count of what is assigned to it. With none defined it says "No plateaus defined" and offers **Add First Plateau**.
- **Work Packages** is a table with Work Package, Status, Priority, Timeline, Gap and Actions. **Add Work Package** asks for a name, status (Planned, In Progress, Completed, On Hold or Cancelled), priority, start date, target date, a Linked Gap, a Target Plateau and a description. Tick several gaps and **AI Generate** offers suggested work packages in a preview. Nothing is saved until you click Confirm.
- **Work Package Timeline** draws the packages against time. A package needs a start and a target date to appear. Without any it says "No timeline data".
- If the roadmap cannot be read, the page says so: "The roadmap could not be read — this is not an empty roadmap."

**Add Gap** takes a name, a type (Coverage, Quality, Retirement, Modernization or Custom), a priority and a description. **Add Plateau** takes a name, a sequence order, a target date and a description.

**Gap Analysis.** The register of recorded gaps. Four tiles show Recorded Gaps, Critical, In Progress and Resolved. The Identified Gaps table can be sorted by name, type, priority, status or impact. A second table, "Gaps Not Addressed by Any Work Package", lists the gaps with no work package linked to them.

## Who asks it

An enterprise architect or programme lead who has to show the board how the organisation gets from the baseline to the target, and which gaps still have no plan behind them.

## How to get your answer

1. Open the roadmap and use **Add Plateau** for each stage, in sequence order, with a target date.
2. Record gaps with **Add Gap**, or run **Detect Gaps**, which asks you to confirm and then creates or updates gap records from capability maturity data.
3. Add a work package for each piece of work and set its Linked Gap and Target Plateau.
4. Read the Work Package Timeline for the sequence and the dates.
5. Open **Gap Analysis** and read "Gaps Not Addressed by Any Work Package" for what is still unplanned.

## Doing this without Entelim

A slide, a spreadsheet and a project plan, reconciled by hand before each board meeting. It is accurate on the day it is made.

## Limits

- The roadmap page lists plateaus and work packages. Its Capability Gaps Detected tile counts gaps worked out from how your applications cover your capabilities. Gap Analysis lists the gap records, so the two counts answer different questions.
- The Gap Analysis register leaves out gaps that only describe a move between plateaus.
- Gap Analysis shows up to 500 gaps.
- A work package without dates does not appear on the timeline.
- The roadmap does not decide the order of plateaus for you. You set the sequence.

## Frequently asked questions

### Do I have to build the plateaus by hand?

Yes. You add each plateau with a name, sequence order and target date.

### How do I know a gap has no work against it?

It appears under "Gaps Not Addressed by Any Work Package" on Gap Analysis.

### Is the roadmap a separate document from the model?

No. Plateaus, gaps and work packages are recorded in Entelim beside your capabilities and applications, and the roadmap page reads them live.

### What does Detect Gaps do?

It asks you to confirm ("Gaps will be detected from capability maturity data. This creates or updates Gap records."), then reports how many gap records it created and updated.

## Related questions and modules

- [Roadmaps](/modules/roadmaps)
- [Gap Analysis](/modules/gap-analysis)
- [Is the programme on time and on budget?](/use-cases/programme-tracking)
- [Which value streams are at risk because the capabilities under them are immature?](/use-cases/value-streams-at-risk)
