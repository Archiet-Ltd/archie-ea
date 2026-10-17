---
page_family: function-per-segment
use_case_id: UC-S4-01
segment_id: S4
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/what-happens-if-a-supplier-fails
icon: server-off
---

# What happens to my business if this person, supplier or system underperforms?

**For owner-operators running a services business, no architecture background needed.**

Open Ask, choose "What breaks if ___ fails or is under-resourced?" and type the name of the team, service or system you are worried about. Entelim lists everything recorded as depending on it, up to three steps away, and shows who owns each one. Every line has a Why? button that opens the links behind it in plain words.

## The question

You are not an architect and you do not want to become one. You want a straight answer to a plain question. If this person leaves, this supplier drops the ball or this system goes down, what actually happens to the business? Today the answer lives in the head of whoever set things up, and they are busy.

## What Entelim answers

On the **Ask** page, the first question reads "What breaks if ___ fails or is under-resourced?". Click it, and a box appears with the prompt "Type a team, service or system name". Choose a match from the list. Under a **Results** heading you get one line per thing that depends on your choice, and a summary line giving the number of connections found for your choice.

Each line shows:

- the **name** of the thing affected
- the **owner**, or a "missing" marker where none is recorded
- **Health**, which currently reads "Not recorded yet"
- a note, "We worked this out — nobody drew it directly", on connections Entelim inferred from the ones you drew
- **Why?**, which opens a panel with "In plain terms", then "The links behind this" in order, and "Decisions about these things", or "No decision is recorded against them." where there are none
- **View on twin map**, which opens the same connections as a map
- **Full detail**, a fold-out for the technical fields (type, layer, depth, chain, rule, confidence), kept out of sight until you ask

When nothing depends on your choice, the page says "Nothing is recorded as depending on this yet", and adds that nothing is missing, there is simply nothing recorded. When it cannot answer, it says "We could not answer that just now." It never fills the gap with a guess.

Inferred connections are worked out ahead of time. If that has not happened yet, a notice reads "We haven't worked out the indirect connections for your model yet" with a **Work them out now** button. If the model has changed since, a notice says the connections "may be out of date". An **Indirect connections** panel at the foot of the page shows when they were last worked out, how many are current and how many may be out of date.

## Who asks it

The owner or managing director of a services firm who has just heard that a key supplier is in trouble, a contractor is leaving or the booking system keeps failing, and wants to know what sits behind it before deciding anything.

## How to get your answer

1. Model what you depend on: your systems, your suppliers, your teams, and the connections between them. A spreadsheet import is one way to start.
2. Open **Ask** and click the first question.
3. Type the name and pick the match.
4. Read the list from the top. Use **Why?** on any line you do not recognise.
5. Pick **View on twin map** to see the same answer as a picture.
6. Ask the other questions on the page for the same thing: what it is meant to achieve, what could hurt it, who is accountable, what data it holds.

## Doing this without Entelim

You gather the people who know, draw it on a whiteboard and photograph it. It is right for an afternoon, and it leaves with whoever drew it.

## Limits

- The answer is only as complete as what has been recorded. A supplier or person that has not been modelled cannot be picked.
- It follows connections up to three steps from your choice.
- It does not put a figure on lost revenue or hours of downtime.
- Health is not yet read from your data, so that field says so.
- It lists what depends on the thing you pick. It does not rank the lines by severity.

## Frequently asked questions

### Do I need to know ArchiMate or any notation?

No. The page uses plain words. Technical detail sits behind **Full detail** if you want it.

### What if a person is not in the list?

Then they have not been modelled yet. Add them and their connections, then ask again.

### Is the answer an estimate?

No. It is a list of connections that were drawn, or worked out from ones that were, and each line says which. **Why?** shows the links behind it.

### Can I ask about an owner rather than a system?

Yes. The accountability question on the same page lists who is recorded as responsible for a system.

## Related questions and modules

- [Architecture Model](/modules/architecture-model)
- [Digital Twin](/modules/digital-twin)
- [Which contracts renew soon, and what depends on them?](/use-cases/contract-renewals)
- [Which of my people is a single point of failure?](/use-cases/key-person-risk)
