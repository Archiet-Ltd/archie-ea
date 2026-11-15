---
wave: 1
page_family: site
title: "Privacy"
page_role: "What Entelim stores, plainly, matched to the models and settings in the codebase."
source: app/models/user.py, app/models/waitlist_signup.py, app/main/views.py (waitlist form),
  .env.example (mail/AI provider settings), read 2026-09-25
---

# Privacy

## What we collect

**Your account.** If you create an account, we store your name, email address, and a salted hash of
your password — never the password itself.

**What you and your team enter.** Everything you record in your organisation's model — applications,
risks, roadmaps and the rest of what Entelim covers — is stored so it can answer your questions. It
belongs to your organisation and is scoped to it; other organisations on the same instance cannot
read it.

**The waiting list.** If you join the waiting list from the home page, we store your email address
and the fact that you agreed to it, and use it only to tell you about the Entelim launch.

## What we don't do

Entelim does not load third-party advertising trackers or analytics scripts on its public pages. We
do send some of your content to AI providers when you use the AI features. What is sent, to whom
and where it is processed is set out under [AI features](#ai-features) and in the
[sub-processor list](#sub-processors) below.

## Analytics on public pages

Our public marketing pages (home, pricing, features, docs, and similar) may load Google Analytics 4
and Microsoft Clarity, but only after you explicitly accept analytics cookies via the consent banner.
Until you accept, no scripts from these services are requested and no cookies are set.

**Google Analytics 4** collects aggregated usage data — pages viewed, time on page, referral source,
device and browser type — to help us understand how visitors use the site. With Consent Mode
enabled, GA4 defaults to denying all storage (ad_storage, analytics_storage, ad_user_data,
ad_personalization) until you accept. You can opt out at any time by clicking "Cookie settings" in
the footer and choosing "Reject analytics", or by clearing the `analytics_consent` cookie.

**Microsoft Clarity** records anonymous session replays and heatmaps (clicks, scrolls, mouse
movements) to help us improve page layout and content. It does not capture keystrokes or form
input. Clarity only loads after you accept analytics cookies. You can opt out the same way as for
GA4.

Neither service receives data from your organisation's model, your account details, or any
authenticated session. Signed-in application pages never load GA4 or Clarity.

## Email

If email is configured (for password resets and notifications), it goes through the mail provider
the operator of that instance has set up.

## AI features {#ai-features}

When you ask a question in the AI chat or use another AI feature, Entelim sends your question and
the parts of your organisation's model needed to answer it to an AI provider. This can include the
names and descriptions of applications, owners, vendors and risks, and the text of documents you
have uploaded. Email addresses, phone numbers and credentials are removed from the chat before it
is sent. Names are not.

On the hosted service the providers are OpenRouter, which passes the request on to the company that
runs the chosen model, and NVIDIA. Both process data in the United States, and the companies behind
the models OpenRouter routes to may process it in other countries. Until we finish moving off it,
the AI chat also still uses DeepSeek, which processes and stores data in the People's Republic of
China.

These are free services. We have no data processing agreement with any of these providers, and we
cannot promise what they do with what they receive. Their terms may let them keep prompts and use
them to train their models. We ask OpenRouter to prefer providers that say they do not train on or
log prompts, but that is a preference, not a guarantee. Do not put personal data into the AI
features that you would not want processed in those countries.

An organisation administrator can save the organisation's own AI provider key, and then the AI
features use that provider instead. If you self-host Entelim, AI features call only the provider
you configure.

## Sub-processors {#sub-processors}

These AI providers receive customer content from the hosted service. Our hosting, email and payment
providers are listed on request through [contact](/contact).

| Provider | Where it processes | Agreement with us |
|---|---|---|
| OpenRouter, and the model companies it routes to | United States, and the countries where the chosen model company runs | None. Prompts may be kept and used for training |
| NVIDIA | United States | None. Prompts may be kept and used for training |
| DeepSeek (Hangzhou DeepSeek Artificial Intelligence Co., Ltd.), until the AI chat moves off it | People's Republic of China | None. Prompts may be kept and used for training |

All three receive the question and the parts of your organisation's model needed to answer it.

Last updated 24 October 2026.

## If you self-host

Running Entelim yourself means your data stays on your own infrastructure end to end. Nothing about
what you store is sent anywhere Entelim's maintainers can see, unless you choose to send it.

## Removing your data

To close an account and remove its data, or ask what we hold about you, [get in touch](/contact). If
you self-host, you already control the database directly.
