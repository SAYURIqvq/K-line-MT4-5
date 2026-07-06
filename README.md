# K_desk

K_desk is a local risk-workbench project that combines:

- Problem account registry: account status ledger, notes, version history, daily Word journal export, and K-line chart linking.
- Trade K-line generator: upload MT5 statement / ReportHistory HTML and generate interactive buy/sell point K-line charts.

Default local services:

- Account registry: `http://127.0.0.1:8776`
- K-line generator: `http://127.0.0.1:8765`

## Online Demo

GitHub Pages demo:

```text
https://sayuriqvq.github.io/K-line-MT4-5/
```

Direct sample chart:

```text
https://sayuriqvq.github.io/K-line-MT4-5/outputs/kline/206263_20240423_132557_20260608_145941_trade_kline.html
```

The online demo is static and can show generated charts and sample output files. Uploading a new statement, reading MT5 M1 quotes, and regenerating charts require the local Windows service.

## Quick Start

Double-click:

```text
scripts\open_k_desk.cmd
```

Or run in PowerShell from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
```

Health check:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\health_check.ps1
```

Stop services:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
```

## Project Structure

```text
apps/
  problem_account_registry/   # Account ledger web service
  trade_kline_web/            # Statement upload web service
tools/
  trade_kline_tool/           # Statement parser and K-line HTML generator
scripts/                      # Start, stop, and health-check scripts
docs/                         # Project management and operations docs
local_data/                   # Local sensitive data, ignored by Git
outputs/                      # Generated charts/logs/journals, ignored by Git
```

## Local Data

Runtime data is intentionally ignored by Git to avoid committing accounts, statements, Excel ledgers, generated charts, and logs.

Put the local account ledger here:

```text
local_data\problem_account_registry\problematic_accounts.xlsx
```

K-line output and uploaded statements are stored under:

```text
outputs\kline
```

## GitHub Setup

After installing Git, publish this folder to `https://github.com/511615/K_desk.git`:

```powershell
cd /d D:\risk\K_desk
git init
git branch -M main
git remote add origin https://github.com/511615/K_desk.git
git add .
git commit -m "Initial K_desk project package"
git push -u origin main
```

## Safety Boundary

This project may read local statement files, local Excel files, and MT5 M1 market data for analysis. It must not create, modify, delete, approve, reject, settle, transfer, close, open, or otherwise change any account, order, symbol, group, balance, permission, leverage, or server-side state in MT4/MT5 Manager.
