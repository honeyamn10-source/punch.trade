# Security

## Threat model

punch.trade binds to `127.0.0.1` and trusts the local user. The API token
guards against accidental exposure (e.g. other local processes, DNS
re-binding of browser origins) — not against a hostile local user.

## Current controls

- **Loopback only** — `config.HOST = "127.0.0.1"`. Do not expose the port.
- **No token in URLs** — REST uses the `X-Punch-Token` header; the
  WebSocket requires an `{"type":"auth","token":...}` message within 5
  seconds (close 4401 otherwise). The token never appears in browser
  history or server logs.
- **Demo-token tripwire** — LIVE mode refuses to arm while
  `PUNCH_TOKEN` is the default `punch-demo-token` (startup check in
  `config.validate_config()` and runtime check in `risk.set_mode()`).
- **Vault** — broker credentials are encrypted at rest (`data/vault.json`
  + key in `data/.secret`, gitignored). The extension never holds broker
  credentials; only the backend does, in memory after restore.
- **No remote code** — the extension has `storage` permissions only, no
  `<all_urls>`, and interpolates signal data through `esc()`.
- **Strict static surface** — only `/static`, `/demo`, `/dashboard` are
  served; `data/` is not web-exposed.
- **Validation** — pydantic constraints on order fields (qty ≥ 1,
  prices > 0, qty ≤ `MAX_QTY`); typed rejections with stable codes.

## Environment

| Variable | Purpose | Default |
|----------|---------|---------|
| `PUNCH_TOKEN` | API token (min 8 chars) | `punch-demo-token` |
| `PUNCH_MODE` | `research` \| `paper` \| `live` | `paper` |
| `PUNCH_SIGNAL_TTL` | signal freshness window (s) | `300` |
| `PUNCH_MAX_POSITIONS` | max open positions | `5` |
| `PUNCH_MAX_QTY` | max qty per order | `10000` |
| `PUNCH_DAILY_LOSS_PCT` | daily realized-loss limit (%) | `5.0` |
| `PUNCH_FEED_STALE_AFTER` | stale-feed order cutoff (s) | `max(30, 5·BAR_SECONDS)` |

## Hardening backlog (from docs/AUDIT.md)

- Session auth + first-run admin setup, extension pairing codes (AUD-002
  follow-up), `storage.local` instead of `storage.sync` (AUD-015).
- Rate limiting on `/api/*` (AUD-016).
- Structured rotating logs (AUD-013).
- CORS stays disabled (same-origin dashboard; extension proxies through
  its background worker with explicit host permissions).