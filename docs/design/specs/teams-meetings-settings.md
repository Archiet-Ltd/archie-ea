# Teams Meeting Intelligence settings - redesign spec

Block: Settings. One card of subscription settings in the page column, the same family as the GitHub connector page
(https://ui.shadcn.com/examples/forms).

## Job
A platform administrator has Entelim read Teams meeting transcripts and turn decisions and risks into governance
signals, and is sure the settings saved.

## What moves
- The page is the column at most 42rem wide. One card holds the Notification URL, two switches (Transcript analysis,
  Create governance signals), the state ("Subscribed" or "Not subscribed") and the two actions: Save settings is the
  one primary action, Subscribe to call records sits beside it.
- The active subscription, when there is one, and how it works are cards below.

## What merges
- The Microsoft 365 prerequisite is one notice above the card with a link to the connectors page.

## What is removed
- Nothing is hidden.

## States
- Empty: "Not subscribed"; while the Microsoft 365 connector is not set up the subscribe control is disabled and the
  notice says why and where to set it up.
- Error: an address that is not https is refused under the field, the typed text is kept and nothing is stored.
- Subscribe: success names the subscription and when it expires; a refusal is reported in a sentence.

## Journey that proves it
A platform administrator saves the address and a switch, reloads and reads them back, is refused for an http address,
and finds the disabled subscribe control with its reason and a working link.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
