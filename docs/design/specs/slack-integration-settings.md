# Slack AI Architect settings - redesign spec

Block: Settings. One card of connection settings in the page column, the same family as the GitHub connector page
(https://ui.shadcn.com/examples/forms).

## Job
A platform administrator connects the AI Architect to Slack and is sure it saved: the bot token, the signing secret,
which channels to watch, and the two behaviours (answer mentions, watch for application names).

## What moves
- The page is the column at most 42rem wide with one card. The title row carries the state: "Connected" once a bot
  token is stored, "Not set" before.
- Each setting is a field with its label and a sentence under it. The two behaviours are switches with their label.
- Save configuration is the one primary action; Test connection sits beside it and reports in a sentence.
- The Events API set-up is a second card below; its request URL is this server's own address.

## What merges
- The scopes sentence moves into the card description. Nothing else is combined.

## What is removed
- Nothing is hidden. The token is no longer written into the page: the field says "Leave blank to keep the existing value".

## States
- Empty: state "Not set", the token field shows the Slack token shape.
- Error: a token that does not start with xoxb- and channel names instead of channel IDs are refused in words under the
  field, and what was typed in the other fields is kept; nothing is stored by a refused save.
- Test: with no token typed or stored the result says to enter one; a refused token says Slack did not accept it.

## Journey that proves it
A platform administrator saves a test token, signing secret, channels and one switch, sees Connected, reloads and reads
every setting back with no secret in the page, then is refused for a wrong token and wrong channel names.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
