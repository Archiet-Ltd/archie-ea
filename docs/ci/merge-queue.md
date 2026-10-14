# Merge queue

The CI workflow runs on the `merge_group` event (type `checks_requested`), so the
same jobs report on the queue's temporary branch (base plus queued pull requests).

## What differs on a merge_group run

- The content-only skip is disabled: the detector step reports `is_content_only=false`, so every shard, smoke and browser job runs.
- `release-image` does not run (it runs only on push), so the queue never publishes an image.
- `failing-first-proof` and `content-page-tests` are pull-request-only and are skipped; a skipped required check counts as passing in the queue.
- Concurrency uses the queue branch ref, so queue runs never cancel pull-request runs.

## Required checks

Static gates (compile + ratchets), Database gates (schema drift), Boot health (no database), SAST (bandit), Secret scan (gitleaks), Dependency CVEs (pip-audit), the test shards and the aggregate `tests` job, plus the smoke, browser-compatibility and walkthrough jobs already required today.

## The external `merge-gate/lead` status

The lead's gate script sets `merge-gate/lead` on the pull request head commit. It is never set on the queue's temporary commit, so it must not be required for `merge_group` runs. Require it only for pull requests: keep it in the branch-protection or ruleset required statuses for the pull-request merge path (GitHub evaluates required status checks on the PR head before it can enter the queue), and do not add it to any check the queue waits on. If the ruleset applies one required-checks list to both, remove `merge-gate/lead` from it and enforce it on pull requests through a separate ruleset targeting pull requests.

## Settings to enable (repository ruleset, `merge_queue` rule)

- Merge method: SQUASH (or the repository's current method).
- Maximum group size / build concurrency: 1 to start.
- Minimum group size: 1; minimum wait time: 0.
- Status check timeout: 90 minutes.
- The required checks above must be listed under the ruleset's required status checks.
