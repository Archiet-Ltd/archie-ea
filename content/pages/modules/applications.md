---
page_family: module
module_label: "Applications"
endpoint: unified_applications.application_list
source: app/modules/modules_directory/routes.py + app/utils/role_access.py (_link calls, read 2026-09-23)
state: on_main
answers_use_cases:
  - {id: UC-S1-05, segment: S1}
  - {id: UC-S2-03, segment: S2}
  - {id: UC-S2-07, segment: S2}
  - {id: UC-S3-01, segment: S3}
  - {id: UC-S4-05, segment: S4}
capture_status: live
cta: plans
description: "An application portfolio management tool: one inventory of every application with owner, cost, lifecycle and risk, linked to what depends on it. Start free."
---

# Application portfolio management tool

Application portfolio management is keeping one true list of every application you run, with its owner, cost, lifecycle stage and risk, and using that list to decide what to renew, consolidate or retire. Entelim's Applications module is that list: an application inventory you type in or import, with a fact sheet for each application that shows its record completeness, capabilities, dependencies and linked risks. It is for the CIO, head of architecture or portfolio manager who needs a current answer when an audit, a budget round or an acquirer asks what you run.

## What application portfolio management is

An application portfolio is the complete list of applications your organisation runs, with four things recorded for each: who owns it, what it costs, what stage of its life it is in, and how much risk it carries. Application portfolio management is keeping that list true and using it to make decisions. It answers what to consolidate, what to renew and what to retire before it becomes a problem instead of after.

Most organisations never get past the first part. The list exists, but it is split across a spreadsheet, three people's memory and the last vendor renewal email, and it is out of date within a week of anyone updating it. The cost of that gap is specific. Two teams pay for software that does the same job. Applications have no named owner. Contracts and end-of-life dates arrive as a surprise because nothing was watching them. It surfaces at the worst moment, when an audit, a security review or an acquirer asks for the current landscape and the honest answer is a diagram drawn months ago by someone who has since left.

A portfolio that holds up has ownership and cost attached to every application, not only the ones someone remembered to document. Renewal and retirement dates are visible before they are urgent, and duplicate spend shows up on its own instead of during a budget review.

## Who it is for

- **A CIO or head of architecture** who has to give an auditor, a board or an acquirer a current list of systems.
- **A portfolio manager** who needs to see which applications have no owner, no cost or no lifecycle stage.
- **An application owner** who wants one place to keep their own applications' records true. See [what I own](/use-cases/what-i-own).
- **A scale-up CTO** who inherited a landscape nobody has written down.

## How it works in Entelim

1. **Add or import the applications.** Add an application by hand with its name, code, type, business criticality, deployment status, lifecycle status, business owner and description. Or import a spreadsheet: CSV and Excel files are supported, with a preview before anything is saved, an import history, and a rollback for an import you did not mean to make. You can also bring in an existing ArchiMate model, which is covered in [importing an ArchiMate model](/use-cases/import-archimate-model).
2. **Read the list.** The Applications page shows every application with its type, lifecycle status, criticality, business owner, vendor and capability. Filter by search text, type, lifecycle status, business domain and capability level, sort the columns, and switch decommissioned applications on or off. A data quality strip above the list states how many applications in the portfolio have a named owner and a vendor recorded.
3. **Fill the gaps in bulk.** Select several applications and set their lifecycle stage, assign an owner or add a tag in one step. An application can carry several owners: primary, backup, technical and business. Entelim also suggests business capabilities for an application, and you accept or ignore each suggestion.
4. **Record the cost.** Cost fields on the application include total cost of ownership and annual licence cost, and the Data Enrichment screen under [Rationalization](/modules/rationalization) lets you add cost, ownership and criticality to many applications in one pass.
5. **Open the fact sheet.** Each application has a fact sheet that gathers its identity, owners, annual and licence cost, lifecycle signal (including the days left before a retirement date), the capabilities it supports, the diagrams it appears on, its linked risks and a record completeness score. The score is out of 100 and weighs owner, business criticality, lifecycle and total cost of ownership most heavily, then business domain, technology stack, deployment model, data classification, vendor, disaster recovery and description. A panel lists exactly which fields are missing.
6. **Check ownership by business unit.** The ownership coverage page shows, for each business unit, how many of its applications have an assigned owner. It is open to the CTO and portfolio manager roles.
7. **Export.** Download the portfolio as CSV for anyone who needs it outside Entelim.

