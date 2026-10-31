---
wave: 4
page_family: function-per-segment
use_case_id: UC-S2-04
segment_id: S1
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/import-from-jira-and-github
icon: github
---

# Connect GitHub and ask what breaks, without typing every system in

**For startup and scale-up CTOs who already track their systems somewhere else.**

Connect GitHub. Entelim reads your repositories, proposes the services, dependencies and owners it finds, and you
approve them in one step. Every fact links back to the repository and file it came from. If your list lives in
ServiceNow instead, you can pull it from there too.

## The situation

"This is one more tool someone has to maintain" is the objection that kills most architecture
tooling at a fifty-person company. Nobody has a headcount for keeping a diagram current by hand,
and the list that matters — what applications you run, who owns each one — is already written down
somewhere: a CMDB an operations team maintains, a backlog an engineering team works from. The
question is whether a new tool reads what already exists, or becomes a third place that drifts out
of date the moment nobody is watching it.

## Who asks it

A CTO or VP of Engineering at a fifty-to-500-person company, setting up Entelim for the first time
and deciding whether the application list has to be re-typed or can be read from where it already
lives. The same question comes from whoever owns the CMDB or Jira instance and gets asked to "just
export a list" for the third time that quarter.

## How to build the model from GitHub

1. Sign up free and, on the "Bring in your company" step, enter your **GitHub organisation** and a **read-only
   token**. The token is stored encrypted for your organisation and never shown again. Choose **Connect GitHub**.
2. Carry on. The import runs in the background, so nothing waits on it.
3. Entelim proposes an application for each deployable repository, the parts that belong to it, who owns each one
   from the code-owners file and the teams, and the links the code states between repositories: a dependency
   manifest, a docker-compose service or a deploy workflow.
4. Open the review page. It lists what GitHub showed, each with why it was proposed and the file it was read from.
   Choose **Approve all**, or skip the ones that stay out of the model. Nothing enters the model until a person
   approves it.
5. Open the **AI chat** and ask what breaks if a service fails. Each connection in the answer shows the
   repository, the file and the commit it was read at, with a link to the file on GitHub.

A repository that is later archived or disappears from your organisation is flagged on its application, never
deleted. A later read proposes only what is new.

You can connect Jira on the same step, and Entelim reads your issues into the model.

## How to pull your application list from ServiceNow

1. In ServiceNow, register an OAuth2 application under **System OAuth > Application Registry** and
   grant it read access to your CMDB (the `cmdb_read` scope). Note the instance URL, client ID and
   client secret it gives you.
2. In Entelim, open the **ServiceNow Connector** screen in Admin and enter the **Instance URL**,
   **Client ID** and **Client Secret** from that registration.
3. Optionally set a **CI Query Filter** — a ServiceNow encoded query such as
   `operational_status=1^install_status=1` — to limit the pull to the CIs you want. Turn on
   **Enable Connector** and save.
4. Click **Sync Now**. Entelim fetches Application CIs from your CMDB's `cmdb_ci_appl` table in the
   background and confirms the sync has started.
5. The **Connector Status** panel on the same screen shows whether the connector is enabled and the
   date and time of the last sync.
6. Open the **Applications** module to see the result: each CI becomes an application record,
   matched to an existing one by name where the names agree, or created as a new one where they
   do not.

## What the pull brings across

Each ServiceNow CI arrives as an application record with its name, a technology type worked out
from the CI's ServiceNow class (an Application CI becomes an Application; most other classes arrive
as a generic Application Component), and the business owner recorded against it in ServiceNow. A CI
with no business owner set on the ServiceNow side arrives in Entelim with that field blank — an
honest gap, not a guess.

## Doing this without a connector

Ask whoever owns the CMDB for an export, open it in a spreadsheet, and copy each application's name,
type and owner into wherever you keep your architecture record. Repeat the export and the copying
every time an application is added, retired or changes hands, since nothing is watching the CMDB on
your behalf. That repeat work, done by hand every time something changes, is exactly what the
connector removes.

## Limits

The ServiceNow pull is one-way: it reads the `cmdb_ci_appl` table and writes nothing back to
ServiceNow. It does not bring across servers, databases, incidents or changes, only Application CIs.

Each ServiceNow sync runs when an admin clicks Sync Now.

Incoming CIs are matched to existing applications by name. A CI whose name does not match an
existing application creates a new one rather than merging into a close variant, so keep names
consistent on the ServiceNow side to avoid near-duplicate entries.

## Frequently asked questions

### Does Entelim read my ServiceNow CMDB on its own?

You choose when. A ServiceNow pull runs when an admin clicks Sync Now on the ServiceNow Connector screen. The sync fetches Application CIs from your CMDB's `cmdb_ci_appl` table and adds or
updates the matching application record in Entelim, matched by name.

### Can I connect Jira to Entelim as well?

Yes. Enter your Jira site address, your email and an API token on the "Bring in your company" step, and
Entelim reads your issues into the model.

### Do I need a ServiceNow administrator to set this up?

Yes, briefly. Someone with access to System OAuth > Application Registry needs to register an
application and grant it CMDB read access, then hand you the instance URL, client ID and client
secret to enter into Entelim. After that, running a sync is a single click.

### What happens if I disable the connector?

The applications Entelim already pulled in stay in your inventory; disabling the connector only
stops future syncs. Clicking Sync Now while the connector is disabled has no effect, and the
Connector Status panel shows Disabled until you turn it back on and save.

## Related questions and modules

- [Applications](/modules/applications), the module that holds the result of every sync.
- [Connectors](/connectors), for every way to bring systems in, GitHub first.
- [What breaks if this service or platform fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)
- [Show me what I own and what depends on it, so I know what I am on the hook for](/use-cases/what-i-own)
- [Import our existing Archi or Open Exchange model](/use-cases/import-archimate-model), another way to bring systems in without typing them.

## Plan

Entelim's plans differ by how many people can edit, not by which modules or connectors you get, so
the ServiceNow connector is there to use on every plan, including the free self-hosted edition. [See
Entelim pricing](/pricing).
