---
page_family: module
module_label: "Procurement"
endpoint: procurement.renewals_dashboard
grouped_sub_pages: [procurement.contracts_list, procurement.spend_analytics, procurement.licenses_list, procurement.compliance_dashboard]
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S4-05, segment: S4}
capture_status: live
cta: plans
description: "Contract management software with a renewals dashboard, licence entitlements against use, spend by contract type and vendor, and a licence compliance view."
---

# Contract management software

Contract management software is the register of every supplier agreement you hold: what it covers, what it costs, when it renews and what you are licensed to use. Entelim's Procurement module keeps contracts linked to the vendors that supply them, with a renewals dashboard, licence entitlements against actual use, spend by contract type and vendor, and a licence compliance view. It is for the procurement, finance or IT operations lead who needs to see renewals before they auto-extend.

## What contract management software is

Contract management software is the system of record for every agreement an organisation has with its suppliers. It holds what each contract covers, its value, its dates and who owns it. Its job is to replace the two usual failures of running contracts by inbox and spreadsheet: a renewal that auto-extends because nobody saw it coming, and a set of terms nobody can find when an audit or a dispute asks for them.

Three terms are worth fixing:

- **A renewal date** is the date a contract renews or expires. Entelim uses the contract's renewal date, and the end date where no renewal date is set.
- **Auto-renewal** is a clause that extends the contract for another term unless you give notice. It is the usual cause of paying for something you meant to drop.
- **A licence entitlement** is the number of users, devices or other units a contract lets you use. Compliance means the number you deploy and use matches what you are entitled to.

Procurement software is the wider process: sourcing, requesting, approving and paying. Once an organisation is past purchase-order basics the two overlap heavily, because the same contract record is what the renewals view watches, what a spend report totals and what a licence check reads.

## Who it is for

- **A procurement lead** who owns the contract register and the renewal calendar.
- **A finance lead** who needs licences and spend by vendor and contract type.
- **An IT operations lead** who has to know which licences are unused before a renewal, and which are over-deployed before an audit.
- **The owner of a services firm** who signs every contract and wants one list of what renews when.

Contracts, renewals and the compliance view are open to people with the procurement or portfolio manager role. Licences and spend are also open to the finance role.

## How it works in Entelim

1. **Add the contracts.** Choose New Contract and enter the name, contract number, vendor, description, type, category, status, total value, annual cost, currency, start date, end date, renewal date, whether it auto-renews, and the contract owner. If your organisation has an AI provider configured, you can paste the contract text into Extract from contract text and the fields it finds are filled in for you to review before you save. Fields the text does not state stay blank. It works from pasted text, not an uploaded file.
2. **Watch the renewals dashboard.** The renewals dashboard counts contracts as critical (under 30 days), warning (under 90 days), upcoming (under 180 days), ok or unknown, and lists the contracts due inside a window you choose: 30, 60, 90, 180 or 365 days. Expired contracts are included, so a missed renewal does not drop off the list. The screenshot on this page shows this dashboard.
3. **Record the licences.** Add a licence entitlement under a contract with the product, licence type, licence metric, quantity entitled, quantity deployed, quantity used and unit cost. Entelim works out the compliance status itself from the quantities: over-deployed when you deploy more than you are entitled to, under-utilised when deployment is under half of the entitlement, compliant otherwise. A licence with no deployment recorded has no status, not a made-up one.
4. **Check compliance and shelfware.** The compliance dashboard counts licences as compliant, warning or violation, shows overall utilisation, and puts a value on the two risks: over-deployed licences at their unit cost, and shelfware, which is entitled but unused licences at their unit cost.
5. **Read the spend.** Spend analytics shows total contract value, annual recurring cost, spend by contract type and top vendors by annual spend. A contract with no value or annual cost is left out of the total and counted as excluded, never added as zero.
6. **Prepare the renewal.** Where AI is configured, a renewal brief for a contract, a licence position summary and spend recommendations are available as advisory text. They write nothing to your records. You act on them through the contract and licence screens.

## What you get

- A contract register with value, annual cost, dates, auto-renewal flag, status and owner for every agreement.
- A renewals dashboard with critical, warning and upcoming counts and a window you choose, from 30 to 365 days.
- Licence entitlements with entitled, deployed and used quantities, and a compliance status calculated from them.
- Licence compliance totals, utilisation, the cost of over-deployment and the value of shelfware.
- Spend by contract type and top vendors, with excluded contracts counted openly.
- Each contract linked to its vendor and the licences beneath it.
- Optional AI help to extract contract fields from pasted text, draft a renewal brief and summarise licence and spend positions.

## Limits

Procurement is a register and a set of views. It is not a purchase-order, approval or payment system, and it does not raise or pay invoices.

The AI features need an AI provider configured for your deployment. Without one they are switched off, and the rest of the module works as normal.

The contract extraction reads text you paste in. It does not read a PDF you upload, and you review every field before it is saved.

The contract record shows its vendor and its licences. It does not itself list the applications that depend on the supplier. To ask what stops if a supplier fails, use [what happens if a supplier fails](/use-cases/what-happens-if-a-supplier-fails).

## Questions it answers

- [Which contracts renew soon, and what depends on them?](/use-cases/contract-renewals)
- [What happens to my business if this person, supplier or system underperforms?](/use-cases/what-happens-if-a-supplier-fails)
- [What are we paying for twice?](/use-cases/duplicate-software-spend)

## Frequently asked questions

### How do I track contract renewals?

Add each contract with its renewal date or end date, then open the renewals dashboard. It counts contracts that are critical (under 30 days), warning (under 90 days) and upcoming (under 180 days), and lists everything due inside a window you choose, from 30 to 365 days. Contracts already past their date stay on the list.

### Does it read contract PDFs?

No. Where an AI provider is configured, you can paste the contract text into the contract form and Entelim proposes values for the fields it finds. You review them before saving, and any field the text does not state stays blank. Entelim does not read an uploaded PDF.

### Can it show what depends on a contract?

Not on the contract record itself. A contract shows its vendor and its licences. To see which systems and owners are affected if a supplier fails, use the [supplier failure question](/use-cases/what-happens-if-a-supplier-fails), which reads your architecture model.

### How does it find unused licences?

Enter the quantity entitled, deployed and used for each licence, with a unit cost. The compliance dashboard then shows utilisation and the value of shelfware, meaning licences you are entitled to but do not use. It also flags over-deployment, where you use more than you are entitled to.

### Is it a procurement system for purchase orders?

No. Entelim records contracts, renewals, licences and spend. It does not raise purchase orders, route approvals or pay suppliers. Use it as the register your procurement process feeds and reads, alongside your finance system.

### Is there a free plan?

Yes. The Community plan is free with no time limit, for one organisation and up to three people, and you can self-host the open-source edition for free. See [Entelim pricing](/pricing) for the paid plans.

## Related modules

- [Vendors](/modules/vendors)
- [Applications](/modules/applications)
- [Rationalization](/modules/rationalization)

## Plan

Procurement is part of every Entelim plan, which differ by how many people can edit and by the extras listed on the pricing page. [See Entelim pricing](/pricing) or browse [all modules](/features).
