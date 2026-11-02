---
wave: 3
page_family: function-per-segment
use_case_id: UC-S3-04
segment_id: S3
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/value-streams-at-risk
icon: alert-triangle
---

# Which value streams are at risk because the capabilities under them are immature?

**For enterprise architecture teams and the CIO they report to.**

Open Value Streams at Risk and choose a maturity threshold from 1 to 5. Entelim lists every value stream that depends
on a capability whose current maturity is below that level, with the capability, its current and target maturity, the gap and
the stages it supports. For the initiatives and success metrics attached to an element, ask the AI chat what you are
trying to achieve there and how it is tracking.

## The question

A value stream depends on a stack of capabilities. When one of them is immature, the value stream is exposed, but that
link rarely shows up until something breaks. You can read a capability maturity heat map and a value stream diagram
side by side and still not know which value streams are carrying the weakest capabilities.

## What Entelim answers

The **Value streams at risk** page reads two things you have already recorded: the links between capabilities and value
stream stages, and each capability's current maturity. The heading says it plainly: value streams that depend on a
capability whose current maturity is below the threshold you choose.

The **Maturity threshold** control offers 1, 2, 3, 4 and 5, and opens on 3. The choice is kept in the address, so a
reload or a shared link shows the same answer. Under it, two summary lines read, for example, "2 of 5 value streams
depend on a capability below maturity 3" and "3 of 12 linked capabilities are below the threshold; 2 have no maturity
recorded." The figures are counted from your own data.

The table has these columns: Value stream, Below threshold, Not assessed and Linked capabilities. A value stream with
one or more capabilities below the threshold carries an "At risk" tag. Click a row to open it. Each capability shows:

- its name and a status of Below threshold, At or above threshold, or Not assessed
- **Current maturity**, **Target maturity** and **Gap**, with a dash where a value is not recorded
- **Stages**, the value stream stages it supports

A capability with no maturity recorded is Not assessed. It is not treated as safe and it is not treated as failing.
When no value stream falls below the threshold, the page says "No value streams are below the maturity threshold."

The strategy side lives in the **AI chat**. Ask "what are we trying to achieve at the checkout, and how is it tracking?"
and it lists the initiatives recorded against the element you name. Each card shows its status, percentage complete and health, the
executive sponsor, budget variance (or "Not costed"), and its success metrics as name, actual against target, and
status.

## Who asks it

A business or enterprise architect preparing a view for the CIO, and the CIO deciding where the next investment goes.
The question comes up before a planning round or after an incident that traced back to a weak capability.

## How to get your answer

1. Record your value streams and their stages under **Value Streams**.
2. In the stage grid, link capabilities to the stages they support. This link is what the page reads.
3. Record each capability's current and target maturity (see the capability maturity view).
4. Open **Value Streams at Risk** from the business architect's sidebar and pick your threshold.
5. Open a flagged value stream to see the capabilities behind it. For the initiatives and metrics, ask the **AI chat**
   about the capability or value stream they are recorded against.

## Doing this without Entelim

You keep a capability maturity spreadsheet and a value stream diagram, and match them by eye before each planning
round. The match goes stale quickly and it hides capabilities nobody has assessed.

## Limits

- With no capabilities linked to value stream stages, the page shows "No capabilities are linked to a value stream
  yet" and offers a link to Value Streams. It does not guess the links.
- It reads the links you recorded. It does not infer dependencies that nobody entered.
- Initiatives and success metrics are not shown on this page. They are in the chat's strategy answer, one element at a
  time.
- Threshold is one number from 1 to 5, applied to every capability. There are no per-capability thresholds.

## Frequently asked questions

### What counts as "at risk"?

A capability whose current maturity is below the threshold you set. A value stream is at risk when at least one of its
linked capabilities is.

### What happens to capabilities with no maturity score?

They are counted separately as Not assessed and shown with a dash. They are not counted as at risk.

### Can I change the threshold?

Yes, choose any level from 1 to 5. The answer reloads for that level.

### Where do the maturity numbers come from?

From the maturity recorded against each capability in your organisation. Shared reference capabilities contribute
their stage links but not their maturity, because that number would not be your own assessment.

## Related questions and modules

- [Value Streams](/modules/value-streams)
- [Digital Twin](/modules/digital-twin)
- [Show capability maturity as a heat map](/use-cases/capability-maturity-heatmap)
- [Is the programme on time and on budget, and what does each project touch?](/use-cases/programme-tracking)
