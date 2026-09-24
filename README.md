![punch.trade](docs/assets/cover.svg)

# punch.trade

<!-- repo-badges:start -->
<div align="center">

[![Stars](https://img.shields.io/github/stars/honeyamn10-source/punch.trade?style=flat-square&logo=github&label=Stars)](https://github.com/honeyamn10-source/punch.trade/stargazers)
[![Forks](https://img.shields.io/github/forks/honeyamn10-source/punch.trade?style=flat-square&logo=github&label=Forks)](https://github.com/honeyamn10-source/punch.trade/forks)
[![Issues](https://img.shields.io/github/issues/honeyamn10-source/punch.trade?style=flat-square&logo=github&label=Issues)](https://github.com/honeyamn10-source/punch.trade/issues)
[![Last Commit](https://img.shields.io/github/last-commit/honeyamn10-source/punch.trade?style=flat-square&logo=github&label=Last%20Commit)](https://github.com/honeyamn10-source/punch.trade/commits/master)
[![License](https://img.shields.io/github/license/honeyamn10-source/punch.trade?style=flat-square&label=License)](https://github.com/honeyamn10-source/punch.trade/blob/master/LICENSE)

[Repository](https://github.com/honeyamn10-source/punch.trade) · [Issues](https://github.com/honeyamn10-source/punch.trade/issues) · [Pull Requests](https://github.com/honeyamn10-source/punch.trade/pulls) · [Actions](https://github.com/honeyamn10-source/punch.trade/actions)

</div>
<!-- repo-badges:end -->

<!-- professional-meta:start -->
<div align="center">

[![ci](https://github.com/honeyamn10-source/punch.trade/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/honeyamn10-source/punch.trade/actions/workflows/ci.yml) [![codeql](https://github.com/honeyamn10-source/punch.trade/actions/workflows/codeql.yml/badge.svg?branch=master)](https://github.com/honeyamn10-source/punch.trade/actions/workflows/codeql.yml)

![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white) ![Browser Extension](https://img.shields.io/badge/Browser%20Extension-4285F4?style=flat-square&logo=googlechrome&logoColor=white) ![Pytest](https://img.shields.io/badge/Pytest-0A9EDC?style=flat-square&logo=pytest&logoColor=white)

[Documentation](docs) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md)

</div>
<!-- professional-meta:end -->


A FastAPI workstation with strategy research, backtesting, risk controls, a local dashboard and a Chrome extension.

[Project website](https://honeyamn10-source.github.io/punch.trade/) · [Build results](https://github.com/honeyamn10-source/punch.trade/actions)

## What it does

- **Inspect strategies.** Explore strategy modules, research tools and backtests.
- **Review risk.** Risk and execution layers sit between a signal and an order.
- **Start in paper mode.** The default feed is synthetic and execution is paper unless explicitly configured.

## Start from source

Python and the dependencies in backend/requirements.txt. Review .env.example and the deployment guide.

```bash
git clone https://github.com/honeyamn10-source/punch.trade.git
cd punch.trade
python -m pip install -r backend/requirements.txt
cd backend
python run.py
```

## Verify

```bash
cd backend
python -m pytest tests -q
```

## Scope

Development workstation. Broker adapters require independent verification. Backtests do not establish future returns; synthetic feed and paper execution are the defaults.

## Find your way around

- [Architecture](docs/ARCHITECTURE.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Test guide](docs/TESTING.md)

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md). Include a minimal reproduction and runtime versions with bug reports; remove credentials from logs.

MIT — see [LICENSE](LICENSE). Third-party dependencies retain their applicable licenses.
