from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import pandas as pd

from fused_trade_kline_features import enhance_trade_kline_html


PROJECT_ROOT = Path(os.environ.get("K_DESK_ROOT", Path(__file__).resolve().parents[2]))
LEGACY_RISK_ROOT = Path(r"D:\risk")
DEFAULT_OUT_DIR = LEGACY_RISK_ROOT / "output_data" if LEGACY_RISK_ROOT.exists() else PROJECT_ROOT / "outputs" / "kline"
OUT_DIR = Path(os.environ.get("TRADE_KLINE_OUT_DIR", DEFAULT_OUT_DIR))
MAX_HTML_BARS_PER_SYMBOL = 30000
PRESERVE_TRADE_WINDOW_MINUTES = 60


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


def infer_stem(trades_csv: Path) -> str:
    name = trades_csv.name
    suffix = "_trades.csv"
    if not name.endswith(suffix):
        raise ValueError(f"trades csv must end with {suffix}: {trades_csv}")
    return name[: -len(suffix)]


def load_bars_for_symbol(out_dir: Path, stem: str, report_symbol: str, mapping: dict) -> pd.DataFrame:
    mt5_symbol = mapping["mt5_symbol"]
    time_mode = mapping["time_mode"]
    exact = out_dir / f"{stem}_{safe_name(report_symbol)}_quote_cache_{safe_name(mt5_symbol)}_M1_{time_mode}.csv"
    if exact.exists():
        return pd.read_csv(exact, parse_dates=["time"])

    candidates = sorted(out_dir.glob(f"{stem}_*quote_cache*{safe_name(mt5_symbol)}*M1*{time_mode}*.csv"))
    if not candidates:
        raise FileNotFoundError(f"No M1 quote cache for {report_symbol} / {mt5_symbol} / {time_mode}")
    return pd.read_csv(candidates[0], parse_dates=["time"])


