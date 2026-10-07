---
page_family: module
module_label: "Rationalization"
endpoint: unified_applications.rationalization_dashboard
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S2-03, segment: S2}
capture_status: live
cta: plans
description: "Application rationalisation software: score every application with the TIME model, find duplicates, and plan consolidation with cost, savings and payback."
---

# Application rationalisation

Application rationalisation is the review of every application you run, one by one, to decide whether to keep it, invest in it, migrate it or retire it. Entelim scores each application with the TIME model, flags likely duplicates with three detection strategies, and turns the decisions into a consolidation list and replacement plans with cost, annual savings and payback in months. It is for the CIO, head of architecture or portfolio manager who has to cut the number of applications before the next budget round.

## What application rationalisation is

Application rationalisation is the process of reviewing an organisation's applications and deciding, for each one, whether to keep, consolidate, replace or retire it. The goal is a smaller, cheaper, better-understood portfolio: fewer tools doing the same job, less licence spend on systems nobody needs, and a recorded reason for every application that stays.

The **TIME model** is the most common way to make the decision. TIME stands for Tolerate, Invest, Migrate and Eliminate. Tolerate means keep it as it is. Invest means the application is valuable but needs work. Migrate means the value is worth keeping but the technology is obsolete. Eliminate means the application is low in value, high in cost or redundant. Each answer maps to an action you can communicate to the business: retain, refactor, replace, retire or consolidate.

The question usually starts simply and is hard to answer without data: how many of our applications are duplicates, and what do they cost? Two regional teams standardise on different tools for the same job without telling each other. A reorganisation leaves three departments each licensing their own copy of the same product. None of it shows until someone goes looking application by application, which is why most organisations run rationalisation as a deliberate exercise, once a budget cycle, rather than catching overlap as it happens.

## Who it is for

- **A CIO or head of architecture** who has been asked for a smaller portfolio before the budget cycle and needs a reasoned list, not a guess.
- **A portfolio manager or enterprise architect** who has to show, application by application, why one stays and another goes.
- **A scale-up CTO** who has just reviewed SaaS spend, found tools that overlap, and needs to decide which to keep.

## How it works in Entelim

1. **Start from your application inventory.** Rationalisation reads the applications in your [Applications module](/modules/applications), with the owner, lifecycle stage and cost fields recorded there. The more of those fields are filled in, the stronger the score. A Data Quality Health panel on the dashboard shows how complete the owner, cost, risk, lifecycle and vendor fields are across the portfolio.
2. **Find the duplicates.** Choose Run Detection and pick a strategy. Fast uses name matching with acronym expansion and suits large portfolios. Hybrid combines hashing with fuzzy matching and is the recommended middle. Enhanced looks across several dimensions with business context. A similarity slider runs from 30 per cent (more results) to 100 per cent (exact matches only). Each result is a duplicate group you review, and exact matches can be resolved in one action.
3. **Score every application.** Choose Score Portfolio. Each application receives an overall health score from 0 to 100, built from technical health, business value, cost efficiency and vendor risk, and a TIME recommendation. The dashboard plots the portfolio on a TIME quadrant and shows a health distribution and a disposition breakdown.
4. **Decide in the workbench.** The Workbench lists every scored application with its health, disposition, readiness, review state and business unit. You can filter, compare applications side by side, open the score breakdown and act on many applications at once. An application whose key evidence is missing is marked as having insufficient evidence instead of being given a confident answer.
5. **Plan the change.** Open an application's planning page to see its score breakdown, decision dossier, retirement blockers and dependency impact, then record a replacement: the replacing application, migration complexity, estimated cost, estimated annual savings, payback in months, and the status of data migration and user migration. Dispositions to retire, replace or consolidate go through architecture review board approval before they are approved.
6. **Track it to completion.** The Tracking page shows portfolio dependency risk, a retirement sequence and dependency import, so you retire applications in an order that does not break the ones that depend on them. Consolidation list entries carry a target date, an estimated annual saving and an owner, and can be added to the roadmap. Export the result as Excel, CSV or an executive summary.

