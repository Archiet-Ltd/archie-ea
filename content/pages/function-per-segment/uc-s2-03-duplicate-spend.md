---
wave: 5
page_family: function-per-segment
use_case_id: UC-S2-03
segment_id: S2
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/duplicate-software-spend
icon: receipt
---

# What are we paying for twice?

**For scale-up CTOs and VPs of Engineering.**

Open **Duplicate Detection** and click **Run Detection**. Entelim compares every application in your list and returns groups of similar ones, each with an estimated saving. You review each group, add the real duplicates to the **Consolidation List** and dismiss the rest. The **Spend** page then shows your vendor contracts by type and by vendor, so you can see what the duplicates cost to keep.

## The question

A SaaS spend review finds three tools doing the same job, bought by three different teams who never
talked to each other. Nobody set out to waste money — nobody had a single list to check against.

## What Entelim answers

Find what you're paying for twice. Run duplicate detection across your whole application list, review the overlapping groups it finds, and add them to a consolidation plan.

**Duplicate Detection** shows four cards: **Duplicate Groups**, **Estimated Savings**, **High Priority** (groups with a similarity of 0.85 or more) and **Last Detection**, which reads "Never" until the first run. **Run Detection** opens a form with a strategy (Hybrid Detection, recommended, Fast Detection or Enhanced Detection), a **Similarity Threshold** slider from 0.4 ("More matches") to 0.9 ("Higher precision"), and an optional run name.

Each group appears as a row with Group Name, Type, Apps, Similarity, Status and estimated savings. You can search and filter by type, status and risk level. **View** opens the group, **Add to List** puts it on the consolidation list and **Dismiss** marks it as ignored, after you confirm. Several groups can be added or dismissed together.

The saving for a group is the average annual cost recorded across its applications, times the number of applications beyond the first. The cost comes from the application record: total cost of ownership, or licence plus maintenance cost. A group shows a saving once an application in it has a cost recorded.

The **Consolidation List** carries the plan. Its cards show **Portfolio Annual Cost**, **Projected Savings** (with a verified figure beside it when one exists), **Migration Investment**, **In Progress**, **Requires Attention** and **High Risk**. Each entry has an Action (Pending Review, Decommission, Retire, Merge or Add to Roadmap), a TIME score, Annual Saving, Migration Cost, Status and Wave. **Score All (TIME)** and **Recalculate Savings** update the whole list.

The **Rationalization** dashboard strings the steps together as Detect, Score, Plan, Roadmap and Track. **Portfolio KPIs**, the Executive Rationalization Scorecard, shows Total Applications, Avg Health Score, Elimination Candidates and Investment Needed, and lists Top Action Candidates.

**Spend** (Spend Analytics) shows Total Contract Value, Annual Recurring Cost, Spend by Contract Type and Top Vendors by Annual Spend. A contract without a value is left out of the total, and the page says how many were left out.

Ask the AI chat for the rationalization status of one application and it answers with an **Open rationalization planning** button that takes you to that application's planning page.

## Who asks it

A CTO or VP of Engineering heading into a budget round, a renewal cycle or an acquisition, who needs a defensible list of what to consolidate.  Spend is limited to the procurement, portfolio manager and finance roles.

## How to get your answer

1. Make sure your applications are in **Applications**, with costs where you know them. Connect GitHub or import a file to bring the applications in.
2. Open **Duplicate Detection** and click **Run Detection**. Start with Hybrid and the default threshold. Lower the threshold if you want more candidates.
3. Open each group with **View**. Click **Add to List** for real duplicates and **Dismiss** for false matches.
4. Open the **Consolidation List**. Set each entry's action and wave, then click **Score All (TIME)** and **Recalculate Savings**.
5. Open **Spend** and check the largest vendors and contract types against the groups you found.
6. Open **Portfolio KPIs** for the summary to share.

## Doing this without Entelim

Export the card statement and the contract register, sort by vendor, and look for tools that sound alike. It finds repeats of one vendor and misses two vendors doing one job.

## Limits

- Detection works on the application records you hold. A duplicate recorded under a different name and description may not be found, and every group needs a person's review.
- Savings are estimates. They need costs on the application records.
- Spend reads the vendor contracts recorded in Entelim, not your card or ledger data. It does not match contracts to applications.
- Spend Recommendations (AI) are suggestions. Read them as prompts for a conversation.

## Frequently asked questions

### Do I need cost data before I start?

No. Detection runs without it. Estimated savings need cost on the application records, so add it where you know it.

### What does Dismiss do?

After you confirm, the group is marked as ignored and leaves the list, so you can concentrate on the real overlaps.

### Who can open Spend?

Access is limited to the procurement, portfolio manager and finance roles.

## Related questions and modules

- [Duplicate Detection](/modules/duplicate-detection)
- [Rationalization](/modules/rationalization)
- [Procurement](/modules/procurement)
- [What breaks if this service fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)
- [Contract renewals](/use-cases/contract-renewals)
