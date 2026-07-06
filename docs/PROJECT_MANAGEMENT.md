# Project Management

## Module Boundaries

- `apps/problem_account_registry`
  - Local account ledger UI, CRUD, account status, notes, version history, daily journal export, and generated chart linking.
- `apps/trade_kline_web`
  - Statement upload UI, local job queue, and calls into the K-line generator.
- `tools/trade_kline_tool`
  - Statement parsing, GMT/GMT+3 alignment checks, read-only M1 quote cache, and chart HTML generation.
- `scripts`
  - One-click start, stop, and health-check entry points.
- `docs`
  - Operations, release process, and project notes.

## Branching

- `main`: stable local version.
- `feature/*`: new features, for example `feature/chart-library`.
- `fix/*`: bug fixes.

## Release Checklist

1. Run `scripts\health_check.ps1`.
2. Open `http://127.0.0.1:8776` and confirm the ledger loads.
3. Open `http://127.0.0.1:8765` and confirm the K-line upload page loads.
4. Generate one chart from a test statement when chart logic changes.
5. Confirm the account registry chart library can see newly generated charts.
6. Update `docs\CHANGELOG.md`.

## Data Policy

Do not commit:

- `local_data/`
- `outputs/`
- Account ledgers
- MT5 statements
- Generated chart HTML
- Quote cache CSV files
- Logs

If sample data is needed, use a minimal sanitized sample under `docs\samples\`.

## Issue Labels

Suggested labels for GitHub:

- `bug`
- `feature`
- `ui`
- `account-registry`
- `kline`
- `docs`
- `safety`
