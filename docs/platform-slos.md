# Platform service level objectives

Three services carry a published objective: answers, the general API, and
approvals. Each is measured from request traffic the application already
records — no separate metrics store, no external Prometheus server.

## The objectives

| Service | Routes | Availability target | p95 latency target |
|---|---|---|---|
| Answers | `/api/v1/intelligence/*` | 99.9% | 2 seconds |
| API | `/api/v1/*` | 99.9% | 1 second |
| Approvals | `/ai-chat/approvals/*` | 99.9% | 1 second |

Availability counts a 5xx response as bad; a 4xx (a caller's own invalid
request) does not count against it. The target window is a rolling 30 days in
production. This service reports over whatever window the running process has
actually observed since it started — it never claims a 30-day figure it has
not measured, and it never reports 100% for a service with no observed
traffic; an unmeasured service reports `measured: false` with the reason
`"not measured"`.

## How attainment is computed

Every request in the application is recorded, by its matched Flask URL rule,
into the same two counters the app already exports:
`app_http_requests_total` (labelled `method`, `endpoint`, `status_code`) and
`app_http_request_duration_seconds` (labelled `method`, `endpoint`). For each
objective, every recorded rule that starts with that objective's route prefix
is summed:

- **Availability** = (total requests − 5xx requests) / total requests.
- **p95 latency** is read off the histogram's own declared bucket
  boundaries — the first bucket whose cumulative count reaches 95% of the
  matching total — never interpolated and never averaged across requests.
- **Burn rate** (Google SRE workbook) would be (1 − observed availability) /
  (1 − target availability): how many times faster than the 30-day budget
  the service is spending it. **Burn alert** would fire when both a 1-hour
  and a 5-minute burn rate exceed 14.4× (the standard multi-window rule,
  which needs both windows agreeing before it fires so a short traffic blip
  does not trigger a page).

  This process holds one process-lifetime cumulative counter per objective,
  not a time-series store with separate 1-hour and 5-minute samples, so
  neither window can honestly be computed today. `/health/slo` reports
  `burn_rate_1h`, `burn_rate_5m` and `burn_alert` as `null`, with
  `burn_window_reason: "window not measured; process-lifetime counts only"`
  — never one real number presented twice as if it were two independent
  windows. Closing this gap needs the counters bucketed by time, which is
  future work; the lifetime availability and latency figures above remain
  real and usable in the meantime.

## The endpoint

`GET /health/slo` — unauthenticated, alongside `/health` and `/health/db`.
Returns the three objectives' current numbers only: request counts,
availability, p95 latency and burn rate/alert. It never returns an
organisation name, a user id, or a request path — only the fixed objective
names and their aggregate numbers.

## Multi-process deployment

The application runs several worker processes. Each worker holds its own
counters in memory, so a single worker's view under-counts the whole fleet's
traffic. When the environment variable `PROMETHEUS_MULTIPROC_DIR` is set, the
counters are combined across every worker before attainment is computed. That
variable is not set in this deployment today, so `/health/slo` currently
reports only the traffic the process serving the request has itself seen —
`multiprocess_aggregated: false` in the response marks this. Setting the
variable (and giving every worker a shared directory to write its counter
files to) is what closes that gap; no code change is needed once it is.

## Wiring the alert into production watch

Add a step to the production watch that polls `/health/slo` and fails when
any objective's `burn_alert` is `true`:

```
curl -sf https://<host>/health/slo | jq -e 'all(.objectives[]; .burn_alert != true)'
```

`jq -e` exits non-zero when the expression is `false` — `all(...)` is `true`
whenever every objective's `burn_alert` is `false` or `null` (not yet
measured), and only turns `false` when at least one objective is actually
alerting, so a null burn alert counts as healthy rather than failing the
watch on a limitation this endpoint has already disclosed.
