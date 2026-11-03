---
page_family: function-per-segment
use_case_id: UC-S3-10
segment_id: S3
state: briefed
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
cta: plans
capture_status: live
url_slug: /use-cases/ai-assistant-for-your-architecture
icon: sparkles
---

# Let our AI assistant query the model instead of guessing

**For enterprise architecture teams already using AI assistants across their toolchain.**

## The question

Your team's AI assistant answers architecture questions from general knowledge, not from your actual
model. It sounds confident either way — only one of those is trustworthy.

## What Entelim already does, and what it's building

Entelim's own [AI Chat](/modules/ai-chat) is live today, on a paid plan: one chat page backed
directly by your model data: it reads your model's own records and shows which ones it cites, so
you can check an answer rather than take it on faith.

An AI assistant your team already uses elsewhere can read the same model too. Entelim's MCP server
at `https://entelim.org/mcp` gives an assistant such as Claude eleven read-only tools over the same
questions Ask answers. The assistant signs in with OAuth as the person who connected it and sees
only what that person can see, on every Entelim plan, including the free Community plan. See
[Connect Claude to your architecture model](/connectors/ai-assistants) for the tools and the set-up
steps.

## Related

- [AI Chat](/modules/ai-chat)
