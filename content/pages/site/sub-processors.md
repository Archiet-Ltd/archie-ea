---
page_family: site
title: "Sub-processors"
description: "The outside services that handle customer content or personal data for the hosted Entelim service, and where the assistant sends your content."
page_role: "The list of outside services used by the hosted service, with what each receives."
---

# Sub-processors

Last updated: 19 October 2026.

These are the outside services that handle customer content or personal data when Archiet Ltd runs
Entelim for your organisation. They are listed in the [data processing agreement](/data-processing-agreement)
by reference to this page. When the list changes, this page and the date above change with it.

## The list

| Provider | What it does | What it receives |
|---|---|---|
| DigitalOcean | Hosts the servers, the database and the proxy that serve Entelim | Everything stored in the service: your organisation's records, uploaded documents and user accounts |
| SendGrid | Delivers account emails through the mail server set up for the service | The recipient's name and email address and the text of the message: password resets, invitations, verification and notifications |
| OpenAI | Language model and embeddings for the assistant and the AI drafting features | The prompt and the organisation records needed for the answer |
| Anthropic | Language model for the assistant and the AI drafting features | The prompt and the organisation records needed for the answer |
| Google Gemini | Language model for the assistant and the AI drafting features, and analysis of architecture documents your users upload | The prompt and the organisation records needed for the answer; the uploaded document when it is analysed |
| Microsoft Azure OpenAI | Language model for the assistant and the AI drafting features | The prompt and the organisation records needed for the answer |
| DeepSeek | Language model for the assistant and the AI drafting features | The prompt and the organisation records needed for the answer |
| OpenRouter | A gateway that passes a request on to the model vendor chosen for it | The prompt and the organisation records needed for the answer |
| Raygun | Error monitoring, when it is switched on for the service | Details of an error and of the request that caused it |
| PostHog | Product usage analytics for signed-in pages, when it is switched on for the service | A page view or usage event with an account identifier, the organisation identifier and the page address |
| Google Analytics | Aggregate visit statistics on the public website, only after a visitor accepts analytics cookies | Pages viewed, referral source, device and browser type |
| Microsoft Clarity | Anonymous session replays and heatmaps on the public website, only after a visitor accepts analytics cookies | Clicks, scrolls and mouse movements on public pages |

The language model providers receive content only when someone uses an AI feature. Google Analytics and Microsoft Clarity run on the
public website only; signed-in pages never load them.

Services your organisation connects itself, such as its own identity provider or the integrations it
enables, receive data only as your organisation configures them.

## Where the assistant sends your content

The assistant answers questions about your organisation's records. To answer, it sends text to a
language model provider from the list above:

- **Which features.** The assistant chat, the AI drafting features in solution design and architecture
  work, and analysis of uploaded architecture documents.
- **What is sent.** The question or instruction, the conversation so far, and the organisation records
  needed to answer it.
- **Which provider.** The provider and model configured in the settings. If no provider key is
  configured, the AI features are unavailable.
- **Your own key.** Your organisation can add its own model provider key in its settings. Requests
  from your organisation then use that key.
- **Reading and changing.** The assistant reads your data to answer. When it proposes a change to
  your records, the change waits in an approval inbox and is written only after a person approves it.

If you run Entelim yourself, nothing is sent to any provider unless you configure one.

Questions about this list: [contact us](/contact).
