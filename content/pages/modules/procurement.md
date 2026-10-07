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
---

# Procurement

*Contracts, renewals, licence entitlements, spend by category, and compliance status — one module,
because they're one workflow, not five separate tools.*

## What is contract management software?

Contract management software is the system of record for every agreement an organisation has with
its suppliers: what each contract covers, when it renews, what it costs, and what obligations —
notice periods, service levels, compliance and security requirements — sit inside it. Its job is to
replace the two usual failure modes of running contracts by inbox and spreadsheet: a renewal that
auto-extends because nobody saw it coming ninety days out, and a set of terms nobody can find again
when a dispute, an audit or a security review asks for them.

Procurement software covers the wider process that contract management sits inside: sourcing,
requesting, approving and paying for what an organisation buys, plus the licence and subscription
entitlements that come out the other side of a signed contract. In practice the two overlap heavily
once an organisation is past purchase-order basics — the same contract record is what a renewals
dashboard watches, what a spend report categorises, and what a compliance check reads to see whether
what's licensed matches what's actually deployed.

## What this module does

Entelim's Procurement module is that system of record. Renewals, contracts, spend analytics and
licences all read from the same vendor and application records. Ask which contracts renew next
quarter, which licences are entitled versus actually used, or which vendor a piece of spend belongs
to, and the answer traces back to your real application list rather than living as a separate,
disconnected spreadsheet.

## Where you'll meet it

- [Which contracts renew soon, and what depends on them?](/use-cases/contract-renewals)

## Related modules

- [Applications](/modules/applications)
- [Vendors](/modules/vendors)
