# Trade K-line Tool

This tool converts MT5 statement / ReportHistory HTML files into interactive buy/sell point K-line charts.

It reads statement files and MT5 M1 quote data, writes local HTML/CSV/JSON outputs, and does not modify MT4/MT5 Manager or server-side state.

## Files

- `generate_trade_kline_from_statement.py`: main entry. Parse statement, validate time alignment, read M1 data, write cache, and build HTML.
- `build_enhanced_trade_kline_from_cache.py`: rebuild chart HTML from existing trades CSV, mapping JSON, and quote cache without connecting to MT5.
- `run_generate_trade_kline.ps1`: PowerShell wrapper for generation.
- `run_rebuild_from_cache.ps1`: PowerShell wrapper for cache-only rebuilds.
- `API.md`: parameters, outputs, and callable functions.

## Defaults

- Output directory: `outputs\kline`
- Optional local dependency directory: `pydeps`
- MT5 terminal: `C:\Program Files\AC Capital Market MT5 Terminal\terminal64.exe`
- Candle period: M1
- Output naming: `{account}_{start:%Y%m%d_%H%M%S}_{end:%Y%m%d_%H%M%S}`

For USC cent accounts, money fields are normalized to USD display scale while prices, volume, timestamps, and M1 quotes are not scaled.

## Generate A Chart

From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File tools\trade_kline_tool\run_generate_trade_kline.ps1 `
  -Statement local_data\statements\Statement_7002326.htm
```

Equivalent Python call:

```powershell
& $env:K_DESK_PYTHON tools\trade_kline_tool\generate_trade_kline_from_statement.py `
  local_data\statements\Statement_7002326.htm `
  --out-dir outputs\kline
```

## Rebuild From Cache

Use this when trades, mapping, and quote cache already exist:

```powershell
powershell -ExecutionPolicy Bypass -File tools\trade_kline_tool\run_rebuild_from_cache.ps1 `
  -Trades outputs\kline\7002326_20260409_134531_20260609_163531_trades.csv
```

## Generated Files

- `{stem}_trade_kline.html`: final interactive chart.
- `{stem}_trades.csv`: normalized trade data.
- `{stem}_mapping.json`: statement symbol to MT5 symbol and time-mode mapping.
- `{stem}_alignment_sample.csv`: GMT/GMT+3 sample evidence.
- `{stem}_m1_price_check_sample.csv`: M1 high-low price check sample.
- `{stem}_{report_symbol}_quote_cache_{mt5_symbol}_M1_{time_mode}.csv`: local M1 quote cache.

## Safety Notes

- Do not export full ticks by default. M1 envelope checks are used for time/price validation.
- If only UI or filter logic changes, rebuild from cache instead of reading MT5 again.
- Statement symbols may have suffixes such as `XAUUSD.P`; the tool tries base-symbol matching.
- To change the terminal or output directory, use `--terminal` and `--out-dir`.
