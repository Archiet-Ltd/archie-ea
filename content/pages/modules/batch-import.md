---
page_family: module
module_label: "Batch Import"
endpoint: batch_import_view.dashboard
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-01, segment: S3}
  - {id: UC-S4-02, segment: S4}
capture_status: awaiting_capture
---

# Batch Import

*Spreadsheets and full architecture exports, all landing in the same model — with a full import
history.*

## What this module does

Whatever format your existing data is in, it comes in here: a spreadsheet of people and suppliers,
or a full architecture export from another tool. Import history keeps a record of exactly what came
in and when.

## Where you'll meet it

- [Import our existing model](/enterprise-architecture/import)
- [Set it up from our spreadsheet in an afternoon](/services-ops/set-up-in-an-afternoon)

## Related modules

- [Architecture Model](/modules/architecture-model)
