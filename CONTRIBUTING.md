# Contributing to punch.trade

Thanks for your interest in contributing. By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- Read the [README](README.md) and the relevant [documentation](docs/) — especially [docs/TESTING.md](docs/TESTING.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- For a substantial change, describe the problem in an issue before building — this avoids duplicate or incompatible work.
- This is a **non-custodial trading tool**. Design changes that touch order execution, the risk gate, broker reconciliation, or secret storage require extra care and review.

## A useful contribution

1. Work on a branch and keep the change focused.
2. Follow the instructions in [AGENTS.md](AGENTS.md).
3. Use fictional test data and keep local credentials outside Git.
4. Run the quality checks:

```bash
cd backend
python -m pytest tests -q     # full isolated test suite
ruff check .                  # lint
ruff format --check .         # formatting
```

5. Update documentation (docs/) when behavior or configuration changes.
6. Open a pull request explaining the problem, the behavior change, and the validation you ran.

## Honesty rules

- Do not replace tests with hardcoded success or remove failing checks to make a badge green.
- Do not claim a strategy or broker integration is verified without evidence.
- Backtest results must follow the honesty model in [docs/BACKTESTING.md](docs/BACKTESTING.md) — one position, one trade, TP-then-stop is a loss.

## Third-party code

Include the upstream repository, exact version or commit, license, and required notices with any imported code. Preserve author attribution.

## Reporting problems

Open an issue with reproducible steps and the tested version. Do not post account tokens, broker keys, or live trading details. For security-sensitive issues, see [SECURITY.md](SECURITY.md).

---

## License

By contributing, you agree your contributions are licensed under the [MIT License](LICENSE).