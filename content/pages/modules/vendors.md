---
page_family: module
module_label: "Vendors"
endpoint: unified_applications.vendors
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S4-05, segment: S4}
capture_status: live
cta: plans
---

# Vendors

*Every supplier and provider behind your applications, in one list, linked straight into what
depends on them.*

## What is vendor management software?

Vendor management software is the system that holds every supplier an organisation buys from — who
they are, what they provide, and what contracts and spend trace back to them — so that "who do we
depend on, and for what" has one answer instead of living in separate spreadsheets per department.
At its simplest that's a directory: one record per vendor, not one per purchase order. Done well, it
also tracks each vendor's standing — contract status, and how concentrated the organisation's spend
or dependency is on that one supplier — because a directory only earns its keep if it also tells you
something about exposure.

Vendor risk management software is the part of that discipline aimed specifically at exposure: which
vendors would take a critical process or application down with them if they failed or ended the
relationship tomorrow, and which capabilities the organisation is sourcing from only one supplier
rather than several. It's a question that rarely gets asked until a vendor actually has an outage,
because answering it ahead of time means linking a vendor record to everything that depends on it
technically, not just contractually — the harder, less common half of vendor management software.

## What this module does

Vendors sits next to Applications as the other half of the same picture: who you buy from, which
applications and contracts trace back to them, and what actually breaks if one of them lets you
down. It's the record vendor risk questions and procurement renewals both read from, so a vendor's
status never has to be entered twice. Each vendor's own record flags, capability by capability, where
it is the only supplier in place — a concentration risk a plain vendor directory would otherwise
never surface.

## Where you'll meet it

- [Which contracts renew soon, and what depends on them?](/use-cases/contract-renewals)

## Related modules

- [Applications](/modules/applications)
- [Procurement](/modules/procurement)
