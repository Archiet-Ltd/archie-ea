---
wave: 3
page_family: function-per-segment
use_case_id: UC-S1-04
segment_id: S1
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/revenue-stream-risk
icon: banknote
---

# What must be true for the revenue stream we are betting on, and which of it is not ready?

**For startup founders betting the company on one thing working.**

Record the revenue stream as a value stream, link the capabilities it needs, and note how mature each one is today. The Value streams at risk page then lists every value stream with the number of its capabilities that sit below a maturity level you choose, and opens each one to show which capability is the cause. A capability with no maturity recorded is counted separately as not assessed. It is never counted as safe.

## The question

You have picked the revenue stream you are betting on. The harder question is what has to be true underneath it: a payments flow that works, a support process that copes, an onboarding step that is not one person's job. Those are rarely written down together.

## What Entelim answers

The **Value streams at risk** page opens with a **Maturity threshold** control: five buttons, 1 to 5, set to 3 until you change it. A capability counts as at risk when its current maturity is below the threshold. Changing the threshold asks again, and puts the choice in the address, so a reload or a shared link shows the same answer.

Two summary lines sit above the table. The first reads "N of M value streams depend on a capability below maturity 3", with your own counts and threshold. The second counts the linked capabilities that are below the threshold and the ones that have no maturity recorded.

The table has one row per value stream with four columns: **Value stream**, **Below threshold**, **Not assessed** and **Linked capabilities**. A stream with at least one capability below the threshold carries an **At risk** badge. Click a row to open its capabilities. Each shows a status of Below threshold, Not assessed or At or above threshold, with the **Causing capability**, **Current maturity**, **Target maturity**, **Gap** and **Stages** it supports. A value stream with nothing linked says "No capability is linked to this value stream."

The answer comes from the links you made between capabilities and stages, and the maturity recorded on each capability. Nothing is estimated.

## Who asks it

A founder or first architect who has to say, to the board or an investor, what the plan for the main revenue stream depends on and which of those parts are weak. Business architects see **Value Streams at Risk** in their sidebar.

## How to get your answer

1. Open **Value Streams** and click **New Value Stream**. Enter a name, and optionally a code, type, strategic importance and business owner.
2. Open the value stream and use **Add Stage** for each step, in order.
3. In the **BIZBOK Capability × Stage Grid**, add each capability the stream relies on and click a cell to set its support. The **Capability Support** form takes a support level (1 to 5), a contribution percentage, a support type and an impact level.
4. In **Capabilities**, open each capability and record its **Current maturity** and **Target maturity**.
5. Open **Value streams at risk**, choose a threshold, and open the row for your revenue stream.

## Doing this without Entelim

A spreadsheet with revenue streams down one side and the things they need across the top does the job for a week. Then it goes stale, and "how ready is it?" becomes an opinion in a meeting.

## Limits

- It reads only what you have recorded. With no value streams, or no capabilities linked to stages, the page says "No capabilities are linked to a value stream yet" and offers a button to **Link capabilities to value streams**.
- You enter value streams, links and maturity yourself. Nothing is created from your Business Model Canvas, because the canvas boxes are free text.
- The answer follows the links you drew between capabilities and stages. Relationships Entelim works out elsewhere in the model are not used here.
- If the answer cannot be loaded, the page says "We could not load the value streams just now." with a **Try again** button, and shows no figures.

## Frequently asked questions

### What if I have not recorded maturity for a capability?

It appears under **Not assessed**, and the status line shows "Not assessed". It does not make the stream at risk, and it does not make it safe. The summary tells you how many capabilities have no maturity.

### Does the Ask page answer this?

Not directly. The strategy question on Ask, "What are we trying to achieve at ___, and how is it tracking?", lists the initiatives recorded against a chosen element, each with status, percentage complete, sponsor and success metrics. The maturity view is the Value streams at risk page.

### Can I pick a different measure than maturity?

No. The page compares current maturity with the threshold you choose, and shows the target and the gap alongside it.

## Related questions and modules

- [Value Streams](/modules/value-streams)
- [Business Model Canvas](/modules/business-model-canvas)
- [Which value streams are at risk because the capabilities under them are immature?](/use-cases/value-streams-at-risk)
- [Put our business model on one page](/use-cases/business-model-canvas-on-one-page)
- [If our one cloud platform or payment provider goes down, what stops?](/use-cases/single-point-of-failure)
