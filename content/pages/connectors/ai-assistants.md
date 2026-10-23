---
page_family: connector
connector_group: "ai-assistants"
connector_label: "AI assistants"
state: held
capture_status: awaiting_capture
cta: plans
description: "Entelim's own AI assistant answers from your architecture model, checks permission on every change it attempts, and queues the changes it proposes for a person to approve."
source: app/modules/ai_chat/tools/executor.py, config.py, read 17 October 2026
---

# Entelim's AI assistant

Entelim has its own AI assistant, AI Chat, that answers questions from your organisation's
architecture model: what stops if a system fails, which risks threaten it, and which programmes and
strategic initiatives it serves. It works inside Entelim, signed in as you, so it only ever sees your
organisation's data. This page describes what it can do, and what stands between it and a change to
your model. Entelim does not connect to an AI assistant that you run elsewhere.

## What it does

The assistant reads the elements already in your model, the business, application and technology
layers described on [Architecture Model](/modules/architecture-model), and answers from them, so an
answer comes from what you have actually recorded rather than from general knowledge alone. An element
with nothing recorded against it comes back as an honest empty answer, the same way it would on screen,
rather than a guess at a connection that is not there.

These are the same questions behind Entelim's
[digital twin of your organisation](/modules/digital-twin), so the assistant and a person on the Ask
screen read from one model, never two. Read more about the chat itself on [AI Chat](/modules/ai-chat).

## Who uses it

Anyone in your organisation with access to AI Chat can ask it a question from their own account.
Enterprise architects and CTOs typically use it to carry
[what breaks if a service fails](/use-cases/what-breaks-and-who-gets-called) or
[which risks sit on the revenue-critical path](/use-cases/risk-blast-radius) into a conversation rather
than opening a separate screen. See the workspaces for
[enterprise architects](/roles/enterprise-architects) and [CTOs and CIOs](/roles/cto-and-cio).

## Permission and approval

Every change the assistant attempts passes a permission check against the person it is acting for, so
it cannot make a change that person could not make. Reading is not a change, so questions that only
read your model are not put through that check. By default, a change the assistant proposes does not
happen straight away: it goes to an approval queue and nothing changes until a person approves it.
These two controls apply to the assistant's tools. Typed chat commands are a separate path, and this
page makes no claim about them.

Read more about how Entelim protects your data on [Security](/security).

## Limits

The assistant answers only from what is already recorded in your model; it does not discover anything
outside it. It does not answer who is accountable for an element unless ownership is recorded on that
element.

## Frequently asked questions

### Can my own AI assistant use Entelim?

No. Entelim does not currently connect to an AI assistant you run elsewhere. The assistant you can use
is Entelim's own, AI Chat, which works inside the product.

### Does the assistant see other companies' data?

No. It works as the person who is signed in, so it is limited to that person's organisation.

### Can the assistant change anything?

It can attempt a change, but every change it attempts is checked against the permissions of the person
it is acting for, and by default a change it proposes waits in an approval queue until a person
approves it.

### Is every question it answers checked for permission?

No. The permission check applies to changes. Questions that only read your model are answered without
that check.

### Which plan includes it?

See [Entelim pricing](/pricing) for what each plan includes.

## Related connectors and modules

- [Connectors](/connectors)
- [Digital twin of your organisation](/modules/digital-twin)
- [AI Chat](/modules/ai-chat)
- [Architecture Model](/modules/architecture-model)
- [What breaks if this service or platform fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)
- [Which risks sit on our revenue-critical path, and who owns the mitigation?](/use-cases/risk-blast-radius)
- [Enterprise architects](/roles/enterprise-architects)
- [CTOs and CIOs](/roles/cto-and-cio)
- [Security](/security)

## Plan

[See Entelim pricing](/pricing).
