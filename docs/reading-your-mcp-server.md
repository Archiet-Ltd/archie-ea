# Reading your enterprise architecture through an AI assistant

Connect ChatGPT, Claude, or any MCP-compatible assistant to your enterprise
architecture model. The assistant can answer six lens questions about any
element, search for elements by name, and read your business model canvases
and business cases — exactly what a signed-in person sees through the product's
own pages, with the same tenant isolation and the same honest empty states.

## What the assistant can answer

| Tool | What it answers |
|---|---|
| `ask_impact` | If this element fails, what stops and who owns it — the full cross-layer impact chain |
| `ask_strategy` | What strategic initiatives this element is linked to, and how each is tracking against its budget |
| `ask_portfolio` | Which application component this element maps to, for rationalization and duplicate detection |
| `ask_programme` | What work packages this element is part of, whether each is on time and on budget, and what each touches |
| `ask_risk` | What risks threaten this element, and what each risk touches |
| `ask_accountability` | Who is accountable for this element |
| `search_elements` | Find elements by name — returns the id, name, type, and layer the lens tools need as input |
| `get_element` | Full detail for one element: its type, layer, description, and linked solutions and capabilities |
| `list_canvases` | All business model canvases and business cases for your organisation |
| `get_canvas` | The full detail of one business model canvas or business case |

Every tool is read-only. The assistant cannot create, update, or delete
anything in your model. It reads exactly what you would see on the
corresponding page — no more, no less.

## What is never returned

No write operation exists through the MCP surface. The assistant cannot
create elements, edit canvases, approve proposals, or change any data.
Every answer is scoped to your organisation: an element that belongs to
another organisation returns the same "not found" response a signed-in
person would see, with no indication the element exists at all.

Fields that carry no recorded value return a named reason code — for
example, "no risk recorded" or "not costed" — rather than a zero, a blank,
or an estimate. The assistant never invents data to fill a gap.

## Connecting an assistant

The MCP endpoint is at `https://app.entelim.com/mcp`. Authentication uses
OAuth 2.1 with PKCE (S256), the standard flow both ChatGPT and Claude
support for remote MCP servers.

1. In your assistant's connector settings, add a new remote MCP server
   with the URL above.
2. The assistant redirects you to sign in to your account. After signing
   in, you see a consent screen listing the scopes being requested.
3. Grant access. The assistant receives a token scoped to your
   organisation and the `mcp:read` permission.
4. The assistant can now call any of the nine read tools on your behalf.

The token is tied to your user account, not to your organisation as a
whole. It expires after one hour and can be revoked at any time from your
account settings.

Two well-known endpoints publish the server's OAuth metadata so any
standards-compliant client can discover the flow automatically:

- `/.well-known/oauth-protected-resource` — the resource server metadata
- `/.well-known/oauth-authorization-server` — the authorization server
  metadata, including supported PKCE methods and grant types

## Revoking access

Open your account settings, find the active tokens list, and revoke any
token. The assistant's next call returns an authentication error. There is
no delay and no grace period — revocation is immediate.

## Self-hosted installations

A self-hosted install runs the same authorization server against its own
domain. Point your assistant at your instance's `/mcp` URL instead of the
hosted product's. A manually minted personal access token is also
available for single-operator installations where a full OAuth flow is
unnecessary.