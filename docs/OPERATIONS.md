# Operations

## Start

From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
```

Or double-click:

```text
scripts\open_k_desk.cmd
```

Services:

- Account registry: `http://127.0.0.1:8776`
- K-line generator: `http://127.0.0.1:8765`

## Stop

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
```

## Health Check

```powershell
powershell -ExecutionPolicy Bypass -File scripts\health_check.ps1
```

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `K_DESK_ROOT` | Project root. Usually set by start scripts. |
| `K_DESK_PYTHON` | Python executable path. |
| `ACCOUNT_REGISTRY_PORT` | Account registry port, default `8776`. |
| `ACCOUNT_REGISTRY_DATA_DIR` | Ledger data directory. |
| `TRADE_KLINE_WEB_PORT` | K-line web port, default `8765`. |
| `TRADE_KLINE_OUT_DIR` | K-line output directory. |
| `TRADE_KLINE_TOOL_DIR` | K-line tool directory. |
| `TRADE_KLINE_TERMINAL` | MT5 terminal64.exe path for read-only M1 quote data. |
| `TRADE_KLINE_PYDEPS` | Optional Python dependency directory containing `MetaTrader5`. |

## Common Checks

Page does not open:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\health_check.ps1
```

Statement upload fails:

- Confirm the file is `.htm` or `.html`.
- Confirm the MT5 terminal path exists.
- Confirm `TRADE_KLINE_PYDEPS` or the active Python environment includes `MetaTrader5`.
- Check `outputs\trade_kline_web.err.log`.

Account ledger is empty:

- Confirm `local_data\problem_account_registry\problematic_accounts.xlsx` exists.
- Confirm the file is not open in Excel with an exclusive lock.
