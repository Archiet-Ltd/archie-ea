---
wave: 2
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

Ask the AI chat in your own words: "what happens if our booking system goes down?" Entelim lists everything recorded as depending on it, up to three steps away, and shows who owns each part and why each link is there.

## The question

You are not an architect and you do not want to become one. You want a straight answer to a plain question. If this person leaves, this supplier drops the ball or this system goes down, what actually happens to the business? Today the answer lives in the head of whoever set things up, and they are busy.

## What Entelim answers

Name the team, service or system in your question. The chat answers with a card for the answer, a summary line giving the number of connections found, and one card for each thing that depends on your choice.

Each card shows:

- the **name** of the thing affected, which opens it in the Composer
- the **owner**, or "No owner recorded" where none is recorded
- its **health**, or "Health not recorded"
- the **source** of the connection: where it was read from, or "Worked out from the model" on a connection Entelim inferred from the ones you drew
- **The links behind this**, which gives the connection in plain terms, the links it came from in order, who drew each one and when, and the decisions about these things, or "No decision is recorded against them." where there are none

When nothing depends on your choice, the answer says "Nothing is recorded as depending on this yet", and adds that nothing is missing, there is simply nothing recorded. It never fills the gap with a guess.

Inferred connections are worked out ahead of time. If that has not happened yet, a notice reads "We haven't worked out the indirect connections for your model yet" with a **Work them out now** button. If the model has changed since, a notice says the connections "may be out of date".

## Who asks it

The owner or managing director of a services firm who has just heard that a key supplier is in trouble, a contractor is leaving or the booking system keeps failing, and wants to know what sits behind it before deciding anything.

## How to get your answer

1. Connect GitHub, or import a file. Entelim proposes the systems and dependencies it finds and you approve them. Suppliers and people you add by hand, or bring in with a spreadsheet.
2. Open the **AI chat** and ask what happens if the supplier, person or system fails.
3. Read the cards from the top. Open **The links behind this** on any line you do not recognise.
4. Click a name to see the same thing in the Composer, with everything it connects to.
5. Ask the follow-ups in the same chat: what it is meant to achieve, what could hurt it, who is accountable, what data it holds.

## Doing this without Entelim

You gather the people who know, draw it on a whiteboard and photograph it. It is right for an afternoon, and it leaves with whoever drew it.

## Limits

- The answer is only as complete as what has been recorded. A supplier or person that has not been modelled cannot be picked.
- It follows connections up to three steps from your choice.
- It does not put a figure on lost revenue or hours of downtime.
- Health shows what is recorded; where none is, the card says so.
- It lists what depends on the thing you pick. It does not rank the lines by severity.

## Frequently asked questions

### Do I need to know ArchiMate or any notation?

No. The chat answers in plain words, and every card links to the record behind it if you want the technical detail.

### What if a person is not in the list?

Then they have not been modelled yet. Add them and their connections, then ask again.

### Is the answer an estimate?

No. It is a list of connections that were drawn, or worked out from ones that were, and each line says which. **The links behind this** shows them.

### Can I ask about an owner rather than a system?

Yes. Ask the chat who is accountable for a system and it lists who is recorded as responsible.

## Related questions and modules

- [Architecture Model](/modules/architecture-model)
- [Digital Twin](/modules/digital-twin)
- [Which contracts renew soon, and what depends on them?](/use-cases/contract-renewals)
- [Which of my people is a single point of failure?](/use-cases/key-person-risk)
