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

## Class-based `StrategyRegistry` (alpha family)

Separate from the declarative configs above: `app/strategies/base.py`
`StrategyRegistry` + `@register_strategy` classes (`app/strategies/*/`).
These are pure functions of OHLCV bars (+ perp `funding` when the provider
annotates it); each carries a `parameter_schema`, warmup, family, and
`generate_signal(bars, idx)` returning `Signal | None`. They are NOT yet
wired into the legacy engine/backtest endpoints (which use dict configs).

| family | strategy | idea | source |
|---|---|---|---|
| TREND | `punch_vol_managed_momentum` | smoothed ROC with inverse-vol sizing (Barroso-Santa-Clara) | J |
| REVERSION | `punch_hurst_reversion` | Hurst H<0.5 gates mean reversion | K |
| BREAKOUT | `punch_volume_flow` | CLV×volume flow + VWAP regime | L |
| CARRY | `punch_trend_carry` | funding carry when APY ≥ gate, trend-only fallback | M |
| ENSEMBLE | `punch_alpha_ensemble` | majority vote: volume-flow + vol-managed momentum + adaptive trend; `min_votes` (default 2), stop = avg of agreeing members | — |

`punch_alpha_ensemble` accepts `use_flow` / `use_momentum` / `use_trend`
switches; `Strategy.__init__` merges schema defaults for subclass params
(parameters not in the schema are discarded).

Honest live backtest results (real Binance OHLCV, 1000 bars, next-open
fills, intrabar stops, 6 bps/side) are logged in CHANGELOG under
[0.4.0] — the ensemble was net-positive on 3 of 4 jobs (best ETH/USDT 1h
+17%, PF 2.68); `trend_carry` abstained while funding APY stayed below its
gate rather than emitting forced signals.