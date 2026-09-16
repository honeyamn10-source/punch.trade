# Research Layer

The research layer (`app/research.py`) produces one honest research dossier per
strategy from historical bars. It is the product-side twin of the backtester:
both share `CompletedTrade`, `pnl.summary_stats` and `ExecutionCostConfig`, so
research numbers can never disagree with backtest numbers.

## Report contents

| Section | What it answers |
|---|---|
| `metrics` | `summary_stats` over the **test split** only |
| `qualityGate` | composite check: win rate vs noise floor, profit factor, trade count, drawdown, sample quality; `passed` + `score` + `reasons` |
| `walkForward` | rolling train/test windows — consistency of win rate / PF across windows |
| `parameterStability` | perturbation of SL/TP/periods around the shipped values; worst-case score |
| `bootstrap` | seeded Monte-Carlo resample of trade outcomes → mean/std expectancy (needs ≥ 5 trades) |
| `regimePerformance` | per-regime breakdown (TRENDING_*/RANGING_*) from `market.regime_of` |
| `sample` | honest summary of the data (bars, span, splits, windows used) |

## Honesty rules (enforced in code, covered by tests)

- Splits are **chronological** (`split_chronological`) — never shuffled.
- The backtest needs ≥ 60 bars; research needs ≥ 100 bars.
- Walk-forward windows adapt so every test slice still has ≥ 60 bars.
- Parameter stability runs on train **+** validation data (never test).
- Bootstrap is seeded (`seed` config) → reproducible.
- `CompletedTrade` carries the `regime` of its exit bar so drift checks and
  regime breakdowns share one source of truth.

## API

`POST /api/research/{strategy_id}` (and `/api/v1/research/{strategy_id}`)
accepts a `ResearchReq` (extends `BacktestReq`: `trainPct`, `valPct`, `testPct`,
`walkForwardWindows`, `bootstrapIterations`, `seed`). 422 on invalid research
config, 400 when fewer than 100 bars are available.

Every run is persisted to the `research_runs` table (see STORAGE.md) and feeds
`strategy_status.compute_status` (see STRATEGIES.md).