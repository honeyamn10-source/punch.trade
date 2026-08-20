"""Strategy Family: Advanced Alpha (research-backed).

Strategies derived from quantitative literature findings:
- Volatility-managed time-series momentum (Barroso & Santa-Clara 2015)
- Hurst-exponent-gated mean reversion (anti-persistence literature)
- Volume-flow imbalance proxy (OHLCV microstructure proxy)
- Trend + carry composite (momentum + funding rate composite)

All strategies consume only OHLCV bar data and are designed for
fee-aware horizons (1h+); 5m microstructure turnover does not survive
realistic exchange fees.
"""