def aggregate_bars_by_position(bars: pd.DataFrame, target_rows: int) -> pd.DataFrame:
    if target_rows <= 0 or bars.empty:
        return bars.iloc[0:0].copy()
    if len(bars) <= target_rows:
        return bars.copy()
    step = max(1, int(len(bars) / target_rows) + 1)
    tmp = bars.reset_index(drop=True).copy()
    tmp["_bucket"] = tmp.index // step
    agg = (
        tmp.groupby("_bucket", sort=True)
        .agg(
            time=("time", "first"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            tick_volume=("tick_volume", "sum"),
        )
        .reset_index(drop=True)
    )
    if len(agg) > target_rows:
        stride = max(1, int(len(agg) / target_rows) + 1)
        agg = agg.iloc[::stride].copy()
    return agg


def bars_for_html(bars: pd.DataFrame, trades_for_symbol: pd.DataFrame) -> pd.DataFrame:
    """Keep local CSV caches full-size, but embed a display-sized OHLC series in HTML."""
    if len(bars) <= MAX_HTML_BARS_PER_SYMBOL:
        return bars.copy()
    b = bars.sort_values("time").reset_index(drop=True).copy()
    b["time"] = pd.to_datetime(b["time"])

    def preserve_mask(window_minutes: int) -> pd.Series:
        mask = pd.Series(False, index=b.index)
        pad = pd.Timedelta(minutes=window_minutes)
        for col in ["Open Time", "Close Time"]:
            if col not in trades_for_symbol.columns:
                continue
            for ts in pd.to_datetime(trades_for_symbol[col], errors="coerce").dropna():
                mask |= b["time"].between(ts - pad, ts + pad)
        return mask

    mask = preserve_mask(PRESERVE_TRADE_WINDOW_MINUTES)
    if int(mask.sum()) > int(MAX_HTML_BARS_PER_SYMBOL * 0.75):
        mask = preserve_mask(20)
    if int(mask.sum()) > int(MAX_HTML_BARS_PER_SYMBOL * 0.85):
        mask = preserve_mask(5)

    kept = b.loc[mask, ["time", "open", "high", "low", "close", "tick_volume"]]
    budget = max(1000, MAX_HTML_BARS_PER_SYMBOL - len(kept))
    rest = b.loc[~mask, ["time", "open", "high", "low", "close", "tick_volume"]]
    rest_agg = aggregate_bars_by_position(rest, budget)
    out = (
        pd.concat([kept, rest_agg], ignore_index=True)
        .drop_duplicates(subset=["time"], keep="first")
        .sort_values("time")
        .reset_index(drop=True)
    )
    if len(out) > MAX_HTML_BARS_PER_SYMBOL:
        out = aggregate_bars_by_position(out, MAX_HTML_BARS_PER_SYMBOL)
    print(f"html bars compressed: {len(bars)} -> {len(out)}")
    return out


def add_plot_prices(trades: pd.DataFrame, bars_by_symbol: dict) -> pd.DataFrame:
    out = trades.copy()
    out["Open Plot Price"] = out["Open Price"]
    out["Close Plot Price"] = out["Close Price"]
    return out


def account_meta_from_trades(trades: pd.DataFrame) -> dict:
    def first_value(col: str, default=None):
        if col not in trades.columns:
            return default
        vals = trades[col].dropna()
        if vals.empty:
            return default
        return vals.iloc[0]

    currency = str(first_value("Account Currency", "") or "").upper()
    display_currency = str(first_value("Display Currency", currency) or currency).upper()
    try:
        money_scale = float(first_value("Money Scale", 1.0) or 1.0)
    except (TypeError, ValueError):
        money_scale = 1.0
    raw_cent = first_value("Is Cent Account", False)
    is_cent = str(raw_cent).strip().lower() in {"true", "1", "yes"} if isinstance(raw_cent, str) else bool(raw_cent)
    note = str(first_value("Money Unit Note", "") or "")
    return {
        "currency": currency,
        "displayCurrency": display_currency,
        "moneyScale": money_scale,
        "isCentAccount": is_cent,
        "note": note,
    }


def find_statement_for_stem(out_dir: Path, account: str) -> Path | None:
    patterns = [
        f"ReportHistory-{account}.html",
        f"ReportHistory-{account}.htm",
        f"ReportHistory_{account}.html",
        f"ReportHistory_{account}.htm",
        f"Statement_{account}.html",
        f"Statement_{account}.htm",
        f"Statement-{account}.html",
        f"Statement-{account}.htm",
    ]
    for name in patterns:
        path = out_dir / name
        if path.exists():
            return path
    uploaded = out_dir / "uploaded_statements"
    if uploaded.exists():
        candidates = sorted(uploaded.glob(f"*{account}*.htm*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            return candidates[0]
    return None


def apply_display_price_alignment(report_symbol: str, bars: pd.DataFrame, trades_for_symbol: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    if bars.empty or trades_for_symbol.empty:
        mapping["display_price_shift"] = 0.0
        mapping["display_price_shift_applied"] = False
        return bars
    b = bars.copy()
    b["time"] = pd.to_datetime(b["time"])
    by_minute = {t: row for t, row in b.set_index("time").iterrows()}
    sample = trades_for_symbol.sort_values("Open Time")
    if len(sample) > 80:
        idx = sorted({round(i * (len(sample) - 1) / 79) for i in range(80)})
        sample = sample.iloc[idx]
    deltas = []
    for _, tr in sample.iterrows():
        for time_col, price_col in [("Open Time", "Open Price"), ("Close Time", "Close Price")]:
            row = by_minute.get(pd.to_datetime(tr[time_col]).floor("min").to_pydatetime())
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
            row = by_minute.get(pd.to_datetime(tr[time_col]).floor("min").to_pydatetime())
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
        series = pd.Series(distances)
        score = (inside_count, -float(series.median()), -float(series.mean()), -abs(candidate))
        if best is None or score > best[0]:
            best = (score, candidate, inside_count)
    shift = float(best[1]) if best else float(pd.Series(deltas).median())
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


def build_html(account: str, stem: str, trades: pd.DataFrame, bars_by_symbol: dict, mapping_by_symbol: dict) -> str:
    chart_trades = add_plot_prices(trades, bars_by_symbol)
    for col in chart_trades.columns:
        if pd.api.types.is_datetime64_any_dtype(chart_trades[col]):
            chart_trades[col] = chart_trades[col].dt.strftime("%Y-%m-%d %H:%M:%S")

    bars_json = {}
    for sym, bars in bars_by_symbol.items():
        symbol_trades = trades[trades["Item"] == sym] if "Item" in trades.columns else trades.iloc[0:0]
        b = bars_for_html(bars, symbol_trades)
        b["time"] = pd.to_datetime(b["time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        bars_json[sym] = b[["time", "open", "high", "low", "close", "tick_volume"]].to_dict(orient="records")

    payload = {
        "account": account,
        "stem": stem,
        "accountMeta": account_meta_from_trades(chart_trades),
        "barsBySymbol": bars_json,
        "trades": chart_trades.where(pd.notna(chart_trades), None).to_dict(orient="records"),
        "mappingBySymbol": mapping_by_symbol,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{stem} 买卖点K线图</title>
<style>
body {{ margin:0; font-family: Arial, "Microsoft YaHei", sans-serif; background:#f5f6f8; color:#1f2937; }}
header {{ padding:14px 20px; background:#111827; color:#fff; }}
h1 {{ margin:0 0 8px; font-size:20px; }}
.meta {{ display:flex; flex-wrap:wrap; gap:10px 22px; font-size:13px; color:#d1d5db; }}
.toolbar {{ display:flex; align-items:center; gap:8px; padding:12px 18px 6px; flex-wrap:wrap; }}
select, button, input {{ border:1px solid #cbd5e1; background:#fff; color:#111827; padding:7px 10px; border-radius:4px; }}
button {{ cursor:pointer; }}
button:hover {{ background:#f1f5f9; }}
label {{ display:inline-flex; align-items:center; gap:5px; }}
.filters {{ display:flex; align-items:center; gap:8px; padding:6px 18px 10px; flex-wrap:wrap; border-bottom:1px solid #e5e7eb; }}
.filters input {{ width:82px; padding:6px 7px; }}
.filters select {{ padding:6px 7px; }}
.filters .filterTitle {{ color:#334155; font-weight:700; }}
.status {{ margin-left:auto; color:#4b5563; font-size:13px; }}
.wrap {{ padding:0 18px 22px; }}
.chartShell {{ position:relative; }}
#chart {{ display:block; width:100%; height:760px; background:#fff; border:1px solid #cbd5e1; cursor:grab; }}
#chart.dragging {{ cursor:grabbing; }}
.panelToggle {{ position:absolute; top:10px; right:12px; display:flex; gap:4px; background:rgba(255,255,255,0.9); border:1px solid #cbd5e1; padding:3px; }}
.panelToggle button {{ padding:4px 9px; border:0; background:transparent; font-size:12px; }}
.panelToggle button.active {{ background:#111827; color:#fff; }}
.chartHelp {{ margin-top:8px; background:#fff; border:1px solid rgba(203,213,225,0.95); padding:6px 8px; }}
.legend {{ display:flex; flex-wrap:wrap; gap:12px; font-size:13px; line-height:1.4; }}
.sw {{ width:12px; height:12px; display:inline-block; margin-right:5px; vertical-align:-1px; }}
.note {{ margin-top:3px; color:#4b5563; font-size:13px; line-height:1.35; }}
.summary {{ display:grid; grid-template-columns:repeat(4,minmax(150px,1fr)); gap:8px; margin-top:12px; }}
.metric {{ background:#fff; border:1px solid #e5e7eb; padding:10px 12px; }}
.metric .k {{ color:#64748b; font-size:12px; }}
.metric .v {{ margin-top:5px; font-size:18px; font-weight:700; color:#111827; }}
.windowControls {{ display:grid; grid-template-columns:1fr 1fr auto; gap:6px; margin-top:6px; align-items:center; }}
.windowControls input {{ width:100%; min-width:0; padding:6px 7px; font-size:12px; }}
.windowControls button {{ padding:6px 9px; white-space:nowrap; }}
.windowHint {{ margin-top:5px; color:#64748b; font-size:11px; }}
.tableWrap {{ overflow:auto; max-height:420px; border:1px solid #e5e7eb; background:#fff; margin-top:12px; }}
table {{ width:100%; border-collapse:collapse; background:#fff; font-size:12px; }}
th, td {{ border:1px solid #e5e7eb; padding:5px 7px; text-align:right; white-space:nowrap; }}
th {{ background:#eef2f7; position:sticky; top:0; z-index:1; }}
td.left, th.left {{ text-align:left; }}
@media (max-width: 900px) {{ .summary {{ grid-template-columns:repeat(1,minmax(150px,1fr)); }} .status {{ margin-left:0; width:100%; }} }}
</style>
</head>
<body>
<header>
<h1>{stem} / 买卖点K线图</h1>
<div class="meta" id="meta"></div>
</header>
<div class="toolbar">
<select id="symbolSelect"></select>
<button id="zoomIn">放大</button>
<button id="zoomOut">缩小</button>
<button id="reset">重置</button>
<button id="fitTrades">只看交易区间</button>
<label>显示订单 <input id="displayLimit" type="number" min="1" step="50" value="300" style="width:86px;"> 笔</label>
<span class="status" id="status"></span>
</div>
<div class="filters">
<span class="filterTitle">过滤</span>
<label>方向 <select id="filterType"><option value="">全部</option><option value="buy">buy</option><option value="sell">sell</option></select></label>
<label>手数 <input id="filterVolumeMin" type="number" step="0.01" placeholder="min"> - <input id="filterVolumeMax" type="number" step="0.01" placeholder="max"></label>
<label>Profit <input id="filterProfitMin" type="number" step="1" placeholder="min"> - <input id="filterProfitMax" type="number" step="1" placeholder="max"></label>
<label>持仓分钟 <input id="filterHoldMin" type="number" step="1" placeholder="min"> - <input id="filterHoldMax" type="number" step="1" placeholder="max"></label>
<button id="clearFilters">清空</button>
</div>
<div class="wrap">
<div class="chartShell">
<canvas id="chart"></canvas>
<div class="panelToggle">
<button id="panelProfit" class="active">Profit</button>
<button id="panelVolume">手数</button>
</div>
<div class="chartHelp">
<div class="legend">
<span><i class="sw" style="background:#16a34a"></i>买入开仓</span>
<span><i class="sw" style="background:#dc2626"></i>卖出开仓</span>
<span><i class="sw" style="background:#2563eb"></i>平仓</span>
<span><i class="sw" style="background:#7c3aed"></i>持仓连线</span>
<span><i class="sw" style="background:#ef4444"></i>盈利柱</span>
<span><i class="sw" style="background:#22c55e"></i>亏损柱</span>
<span><i class="sw" style="background:#3b82f6"></i>手数柱</span>
<span>右上角切换 Profit/手数；过滤条件会同步影响图上订单、表格和底部指标；滚轮缩放，按住拖动，双击重置；鼠标移动显示十字光标</span>
</div>
<div class="note">报价/K线缓存保存在 {OUT_DIR}；HTML 按 account + 时间范围命名。“显示订单”会同步控制图上点位、下方表格和盈亏柱状图。</div>
</div>
</div>
<div class="summary">
<div class="metric"><div class="k">当前显示订单</div><div class="v" id="shownCount">0</div></div>
<div class="metric"><div class="k">当前显示 Profit</div><div class="v" id="shownProfit">0.00</div></div>
<div class="metric"><div class="k">全量 Closed P/L</div><div class="v" id="totalClosedPL">0.00</div></div>
<div class="metric">
<div class="k">时间窗口</div>
<div class="windowControls">
<input id="windowStart" type="text" placeholder="YYYY-MM-DD HH:MM">
<input id="windowEnd" type="text" placeholder="YYYY-MM-DD HH:MM">
<button id="applyWindow">应用</button>
</div>
<div class="windowHint" id="windowLabel">输入时间后定位到该区间</div>
</div>
</div>
<div class="tableWrap"><table id="tradeTable"></table></div>
</div>
<script>
const DATA = {payload_json};
const canvas = document.getElementById('chart');
const ctx = canvas.getContext('2d');
const statusEl = document.getElementById('status');
const metaEl = document.getElementById('meta');
const symbolSelect = document.getElementById('symbolSelect');
const displayLimitInput = document.getElementById('displayLimit');
const windowStartInput = document.getElementById('windowStart');
const windowEndInput = document.getElementById('windowEnd');
const filterTypeInput = document.getElementById('filterType');
const filterVolumeMinInput = document.getElementById('filterVolumeMin');
const filterVolumeMaxInput = document.getElementById('filterVolumeMax');
const filterProfitMinInput = document.getElementById('filterProfitMin');
const filterProfitMaxInput = document.getElementById('filterProfitMax');
const filterHoldMinInput = document.getElementById('filterHoldMin');
const filterHoldMaxInput = document.getElementById('filterHoldMax');
let symbol = Object.keys(DATA.barsBySymbol)[0];
let bars = [], trades = [], viewStart = 0, viewEnd = 1, drag = null, crosshair = null;
let panelMode = 'profit';

function findIndex(time) {{
  const key = String(time).slice(0,16);
  let lo = 0, hi = bars.length - 1;
  while (lo <= hi) {{
    const mid = (lo + hi) >> 1, bt = bars[mid].time.slice(0,16);
    if (bt === key) return mid;
    if (bt < key) lo = mid + 1; else hi = mid - 1;
  }}
  return Math.max(0, Math.min(bars.length - 1, lo));
}}
function setSymbol(sym) {{
  symbol = sym;
  bars = DATA.barsBySymbol[symbol] || [];
  trades = DATA.trades.filter(t => t.Item === symbol).map(t => ({{...t, openIdx: findIndex(t["Open Time"]), closeIdx: findIndex(t["Close Time"])}}));
  viewStart = 0; viewEnd = Math.max(1, bars.length - 1);
  const m = DATA.mappingBySymbol[symbol] || {{}};
  const am = DATA.accountMeta || {{}};
  const currencyText = am.isCentAccount
    ? `币种：${{am.currency}} 美分账户，金额已按 ${{am.displayCurrency || 'USD'}} 口径显示`
    : (am.currency ? `币种：${{am.currency}}` : '');
  metaEl.innerHTML = `<span>账户：${{DATA.account}}</span>${{currencyText ? `<span>${{currencyText}}</span>` : ''}}<span>品种：${{symbol}} -> ${{m.mt5_symbol || ''}}</span><span>时间判断：${{m.time_mode || ''}}</span><span>查询偏移：${{m.hour_delta ?? ''}}小时</span><span>M1包络中位价差：${{m.median_distance_to_m1_range == null ? '' : Number(m.median_distance_to_m1_range).toFixed(5)}}</span><span>订单数：${{trades.length}}</span>`;
  fitTrades();
}}
function displayLimit() {{ return Math.max(1, Number(displayLimitInput.value) || 300); }}
function parseTimeInput(value) {{
  const text = String(value || '').trim();
  if (!text) return null;
  return (text.length === 16 ? text + ':00' : text).replace('T', ' ');
}}
function setInputsFromView() {{
  if (!bars.length) return;
  const s = Math.max(0, Math.floor(viewStart)), e = Math.min(bars.length - 1, Math.floor(viewEnd));
  windowStartInput.value = bars[s].time.slice(0, 16);
  windowEndInput.value = bars[e].time.slice(0, 16);
}}
function applyWindow() {{
  const start = parseTimeInput(windowStartInput.value), end = parseTimeInput(windowEndInput.value);
  if (!start || !end || !bars.length) return;
  const startIdx = findIndex(start), endIdx = findIndex(end);
  viewStart = Math.max(0, Math.min(startIdx, endIdx));
  viewEnd = Math.min(bars.length - 1, Math.max(startIdx, endIdx));
  clampView();
  draw(false);
}}
function applyWindowOnEnter(ev) {{
  if (ev.key === 'Enter') applyWindow();
}}
function holdSeconds(t) {{
  if (t["Holding Seconds"] != null && t["Holding Seconds"] !== '') return Math.max(0, Number(t["Holding Seconds"]) || 0);
  const open = new Date(String(t["Open Time"]).replace(' ', 'T'));
  const close = new Date(String(t["Close Time"]).replace(' ', 'T'));
  const sec = Math.round((close - open) / 1000);
  return Number.isFinite(sec) ? Math.max(0, sec) : 0;
}}
function formatDuration(sec) {{
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return `${{h}}小时${{m}}分`;
  if (m) return `${{m}}分${{s}}秒`;
  return `${{s}}秒`;
}}
function clampView() {{
  const minSpan = 8;
  if (viewEnd - viewStart < minSpan) {{
    const mid = (viewStart + viewEnd) / 2;
    viewStart = mid - minSpan / 2; viewEnd = mid + minSpan / 2;
  }}
  const span = viewEnd - viewStart;
  if (viewStart < 0) {{ viewStart = 0; viewEnd = span; }}
  if (viewEnd > bars.length - 1) {{ viewEnd = bars.length - 1; viewStart = viewEnd - span; }}
  viewStart = Math.max(0, viewStart); viewEnd = Math.min(bars.length - 1, viewEnd);
}}
function zoom(factor, anchorRatio=0.5) {{
  const span = viewEnd - viewStart, anchor = viewStart + span * anchorRatio;
  const newSpan = Math.max(8, Math.min(bars.length - 1, span * factor));
  viewStart = anchor - newSpan * anchorRatio;
  viewEnd = anchor + newSpan * (1 - anchorRatio);
  clampView(); draw();
}}
function reset() {{ viewStart = 0; viewEnd = Math.max(1, bars.length - 1); draw(); }}
function fitTrades() {{
  const all = filteredTrades();
  const focusLimit = Math.min(displayLimit(), 60);
  const focus = all.length > focusLimit ? all.slice(-focusLimit) : all;
  const idxs = focus.flatMap(t => [t.openIdx, t.closeIdx]).filter(Number.isFinite);
  if (!idxs.length) return draw();
  viewStart = Math.max(0, Math.min(...idxs) - 20);
  viewEnd = Math.min(bars.length - 1, Math.max(...idxs) + 20);
  draw();
}}
function visibleBars() {{
  const s = Math.max(0, Math.floor(viewStart)), e = Math.min(bars.length - 1, Math.ceil(viewEnd));
  return bars.slice(s, e + 1).map((b, i) => [b, s + i]);
}}
function visibleTrades() {{
  return filteredTrades().filter(t => (t.openIdx >= viewStart && t.openIdx <= viewEnd) || (t.closeIdx >= viewStart && t.closeIdx <= viewEnd));
}}
function numberFilter(input) {{
  const text = String(input.value || '').trim();
  if (!text) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}}
function filteredTrades() {{
  const type = filterTypeInput.value;
  const volMin = numberFilter(filterVolumeMinInput), volMax = numberFilter(filterVolumeMaxInput);
  const profitMin = numberFilter(filterProfitMinInput), profitMax = numberFilter(filterProfitMaxInput);
  const holdMin = numberFilter(filterHoldMinInput), holdMax = numberFilter(filterHoldMaxInput);
  return trades.filter(t => {{
    const volume = Number(t.Volume) || 0;
    const profit = Number(t.Profit) || 0;
    const holdMinValue = holdSeconds(t) / 60;
    if (type && t.Type !== type) return false;
    if (volMin != null && volume < volMin) return false;
    if (volMax != null && volume > volMax) return false;
    if (profitMin != null && profit < profitMin) return false;
    if (profitMax != null && profit > profitMax) return false;
    if (holdMin != null && holdMinValue < holdMin) return false;
    if (holdMax != null && holdMinValue > holdMax) return false;
    return true;
  }});
}}
function resizeCanvas(c, cctx) {{
  const dpr = window.devicePixelRatio || 1, rect = c.getBoundingClientRect();
  c.width = Math.floor(rect.width * dpr); c.height = Math.floor(rect.height * dpr);
  cctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}}
function resize() {{ resizeCanvas(canvas, ctx); draw(); }}
function digits() {{ return symbol.includes('XAU') ? 2 : 5; }}
function draw(syncInputs=true) {{
  const rect = canvas.getBoundingClientRect(), W = rect.width, H = rect.height;
  ctx.clearRect(0, 0, W, H);
  const pad = {{l:72, r:24, t:20, b:74}}, plotW = W - pad.l - pad.r;
  const profitH = 118, profitGap = 30;
  const plotH = Math.max(280, H - pad.t - pad.b - profitH - profitGap);
  const profitTop = pad.t + plotH + profitGap;
  const allFiltered = filteredTrades();
  const vb = visibleBars(), vt = visibleTrades(), shown = vt.slice(0, displayLimit());
  if (!vb.length) return;
  const prices = [];
  vb.forEach(([b]) => prices.push(Number(b.low), Number(b.high)));
  shown.forEach(t => prices.push(Number(t["Open Plot Price"] ?? t["Open Price"]), Number(t["Close Plot Price"] ?? t["Close Price"])));
  let lo = Math.min(...prices), hi = Math.max(...prices);
  const d = digits(), margin = Math.max(d === 2 ? 0.5 : 0.0002, (hi - lo) * 0.08);
  lo -= margin; hi += margin;
  const y = p => pad.t + (hi - p) / (hi - lo) * plotH;
  const x = idx => pad.l + (idx - viewStart) / (viewEnd - viewStart) * plotW;
  const candleX = idx => x(idx);
  const priceFromY = yy => hi - ((yy - pad.t) / plotH) * (hi - lo);
  const indexFromX = xx => viewStart + ((xx - pad.l) / plotW) * (viewEnd - viewStart);
  ctx.fillStyle = '#fff'; ctx.fillRect(pad.l, pad.t, plotW, plotH);
  ctx.strokeStyle = '#e5e7eb'; ctx.lineWidth = 1; ctx.font = '12px Arial'; ctx.fillStyle = '#4b5563';
  for (let k = 0; k <= 7; k++) {{
    const yy = pad.t + plotH * k / 7;
    ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(pad.l + plotW, yy); ctx.stroke();
    ctx.fillText((hi - (hi - lo) * k / 7).toFixed(d), 8, yy + 4);
  }}
  const span = viewEnd - viewStart, labelStep = Math.max(1, Math.ceil(span / 9));
  for (let i = Math.ceil(viewStart); i <= Math.floor(viewEnd); i += labelStep) ctx.fillText(bars[i].time.slice(5,16), x(i) - 28, H - 38);
  const maxDrawBars = Math.max(1, Math.floor(plotW * 1.5));
  let candleBars = vb;
  if (vb.length > maxDrawBars) {{
    const buckets = new Map();
    vb.forEach(([b, idx]) => {{
      const key = Math.floor(candleX(idx));
      let g = buckets.get(key);
      if (!g) {{
        g = {{idxSum: 0, count: 0, open: Number(b.open), close: Number(b.close), high: Number(b.high), low: Number(b.low)}};
        buckets.set(key, g);
      }}
      g.idxSum += idx; g.count += 1;
      g.high = Math.max(g.high, Number(b.high));
      g.low = Math.min(g.low, Number(b.low));
      g.close = Number(b.close);
    }});
    candleBars = Array.from(buckets.values()).map(g => [{{open: g.open, high: g.high, low: g.low, close: g.close}}, g.idxSum / g.count]);
  }}
  const candleW = vb.length > maxDrawBars ? 1.2 : Math.max(2, Math.min(13, Math.abs(x(viewStart + 1) - x(viewStart)) * 0.62));
  candleBars.forEach(([b, idx]) => {{
    const xx = candleX(idx), up = Number(b.close) >= Number(b.open);
    ctx.strokeStyle = up ? '#16a34a' : '#dc2626'; ctx.fillStyle = ctx.strokeStyle;
    ctx.beginPath(); ctx.moveTo(xx, y(Number(b.high))); ctx.lineTo(xx, y(Number(b.low))); ctx.stroke();
    const top = y(Math.max(Number(b.open), Number(b.close))), bot = y(Math.min(Number(b.open), Number(b.close)));
    ctx.fillRect(xx - candleW / 2, top, candleW, Math.max(1, bot - top));
  }});
  shown.forEach(t => {{
    const xo = x(t.openIdx), xc = x(t.closeIdx), yo = y(Number(t["Open Plot Price"] ?? t["Open Price"])), yc = y(Number(t["Close Plot Price"] ?? t["Close Price"]));
    ctx.strokeStyle = '#7c3aed'; ctx.lineWidth = 1.6; ctx.setLineDash([5,4]);
    ctx.beginPath(); ctx.moveTo(xo, yo); ctx.lineTo(xc, yc); ctx.stroke(); ctx.setLineDash([]);
    if (t.openIdx >= viewStart && t.openIdx <= viewEnd) {{
      ctx.fillStyle = t.Type === 'buy' ? '#16a34a' : '#dc2626';
      ctx.beginPath(); ctx.arc(xo, yo, 5, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
    }}
    if (t.closeIdx >= viewStart && t.closeIdx <= viewEnd) {{
      ctx.fillStyle = '#2563eb'; ctx.fillRect(xc - 4, yc - 4, 8, 8);
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.2; ctx.strokeRect(xc - 4, yc - 4, 8, 8);
    }}
  }});
  ctx.strokeStyle = '#9ca3af'; ctx.strokeRect(pad.l, pad.t, plotW, plotH);
  drawBottomPanel(shown, pad, plotW, profitTop, profitH, x);
  const crosshairBottom = profitTop + profitH;
  if (crosshair && crosshair.x >= pad.l && crosshair.x <= pad.l + plotW && crosshair.y >= pad.t && crosshair.y <= crosshairBottom) {{
    const idx = Math.max(0, Math.min(bars.length - 1, Math.round(indexFromX(crosshair.x)))), cx = x(idx), cy = crosshair.y, price = priceFromY(cy);
    const xLabel = Math.max(pad.l, Math.min(cx - 58, pad.l + plotW - 116)), yLabel = Math.max(pad.t, Math.min(cy - 10, pad.t + plotH - 20));
    ctx.save(); ctx.strokeStyle = '#111827'; ctx.setLineDash([3,3]);
    ctx.beginPath(); ctx.moveTo(cx, pad.t); ctx.lineTo(cx, crosshairBottom); ctx.stroke();
    if (cy <= pad.t + plotH) {{
      ctx.beginPath(); ctx.moveTo(pad.l, cy); ctx.lineTo(pad.l + plotW, cy); ctx.stroke();
      const priceLabelX = Math.max(4, pad.l - 68);
      ctx.setLineDash([]); ctx.fillStyle = '#111827'; ctx.fillRect(priceLabelX, yLabel, 64, 20);
      ctx.fillStyle = '#fff'; ctx.fillText(price.toFixed(d), priceLabelX + 6, yLabel + 14);
    }} else {{
      ctx.setLineDash([]);
    }}
    ctx.fillStyle = '#111827'; ctx.fillRect(xLabel, crosshairBottom + 8, 116, 20);
    ctx.fillStyle = '#fff'; ctx.fillText(bars[idx].time.slice(5,16), xLabel + 8, crosshairBottom + 22); ctx.restore();
  }}
  const s = Math.max(0, Math.floor(viewStart)), e = Math.min(bars.length - 1, Math.floor(viewEnd));
  statusEl.textContent = `${{bars[s].time}} - ${{bars[e].time}} | 可见K线 ${{e - s + 1}} / ${{bars.length}} | 过滤后 ${{allFiltered.length}} | 可见交易 ${{vt.length}} | 实际显示 ${{shown.length}}`;
  if (syncInputs) setInputsFromView();
  updateTable(shown); updateSummary(shown, s, e);
}}
function updateSummary(rows, s, e) {{
  const total = rows.reduce((sum, t) => sum + (Number(t.Profit) || 0), 0);
  const shownClosedPL = rows.reduce((sum, t) => sum + (Number(t.Profit) || 0) + (Number(t.Commission) || 0) + (Number(t.Taxes) || 0) + (Number(t.Swap) || 0), 0);
  const totalClosedPL = trades.reduce((sum, t) => sum + (Number(t.Profit) || 0) + (Number(t.Commission) || 0) + (Number(t.Taxes) || 0) + (Number(t.Swap) || 0), 0);
  document.getElementById('shownCount').textContent = String(rows.length);
  document.getElementById('shownProfit').textContent = `${{total.toFixed(2)}} / Net ${{shownClosedPL.toFixed(2)}}`;
  document.getElementById('shownProfit').style.color = shownClosedPL >= 0 ? '#dc2626' : '#16a34a';
  document.getElementById('totalClosedPL').textContent = totalClosedPL.toFixed(2);
  document.getElementById('totalClosedPL').style.color = totalClosedPL >= 0 ? '#dc2626' : '#16a34a';
  document.getElementById('windowLabel').textContent = bars.length ? `${{bars[s].time}} 至 ${{bars[e].time}}` : '-';
}}
function updateTable(rows) {{
  const cols = ["Ticket","Type","Volume","Open Time","Open Price","S/L","T/P","Close Time","Close Price","Hold Time","Profit","Comment"];
  document.getElementById('tradeTable').innerHTML = '<thead><tr>' + cols.map(c => `<th class="${{["Ticket","Type","Open Time","Close Time","Hold Time"].includes(c) ? 'left' : ''}}">${{c}}</th>`).join('') + '</tr></thead><tbody>' + rows.map(t => '<tr>' + cols.map(c => {{
    let v = c === "Hold Time" ? formatDuration(holdSeconds(t)) : t[c];
    if (typeof v === 'number') v = Math.abs(v) < 10 ? v.toFixed(5) : v.toFixed(2);
    return `<td class="${{["Ticket","Type","Open Time","Close Time","Hold Time","Comment"].includes(c) ? 'left' : ''}}">${{v ?? ''}}</td>`;
  }}).join('') + '</tr>').join('') + '</tbody>';
}}
function drawBottomPanel(rows, pad, plotW, top, height, xScale) {{
  ctx.save();
  ctx.fillStyle = 'rgba(248,250,252,0.96)';
  ctx.fillRect(pad.l, top, plotW, height);
  ctx.strokeStyle = '#dbe4ee';
  ctx.strokeRect(pad.l, top, plotW, height);
  ctx.font = '12px Arial';
  const label = panelMode === 'volume' ? 'Volume' : 'Profit';
  ctx.fillStyle = '#4b5563';
  ctx.fillText(label, pad.l + 8, top + 16);
  if (!rows.length) {{ ctx.restore(); return; }}
  const bw = Math.max(5, Math.min(18, plotW / Math.max(1, rows.length) * 0.72));

  if (panelMode === 'volume') {{
    const volumes = rows.map(t => Math.max(0, Number(t.Volume) || 0));
    const maxVol = Math.max(0.01, ...volumes);
    const baseY = top + height - 22;
    ctx.strokeStyle = '#94a3b8';
    ctx.beginPath(); ctx.moveTo(pad.l, baseY); ctx.lineTo(pad.l + plotW, baseY); ctx.stroke();
    ctx.fillStyle = '#4b5563';
    ctx.fillText(maxVol.toFixed(2), pad.l + 8, top + 34);
    ctx.fillText('0', pad.l + 8, baseY - 4);
    rows.forEach(t => {{
      const v = Math.max(0, Number(t.Volume) || 0);
      const h = v / maxVol * (height - 42);
      const cx = xScale(t.openIdx);
      if (cx < pad.l - bw || cx > pad.l + plotW + bw) return;
      ctx.fillStyle = 'rgba(59,130,246,0.86)';
      ctx.fillRect(cx - bw / 2, baseY - h, bw, Math.max(1, h));
    }});
    ctx.fillStyle = '#4b5563';
    ctx.fillText(`当前显示 ${{rows.length}} 笔，柱高代表手数`, pad.l + 8, top + height - 7);
    ctx.restore();
    return;
  }}

  const profits = rows.map(t => Number(t.Profit) || 0);
  const maxAbs = Math.max(1, ...profits.map(v => Math.abs(v)));
  const zeroY = top + height / 2;
  ctx.strokeStyle = '#64748b';
  ctx.setLineDash([5,4]);
  ctx.beginPath(); ctx.moveTo(pad.l, zeroY); ctx.lineTo(pad.l + plotW, zeroY); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = '#4b5563';
  ctx.fillText(maxAbs.toFixed(2), pad.l + 8, top + 34);
  ctx.fillText('0', pad.l + 8, zeroY - 4);
  ctx.fillText((-maxAbs).toFixed(2), pad.l + 8, top + height - 24);
  rows.forEach(t => {{
    const p = Number(t.Profit) || 0;
    const h = Math.abs(p) / maxAbs * (height / 2 - 14);
    const cx = xScale(t.openIdx);
    if (cx < pad.l - bw || cx > pad.l + plotW + bw) return;
    const y = p >= 0 ? zeroY - h : zeroY;
    ctx.fillStyle = p >= 0 ? 'rgba(239,68,68,0.86)' : 'rgba(34,197,94,0.86)';
    ctx.fillRect(cx - bw / 2, y, bw, Math.max(1, h));
  }});
  ctx.fillStyle = '#4b5563';
  ctx.fillText(`当前显示 ${{rows.length}} 笔，红色为盈利，绿色为亏损`, pad.l + 8, top + height - 7);
  ctx.restore();
}}
function drawProfitPanel(rows, pad, plotW, top, height, xScale) {{
  ctx.save();
  ctx.fillStyle = 'rgba(248,250,252,0.94)';
  ctx.fillRect(pad.l, top, plotW, height);
  ctx.strokeStyle = '#dbe4ee';
  ctx.strokeRect(pad.l, top, plotW, height);
  ctx.font = '12px Arial';
  ctx.fillStyle = '#4b5563';
  ctx.fillText('Profit', 20, top + 16);
  if (!rows.length) {{ ctx.restore(); return; }}
  const profits = rows.map(t => Number(t.Profit) || 0);
  const maxAbs = Math.max(1, ...profits.map(v => Math.abs(v)));
  const zeroY = top + height / 2;
  ctx.strokeStyle = '#64748b';
  ctx.setLineDash([5,4]);
  ctx.beginPath(); ctx.moveTo(pad.l, zeroY); ctx.lineTo(pad.l + plotW, zeroY); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = '#4b5563';
  ctx.fillText('0', 48, zeroY + 4);
  ctx.fillText(maxAbs.toFixed(2), 10, top + 12);
  ctx.fillText((-maxAbs).toFixed(2), 8, top + height - 6);
  const bw = Math.max(5, Math.min(18, plotW / Math.max(1, rows.length) * 0.72));
  rows.forEach(t => {{
    const p = Number(t.Profit) || 0;
    const h = Math.abs(p) / maxAbs * (height / 2 - 10);
    const cx = xScale(t.openIdx);
    if (cx < pad.l - bw || cx > pad.l + plotW + bw) return;
    const y = p >= 0 ? zeroY - h : zeroY;
    ctx.fillStyle = p >= 0 ? 'rgba(239,68,68,0.86)' : 'rgba(34,197,94,0.86)';
    ctx.fillRect(cx - bw / 2, y, bw, Math.max(1, h));
  }});
  ctx.fillStyle = '#4b5563';
  ctx.fillText(`当前显示 ${{rows.length}} 笔，红色为盈利，绿色为亏损`, pad.l + 4, top + height - 8);
  ctx.restore();
}}
function drawProfitChart(rows) {{
  const rect = profitCanvas.getBoundingClientRect(), W = rect.width, H = rect.height;
  profitCtx.clearRect(0, 0, W, H);
  const pad = {{l:72, r:24, t:22, b:38}}, plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
  profitCtx.fillStyle = '#fff'; profitCtx.fillRect(0, 0, W, H);
  profitCtx.strokeStyle = '#e5e7eb'; profitCtx.strokeRect(pad.l, pad.t, plotW, plotH);
  profitCtx.font = '12px Arial'; profitCtx.fillStyle = '#4b5563'; profitCtx.fillText('Profit', 18, pad.t + 12);
  if (!rows.length) return;
  const profits = rows.map(t => Number(t.Profit) || 0), maxAbs = Math.max(1, ...profits.map(v => Math.abs(v)));
  const zeroY = pad.t + plotH / 2;
  profitCtx.strokeStyle = '#111827'; profitCtx.setLineDash([5,4]); profitCtx.beginPath(); profitCtx.moveTo(pad.l, zeroY); profitCtx.lineTo(pad.l + plotW, zeroY); profitCtx.stroke(); profitCtx.setLineDash([]);
  profitCtx.fillStyle = '#4b5563'; profitCtx.fillText('0', 44, zeroY + 4); profitCtx.fillText(maxAbs.toFixed(2), 16, pad.t + 4); profitCtx.fillText((-maxAbs).toFixed(2), 12, pad.t + plotH + 4);
  const gap = Math.min(3, plotW / rows.length * 0.18), bw = Math.max(1, plotW / rows.length - gap);
  rows.forEach((t, i) => {{
    const p = Number(t.Profit) || 0, h = Math.abs(p) / maxAbs * (plotH / 2 - 8);
    const x = pad.l + i * (plotW / rows.length) + gap / 2, y = p >= 0 ? zeroY - h : zeroY;
    profitCtx.fillStyle = p >= 0 ? '#ef4444' : '#22c55e';
    profitCtx.fillRect(x, y, bw, Math.max(1, h));
  }});
  profitCtx.fillStyle = '#4b5563'; profitCtx.fillText(`当前显示 ${{rows.length}} 笔，红色为盈利，绿色为亏损`, pad.l, H - 13);
}}
canvas.addEventListener('wheel', ev => {{
  ev.preventDefault();
  const rect = canvas.getBoundingClientRect();
  zoom(ev.deltaY < 0 ? 0.72 : 1.38, Math.max(0, Math.min(1, (ev.clientX - rect.left - 72) / (rect.width - 96))));
}}, {{passive:false}});
canvas.addEventListener('mousemove', ev => {{ const rect = canvas.getBoundingClientRect(); crosshair = {{x:ev.clientX - rect.left, y:ev.clientY - rect.top}}; draw(false); }});
canvas.addEventListener('mouseleave', () => {{ crosshair = null; draw(false); }});
canvas.addEventListener('mousedown', ev => {{ canvas.classList.add('dragging'); drag = {{x:ev.clientX, start:viewStart, end:viewEnd}}; }});
window.addEventListener('mousemove', ev => {{
  if (!drag) return;
  const rect = canvas.getBoundingClientRect(), span = drag.end - drag.start, deltaPx = ev.clientX - drag.x;
  viewStart = drag.start - deltaPx / Math.max(1, rect.width - 96) * span;
  viewEnd = drag.end - deltaPx / Math.max(1, rect.width - 96) * span;
  clampView(); draw();
}});
window.addEventListener('mouseup', () => {{ drag = null; canvas.classList.remove('dragging'); }});
canvas.addEventListener('dblclick', reset);
document.getElementById('zoomIn').addEventListener('click', () => zoom(0.65));
document.getElementById('zoomOut').addEventListener('click', () => zoom(1.55));
document.getElementById('reset').addEventListener('click', reset);
document.getElementById('fitTrades').addEventListener('click', fitTrades);
document.getElementById('applyWindow').addEventListener('click', applyWindow);
windowStartInput.addEventListener('keydown', applyWindowOnEnter);
windowEndInput.addEventListener('keydown', applyWindowOnEnter);
document.getElementById('panelProfit').addEventListener('click', () => {{
  panelMode = 'profit';
  document.getElementById('panelProfit').classList.add('active');
  document.getElementById('panelVolume').classList.remove('active');
  draw(false);
}});
document.getElementById('panelVolume').addEventListener('click', () => {{
  panelMode = 'volume';
  document.getElementById('panelVolume').classList.add('active');
  document.getElementById('panelProfit').classList.remove('active');
  draw(false);
}});
displayLimitInput.addEventListener('input', () => draw(false));
[
  filterTypeInput,
  filterVolumeMinInput,
  filterVolumeMaxInput,
  filterProfitMinInput,
  filterProfitMaxInput,
  filterHoldMinInput,
  filterHoldMaxInput,
].forEach(el => el.addEventListener('input', () => draw(false)));
document.getElementById('clearFilters').addEventListener('click', () => {{
  filterTypeInput.value = '';
  filterVolumeMinInput.value = '';
  filterVolumeMaxInput.value = '';
  filterProfitMinInput.value = '';
  filterProfitMaxInput.value = '';
  filterHoldMinInput.value = '';
  filterHoldMaxInput.value = '';
  draw(false);
}});
symbolSelect.innerHTML = Object.keys(DATA.barsBySymbol).map(s => `<option value="${{s}}">${{s}}</option>`).join('');
symbolSelect.addEventListener('change', ev => setSymbol(ev.target.value));
window.addEventListener('resize', resize);
setSymbol(symbol); resize();
</script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build enhanced buy/sell K-line HTML from cached trades and M1 bars.")
    parser.add_argument("--trades", required=True, help="Path to {stem}_trades.csv")
    parser.add_argument("--mapping", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    trades_path = Path(args.trades)
    stem = infer_stem(trades_path)
    account = stem.split("_", 1)[0]
    mapping_path = Path(args.mapping) if args.mapping else trades_path.with_name(f"{stem}_mapping.json")
    out_path = Path(args.out) if args.out else trades_path.with_name(f"{stem}_trade_kline.html")

    trades = pd.read_csv(trades_path, parse_dates=["Open Time", "Close Time"])
    if "Holding Seconds" not in trades.columns:
        trades["Holding Seconds"] = (trades["Close Time"] - trades["Open Time"]).dt.total_seconds()
    mapping_by_symbol = json.loads(mapping_path.read_text(encoding="utf-8"))
    bars_by_symbol = {}
    for report_symbol, mapping in mapping_by_symbol.items():
        symbol_trades = trades[trades["Item"] == report_symbol] if "Item" in trades.columns else trades.iloc[0:0]
        bars = load_bars_for_symbol(trades_path.parent, stem, report_symbol, mapping)
        bars_by_symbol[report_symbol] = apply_display_price_alignment(report_symbol, bars, symbol_trades, mapping)
    statement_path = find_statement_for_stem(trades_path.parent, account)
    html = enhance_trade_kline_html(build_html(account, stem, trades, bars_by_symbol, mapping_by_symbol), statement_path, trades)
    out_path.write_text(html, encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
