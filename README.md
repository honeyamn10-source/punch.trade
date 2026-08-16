# punch.trade

A self-hosted signal platform: strategy engine + backtester + real broker
execution (Zerodha Kite, Binance, and 34+ Indian brokers via OpenAlgo) with a
Chrome extension that overlays live signals and one-tap bracket orders on the
chart pages you already use.

```
market data ──> strategy engine ──> Signal ──> WebSocket ──> extension overlay
                       │                                │
                   backtest                      PUNCH button
                  (same code)                         │
                       │                                v
                   win rate / drawdown        broker API (user's own account)
                                              entry + TP + SL as one bracket
```

Key design decisions (from the original architecture brief):

- **Non-custodial**: orders execute through *your* broker account with *your*
  tokens. The backend never holds money; it holds encrypted access tokens
  (Fernet, at rest) and proxies orders.
- **One evaluation path**: the same `StrategyRunner` drives live bars and
  backtests, so win-rate/drawdown numbers are not fiction.
- **Declarative strategies**: configs reference a fixed indicator/condition
  library — no arbitrary code execution, safe to share.
- **Adapter pattern**: one internal interface, one adapter per broker.

## Repo layout

```
backend/          FastAPI server (REST + WebSocket signal feed)
  app/
    engine.py     StrategyRunner — bar-driven, per-symbol dedup state
    backtest.py   replay of engine against historical bars
    indicators.py SMA / EMA / RSI + cross conditions (no deps)
    strategies.py declarative strategy configs
    feed.py       live feeds: paper / binance (CCXT polling) / kite (ticks)
    broker/       paper.py · kite.py · ccxt_bt.py · openalgo.py
    vault.py      Fernet-encrypted broker token storage
    api.py        REST + WS + audit log (data/signals.json, orders.json)
  static/demo.html  fake broker SPA for testing the overlay
  tests/          pytest unit tests (CI runs them)
extension/        Chrome MV3 extension (overlay + popup)
.github/workflows/ci.yml
```

## Quick start (zero cost, 5 minutes)

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # or source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

1. Open `http://127.0.0.1:8000/demo` — a fake broker page.
2. Load the extension: `chrome://extensions` → Developer mode → **Load unpacked**
   → `extension/` folder.
3. The overlay appears top-right of the chart. Signals land over WebSocket;
   hit **PUNCH** to place a bracket order (entry + TP + SL) on the paper broker.

Backtests (real numbers on real data once a broker is connected):

```powershell
$t = "punch-demo-token"
Invoke-RestMethod -Method Post -ContentType "application/json" `
  -Body '{"broker":"paper","interval":"5m","days":30}' `
  "http://127.0.0.1:8000/api/strategies/rsi-reversal/backtest?token=$t"
```

Tests: `python -m pytest backend/tests -q` (also runs in CI on push).

## Connect real brokers (all free)

### Zerodha Kite (India, NSE/BSE)
1. Get an API key + secret at `developers.zerodha.com` (free; requires a Zerodha
   account).
2. In the extension popup → *Connect a real broker* → enter API key → **Get
   login URL** → log in → paste `request_token` from the redirect URL + secret
   → **Connect Kite**.
3. Live: the backend subscribes to the Kite ticker websocket and builds candles.
   Backtests use real NSE historical data. Bracket orders are placed as
   `product=BO` — one request carries entry + take-profit + stop-loss.

### Binance (global crypto)
1. Create API keys at binance.com (spot trading) — or use the **testnet**
   checkbox for fake money.
2. Paste in the popup → **Connect Binance**.
3. Live signals: the backend polls public OHLCV (no account needed). The BTC
   strategy ships by default; add strategies with `*USDT` symbols for others.

### OpenAlgo (Angel One, Fyers, Dhan, Upstox, …)
1. Self-host OpenAlgo (`pip install openalgo`, it's free), configure broker
   keys inside OpenAlgo.
2. Popup → OpenAlgo host + API key + broker → **Connect**.
3. punch.trade then routes execution through OpenAlgo's unified API (34+
   brokers), including GTT take-profit/stop-loss legs where supported.

Execution always routes through the selected broker — switch in the popup
(`paper` / `kite` / `binance` / `openalgo`).

## Hosting (free tiers)

- **Local pilot**: run `python run.py` on your machine; friends load the
  extension with your server URL. No Chrome Web Store needed (Load unpacked).
- **24/7 server**: Oracle Cloud *Always Free* — includes a reserved static IP,
  which also satisfies SEBI's static-IP requirement for algo API access. Deploy
  with `pip install -r requirements.txt` + a systemd unit / `uvicorn`.
- **Simplest managed**: Render or Fly.io free tier (note: bind port 8000,
  change `HOST`/`PORT` in `app/config.py`; HTTPS recommended before real tokens
  travel over the internet).

## Security & compliance notes

- Broker access tokens are Fernet-encrypted at rest (`data/.secret` key is
  git-ignored; back it up or lose the vault). The extension only ever holds the
  punch.trade session token — never broker credentials.
- Signals and orders are appended to `data/signals.json` / `data/orders.json`
  — your audit trail.
- This is built for private use by you and people you trust. Signals with
  entry/TP/SL are personalized advice in most jurisdictions — SEBI (India),
  SEC (US), FCA (UK), MAS (SG) all regulate this area. Do not monetize or
  publicize without local counsel per market.

## API surface

| Endpoint | Purpose |
|---|---|
| `GET /api/strategies` | strategy list |
| `POST /api/strategies/{id}/backtest` | real backtest stats (win rate, drawdown) |
| `POST /api/orders` | place bracket (entry+TP+SL) on the chosen broker |
| `GET /api/positions` · `GET /api/fills` | reconciliation / audit |
| `POST /api/broker/{kite,binance,openalgo}/connect` | broker onboarding |
| `WS /ws/signals?token=` | live signal feed (snapshot on connect) |

All endpoints require `?token=` (default `punch-demo-token`, change in
`app/config.py`).