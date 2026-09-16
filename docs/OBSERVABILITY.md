# Observability

## Event log

Every API request is appended as one sanitized JSON line to
`data/logs/events.jsonl`:

```json
{"ts": 1786932646.04, "kind": "api.request", "requestId": "934cc7c61a284e13", "method": "POST", "path": "/api/v1/orders", "status": 422, "ms": 1.0}
```

`app/obs.py` provides `log_event`, `log_request`, counters and error buckets.
All fields pass through the security sanitizer (control chars stripped,
length-capped) — nothing sensitive is ever written.

## Request tracing

- Every response carries `X-Request-Id` (16 hex chars; caller-supplied
  `X-Request-Id` headers are honored).
- Error responses carry the same id inside the envelope:
  `{"error": {"code": …, "requestId": …}}` — log lines and client errors
  correlate directly.
- 5xx responses are logged once with type + message
  (`kind: "api.error"`).

## Counters & metrics

`/api/v1/system/metrics` exposes:

- `counters` — `requests`, plus anything `obs.incr` is told about
- `errorBuckets` — 4xx / 5xx counts
- `signals` — live + stored; `orders` — ledger / filled / rejected / open
- `trades` — closed + stored; `risk` — breaker, consecutive losses, recon, armed

## Deep health

`/api/v1/system/health` checks, in one call:

- **db**: SQLite reachable + schema version
- **feed**: per-symbol staleness (no stale symbols → ok), bar counts
- **brokers**: connected adapters + mode

Returns `status: ok | degraded` with the failing components enumerated. The
dashboard System tab renders health, metrics, storage and per-symbol feed
quality (bars, last-bar age, quality rating GOOD/WARNING/BAD/UNKNOWN, last
error).