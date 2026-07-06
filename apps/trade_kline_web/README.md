# Trade K-line Web

Local upload page for generating interactive buy/sell point K-line charts from MT5 statement / ReportHistory HTML files.

## Service

```text
http://127.0.0.1:8765
```

Start only this service:

```powershell
powershell -ExecutionPolicy Bypass -File apps\trade_kline_web\open_trade_kline_web.ps1
```

Open from this folder:

```text
open_trade_kline_web.cmd
```

## Workflow

1. Upload a `.htm` or `.html` statement.
2. The service calls `tools\trade_kline_tool\generate_trade_kline_from_statement.py`.
3. The generator parses trades, checks GMT/GMT+3 alignment, reads or reuses local MT5 M1 quote cache, and builds the final chart.
4. Outputs are written to `outputs\kline`.

Uploaded source statements are stored under:

```text
outputs\kline\uploaded_statements
```

## Safety Boundary

The service may read uploaded files and read MT5 symbol/M1 quote data through the Python API. It must not modify MT4/MT5 Manager, accounts, orders, balances, groups, symbols, permissions, or server-side state.