## What you get

- A TIME recommendation and a 0 to 100 health score for every application, with the breakdown behind it.
- Duplicate groups from three detection strategies, with an adjustable similarity threshold.
- A consolidation list with a recommended action, priority, target date and estimated annual saving per entry.
- Replacement plans with migration complexity, estimated cost, estimated annual savings, payback in months, and data and user migration status.
- A retirement sequence and dependency view, so blockers are visible before a decision is made.
- A decision audit trail, and approval through the architecture review board for retire, replace and consolidate decisions.
- Excel, CSV and executive summary exports for leadership.

## Duplicate detection

Duplicate detection is the first step of rationalisation. It scans your application list for functional overlap and proposes candidate groups, such as two project-tracking tools or three survey platforms. The [Duplicate Detection module](/modules/duplicate-detection) shows the detection screen on its own. Detection proposes; a person decides. Nothing is merged or retired until someone approves it. The question of what you pay for twice is answered in full on [duplicate software spend](/use-cases/duplicate-software-spend), which reads the same duplicate groups alongside contracts.

## Limits

Detection proposes candidates. It matches on names and recorded attributes, so two applications with different names and the same function will only appear if the record describes them alike.

Savings and payback on a replacement plan are the figures you enter. Entelim does not estimate them for you from market prices.

The score is only as good as the fields behind it. Where the owner, cost or risk is missing, the application is flagged for insufficient evidence instead of scored with a guess.

Entelim does not discover applications on your network. The inventory is what you enter or import. Pair rationalisation with [Procurement](/modules/procurement) for the contracts and renewal dates that decide when a retirement can take effect.

## Frequently asked questions

### What is the TIME model?

The TIME model sorts applications into Tolerate, Invest, Migrate and Eliminate. Entelim scores each application on technical health, business value, cost efficiency and vendor risk, assigns a TIME recommendation, and maps it to an action: retain, refactor, replace, retire or consolidate. You can override the recommendation with a recorded rationale.

### How do you find duplicate applications?

Run duplicate detection over your application list. Entelim offers three strategies (fast name matching with acronym expansion, hybrid hash and fuzzy matching, and an enhanced multi-dimension match) and a similarity threshold from 30 to 100 per cent. Each result is a group of likely duplicates for a person to review and resolve.

### How long does application rationalisation take?

It depends on how many applications you have and how complete their records are. Scoring and duplicate detection run on demand in Entelim, so the first pass can be done the day your inventory is loaded. The slow part is the decisions, approvals and migrations that follow. The [architecture health check](/architecture-health-check) runs a first pass for you in two weeks.

### What is the difference between rationalisation and portfolio management?

Application portfolio management keeps the list of applications true: owner, cost, lifecycle and risk. Rationalisation uses that list to decide what to keep, merge or retire. In Entelim the [Applications module](/modules/applications) holds the portfolio, and this module scores it and plans the changes.

### Can I import my application list?

Yes. Applications can be added one at a time or imported from CSV or Excel files, and an existing ArchiMate model can be imported as well. See the [Applications module](/modules/applications) and [importing an ArchiMate model](/use-cases/import-archimate-model).

### Is rationalisation included in the free plan?

Yes. The Community plan is free with no time limit, for one organisation and up to three people, and self-hosting the open-source edition is free. See [Entelim pricing](/pricing) for the paid plans.

## Related modules

- [Applications](/modules/applications)
- [Duplicate Detection](/modules/duplicate-detection)
- [Procurement](/modules/procurement)
- [Business Case](/modules/business-case)

## Plan

Rationalisation is available in the self-hosted edition and on the hosted plans, which differ by how many people can edit. [See Entelim pricing](/pricing), or compare Entelim with [LeanIX](/vs/leanix).
