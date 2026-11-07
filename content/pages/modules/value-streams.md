---
wave: 3
page_family: module
module_label: "Value Streams"
endpoint: value_stream.index
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S1-04, segment: S1}
capture_status: live
cta: plans
icon: workflow
headline: See which value streams rest on capabilities that are not ready
summary: Each value stream and the capabilities underneath it that have to work for it to deliver.
who_for:
- A business architect who models how the business delivers.
- A CTO who needs to know what must be true for a revenue stream.
questions:
- q: What must be true for our revenue stream?
  who: a CTO
- q: Which capabilities sit under this value stream?
  who: a business architect
- q: Which value streams rest on immature capabilities?
  who: a portfolio manager
- q: Where should we invest to protect delivery?
  who: a CIO
steps:
- name: Record the value streams
  text: Each customer request, fulfilled.
- name: Link the capabilities
  text: The capabilities each stage depends on.
- name: Trace the weak points
  text: See which streams sit on capabilities that are not mature enough.
- name: Act
  text: Take the finding to your portfolio.
---

# Value Streams

*Each revenue stream, and the capabilities underneath it that have to actually work for it to
deliver.*

## What this module does

A value stream is the thing your business promises — a customer request, fulfilled. Record each one
and the capabilities it depends on, and Entelim can trace which value streams sit on capabilities
that aren't mature enough yet.

## Where you'll meet it

- [What must be true for our revenue stream?](/use-cases/revenue-stream-risk)

## Related modules

- [Portfolio](/modules/portfolio)
