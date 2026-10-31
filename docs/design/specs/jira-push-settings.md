# Jira push settings - redesign spec

Block: Settings. One column of cards, each at most 42rem wide, in the order a person uses them.

## Job
A platform administrator connects Jira, checks the connection, pushes the portfolio and the Kanban board, and sees
what has been pushed.

## What moves
- Connection status (state badge, last push, last error) with Test Connection and Push to Jira in its footer.
- Configuration: Jira Base URL, Username / Email, API Token, Project Key, Issue Type, Country Filter and the Enable
  switch; one Save settings button in the card footer.
- Push status and the latest job, the Kanban pushes, the application push, the field mapping table and the note on
  which system wins follow as their own cards.

## What merges
- Coloured dots and pill spans become outline badges that say the state in words.

## What is removed
- Nothing is hidden. Icon-only decoration is dropped; every button keeps its action and gains a plain label.

## States
- Empty: before any push, the counts card says nothing has been pushed; the Kanban counters read a dash until loaded.
- Error: an address the platform refuses is explained beside the field and the form keeps what was typed; a failed
  status read says "Status unavailable", never a zero.
- The API token is never written back; the field says it keeps the stored value when left blank.

## Journey that proves it
An administrator saves Jira settings, reloads and finds them saved without the token; saves an address that is
refused and reads why with the fields kept; presses Test Connection and reads the answer.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
