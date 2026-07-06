from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("K_DESK_ROOT", Path(__file__).resolve().parents[2]))
LEGACY_RISK_ROOT = Path(r"D:\risk")
DEFAULT_PYDEPS = LEGACY_RISK_ROOT / "pydeps" if (LEGACY_RISK_ROOT / "pydeps").exists() else PROJECT_ROOT / "pydeps"
PYDEPS = Path(os.environ.get("TRADE_KLINE_PYDEPS", DEFAULT_PYDEPS))
if PYDEPS.exists():
    sys.path.insert(0, str(PYDEPS))

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from build_enhanced_trade_kline_from_cache import build_html, safe_name
from fused_trade_kline_features import enhance_trade_kline_html


DEFAULT_OUT_DIR = LEGACY_RISK_ROOT / "output_data" if LEGACY_RISK_ROOT.exists() else PROJECT_ROOT / "outputs" / "kline"
OUT_DIR = Path(os.environ.get("TRADE_KLINE_OUT_DIR", DEFAULT_OUT_DIR))
TERMINAL = os.environ.get("TRADE_KLINE_TERMINAL", r"C:\Program Files\AC Capital Market MT5 Terminal\terminal64.exe")
TIMEFRAME_LABEL = "M1"
MT5_TIMEFRAME = mt5.TIMEFRAME_M1


def utc(ts):
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    return ts.replace(tzinfo=timezone.utc)


def copy_rates_range_retry(symbol: str, timeframe: int, start, end, *, attempts: int = 4, delay: float = 0.6, warn: bool = True):
    """MT5 can return an empty result while a symbol's history is still syncing."""
    mt5.symbol_select(symbol, True)
    last_error = None
    for attempt in range(1, attempts + 1):
        rates = mt5.copy_rates_range(symbol, timeframe, start, end)
        if rates is not None and len(rates):
            return rates
        last_error = mt5.last_error()
        if attempt < attempts:
            time.sleep(delay * attempt)
    if warn:
        print(f"INFO: no M1 rates for {symbol} {start} -> {end}; last_error={last_error}")
    return rates


