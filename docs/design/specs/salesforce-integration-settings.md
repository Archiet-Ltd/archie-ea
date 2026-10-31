# Salesforce org discovery settings - redesign spec

Block: Settings. Credentials in one card in the page column, discovery in a second, the same family as the GitHub
connector page (https://ui.shadcn.com/examples/forms).

## Job
A platform administrator connects a Salesforce org, checks the connection, and brings its Lightning apps and
AppExchange packages into the portfolio.

## What moves
- The page is the column at most 42rem wide. The credentials card holds three labelled fields and the state:
  "Connected" once credentials are stored, "Not set" before. Save credentials is the one primary action; Test
  connection sits beside it.
- Discovery is its own card. Discover is its action; the result is a table with a checkbox per row, the counts above
  it, the programme to link to and Import selected below it.

## What merges
- Nothing is combined. The three counts and the table keep their meaning.

## What is removed
- Nothing is hidden. The Consumer Secret is no longer a masked placeholder: the field says "Leave blank to keep the
  existing value".

## States
- Empty: the discovery card says to save and test the credentials, then choose Discover.
- Error: an address that is not the org's own Salesforce address is refused under the field and nothing is stored; a
  missing Consumer Key is named. A refused login is reported as that, never as "0 found".
- Test: Salesforce refusing the credentials names what to check; success names the org address.

## Journey that proves it
A platform administrator saves credentials, reloads and reads them back with no secret in the page, is refused for a
wrong address, tests the connection and discovers three items whose count equals the rows.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