The screenshot on this page shows the application list for the demonstration company.

## Application dependencies

An application dependency is a connection where one application relies on another for data, authentication or a shared capability. In Entelim, once an application is linked to its ArchiMate element, its fact sheet lists what it depends on and what depends on it, and a View impact graph button opens the graph centred on that element. For the question people ask during an outage or before a decommission, read [what breaks if a service fails, and who gets called](/use-cases/what-breaks-and-who-gets-called), which answers it across every layer with the owner of each affected element.

## What you get

- One inventory of every application, searchable and filterable, with lifecycle, criticality, owner, vendor and capability on each row.
- Several owners per application, in four roles, with a portfolio-wide count of applications that have none.
- A fact sheet per application with a record completeness score and a list of missing fields.
- Cost fields, including total cost of ownership and annual licence cost, that feed [Rationalization](/modules/rationalization) and the [Business Case](/modules/business-case) module.
- Dependencies upstream and downstream of each application that is linked to the architecture model, with an impact graph.
- Bulk actions for lifecycle stage, owner and tags.
- Import from CSV, Excel or an ArchiMate model with history and rollback, and CSV export.

## Limits

The inventory is what you enter or import. Entelim has no discovery agent that scans your network and finds applications by itself.

Dependencies appear for applications linked to an element in your ArchiMate model. An application with no link shows no dependencies, and the fact sheet says so instead of guessing.

A figure that is not recorded is shown as missing. The completeness score counts it as a gap and does not fill it in.

Pair the module with [Procurement](/modules/procurement) for contracts and renewals, and with [Vendors](/modules/vendors) for the suppliers behind each application.

## Questions it answers

- [Show me what I own and what depends on it](/use-cases/what-i-own)
- [What breaks if this service or platform fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)
- [What are we paying for twice?](/use-cases/duplicate-software-spend)
- [Import our existing Archi or Open Exchange model](/use-cases/import-archimate-model)
- [Which contracts renew soon, and what depends on them?](/use-cases/contract-renewals)
- [Give the acquirer or the auditor a current architecture picture](/use-cases/architecture-map-for-due-diligence)

## Frequently asked questions

### What is application portfolio management?

Application portfolio management is keeping a single, current list of every application an organisation runs, with its owner, cost, lifecycle stage and risk, and using it to decide what to keep, consolidate or retire. Entelim's Applications module is that list, with a fact sheet for each application.

### How do I build an application inventory?

Add applications one at a time, or import a CSV or Excel spreadsheet. The import shows a preview, keeps a history and can be rolled back. Then use bulk actions to set lifecycle stages and owners, and the completeness score on each fact sheet to see what is still missing.

### Can I import from a spreadsheet?

Yes. Entelim imports CSV and Excel files into the application inventory. You see a preview before saving, and an import can be rolled back from the import history. If your applications already live in an ArchiMate model, import that instead.

### How does it show what depends on an application?

Link the application to its element in your ArchiMate model. Its fact sheet then lists what it depends on and what depends on it, and a View impact graph button opens the full graph. An application that is not linked shows no dependencies.

### Does it find applications on my network automatically?

No. The inventory is what you add or import. Entelim does not run a discovery agent. If you already hold a list in a spreadsheet, an ArchiMate model or another tool that can export CSV or Excel, you can bring it in.

### Is there a free plan?

Yes. The Community plan is free with no time limit, for one organisation and up to three people, and you can self-host the open-source edition under AGPL-3.0. See [Entelim pricing](/pricing) for Startup, Team and Enterprise.

## Related modules

- [Rationalization](/modules/rationalization)
- [Vendors](/modules/vendors)
- [Procurement](/modules/procurement)
- [Business Case](/modules/business-case)

## Plan

The plans differ by how many people can edit and by the extras listed on the pricing page, not by which modules you get. [See Entelim pricing](/pricing), browse [all modules](/features), or read how Entelim compares with [ServiceNow Application Portfolio Management](/vs/servicenow-apm).
