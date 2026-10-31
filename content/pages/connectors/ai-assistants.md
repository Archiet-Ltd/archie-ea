---
wave: 4
page_family: connector
connector_group: "ai-assistants"
connector_label: "AI assistants"
state: on_main
capture_status: awaiting_capture
cta: plans
description: "Entelim's MCP server lets an AI assistant such as Claude read your architecture model and answer what it affects, read-only and scoped to your own data."
source: app/modules/mcp/blueprint.py, app/modules/ai_chat/tools/route_read_tools.py,
  app/modules/oauth_provider/{routes,metadata_routes,assistants_routes,scopes}.py, config.py,
  read 11 October 2026
---

# Connect Claude to your architecture model

Entelim's MCP server for enterprise architecture lets an AI assistant such as Claude read your
organisation's architecture model and answer from it: what stops if a system fails, which risks
threaten it, and which programmes and strategic initiatives it serves. The assistant signs in with
your own Entelim account, so it only ever sees your organisation's data, and every tool it calls is
read-only — it can look at what is in your model, but it can never change any of it. Every Entelim
plan can connect an assistant, including the free Community plan.

## What the Model Context Protocol is

The Model Context Protocol, or MCP, is an open standard that lets an AI assistant call tools in
another application, the same way a plugin lets it search the web or run code. An assistant that
supports MCP adds Entelim as a custom connector the same way it adds any other MCP server, and from
then on it can call the tools below instead of answering from general knowledge alone. Entelim's
ArchiMate MCP server reads from the elements already in your model — the business, application and
technology layers described on [Architecture Model](/modules/architecture-model) — so an answer only
ever comes from what you have actually recorded.

OAuth is the sign-in standard behind the connection, which means the assistant never sees your
password. Adding the connector redirects you to Entelim's own sign-in and consent screen, which names
the assistant and your organisation; you approve the connection as yourself, and the assistant
receives a token scoped to read access only — never to your credentials, and never to write access.

## What it does

Once connected, an assistant can call nine tools, each a read of something already in your model.

| Tool | What it answers |
|---|---|
| Search elements | Find an element in your model by name, with its type and layer. |
| Get element detail | The full detail of one element: its type, layer, description, and the solutions and capabilities it is linked to. |
| What stops if it fails | What stops if an element fails, and who owns it. |
| Strategic initiatives | Which strategic initiatives an element is linked to, and how each is tracking against its budget. |
| Portfolio and cost | Which application component an element maps to, and its cost figures where the person who connected the assistant has budget access. |
| Work packages | Which work packages an element is part of, whether each is on time and on budget, and what each one touches. |
| Risks | Which risks threaten an element, and what each risk also touches. |
| List canvases | Every Business Model Canvas and business case recorded for the organisation. |
| Read a canvas | The full detail of one Business Model Canvas or business case. |

An element with nothing recorded against it comes back as an honest empty answer, the same way it
would on screen, rather than the assistant guessing at a connection that is not there. These are the
same questions behind Entelim's [digital twin of your organisation](/modules/digital-twin), so an
assistant and a person in the AI chat read from one model, never two.

## Who uses it

Anyone in your organisation who already uses an AI assistant can connect it from their own account,
under Account > Connected assistants — no administrator has to set it up first. The assistant then
reads at the level the person who connected it can see on screen: a reviewer sees what a reviewer can
see, and someone without budget access does not see cost figures through the assistant either. An
administrator sees who in the organisation has connected an assistant and can disconnect any of them.
Enterprise architects and CTOs typically connect an assistant to carry
[what breaks if a service fails](/use-cases/what-breaks-and-who-gets-called) or
[which risks sit on the revenue-critical path](/use-cases/risk-blast-radius) into a conversation they
are already having, rather than opening a separate screen — see the workspaces for
[enterprise architects](/roles/enterprise-architects) and [CTOs and CIOs](/roles/cto-and-cio).

## Set it up

1. In your AI assistant, add a custom connector with the address `https://entelim.org/mcp`.
2. The assistant redirects you to Entelim to sign in, if you are not signed in already.
3. Entelim shows a consent screen naming the assistant and the organisation whose data it is asking
   to read. Approve it.
4. Ask the assistant a question about an application, a risk or an initiative in your model.

## What you see afterwards

The answer appears inside your own AI assistant, not on an Entelim screen: the assistant names the
element it read and what it found linked to it, in its own words rather than a raw export. The
connection changes nothing about what you see signed in to Entelim itself — the model and the
[AI chat](/modules/ai-chat) read the same data the assistant just did.

## Reading the same model from your own scripts

The same model is reachable from your own code as well as from an assistant. Entelim's enterprise
architecture API issues tokens carrying a read-only scope, including a service-identity token for a
scheduled job that has no person behind it. Tokens issued to an assistant and tokens issued to a
script are kept completely separate: neither can be used in place of the other.

## Security and permissions

Every tool an assistant can call is read-only — none of them creates, edits or deletes anything in
your model. The organisation an assistant reads from comes from its own token, never from anything
the assistant sends in a request, so a token issued to one organisation cannot be pointed at another
organisation's data. Anything shaped like a password, a key or another credential is withheld from
every answer an assistant receives, whatever the underlying screen would otherwise show. You see
every assistant connected to your account, under Account > Connected assistants, and disconnect any
of them in one step; an administrator can disconnect an assistant connected by anyone else in the
organisation. Disconnecting stops access at once — there is nothing left for the assistant to read
with the old connection. Read more about how Entelim protects your data on [Security](/security).

## Limits

The connector answers only from what is already recorded in your model; it does not discover
anything outside it. It does not answer who is accountable for an element — use
the [AI chat](/modules/ai-chat) for that question, or record ownership directly on the
element instead. The assistant uses your own AI assistant subscription: Entelim's side of the
connection adds nothing to your bill.

## Frequently asked questions

### Can Claude read our architecture model?

Yes. Claude, or any AI assistant that supports the Model Context Protocol, can add Entelim as a
custom connector and read your architecture model once you approve the connection, the same way it
connects to any other MCP server.

### Does the assistant see other companies' data?

No. The connection is scoped to your own organisation by the token the assistant holds, not by
anything the assistant itself sends, so a connected assistant cannot reach another organisation's
model.

### Can the assistant change anything?

No. Every tool it can call is read-only. There is no tool or scope that lets a connected assistant
create, edit or delete anything in your model.

### How do I disconnect an assistant?

Go to Account > Connected assistants and remove it there. Access stops at once. An administrator can
also disconnect an assistant connected by anyone else in the organisation.

### Which plan includes it?

Every Entelim plan, including the free Community plan, can connect an assistant.

### Which assistants can connect?

Any AI assistant that supports the Model Context Protocol over a standard OAuth connection can add
Entelim as a custom connector, the same way it would add any other MCP server.

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

The assistant connector is included on every plan, including the free Community plan.
[See Entelim pricing](/pricing).
