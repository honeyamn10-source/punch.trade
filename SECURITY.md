# Security Policy

## Supported versions

| Version | Supported          |
| ------- | ------------------ |
| master  | :white_check_mark: |
| < 0.4.0 | :x:                |

Security fixes are applied to the latest commit on `master`. This is a self-hosted, non-custodial tool — keep your deployment updated.

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

Email the maintainer privately with the subject `[punch.trade] Security`, optionally GPG-encrypted, and include:

- Description of the vulnerability and potential impact
- Affected endpoint, module, or data path
- Reproduction steps (fictional data only — never broker tokens or account details)
- Suggested fix, if known

You will receive an acknowledgement within 5 business days and a remediation plan or a written explanation if the finding is not a vulnerability.

## What we take seriously

- Broker token storage and the Fernet vault (key rotation, at-rest encryption)
- Session / CSRF handling on the dashboard
- Authorization boundaries between research, paper, and live modes
- The risk gate: position limits, daily-loss limits, circuit breaker, type-safe broker reconciliation
- Rate limiting and the sanitized log surface

## Deployment guidance

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [docs/RISK.md](docs/RISK.md). Keep `PUNCH_MODE` at `paper` until you have validated live flows. Back up `data/.secret` — loss means the vault is unrecoverable.