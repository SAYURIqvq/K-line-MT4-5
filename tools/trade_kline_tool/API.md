# API

## Generate From Statement

```powershell
python tools\trade_kline_tool\generate_trade_kline_from_statement.py <statement> [--out-dir <dir>] [--terminal <terminal64.exe>] [--mt5-timeout <ms>]
```

| Parameter | Required | Default | Description |
| --- | --- | --- | --- |
| `statement` | Yes | None | MT5/Manager statement `.htm` or `.html` file path. |
| `--out-dir` | No | `outputs\kline` | Output and quote-cache directory. |
| `--terminal` | No | AC Capital Market MT5 terminal path | Terminal used for read-only M1 quote access. |
| `--mt5-timeout` | No | `10000` | `mt5.initialize` timeout in milliseconds. |

Environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `K_DESK_ROOT` | Project root inferred from file path | Project root. |
| `TRADE_KLINE_OUT_DIR` | `outputs\kline` | Default output directory, overridden by `--out-dir`. |
| `TRADE_KLINE_TERMINAL` | AC Capital Market MT5 terminal path | Default MT5 terminal, overridden by `--terminal`. |
| `TRADE_KLINE_PYDEPS` | `pydeps` | Optional local Python dependency directory. |

Python example:

```python
from pathlib import Path
from generate_trade_kline_from_statement import parse_statement, stem_for

account, trades = parse_statement(Path("local_data/statements/Statement_7002326.htm"))
stem = stem_for(account, trades)
```

Main functions:

| Function | Purpose |
| --- | --- |
| `parse_statement(statement)` | Parse statement HTML and return `account, trades`. |
| `stem_for(account, trades)` | Build the output stem from account and trade time range. |
| `choose_by_m1_envelope(report_symbol, trades)` | Use 3-5 orders to choose GMT or GMT+3 time mode. Requires initialized MT5. |
| `load_or_fetch_bars(stem, report_symbol, mt5_symbol, time_mode, hour_delta, trades)` | Read local M1 cache or fetch read-only MT5 M1 data and save cache. |
| `make_price_check_from_bars(report_symbol, sample, bars, mapping)` | Build the M1 high-low price-check sample. |

## Rebuild HTML From Cache

```powershell
python tools\trade_kline_tool\build_enhanced_trade_kline_from_cache.py --trades <stem_trades.csv> [--mapping <stem_mapping.json>] [--out <output.html>]
```

| Parameter | Required | Default | Description |
| --- | --- | --- | --- |
| `--trades` | Yes | None | Normalized trade CSV. File name must end with `_trades.csv`. |
| `--mapping` | No | `{stem}_mapping.json` beside trades | Symbol and time-mode mapping. |
| `--out` | No | `{stem}_trade_kline.html` beside trades | Output HTML path. |

Python example:

```python
import json
import pandas as pd
from pathlib import Path
from build_enhanced_trade_kline_from_cache import build_html, infer_stem, load_bars_for_symbol

trades_path = Path("outputs/kline/7002326_20260409_134531_20260609_163531_trades.csv")
stem = infer_stem(trades_path)
trades = pd.read_csv(trades_path, parse_dates=["Open Time", "Close Time"])
mapping = json.loads(trades_path.with_name(f"{stem}_mapping.json").read_text(encoding="utf-8"))
bars_by_symbol = {
    symbol: load_bars_for_symbol(trades_path.parent, stem, symbol, item)
    for symbol, item in mapping.items()
}
html = build_html(stem.split("_", 1)[0], stem, trades, bars_by_symbol, mapping)
```

## Normalized Trade Fields

`{stem}_trades.csv` should include at least:

| Field | Description |
| --- | --- |
| `Ticket` | Order ticket. |
| `Open Time` | Open time in statement display timezone. |
| `Close Time` | Close time in statement display timezone. |
| `Type` | `buy` or `sell`. |
| `Volume` | Lots. |
| `Item` | Statement symbol, for example `XAUUSD.P`. |
| `Open Price` | Open price. |
| `Close Price` | Close price. |
| `Commission` | Commission. |
| `Taxes` | Taxes. |
| `Swap` | Swap. |
| `Profit` | Profit. |
| `Holding Seconds` | Holding duration in seconds. |

## Safety Boundary

Allowed:

- Read statement files.
- Read MT5 symbol information and M1 quote data through the Python API.
- Generate or overwrite local analysis outputs under `outputs\kline`.

Forbidden:

- Modify accounts, orders, positions, balances, credit, leverage, groups, symbols, permissions, or server settings.
- Perform any non-export or non-read-only operation in MT4/MT5 Manager.
