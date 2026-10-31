---
wave: 5
page_family: function-per-segment
use_case_id: UC-S4-05
segment_id: S4
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/contract-renewals
icon: calendar
---

# Which contracts renew soon, and what depends on them?

**For owner-operators and managing directors running a services business.**

The Renewal Alerts screen lists every vendor contract coming up for renewal or already past it, soonest first, with the vendor, the annual cost and the renewal date. To see what depends on a contract, open its element in the model from the contract, or ask "What breaks if ___ fails or is under-resourced?" in the AI chat, which answers with the records it read.

## The question

A contract auto-renews next month. Nobody has checked in a year whether you still need what it provides, what it costs, or what would stop working if you cancelled it. The date is in someone's inbox. The dependency is in someone's head.

## What Entelim answers

**Renewal Alerts** opens with six tiles: Total Active, Critical (<30d), Warning (<90d), Upcoming (<180d), OK (>180d) and No Date. A dropdown sets the window to 30, 60, 90 or 180 days, or one year, and starts at 30. Below it, each contract in the window shows:

- the contract name and a badge: EXPIRED, CRITICAL, WARNING or UPCOMING
- the **Vendor**, **Contract #** and **Annual Cost**, with a dash wherever one is not recorded
- the **Renewal Date**, taken from the renewal date or, failing that, the end date
- the days remaining, such as "12d", or "Expired"
- **View Details**, and an **AI brief** button that drafts a stance, a summary, leverage points, risks and questions for the vendor

A contract with neither a renewal date nor an end date is counted under No Date and left out of the list. When nothing falls in the window the page says "No expiring contracts".

**Vendor Contracts** lists all contracts with Contract, Vendor, Annual Cost, Status and Renewal, and a **Renewal Alerts** button. A contract's page shows its type, category, pricing model, whether it auto-renews, total value, annual cost, payment terms, start, end and renewal dates, notice period and vendor risk. Its **Architecture model** row links to the contract's element where one exists.

**License Entitlements** lists each product with its vendor and the numbers Entitled, Consumed and Used, plus Utilization and Status. These are the terms inside the contracts.

**Your vendors** shows tiles for Total Vendors, Active Vendors, Strategic Vendors, Product Portfolio, Portfolio ACV and Renewals Due (90d).

## Who asks it

The managing director, or whoever signs off spend, in the weeks before a renewal. The same question comes from an operations lead who has just found a three-year auto-renewal nobody remembered.

## How to get your answer

1. Record each contract with its vendor, annual cost and renewal or end date.
2. Open **Renewal Alerts** and choose the window you care about.
3. Read the badges and the days remaining. Open **View Details** on the first contract.
4. To see what depends on it, follow the **Contract element** link under Architecture model, or open the AI chat and ask "What breaks if ___ fails or is under-resourced?" with the system or supplier name. The answer links the records it read.
5. Check **License Entitlements** for the quantities you pay for against those you use.

## Doing this without Entelim

A contracts folder, a renewal calendar kept by one person and a separate list of what depends on what. The dates get missed when that person moves on.

## Limits

- Procurement, Vendor Contracts and Renewal Alerts are built for the procurement and portfolio manager roles. License Entitlements is open to the finance role too.
- Your vendors and the contracts behind them are recorded in Procurement. The AI chat finds a system, a product or a supplier only if it has been modelled as an element.
- The AI brief is a draft to work from. It does not replace reading the contract.
- A contract without a date is never flagged as renewing.
- Entelim holds the dates and terms you record. On the contract form, "Extract from contract text" reads text you paste in and proposes fields for you to review and apply. It does not go and find contract documents itself.

## Frequently asked questions

### Does it warn me before a contract renews?

It lists contracts inside your chosen window and colours them by days remaining. You open Renewal Alerts to see them.

### What if a contract has no renewal date?

It appears in the No Date tile and is left out of the list. Add a date to see it.

### Can it tell me what breaks if I cancel?

It shows what is recorded as depending on the contract's element or the system it supports. It is only as complete as your model.

### Where do licence quantities come from?

From the entitlements you record or import against each contract.

## Related questions and modules

- [Procurement](/modules/procurement)
- [Vendors](/modules/vendors)
- [What happens to my business if this person, supplier or system underperforms?](/use-cases/what-happens-if-a-supplier-fails)
- [What are we paying for twice?](/use-cases/duplicate-software-spend)
