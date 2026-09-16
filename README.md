<!-- punch.trade | Bittu Sharma | ultra-level professional README -->
<p align="center">
  <strong style="font-size:3rem;color:#0EA5E9;">punch.trade</strong>
</p>
<p align="center">
  <em style="font-size:1.2rem;color:#94A3B8;">Trading automation platform with honest backtests</em>
</p>
<p align="center">
  <img src="https://img.shields.io/badge/Python-Python-blue?logo=python&logoColor=white" alt="Python"/>  <img src="https://img.shields.io/badge/FastAPI-FastAPI-blue?logo=fastapi&logoColor=white" alt="FastAPI"/>  <img src="https://img.shields.io/badge/PostgreSQL-PostgreSQL-blue?logo=postgresql&logoColor=white" alt="PostgreSQL"/>  <img src="https://img.shields.io/badge/Redis-Redis-blue?logo=redis&logoColor=white" alt="Redis"/>
  <img src="https://img.shields.io/badge/License-MIT-blue" alt="MIT License"/>
  <img src="https://img.shields.io/badge/Loopback-Only-0EA5E9" alt="Loopback Only"/>
  <img src="https://img.shields.io/badge/ADR-Trail%20(0001..0003)-F59E0B" alt="ADR Trail"/>
</p>

---

## Why this exists

A professional trading automation platform with honest backtests built to the portfolio ultra-level standard:
honest code, loopback-only demos, zero personal emails in history, and every
architectural decision recorded in the ADR trail.

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/honeyamn10-source/punch.trade.git
cd punch.trade
# Follow repo-specific setup instructions
```

---

## Features

- Professional codebase with full test coverage
- Loopback-only serving — zero unauthenticated remote access
- ADR trail documenting all architectural decisions
- CI/CD pipeline with lint, test, typecheck, and build
- Professional identity on all commits

---

## Architecture

```mermaid
graph TB
    subgraph "Client"
        UI[Web UI / CLI]
    end
    subgraph "Server"
        API[API Layer]
        DB[(Database)]
    end
    UI --> API
    API --> DB
    API -->|Loopback Only| LB[127.0.0.1]
```

---

## Security

- Loopback-only serving (127.0.0.1)
- Professional commit identity
- Zero personal emails in history
- ADR trail for all decisions

---

## License

MIT © 2026 Bittu Sharma
