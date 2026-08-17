# Strategies

Strategies are **declarative configs** (`app/strategies.py`) that reference a
fixed indicator/condition library — no arbitrary code execution, safe to share.
Nine are shipped (RSI, EMA, SMA, MACD, Bollinger, Donchian/turtle, VWAP, golden
cross, BTC RSI).

## Metadata

Every strategy carries canonical metadata (`_META`): `id`, `name`, `family`,
`version`, `warmup`, `symbol`, `timeframe`, `status`, and a `reason` template.
`strategy_id` is `id@version`; `parameter_snapshot` freezes the tuned parameters
so drift checks compare like-for-like.

## Signals

`engine.Signal` is built from `strategy.condition_met(series)` with:
`entry`, `targetPrice`, `stopLoss`, `targets` (TP1/TP2/TP3 fractions),
`close_time`, a `snapshot` of the decision inputs, and the current
`market.regime_of`. `explain_condition` mirrors the condition logic so the
signal carries a human-readable "why".

## Lifecycle

`app/strategy_status.py` enforces the ladder:

```
DRAFT ──> BACKTESTED ──> RESEARCHED ──> LIVE_ACTIVE ──> DISABLED
                                    └──> LIVE_DEGRADED (drift)
```

- `DRAFT → BACKTESTED`: a backtest ran.
- `BACKTESTED → RESEARCHED`: the research **quality gate passed**.
- `RESEARCHED → LIVE_ACTIVE`: the strategy is explicitly promoted for live.
- `LIVE_ACTIVE → LIVE_DEGRADED`: live drift vs backtest expectancy (see below).
- any state → `DISABLED`.

`composite_score` is never win-rate-only:

```
score = 0.6 × quality gate + 0.2 × parameter stability + 0.2 × live drift
```

`live_drift` is neutral until 3 live trades exist; then it degrades when the
live mean net PnL falls below 0.5× the backtest expectancy.

## Endpoints

- `GET /api/strategies` — all configs
- `GET /api/strategies/status` — ladder + score per strategy (10 min cache;
  snapshots persisted to `strategy_status`)
- `GET /api/strategies/leaderboard` — ranked honest backtest metrics (60 s cache)
- `POST /api/strategies/{id}/backtest` — run the honest backtester
- `POST /api/research/{id}` — research dossier (see RESEARCH.md)