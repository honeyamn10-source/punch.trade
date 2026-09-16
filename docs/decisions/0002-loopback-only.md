# ADR-0002: Loopback-Only Serving
- Status: Accepted
- Date: 2026-09-16

## Context
The service must never reach out unauthenticated, never serve beyond controlled loopback.

## Decision
Interactive demos bind 127.0.0.1 only; CI runs against localhost. No remote socket.

## Consequences
Reproducible demos, honest screenshots, same permission-led rule as portfolio.