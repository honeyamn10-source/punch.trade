# AI Analyst

`app/ai/` is a local, private, offline-safe analyst. It reads a strategy's
research dossier and writes a natural-language assessment.

## Hard rules

1. **Auto-detect, never download.** Only models already installed in Ollama
   are used (`ollama list` → first `qwen2.5*`, largest preferred; override with
   `PUNCH_OLLAMA_MODEL`, host with `PUNCH_OLLAMA_HOST`). The app never runs
   `ollama pull`.
2. **Whitelist-only prompts.** `build_prompt` passes *only* research fields
   (metrics, quality gate, parameter stability, walk-forward, bootstrap,
   regimes, status, drift) into the model. Broker credentials, vault contents
   and order payloads are structurally excluded — verified by tests.
3. **Offline-safe.** Every failure path (no model, Ollama down, timeout, empty
   reply) returns `{model: null, analysis: null, error: "<hint>"}` — never a
   crash, never a secret, never a blocking wait for the dashboard.
4. **Sanitized output.** Model text passes through the same sanitizer as all
   user-supplied strings before it is stored or displayed.

## Endpoints

- `GET /api/ai/status` — `{enabled, model, host, reason}` (and `/api/v1/ai/status`)
- `POST /api/ai/analyze/{strategy_id}` — builds the dossier (research report +
  lifecycle status + live drift) and runs the local model. `/api/v1/ai/analyze/{id}` likewise.

## Verified behavior

With `qwen2.5-coder:7b` installed, a live run produced a
VERDICT / STRENGTHS / RISKS assessment in ~7 s, correctly quoting the research
numbers (e.g. "walk-forward consistency is low (33%)") and the honest
DEGRADED sample verdict — the model sees exactly what the dashboard shows.