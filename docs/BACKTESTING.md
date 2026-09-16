# Backtesting

`POST /api/strategies/{id}/backtest` replays the **same** `StrategyRunner`
used live (docs/ARCHITECTURE.md), so there is exactly one code path.

## Fill model (conservative)

- Signal fires on bar close → entry fills **at that close**.
- Each bar: SL is evaluated first; if the bar touches a TP *and* the SL,
  the SL is booked (worst case).
- Multi-TP: one level per bar, equal fractions summing to one position —
  so the backtest equity math is consistent (no double-counting).
- No fees/slippage modeled (paper live fills include slippage, so live
  paper results will trail the backtest slightly).

## Metrics

Per-exit events (a multi-TP position produces several events): win rate,
net return %, max drawdown (on cumulative % equity), Sharpe (per-event,
**non-annualized**), profit factor, avg win/loss %, avg bars held, exit
split (TP1/TP2/TP3/SL counts).

## Honesty rules

- All exits count — every losing exit is in the numbers; no cherry-picking.
- Leaderboard uses the same paper feed for every strategy (60s cache).
- Minimum sample: < 60 bars → `error` instead of fake numbers.
- Known caveats (AUDIT.md AUD-010/AUD-011, pending):
  - fees/slippage/gap costs are not modeled;
  - Sharpe on tiny samples has no confidence bounds;
  - per-position vs per-event stats are not separated yet.
- Never fabricate results: if data is missing the API says so
  (`"error": "Not enough historical bars to backtest"`).