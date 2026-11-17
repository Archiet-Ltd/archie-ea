---
wave: 1
page_family: module
module_label: "Batch Import"
endpoint: batch_import_view.dashboard
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-01, segment: S3}
  - {id: UC-S4-02, segment: S4}
capture_status: live
cta: plans
icon: upload
headline: Bring spreadsheet data into the model, and commit it when you are happy
summary: Upload a CSV or Excel file or paste rows, review what was drafted in batches, and commit only the batches you accept.
who_for:
- An architect or analyst who holds their applications in a spreadsheet.
- A portfolio manager who inherited a list and wants it in the model without retyping it.
questions:
- q: Can I load our application spreadsheet into the model?
  who: an analyst starting out
- q: What did the import draft from my rows?
  who: an architect reviewing a batch
- q: Which of my import jobs have finished?
  who: a portfolio manager
- q: Does our SAP landscape export import cleanly?
  who: an enterprise architect
steps:
- name: Upload or paste
  text: Add a CSV or Excel file, or paste rows straight from a spreadsheet.
- name: Choose the detail
  text: Quick, standard or comprehensive.
- name: Review each batch
  text: Nothing lands in the repository until you have reviewed it.
- name: Commit
  text: The import dashboard lists every job you have run, with its status.
---

# Batch Import

*Bring spreadsheet and pasted application data into the model, review what was drafted, and commit
it only when you are happy.*

## What this module does

Upload a CSV or Excel file, or paste rows straight from a spreadsheet. Pick how much detail to generate — quick, standard or comprehensive — and the import
drafts architecture elements from your rows in batches. Nothing lands in the repository until you
have reviewed a batch and chosen to commit it. SAP landscape exports are recognised and tidied up
automatically. The import dashboard lists the jobs you have run, with their status.

Importing a model from another modelling tool is a different route: the ArchiMate import page
takes Open Exchange files.

## Where you'll meet it

- [Import our existing model](/use-cases/import-archimate-model)
- [Set it up from our spreadsheet in an afternoon](/use-cases/set-up-in-an-afternoon)

## Related modules

- [Architecture Model](/modules/architecture-model)