def clean_number(value):
    if pd.isna(value):
        return None
    text = str(value).replace("\xa0", " ").strip()
    if not text:
        return None
    text = text.replace(" ", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def extract_account_meta(table: pd.DataFrame) -> dict:
    text = " ".join(str(v).replace("\xa0", " ") for v in table.head(12).to_numpy().ravel())
    account_block = ""
    m = re.search(r"\b\d{4,}\s*\(([^)]*)\)", text)
    if m:
        account_block = m.group(1)
    parts = [p.strip() for p in account_block.split(",") if p.strip()]
    currency = parts[0].upper() if parts else ""
    leverage_match = re.search(r"\b1\s*:\s*(\d+)\b", account_block or text)
    leverage = int(leverage_match.group(1)) if leverage_match else None
    is_cent = currency in {"USC", "CENT", "CENTS", "US CENT", "US CENTS"}
    money_scale = 0.01 if is_cent else 1.0
    display_currency = "USD" if currency == "USC" else currency
    return {
        "currency": currency,
        "displayCurrency": display_currency,
        "moneyScale": money_scale,
        "isCentAccount": is_cent,
        "leverage": leverage,
        "note": f"{currency} cent account; money fields divided by 100 for USD display" if is_cent else "",
    }


def apply_account_money_scale(trades: pd.DataFrame, meta: dict) -> pd.DataFrame:
    out = trades.copy()
    scale = float(meta.get("moneyScale") or 1.0)
    money_cols = ["Commission", "Taxes", "Swap", "Profit"]
    if scale != 1.0:
        for col in money_cols:
            if col in out.columns:
                out[col] = out[col].astype(float) * scale
    out["Account Currency"] = meta.get("currency") or ""
    out["Display Currency"] = meta.get("displayCurrency") or meta.get("currency") or ""
    out["Money Scale"] = scale
    out["Is Cent Account"] = bool(meta.get("isCentAccount"))
    out["Money Unit Note"] = meta.get("note") or ""
    return out


def extract_account(table: pd.DataFrame, statement: Path) -> str:
    text = " ".join(str(v) for v in table.head(3).to_numpy().ravel())
    m = re.search(r"Account:\s*(\d+)", text, re.I)
    if m:
        return m.group(1)
    account_rows = table[table.apply(lambda row: row.astype(str).str.contains("账户", regex=False).any(), axis=1)]
    if not account_rows.empty:
        row_text = " ".join(str(v).replace("\xa0", " ") for v in account_rows.iloc[0].tolist())
        m = re.search(r"\b(\d{4,})\b", row_text)
        if m:
            return m.group(1)
    m = re.search(r"(?:Statement|ReportHistory)[_ -]?(\d+)", statement.stem, re.I)
    if m:
        return m.group(1)
    return statement.stem


def unique_headers(headers):
    seen = {}
    out = []
    for h in headers:
        h = str(h).strip()
        seen[h] = seen.get(h, 0) + 1
        out.append(h if seen[h] == 1 else f"{h}.{seen[h] - 1}")
    return out


def parse_statement(statement: Path) -> tuple[str, pd.DataFrame]:
    table = pd.read_html(statement)[0]
    account = extract_account(table, statement)
    account_meta = extract_account_meta(table)
    header_idx = None
    for idx, row in table.iterrows():
        values = [str(v).strip() for v in row.tolist()]
        if "Ticket" in values and "Open Time" in values and "Close Time" in values:
            header_idx = idx
            break
    if header_idx is None:
        return parse_chinese_report_history(table, account, statement, account_meta)

    headers = unique_headers(table.iloc[header_idx].tolist())
    trades = table.iloc[header_idx + 1 :].copy()
    trades.columns = headers
    trades = trades[trades["Type"].isin(["buy", "sell"])].copy()
    for col in ["Open Time", "Close Time"]:
        trades[col] = pd.to_datetime(trades[col], format="%Y.%m.%d %H:%M:%S", errors="coerce")
    for col in ["Ticket", "Volume", "Price", "Price.1", "S / L", "T / P", "S/L", "T/P", "Commission", "Taxes", "Swap", "Profit"]:
        if col in trades.columns:
            trades[col] = trades[col].map(clean_number)

    trades = trades.rename(columns={"Price": "Open Price", "Price.1": "Close Price"})
    trades["S/L"] = trades["S / L"] if "S / L" in trades.columns else trades["S/L"] if "S/L" in trades.columns else None
    trades["T/P"] = trades["T / P"] if "T / P" in trades.columns else trades["T/P"] if "T/P" in trades.columns else None
    if "Comment" not in trades.columns:
        trades["Comment"] = ""
    trades["Item"] = trades["Item"].astype(str).str.upper()
    trades["Holding Seconds"] = (trades["Close Time"] - trades["Open Time"]).dt.total_seconds()
    cols = [
        "Ticket",
        "Open Time",
        "Close Time",
        "Type",
        "Volume",
        "Item",
        "Open Price",
        "Close Price",
        "Commission",
        "Taxes",
        "Swap",
        "Profit",
        "S/L",
        "T/P",
        "Comment",
        "Holding Seconds",
    ]
    for col in cols:
        if col not in trades.columns:
            trades[col] = None
    trades = trades[cols].dropna(subset=["Open Time", "Close Time", "Open Price", "Close Price"])
    trades = trades.sort_values("Open Time").reset_index(drop=True)
    trades = apply_account_money_scale(trades, account_meta)
    return account, trades


def parse_chinese_report_history(table: pd.DataFrame, account: str, statement: Path, account_meta: dict | None = None) -> tuple[str, pd.DataFrame]:
    """Parse MT5 Chinese ReportHistory position-summary rows.

    Pandas expands the HTML colspan/rowspan layout into repeated and shifted
    columns. The closed-position section has stable useful columns:
    0 open time, 1 position/ticket, 2 symbol, 3 type, 12 volume,
    13 open price, 16 close time, 17 close price, 18 commission,
    19 swap, 20 profit.
    """
    if table.shape[1] < 21:
        raise RuntimeError(f"Unsupported ReportHistory table shape {table.shape}: {statement}")

    raw = table.copy()
    rows = raw[raw.iloc[:, 3].isin(["buy", "sell"])].copy()
    parsed = pd.DataFrame(
        {
            "Ticket": rows.iloc[:, 1].map(clean_number),
            "Open Time": pd.to_datetime(rows.iloc[:, 0], format="%Y.%m.%d %H:%M:%S", errors="coerce"),
            "Close Time": pd.to_datetime(rows.iloc[:, 16], format="%Y.%m.%d %H:%M:%S", errors="coerce"),
            "Type": rows.iloc[:, 3].astype(str),
            "Volume": rows.iloc[:, 12].map(clean_number),
            "Item": rows.iloc[:, 2].astype(str).str.upper(),
            "Open Price": rows.iloc[:, 13].map(clean_number),
            "Close Price": rows.iloc[:, 17].map(clean_number),
            "Commission": rows.iloc[:, 18].map(clean_number),
            "Taxes": 0.0,
            "Swap": rows.iloc[:, 19].map(clean_number),
            "Profit": rows.iloc[:, 20].map(clean_number),
            "S/L": rows.iloc[:, 14].map(clean_number),
            "T/P": rows.iloc[:, 15].map(clean_number),
            "Comment": "",
        }
    )
    parsed["Holding Seconds"] = (parsed["Close Time"] - parsed["Open Time"]).dt.total_seconds()
    cols = [
        "Ticket",
        "Open Time",
        "Close Time",
        "Type",
        "Volume",
        "Item",
        "Open Price",
        "Close Price",
        "Commission",
        "Taxes",
        "Swap",
        "Profit",
        "S/L",
        "T/P",
        "Comment",
        "Holding Seconds",
    ]
    parsed = parsed[cols].dropna(subset=["Open Time", "Close Time", "Open Price", "Close Price", "Volume"])
    parsed = parsed[parsed["Item"].ne("NAN")]
    parsed = parsed.sort_values("Open Time").reset_index(drop=True)
    if parsed.empty:
        raise RuntimeError(f"No closed buy/sell positions parsed from Chinese ReportHistory: {statement}")
    parsed = apply_account_money_scale(parsed, account_meta or extract_account_meta(table))
    return account, parsed


def stem_for(account: str, trades: pd.DataFrame) -> str:
    start = trades["Open Time"].min().strftime("%Y%m%d_%H%M%S")
    end = trades["Close Time"].max().strftime("%Y%m%d_%H%M%S")
    return f"{account}_{start}_{end}"


def parse_filter_time(value: str | None):
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    if not text:
        return None
    return pd.to_datetime(text, errors="raise")


def filter_trades(trades: pd.DataFrame, symbols: str | None = None, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    out = trades.copy()
    if symbols:
        selected = {item.strip().upper() for item in re.split(r"[,;\s]+", symbols) if item.strip()}
        if selected:
            out = out[out["Item"].astype(str).str.upper().isin(selected)].copy()
    start_ts = parse_filter_time(start)
    end_ts = parse_filter_time(end)
    if start_ts is not None:
        out = out[out["Close Time"] >= start_ts].copy()
    if end_ts is not None:
        out = out[out["Open Time"] <= end_ts].copy()
    out = out.sort_values("Open Time").reset_index(drop=True)
    if out.empty:
        raise RuntimeError("No trades left after applying symbol/time filters.")
    return out


def statement_preview(account: str, trades: pd.DataFrame) -> dict:
    symbols = []
    for symbol, group in trades.groupby("Item", sort=True):
        symbols.append(
            {
                "symbol": str(symbol),
                "trades": int(len(group)),
                "open_start": group["Open Time"].min().strftime("%Y-%m-%d %H:%M:%S"),
                "close_end": group["Close Time"].max().strftime("%Y-%m-%d %H:%M:%S"),
                "profit": float(group["Profit"].fillna(0).sum()),
            }
        )
    return {
        "account": str(account),
        "trade_count": int(len(trades)),
        "start": trades["Open Time"].min().strftime("%Y-%m-%d %H:%M:%S"),
        "end": trades["Close Time"].max().strftime("%Y-%m-%d %H:%M:%S"),
        "symbols": symbols,
    }


def base_symbol(report_symbol: str) -> str:
    return str(report_symbol).split(".")[0].upper()


def mt5_symbol_for(report_symbol: str) -> str:
    candidates = []
    raw = str(report_symbol).upper()
    base = base_symbol(report_symbol)
    base_no_roll = base.replace("ROLL", "")
    roll_base = raw.replace("ROLL", "")
    roll_title = f"{base_no_roll}Roll" if base_no_roll else f"{base}Roll"
    alias_candidates = {
        "CHINA50": ["CN50Roll", "CN50Roll.ECN", "CN50Roll.PRO"],
        "CN50": ["CN50Roll", "CN50Roll.ECN", "CN50Roll.PRO"],
        "HKG50": ["HKG50Roll", "HKG50Roll.ECN", "HKG50Roll.PRO"],
        "HK50": ["HKG50Roll", "HKG50Roll.ECN", "HKG50Roll.PRO"],
        "NAS100": ["NAS100Roll", "NAS100Roll.ECN", "NAS100Roll.PRO"],
        "US30": ["US30Roll", "US30Roll.ECN", "US30Roll.PRO"],
        "SPX500": ["SPX500Roll", "SPX500Roll.ECN", "SPX500Roll.PRO"],
        "UK100": ["UK100Roll", "UK100Roll.ECN", "UK100Roll.PRO"],
        "GER40": ["GER40Roll", "GER40Roll.ECN", "GER40Roll.PRO"],
        "JPN225": ["JPN225Roll", "JPN225Roll.ECN", "JPN225Roll.PRO"],
        "AUS200": ["AUS200Roll", "AUS200Roll.ECN", "AUS200Roll.PRO"],
        "UKOIL": ["UKOILRoll", "UKOILRoll.ECN", "UKOILRoll.PRO"],
        "USOIL": ["USOILRoll", "USOILRoll.ECN", "USOILRoll.PRO"],
        "NGAS": ["NGASRoll", "NGASRoll.ECN", "NGASRoll.PRO"],
    }
    aliases = [*alias_candidates.get(base, []), *alias_candidates.get(base_no_roll, [])]
    for item in [
        raw,
        base,
        base_no_roll,
        roll_title,
        f"{base_no_roll}Roll",
        f"{base_no_roll}Roll.ECN",
        f"{base_no_roll}Roll.PRO",
        f"{base}Roll",
        f"{base}Roll.ECN",
        f"{base}Roll.PRO",
        *aliases,
    ]:
        if item and item not in candidates:
            candidates.append(item)
    for sym in candidates:
        info = mt5.symbol_info(sym)
        if info:
            mt5.symbol_select(sym, True)
            return sym
    search_keys = [raw, base, base_no_roll, roll_base, roll_title, *aliases]
    found = []
    for key in search_keys:
        if not key:
            continue
        for info in mt5.symbols_get(f"*{key}*") or []:
            if info.name not in found:
                found.append(info.name)
    preferred = sorted(
        found,
        key=lambda name: (
            0 if name.upper() == raw else 1,
            0 if name == roll_title else 1,
            0 if name.endswith(".ECN") else 1,
            len(name),
            name,
        ),
    )
    for sym in preferred:
        info = mt5.symbol_info(sym)
        if info:
            mt5.symbol_select(sym, True)
            return sym
    raise RuntimeError(f"MT5 symbol not found for report symbol {report_symbol}; tried {candidates}")


def sample_even(df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    if len(df) <= n:
        return df.copy()
    idx = sorted({round(i * (len(df) - 1) / (n - 1)) for i in range(n)})
    return df.iloc[idx].copy()


def distance_to_bar(price: float, row) -> float:
    if row is None:
        return np.nan
    low = float(row["low"])
    high = float(row["high"])
    if low <= price <= high:
        return 0.0
    return min(abs(price - low), abs(price - high))


def choose_by_m1_envelope(report_symbol: str, trades: pd.DataFrame) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    mt5_symbol = mt5_symbol_for(report_symbol)
    sample = sample_even(trades.sort_values("Open Time"), min(5, len(trades)))
    rows = []
    for mode, hour_delta in {"report_is_GMT": 0, "report_is_GMT+3": -3}.items():
        parts = []
        for _, tr in sample.iterrows():
            start = tr["Open Time"] + timedelta(hours=hour_delta) - timedelta(minutes=3)
            end = tr["Open Time"] + timedelta(hours=hour_delta) + timedelta(minutes=3)
            rates = copy_rates_range_retry(mt5_symbol, MT5_TIMEFRAME, utc(start), utc(end), attempts=3, delay=0.4, warn=False)
            if rates is not None and len(rates):
                df = pd.DataFrame(rates)
                df["time"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(None) - timedelta(hours=hour_delta)
                parts.append(df)
        bars = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["time"]).sort_values("time") if parts else pd.DataFrame()
        by_minute = {t: row for t, row in bars.set_index("time").iterrows()} if not bars.empty else {}
        distances = []
        matched = 0
        inside = 0
        for _, tr in sample.iterrows():
            minute = tr["Open Time"].floor("min").to_pydatetime()
            row = by_minute.get(minute)
            d = distance_to_bar(float(tr["Open Price"]), row)
            if not np.isnan(d):
                matched += 1
                distances.append(d)
                if d == 0:
                    inside += 1
        rows.append(
            {
                "Report Symbol": report_symbol,
                "MT5 Symbol": mt5_symbol,
                "Time Mode": mode,
                "Hour Delta To MT5": hour_delta,
                "Sample Count": len(sample),
                "Matched Count": matched,
                "Inside M1 Range Count": inside,
                "Inside M1 Range Ratio": inside / matched if matched else None,
                "Avg Distance To M1 Range": float(np.mean(distances)) if distances else None,
                "Median Distance To M1 Range": float(np.median(distances)) if distances else None,
                "Max Distance To M1 Range": float(np.max(distances)) if distances else None,
            }
        )
    align = pd.DataFrame(rows).sort_values(
        [
            "Inside M1 Range Ratio",
            "Inside M1 Range Count",
            "Median Distance To M1 Range",
            "Avg Distance To M1 Range",
        ],
        ascending=[False, False, True, True],
        na_position="last",
    )
    valid = align.dropna(subset=["Median Distance To M1 Range"])
    best = valid.iloc[0] if not valid.empty else align.iloc[0]
    mapping = {
        "report_symbol": report_symbol,
        "mt5_symbol": mt5_symbol,
        "time_mode": best["Time Mode"],
        "hour_delta": int(best["Hour Delta To MT5"]),
        "sample_count": int(best["Sample Count"]),
        "matched_count": int(best["Matched Count"]),
        "inside_m1_range_ratio": None if pd.isna(best["Inside M1 Range Ratio"]) else float(best["Inside M1 Range Ratio"]),
        "median_distance_to_m1_range": None if pd.isna(best["Median Distance To M1 Range"]) else float(best["Median Distance To M1 Range"]),
        "max_distance_to_m1_range": None if pd.isna(best["Max Distance To M1 Range"]) else float(best["Max Distance To M1 Range"]),
    }
    return mapping, align, sample


def load_or_fetch_bars(stem: str, report_symbol: str, mt5_symbol: str, time_mode: str, hour_delta: int, trades: pd.DataFrame) -> pd.DataFrame:
    cache_path = OUT_DIR / f"{stem}_{safe_name(report_symbol)}_quote_cache_{safe_name(mt5_symbol)}_{TIMEFRAME_LABEL}_{time_mode}.csv"
    query_start = trades["Open Time"].min() + timedelta(hours=hour_delta) - timedelta(minutes=30)
    query_end = trades["Close Time"].max() + timedelta(hours=hour_delta) + timedelta(minutes=30)
    display_delta = -hour_delta
    if cache_path.exists():
        bars = pd.read_csv(cache_path, parse_dates=["time"])
        expected_start = query_start + timedelta(hours=display_delta)
        expected_end = query_end + timedelta(hours=display_delta)
        if not bars.empty and bars["time"].min() <= expected_start + timedelta(minutes=5) and bars["time"].max() >= expected_end - timedelta(minutes=5):
            print(f"cache hit: {cache_path} rows={len(bars)}")
            return bars
        print(f"cache ignored (range mismatch): {cache_path} rows={len(bars)}")
    frames = []
    mt5.symbol_select(mt5_symbol, True)
    time.sleep(0.3)
    cursor = query_start
    while cursor < query_end:
        chunk_end = min(cursor + timedelta(days=7), query_end)
        rates = copy_rates_range_retry(mt5_symbol, MT5_TIMEFRAME, utc(cursor), utc(chunk_end), attempts=4, delay=0.7)
        if rates is not None and len(rates):
            frames.append(pd.DataFrame(rates))
            print(f"{report_symbol}/{mt5_symbol} M1 {cursor:%Y-%m-%d} -> {chunk_end:%Y-%m-%d}: {len(rates)}")
        cursor = chunk_end
    if not frames:
        raise RuntimeError(f"No M1 bars fetched for {report_symbol} -> {mt5_symbol}")
    bars = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["time"]).sort_values("time")
    bars["time"] = pd.to_datetime(bars["time"], unit="s", utc=True).dt.tz_convert(None) + timedelta(hours=display_delta)
    bars.to_csv(cache_path, index=False, encoding="utf-8-sig")
    print(f"cache saved: {cache_path} rows={len(bars)}")
    return bars


def make_price_check_from_bars(report_symbol: str, sample: pd.DataFrame, bars: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    by_minute = {t: row for t, row in bars.set_index("time").iterrows()}
    rows = []
    for _, tr in sample.iterrows():
        minute = tr["Open Time"].floor("min").to_pydatetime()
        row = by_minute.get(minute)
        out = tr.to_dict()
        out.update({"MT5 Symbol": mapping["mt5_symbol"], "Time Mode": mapping["time_mode"]})
        if row is not None:
            out.update(
                {
                    "M1 Time": minute,
                    "M1 Open": float(row["open"]),
                    "M1 High": float(row["high"]),
                    "M1 Low": float(row["low"]),
                    "M1 Close": float(row["close"]),
                    "Open Price Distance To M1 Range": distance_to_bar(float(tr["Open Price"]), row),
                }
            )
        rows.append(out)
    return pd.DataFrame(rows)


def apply_display_price_alignment(report_symbol: str, bars: pd.DataFrame, sample: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    if bars.empty or sample.empty:
        mapping["display_price_shift"] = 0.0
        mapping["display_price_shift_applied"] = False
        return bars
    by_minute = {t: row for t, row in bars.set_index("time").iterrows()}
    deltas = []
    for _, tr in sample.iterrows():
        for time_col, price_col in [("Open Time", "Open Price"), ("Close Time", "Close Price")]:
            row = by_minute.get(tr[time_col].floor("min").to_pydatetime())
            if row is None:
                continue
            price = float(tr[price_col])
            low, high = float(row["low"]), float(row["high"])
            if price > high:
                deltas.append(price - high)
            elif price < low:
                deltas.append(price - low)
            else:
                deltas.append(0.0)
    if not deltas:
        mapping["display_price_shift"] = 0.0
        mapping["display_price_shift_applied"] = False
        return bars
    intervals = []
    for _, tr in sample.iterrows():
        for time_col, price_col in [("Open Time", "Open Price"), ("Close Time", "Close Price")]:
            row = by_minute.get(tr[time_col].floor("min").to_pydatetime())
            if row is None:
                continue
            price = float(tr[price_col])
            intervals.append((price - float(row["high"]), price - float(row["low"])))
    candidates = set(deltas)
    for lo_shift, hi_shift in intervals:
        candidates.add(lo_shift)
        candidates.add(hi_shift)
        candidates.add((lo_shift + hi_shift) / 2)
    best = None
    for candidate in candidates:
        distances = []
        inside_count = 0
        for lo_shift, hi_shift in intervals:
            if lo_shift <= candidate <= hi_shift:
                inside_count += 1
                distances.append(0.0)
            else:
                distances.append(min(abs(candidate - lo_shift), abs(candidate - hi_shift)))
        median_dist = float(np.median(distances)) if distances else float("inf")
        mean_dist = float(np.mean(distances)) if distances else float("inf")
        score = (inside_count, -median_dist, -mean_dist, -abs(candidate))
        if best is None or score > best[0]:
            best = (score, candidate, inside_count)
    shift = float(best[1]) if best else float(np.median(deltas))
    nonzero_ratio = sum(abs(v) > 1e-12 for v in deltas) / len(deltas)
    threshold = 0.2 if "XAU" in str(report_symbol).upper() else 0.0003
    mapping["display_price_shift"] = shift
    mapping["display_price_shift_applied"] = abs(shift) >= threshold and nonzero_ratio >= 0.35
    mapping["display_price_shift_nonzero_ratio"] = nonzero_ratio
    mapping["display_price_shift_inside_count"] = int(best[2]) if best else 0
    mapping["display_price_shift_sample_count"] = len(intervals)
    if not mapping["display_price_shift_applied"]:
        return bars
    out = bars.copy()
    for col in ["open", "high", "low", "close"]:
        out[col] = out[col].astype(float) + shift
    print(f"{report_symbol}: applied display price shift {shift:.8f} to align K-line display with report prices")
    return out


def main() -> None:
    global OUT_DIR, TERMINAL
    parser = argparse.ArgumentParser(description="Generate cached MT5 buy/sell K-line chart from a statement HTML.")
    parser.add_argument("statement", help="Path to statement .htm/.html")
    parser.add_argument("--out-dir", default=str(OUT_DIR), help=f"Output/cache directory. Default: {OUT_DIR}")
    parser.add_argument("--terminal", default=TERMINAL, help="MT5 terminal64.exe path used for read-only M1 data fetch.")
    parser.add_argument("--mt5-timeout", type=int, default=10000, help="MT5 initialize timeout in milliseconds.")
    parser.add_argument("--symbols", default="", help="Comma/space separated report symbols to generate, for example XAUUSD.PRO,EURUSD.PRO.")
    parser.add_argument("--start", default="", help="Only include trades overlapping this report-time start, e.g. 2026-06-01 00:00.")
    parser.add_argument("--end", default="", help="Only include trades overlapping this report-time end, e.g. 2026-06-30 23:59.")
    parser.add_argument("--inspect", action="store_true", help="Parse statement and print JSON preview only; do not connect to MT5.")
    args = parser.parse_args()
    OUT_DIR = Path(args.out_dir)
    TERMINAL = args.terminal
    statement = Path(args.statement)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    account, all_trades = parse_statement(statement)
    if args.inspect:
        print(json.dumps(statement_preview(account, all_trades), ensure_ascii=False, indent=2))
        return
    trades = filter_trades(all_trades, args.symbols, args.start, args.end)
    stem = stem_for(account, trades)
    trades_path = OUT_DIR / f"{stem}_trades.csv"
    trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    print(f"account={account} trades={len(trades)} / parsed={len(all_trades)} symbols={','.join(sorted(trades['Item'].unique()))}")
    print(f"range={trades['Open Time'].min()} -> {trades['Close Time'].max()}")
    print(f"trades_csv={trades_path}")

    if not mt5.initialize(path=TERMINAL, timeout=args.mt5_timeout):
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    acc = mt5.account_info()
    print(f"MT5 account={acc.login if acc else None} server={acc.server if acc else None}")
    try:
        mapping_by_symbol = {}
        align_parts = []
        check_parts = []
        bars_by_symbol = {}
        skipped_symbols = []
        symbol_groups = sorted(trades.groupby("Item", sort=True), key=lambda item: len(item[1]), reverse=True)
        fallback_time = None
        for report_symbol, group in symbol_groups:
            group = group.sort_values("Open Time").reset_index(drop=True)
            mapping, align, sample = choose_by_m1_envelope(report_symbol, group)
            if mapping["median_distance_to_m1_range"] is None and fallback_time is not None:
                mapping["time_mode"] = fallback_time["time_mode"]
                mapping["hour_delta"] = fallback_time["hour_delta"]
                mapping["fallback_time_mode_from"] = fallback_time["report_symbol"]
            elif mapping["median_distance_to_m1_range"] is not None:
                fallback_time = {
                    "report_symbol": report_symbol,
                    "time_mode": mapping["time_mode"],
                    "hour_delta": mapping["hour_delta"],
                }
            mapping_by_symbol[report_symbol] = mapping
            align_parts.append(align)
            try:
                bars = load_or_fetch_bars(stem, report_symbol, mapping["mt5_symbol"], mapping["time_mode"], mapping["hour_delta"], group)
            except RuntimeError as exc:
                mapping["quote_error"] = str(exc)
                skipped_symbols.append(report_symbol)
                print(f"WARNING: skipped {report_symbol}: {exc}")
                continue
            bars = apply_display_price_alignment(report_symbol, bars, sample, mapping)
            bars_by_symbol[report_symbol] = bars
            check_parts.append(make_price_check_from_bars(report_symbol, sample, bars, mapping))
            print(f"{report_symbol}: {json.dumps(mapping, ensure_ascii=False)}")
    finally:
        mt5.shutdown()

    if not bars_by_symbol:
        raise RuntimeError("No symbols have usable M1 bars; cannot build chart.")

    align_path = OUT_DIR / f"{stem}_alignment_sample.csv"
    pd.concat(align_parts, ignore_index=True).to_csv(align_path, index=False, encoding="utf-8-sig")
    checks_path = OUT_DIR / f"{stem}_m1_price_check_sample.csv"
    if check_parts:
        pd.concat(check_parts, ignore_index=True).to_csv(checks_path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(checks_path, index=False, encoding="utf-8-sig")
    mapping_path = OUT_DIR / f"{stem}_mapping.json"
    mapping_path.write_text(json.dumps(mapping_by_symbol, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = OUT_DIR / f"{stem}_trade_kline.html"
    html = enhance_trade_kline_html(build_html(account, stem, trades, bars_by_symbol, mapping_by_symbol), statement, trades)
    html_path.write_text(html, encoding="utf-8")

    print("outputs")
    print(align_path)
    print(checks_path)
    print(mapping_path)
    print(html_path)
    if skipped_symbols:
        print(f"warnings: skipped symbols without usable M1 bars: {','.join(skipped_symbols)}")


if __name__ == "__main__":
    main()
