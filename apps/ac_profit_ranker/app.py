from __future__ import annotations

import csv
import calendar
import html
import io
import json
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import pymysql


HOST = "127.0.0.1"
PORT = int(os.environ.get("AC_PROFIT_RANKER_PORT", "8788"))

DB_HOST = os.environ.get("AC_DB_HOST", "rm-3nsv8k160ht47x44uio.mysql.rds.aliyuncs.com")
DB_PORT = int(os.environ.get("AC_DB_PORT", "3306"))
DB_USER = os.environ.get("AC_DB_USER", "intern")
DB_PASSWORD = os.environ.get("AC_DB_PASSWORD", "")

MAX_LIMIT = 1000

FIELDS = [
    "login",
    "balance",
    "REGDATE",
    "name",
    "group_name",
    "status",
    "open_symbols",
    "volume_sum_open",
    "close_symbols",
    "volume_sum_close",
    "vol_diff",
    "profit_sum",
    "floating_symbols",
    "volume_floating",
    "profit_floating",
]

TRADE_FIELDS = [
    "source",
    "login",
    "name",
    "group_name",
    "ticket",
    "order_id",
    "position_id",
    "side",
    "entry",
    "symbol",
    "volume",
    "open_time",
    "close_time",
    "price_open",
    "price_close",
    "profit",
    "storage",
    "commission",
    "fee",
    "comment",
]


def trading_window_start(days: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    today_21 = datetime.combine(now.date(), time(21, 0, 0))
    current_session_start = today_21 if now >= today_21 else today_21 - timedelta(days=1)
    return current_session_start - timedelta(days=max(days, 1) - 1)


def trading_window(days: int, trade_date: str | None = None) -> tuple[datetime, datetime]:
    if trade_date:
        target = date.fromisoformat(trade_date)
        return (
            datetime.combine(target - timedelta(days=max(days, 1)), time(21, 0, 0)),
            datetime.combine(target, time(20, 59, 59)),
        )
    return trading_window_start(days), datetime.now()


def parse_positive_int(value: str | None, default: int, max_value: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return default
    return min(max(parsed, 1), max_value)


def parse_date_text(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    date.fromisoformat(value)
    return value


def db_conn():
    if not DB_PASSWORD:
        raise RuntimeError("Missing AC_DB_PASSWORD environment variable.")
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=900,
        write_timeout=30,
    )


QUERY = """
SELECT
  ranked.login,
  ROUND(COALESCE(u.balance, 0), 2) AS balance,
  u.REGDATE,
  u.name,
  u.group_name,
  u.status,
  COALESCE(o.open_symbols, 'NULL') AS open_symbols,
  ROUND(COALESCE(o.volume_sum_open, 0), 4) AS volume_sum_open,
  COALESCE(c.close_symbols, 'NULL') AS close_symbols,
  ROUND(COALESCE(c.volume_sum_close, 0), 4) AS volume_sum_close,
  ROUND(COALESCE(c.volume_sum_close, 0) - COALESCE(o.volume_sum_open, 0), 4) AS vol_diff,
  ROUND(COALESCE(c.profit_sum, 0), 4) AS profit_sum,
  COALESCE(f.floating_symbols, 'NULL') AS floating_symbols,
  ROUND(COALESCE(f.volume_floating, 0), 2) AS volume_floating,
  ROUND(COALESCE(f.profit_floating, 0), 2) AS profit_floating
FROM (
  SELECT login, SUM(profit_sum) AS profit_sum
  FROM (
    SELECT d.Login AS login,
           SUM((d.Profit + d.Storage + d.Commission + d.Fee) / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS profit_sum
    FROM sass_crm_ac_mt5_live.mt5_deals d
    LEFT JOIN sass_crm_ac_mt5_live.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= %s
      AND d.Time <= %s
      AND d.Action IN (0, 1)
      AND d.Entry IN (1, 3)
      AND u.`Group` NOT LIKE '%%Test%%'
      AND u.`Group` NOT LIKE 'ACFIX%%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login
    UNION ALL
    SELECT d.Login AS login,
           SUM((d.Profit + d.Storage + d.Commission + d.Fee) / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS profit_sum
    FROM int_sass_crm_ac_mt5_live_new.mt5_deals d
    LEFT JOIN int_sass_crm_ac_mt5_live_new.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= %s
      AND d.Time <= %s
      AND d.Action IN (0, 1)
      AND d.Entry IN (1, 3)
      AND u.`Group` NOT LIKE '%%Test%%'
      AND u.`Group` NOT LIKE 'ACFIX%%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login
    UNION ALL
    SELECT d.Login AS login,
           SUM((d.Profit + d.Storage + d.Commission + d.Fee) / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS profit_sum
    FROM sass_crm_ac_mt5_live3.mt5_deals d
    LEFT JOIN sass_crm_ac_mt5_live3.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= %s
      AND d.Time <= %s
      AND d.Action IN (0, 1)
      AND d.Entry IN (1, 3)
      AND u.`Group` NOT LIKE '%%Test%%'
      AND u.`Group` NOT LIKE 'ACFIX%%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login
    UNION ALL
    SELECT t.LOGIN AS login, SUM(t.PROFIT + t.SWAPS + t.COMMISSION) AS profit_sum
    FROM mt4_export_syc.mt4_trades t
    LEFT JOIN mt4_export_syc.mt4_users_view u ON u.LOGIN = t.LOGIN
    WHERE t.CLOSE_TIME >= %s
      AND t.CLOSE_TIME <= %s
      AND t.CMD IN (0, 1)
      AND (u.`GROUP` LIKE 'PXM-%%' OR u.`GROUP` LIKE 'TO-%%')
    GROUP BY t.LOGIN
  ) close_union
  GROUP BY login
  ORDER BY profit_sum DESC
  LIMIT %s
) ranked
LEFT JOIN (
  SELECT Login AS login, Balance AS balance, Registration AS REGDATE, Name AS name, `Group` AS group_name, COALESCE(Status, '') AS status
  FROM sass_crm_ac_mt5_live.mt5_users_view
  UNION ALL
  SELECT Login AS login, Balance AS balance, Registration AS REGDATE, Name AS name, `Group` AS group_name, COALESCE(Status, '') AS status
  FROM int_sass_crm_ac_mt5_live_new.mt5_users_view
  UNION ALL
  SELECT Login AS login, Balance AS balance, Registration AS REGDATE, Name AS name, `Group` AS group_name, COALESCE(Status, '') AS status
  FROM sass_crm_ac_mt5_live3.mt5_users_view
  UNION ALL
  SELECT LOGIN AS login, BALANCE AS balance, REGDATE AS REGDATE, NAME AS name, `GROUP` AS group_name, COALESCE(STATUS, '') AS status
  FROM mt4_export_syc.mt4_users_view
) u ON u.login = ranked.login
LEFT JOIN (
  SELECT
    login,
    GROUP_CONCAT(Symbol ORDER BY Symbol ASC SEPARATOR ',') AS open_symbols,
    SUM(lots) AS volume_sum_open
  FROM (
    SELECT d.Login AS login, d.Symbol AS Symbol,
           SUM(d.Volume / 10000 / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS lots
    FROM sass_crm_ac_mt5_live.mt5_deals d
    LEFT JOIN sass_crm_ac_mt5_live.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= %s
      AND d.Time <= %s
      AND d.Action IN (0, 1)
      AND d.Entry = 0
      AND u.`Group` NOT LIKE '%%Test%%'
      AND u.`Group` NOT LIKE 'ACFIX%%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login, d.Symbol
    UNION ALL
    SELECT d.Login AS login, d.Symbol AS Symbol,
           SUM(d.Volume / 10000 / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS lots
    FROM int_sass_crm_ac_mt5_live_new.mt5_deals d
    LEFT JOIN int_sass_crm_ac_mt5_live_new.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= %s
      AND d.Time <= %s
      AND d.Action IN (0, 1)
      AND d.Entry = 0
      AND u.`Group` NOT LIKE '%%Test%%'
      AND u.`Group` NOT LIKE 'ACFIX%%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login, d.Symbol
    UNION ALL
    SELECT d.Login AS login, d.Symbol AS Symbol,
           SUM(d.Volume / 10000 / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS lots
    FROM sass_crm_ac_mt5_live3.mt5_deals d
    LEFT JOIN sass_crm_ac_mt5_live3.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= %s
      AND d.Time <= %s
      AND d.Action IN (0, 1)
      AND d.Entry = 0
      AND u.`Group` NOT LIKE '%%Test%%'
      AND u.`Group` NOT LIKE 'ACFIX%%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login, d.Symbol
    UNION ALL
    SELECT t.LOGIN AS login, t.SYMBOL AS Symbol, SUM(t.VOLUME) / 100 AS lots
    FROM mt4_export_syc.mt4_trades t
    LEFT JOIN mt4_export_syc.mt4_users_view u ON u.LOGIN = t.LOGIN
    WHERE t.OPEN_TIME >= %s
      AND t.OPEN_TIME <= %s
      AND t.CMD IN (0, 1)
      AND (u.`GROUP` LIKE 'PXM-%%' OR u.`GROUP` LIKE 'TO-%%')
    GROUP BY t.LOGIN, t.SYMBOL
  ) open_by_symbol
  GROUP BY login
) o ON o.login = ranked.login
LEFT JOIN (
  SELECT
    login,
    GROUP_CONCAT(Symbol ORDER BY Symbol ASC SEPARATOR ',') AS close_symbols,
    SUM(lots) AS volume_sum_close,
    SUM(profit) AS profit_sum
  FROM (
    SELECT
      d.Login AS login,
      d.Symbol AS Symbol,
      SUM(d.Volume / 10000 / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS lots,
      SUM((d.Profit + d.Storage + d.Commission + d.Fee) / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS profit
    FROM sass_crm_ac_mt5_live.mt5_deals d
    LEFT JOIN sass_crm_ac_mt5_live.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= %s
      AND d.Time <= %s
      AND d.Action IN (0, 1)
      AND d.Entry IN (1, 3)
      AND u.`Group` NOT LIKE '%%Test%%'
      AND u.`Group` NOT LIKE 'ACFIX%%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login, d.Symbol
    UNION ALL
    SELECT
      d.Login AS login,
      d.Symbol AS Symbol,
      SUM(d.Volume / 10000 / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS lots,
      SUM((d.Profit + d.Storage + d.Commission + d.Fee) / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS profit
    FROM int_sass_crm_ac_mt5_live_new.mt5_deals d
    LEFT JOIN int_sass_crm_ac_mt5_live_new.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= %s
      AND d.Time <= %s
      AND d.Action IN (0, 1)
      AND d.Entry IN (1, 3)
      AND u.`Group` NOT LIKE '%%Test%%'
      AND u.`Group` NOT LIKE 'ACFIX%%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login, d.Symbol
    UNION ALL
    SELECT
      d.Login AS login,
      d.Symbol AS Symbol,
      SUM(d.Volume / 10000 / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS lots,
      SUM((d.Profit + d.Storage + d.Commission + d.Fee) / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS profit
    FROM sass_crm_ac_mt5_live3.mt5_deals d
    LEFT JOIN sass_crm_ac_mt5_live3.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= %s
      AND d.Time <= %s
      AND d.Action IN (0, 1)
      AND d.Entry IN (1, 3)
      AND u.`Group` NOT LIKE '%%Test%%'
      AND u.`Group` NOT LIKE 'ACFIX%%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login, d.Symbol
    UNION ALL
    SELECT t.LOGIN AS login, t.SYMBOL AS Symbol,
           SUM(t.VOLUME) / 100 AS lots,
           SUM(t.PROFIT + t.SWAPS + t.COMMISSION) AS profit
    FROM mt4_export_syc.mt4_trades t
    LEFT JOIN mt4_export_syc.mt4_users_view u ON u.LOGIN = t.LOGIN
    WHERE t.CLOSE_TIME >= %s
      AND t.CLOSE_TIME <= %s
      AND t.CMD IN (0, 1)
      AND (u.`GROUP` LIKE 'PXM-%%' OR u.`GROUP` LIKE 'TO-%%')
    GROUP BY t.LOGIN, t.SYMBOL
  ) close_by_symbol
  GROUP BY login
) c ON c.login = ranked.login
LEFT JOIN (
  SELECT
    login,
    GROUP_CONCAT(Symbol ORDER BY Symbol ASC SEPARATOR ',') AS floating_symbols,
    SUM(lots) AS volume_floating,
    SUM(profit_floating) AS profit_floating
  FROM (
    SELECT
      p.Login AS login,
      p.Symbol AS Symbol,
      SUM(p.Volume / 10000 / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS lots,
      SUM((p.Profit + p.Storage) / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS profit_floating
    FROM sass_crm_ac_mt5_live.mt5_positions p
    LEFT JOIN sass_crm_ac_mt5_live.mt5_users_view u ON u.Login = p.Login
    WHERE u.`Group` NOT LIKE '%%Test%%'
      AND u.`Group` NOT LIKE 'ACFIX%%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY p.Login, p.Symbol
    UNION ALL
    SELECT
      p.Login AS login,
      p.Symbol AS Symbol,
      SUM(p.Volume / 10000 / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS lots,
      SUM((p.Profit + p.Storage) / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS profit_floating
    FROM int_sass_crm_ac_mt5_live_new.mt5_positions p
    LEFT JOIN int_sass_crm_ac_mt5_live_new.mt5_users_view u ON u.Login = p.Login
    WHERE u.`Group` NOT LIKE '%%Test%%'
      AND u.`Group` NOT LIKE 'ACFIX%%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY p.Login, p.Symbol
    UNION ALL
    SELECT
      p.Login AS login,
      p.Symbol AS Symbol,
      SUM(p.Volume / 10000 / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS lots,
      SUM((p.Profit + p.Storage) / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS profit_floating
    FROM sass_crm_ac_mt5_live3.mt5_positions p
    LEFT JOIN sass_crm_ac_mt5_live3.mt5_users_view u ON u.Login = p.Login
    WHERE u.`Group` NOT LIKE '%%Test%%'
      AND u.`Group` NOT LIKE 'ACFIX%%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY p.Login, p.Symbol
    UNION ALL
    SELECT t.LOGIN AS login, t.SYMBOL AS Symbol,
           SUM(t.VOLUME) / 100 AS lots,
           SUM(t.PROFIT + t.SWAPS + t.COMMISSION) AS profit_floating
    FROM mt4_export_syc.mt4_trades t
    LEFT JOIN mt4_export_syc.mt4_users_view u ON u.LOGIN = t.LOGIN
    WHERE t.CLOSE_TIME = '1970-01-01 00:00:00'
      AND t.CMD IN (0, 1)
      AND (u.`GROUP` LIKE 'PXM-%%' OR u.`GROUP` LIKE 'TO-%%')
    GROUP BY t.LOGIN, t.SYMBOL
  ) floating_by_symbol
  GROUP BY login
) f ON f.login = ranked.login
ORDER BY profit_sum DESC;
"""


def run_query(days: int, top_n: int, trade_date: str | None = None) -> tuple[datetime, datetime, list[dict]]:
    start, end = trading_window(days, trade_date)
    start_text = start.strftime("%Y-%m-%d %H:%M:%S")
    end_text = end.strftime("%Y-%m-%d %H:%M:%S")
    with db_conn() as conn:
        with conn.cursor() as cur:
            window = (start_text, end_text)
            params = (
                window + window + window + window + (top_n,) +
                window + window + window + window +
                window + window + window + window
            )
            cur.execute(QUERY, params)
            rows = cur.fetchall()
    return start, end, rows


def html_response(handler: BaseHTTPRequestHandler, body: str, status: int = 200) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def csv_response(handler: BaseHTTPRequestHandler, rows: list[dict], filename: str) -> None:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in FIELDS})
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/csv; charset=utf-8")
    handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def csv_response_for(handler: BaseHTTPRequestHandler, rows: list[dict], filename: str, fields: list[str]) -> None:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fields})
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/csv; charset=utf-8")
    handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


MT5_TRADE_SELECT = """
SELECT
  %s AS source,
  d.Login AS login,
  u.Name AS name,
  u.`Group` AS group_name,
  d.Deal AS ticket,
  d.`Order` AS order_id,
  d.PositionID AS position_id,
  CASE d.Action WHEN 0 THEN 'buy' WHEN 1 THEN 'sell' ELSE CONCAT('action_', d.Action) END AS side,
  CASE d.Entry WHEN 0 THEN 'open' WHEN 1 THEN 'close' WHEN 2 THEN 'inout' WHEN 3 THEN 'out_by' ELSE CONCAT('entry_', d.Entry) END AS entry,
  d.Symbol AS symbol,
  ROUND(d.Volume / 10000 / CASE WHEN u.`Group` LIKE '%%%%Cent%%%%' THEN 100 ELSE 1 END, 4) AS volume,
  d.Time AS open_time,
  d.Time AS close_time,
  d.Price AS price_open,
  d.Price AS price_close,
  ROUND((d.Profit + d.Storage + d.Commission + d.Fee) / CASE WHEN u.`Group` LIKE '%%%%Cent%%%%' THEN 100 ELSE 1 END, 4) AS profit,
  ROUND(d.Storage / CASE WHEN u.`Group` LIKE '%%%%Cent%%%%' THEN 100 ELSE 1 END, 4) AS storage,
  ROUND(d.Commission / CASE WHEN u.`Group` LIKE '%%%%Cent%%%%' THEN 100 ELSE 1 END, 4) AS commission,
  ROUND(d.Fee / CASE WHEN u.`Group` LIKE '%%%%Cent%%%%' THEN 100 ELSE 1 END, 4) AS fee,
  d.Comment AS comment
FROM %s.mt5_deals d
LEFT JOIN %s.mt5_users_view u ON u.Login = d.Login
WHERE d.Login = %%s
  AND d.Time >= %%s
  AND d.Time <= %%s
  AND d.Action IN (0, 1)
"""


MT4_TRADE_SELECT = """
SELECT
  'mt4_export_syc' AS source,
  t.LOGIN AS login,
  u.NAME AS name,
  u.`GROUP` AS group_name,
  t.TICKET AS ticket,
  '' AS order_id,
  '' AS position_id,
  CASE t.CMD WHEN 0 THEN 'buy' WHEN 1 THEN 'sell' ELSE CONCAT('cmd_', t.CMD) END AS side,
  CASE WHEN t.CLOSE_TIME = '1970-01-01 00:00:00' THEN 'open' ELSE 'close' END AS entry,
  t.SYMBOL AS symbol,
  ROUND(t.VOLUME / 100, 4) AS volume,
  t.OPEN_TIME AS open_time,
  CASE WHEN t.CLOSE_TIME = '1970-01-01 00:00:00' THEN NULL ELSE t.CLOSE_TIME END AS close_time,
  t.OPEN_PRICE AS price_open,
  t.CLOSE_PRICE AS price_close,
  ROUND(t.PROFIT + t.SWAPS + t.COMMISSION, 4) AS profit,
  ROUND(t.SWAPS, 4) AS storage,
  ROUND(t.COMMISSION, 4) AS commission,
  0 AS fee,
  t.COMMENT AS comment
FROM mt4_export_syc.mt4_trades t
LEFT JOIN mt4_export_syc.mt4_users_view u ON u.LOGIN = t.LOGIN
WHERE t.LOGIN = %s
  AND t.CMD IN (0, 1)
  AND (
    (t.OPEN_TIME >= %s AND t.OPEN_TIME <= %s)
    OR (t.CLOSE_TIME >= %s AND t.CLOSE_TIME <= %s)
    OR t.CLOSE_TIME = '1970-01-01 00:00:00'
  )
"""


def run_trade_query(login: str, start_date: str, end_date: str, limit: int) -> list[dict]:
    login = login.strip()
    if not login.isdigit():
        raise ValueError("login must be numeric.")
    start_text = f"{start_date} 00:00:00"
    end_text = f"{end_date} 23:59:59"
    mt5_sources = [
        ("mt5_live", "sass_crm_ac_mt5_live"),
        ("int_mt5_live_new", "int_sass_crm_ac_mt5_live_new"),
        ("mt5_live3", "sass_crm_ac_mt5_live3"),
    ]
    rows: list[dict] = []
    with db_conn() as conn:
        with conn.cursor() as cur:
            for label, schema in mt5_sources:
                cur.execute(MT5_TRADE_SELECT % ("%s", schema, schema), (label, login, start_text, end_text))
                rows.extend(cur.fetchall())
            cur.execute(MT4_TRADE_SELECT, (login, start_text, end_text, start_text, end_text))
            rows.extend(cur.fetchall())
    rows.sort(key=lambda row: str(row.get("close_time") or row.get("open_time") or ""), reverse=True)
    return rows[:limit]


def render_trade_page(login: str, start_date: str, end_date: str, limit: int, rows: list[dict], error: str = "") -> str:
    query = urlencode({"login": login, "start": start_date, "end": end_date, "limit": limit})
    headers = "".join(f"<th>{html.escape(field)}</th>" for field in TRADE_FIELDS)
    table_rows = []
    for row in rows:
        cells = []
        for field in TRADE_FIELDS:
            value = row.get(field, "")
            if isinstance(value, datetime):
                value = value.strftime("%Y-%m-%d %H:%M:%S")
            cls = "num" if field in {"login", "ticket", "order_id", "position_id", "volume", "price_open", "price_close", "profit", "storage", "commission", "fee"} else ""
            cells.append(f"<td class='{cls}'>{html.escape(str(value or ''))}</td>")
        table_rows.append("<tr>" + "".join(cells) + "</tr>")
    body_rows = "\n".join(table_rows) or f"<tr><td colspan='{len(TRADE_FIELDS)}' class='empty'>请输入 login 查询</td></tr>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Login 交易记录查询</title>
<style>
body{{margin:0;background:#f4f6f8;color:#172033;font-family:Arial,"Microsoft YaHei",sans-serif}}
header{{background:#111827;color:white;padding:16px 22px}}
h1{{font-size:20px;margin:0}}
main{{padding:18px 22px 28px}}
.panel{{background:white;border:1px solid #d9e2ec;border-radius:8px;padding:14px;margin-bottom:14px}}
.form-row{{display:flex;gap:10px;align-items:end;flex-wrap:wrap}}
label{{display:grid;gap:5px;font-size:13px;color:#475569}}
input{{width:150px;border:1px solid #cbd5e1;border-radius:6px;padding:8px;font-size:14px}}
button,.btn{{border:1px solid #111827;background:#111827;color:white;border-radius:6px;padding:9px 13px;cursor:pointer;text-decoration:none;font-size:14px}}
.btn.secondary{{background:white;color:#111827;border-color:#cbd5e1}}
.hint{{color:#64748b;font-size:13px;line-height:1.55;margin-top:10px}}
.error{{color:#b91c1c;background:#fef2f2;border:1px solid #fecaca;padding:10px;border-radius:6px;margin-top:10px}}
.table-wrap{{height:calc(100vh - 250px);min-height:420px;overflow:auto;border:1px solid #d9e2ec;border-radius:8px;background:white}}
table{{border-collapse:separate;border-spacing:0;width:max-content;min-width:100%;font-size:12px}}
th,td{{border-right:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb;padding:7px 8px;white-space:nowrap;vertical-align:top}}
th{{position:sticky;top:0;background:#eef2f7;z-index:2;text-align:left}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.empty{{text-align:center;color:#64748b;padding:36px}}
</style>
</head>
<body>
<header><h1>Login 交易记录查询</h1></header>
<main>
  <section class="panel">
    <form class="form-row" method="get" action="/trades">
      <label>Login<input name="login" value="{html.escape(login)}" placeholder="例如 32087"></label>
      <label>开始日期<input name="start" type="date" value="{html.escape(start_date)}"></label>
      <label>结束日期<input name="end" type="date" value="{html.escape(end_date)}"></label>
      <label>最多行数<input name="limit" type="number" min="1" max="5000" value="{limit}"></label>
      <button type="submit">查询</button>
      <a class="btn secondary" href="/trades/download?{query}">下载 CSV</a>
      <a class="btn secondary" href="/">返回排名</a>
    </form>
    <div class="hint">自动查询 MT4、主 MT5、Int MT5、MT5 live3。默认按交易时间倒序显示。</div>
    {f"<div class='error'>{html.escape(error)}</div>" if error else ""}
  </section>
  <section class="panel">
    <div class="hint">结果：{len(rows)} 行</div>
    <div class="table-wrap"><table><thead><tr>{headers}</tr></thead><tbody>{body_rows}</tbody></table></div>
  </section>
</main>
</body>
</html>"""


def render_page(
    days: int,
    top_n: int,
    start: datetime | None,
    rows: list[dict] | None,
    error: str = "",
    end: datetime | None = None,
    trade_date: str = "",
) -> str:
    rows = rows or []
    query = urlencode({"days": days, "top": top_n, "date": trade_date} if trade_date else {"days": days, "top": top_n})
    table_rows = []
    for row in rows:
        cells = []
        for field in FIELDS:
            value = row.get(field, "")
            if isinstance(value, datetime):
                value = value.strftime("%Y-%m-%d %H:%M:%S")
            cls = "num" if field in {"balance", "volume_sum_open", "volume_sum_close", "vol_diff", "profit_sum", "volume_floating", "profit_floating"} else ""
            cells.append(f"<td class='{cls}'>{html.escape(str(value or ''))}</td>")
        table_rows.append("<tr>" + "".join(cells) + "</tr>")
    body_rows = "\n".join(table_rows) or f"<tr><td colspan='{len(FIELDS)}' class='empty'>暂无结果</td></tr>"
    headers = "".join(f"<th>{html.escape(field)}</th>" for field in FIELDS)
    start_text = start.strftime("%Y-%m-%d %H:%M:%S") if start else "-"
    end_text = end.strftime("%Y-%m-%d %H:%M:%S") if end else "-"
    rows_json = json.dumps(rows, ensure_ascii=False, default=str)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AC 盈利排行筛选</title>
<style>
body{{margin:0;background:#f4f6f8;color:#172033;font-family:Arial,"Microsoft YaHei",sans-serif}}
header{{background:#111827;color:white;padding:16px 22px}}
h1{{font-size:20px;margin:0}}
.meta{{color:#cbd5e1;font-size:13px;margin-top:6px}}
main{{padding:18px 22px 28px}}
.panel{{background:white;border:1px solid #d9e2ec;border-radius:8px;padding:14px;margin-bottom:14px}}
.form-row{{display:flex;gap:10px;align-items:end;flex-wrap:wrap}}
label{{display:grid;gap:5px;font-size:13px;color:#475569}}
input{{width:130px;border:1px solid #cbd5e1;border-radius:6px;padding:8px;font-size:14px}}
button,.btn{{border:1px solid #111827;background:#111827;color:white;border-radius:6px;padding:9px 13px;cursor:pointer;text-decoration:none;font-size:14px}}
.btn.secondary{{background:white;color:#111827;border-color:#cbd5e1}}
.hint{{color:#64748b;font-size:13px;line-height:1.55;margin-top:10px}}
.error{{color:#b91c1c;background:#fef2f2;border:1px solid #fecaca;padding:10px;border-radius:6px;margin-top:10px}}
.table-wrap{{height:calc(100vh - 250px);min-height:420px;overflow:auto;border:1px solid #d9e2ec;border-radius:8px;background:white}}
table{{border-collapse:separate;border-spacing:0;width:max-content;min-width:100%;font-size:12px}}
th,td{{border-right:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb;padding:7px 8px;white-space:nowrap;vertical-align:top}}
th{{position:sticky;top:0;background:#eef2f7;z-index:2;text-align:left}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
tr:nth-child(even) td{{background:#fafafa}}
.empty{{text-align:center;color:#64748b;padding:36px}}
</style>
</head>
<body>
<header>
  <h1>AC 盈利排行筛选</h1>
  <div class="meta">交易日按 21:00 开始计算，只读连接 AC 数据库。</div>
</header>
<main>
  <section class="panel">
    <form class="form-row" method="get" action="/">
      <label>最近 N 个交易日<input name="days" type="number" min="1" max="365" value="{days}"></label>
      <label>盈利前 N 名<input name="top" type="number" min="1" max="{MAX_LIMIT}" value="{top_n}"></label>
      <button type="submit">筛选</button>
      <a class="btn secondary" href="/download?{query}">下载 CSV</a>
    </form>
    <div class="hint">当前查询起点：<b>{html.escape(start_text)}</b>。导出字段顺序：{html.escape(", ".join(FIELDS))}</div>
    {f"<div class='error'>{html.escape(error)}</div>" if error else ""}
  </section>
  <section class="panel">
    <div class="hint">结果：{len(rows)} 行</div>
    <div class="table-wrap">
      <table>
        <thead><tr>{headers}</tr></thead>
        <tbody>{body_rows}</tbody>
      </table>
    </div>
  </section>
</main>
<script>window.__ROWS__ = {rows_json};</script>
</body>
</html>"""


BASE_STYLE = """
body{margin:0;background:#f4f6f8;color:#172033;font-family:Arial,"Microsoft YaHei",sans-serif}
header{background:#111827;color:#fff;padding:14px 22px 0}
h1{font-size:20px;margin:0 0 12px}
.tabs{display:flex;gap:4px;border-bottom:1px solid #334155}
.tab{display:inline-flex;align-items:center;min-height:40px;padding:0 16px;color:#cbd5e1;text-decoration:none;border:1px solid transparent;border-bottom:0;border-radius:8px 8px 0 0;font-size:14px}
.tab.active{background:#f4f6f8;color:#111827;border-color:#334155}
main{padding:18px 22px 28px}
.panel{background:white;border:1px solid #d9e2ec;border-radius:8px;padding:14px;margin-bottom:14px}
.form-row{display:flex;gap:10px;align-items:end;flex-wrap:wrap}
label{display:grid;gap:5px;font-size:13px;color:#475569}
input{width:150px;border:1px solid #cbd5e1;border-radius:6px;padding:8px;font-size:14px;background:#fff}
button,.btn{border:1px solid #111827;background:#111827;color:white;border-radius:6px;padding:9px 13px;cursor:pointer;text-decoration:none;font-size:14px}
.btn.secondary{background:white;color:#111827;border-color:#cbd5e1}
.hint{color:#64748b;font-size:13px;line-height:1.55;margin-top:10px}
.error{color:#b91c1c;background:#fef2f2;border:1px solid #fecaca;padding:10px;border-radius:6px;margin-top:10px}
.table-wrap{height:calc(100vh - 250px);min-height:420px;overflow:auto;border:1px solid #d9e2ec;border-radius:8px;background:white}
table{border-collapse:separate;border-spacing:0;width:max-content;min-width:100%;font-size:12px}
th,td{border-right:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb;padding:7px 8px;white-space:nowrap;vertical-align:top}
th{position:sticky;top:0;background:#eef2f7;z-index:2;text-align:left}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tr:nth-child(even) td{background:#fafafa}
.empty{text-align:center;color:#64748b;padding:36px}
.split{height:1px;background:#e5e7eb;margin:14px 0}
.batch-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px}
.loading{color:#0f766e;font-size:13px}
.page-loading{display:none;position:fixed;right:18px;bottom:18px;background:#111827;color:white;border-radius:8px;padding:10px 12px;font-size:13px;box-shadow:0 8px 24px rgba(15,23,42,.22);z-index:20}
.account-card{border:1px solid #d9e2ec;border-radius:8px;margin:10px 0;background:#fff}
.account-card summary{cursor:pointer;padding:10px 12px;font-weight:700;background:#f8fafc;border-radius:8px}
.account-card[open] summary{border-bottom:1px solid #e5e7eb;border-radius:8px 8px 0 0}
.account-body{padding:10px 12px}
.mini-table-wrap{max-height:340px;overflow:auto;border:1px solid #e5e7eb;border-radius:6px}
@media (max-width:760px){header,main{padding-left:14px;padding-right:14px}.tabs{overflow:auto}.tab{white-space:nowrap}input{width:140px}}
"""


def render_table(fields: list[str], rows: list[dict], numeric_fields: set[str], empty_text: str) -> str:
    headers = "".join(f"<th>{html.escape(field)}</th>" for field in fields)
    table_rows = []
    for row in rows:
        cells = []
        for field in fields:
            value = row.get(field, "")
            if isinstance(value, datetime):
                value = value.strftime("%Y-%m-%d %H:%M:%S")
            cls = "num" if field in numeric_fields else ""
            cells.append(f"<td class='{cls}'>{html.escape(str(value or ''))}</td>")
        table_rows.append("<tr>" + "".join(cells) + "</tr>")
    body = "\n".join(table_rows) or f"<tr><td colspan='{len(fields)}' class='empty'>{html.escape(empty_text)}</td></tr>"
    return f"<div class='table-wrap'><table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table></div>"


def render_rank_page_ui(days: int, top_n: int, start: datetime | None, rows: list[dict] | None, error: str = "", end: datetime | None = None, trade_date: str = "") -> str:
    rows = rows or []
    trade_date = trade_date or previous_trading_date()
    query = urlencode({"days": days, "top": top_n, "date": trade_date} if trade_date else {"days": days, "top": top_n})
    start_text = start.strftime("%Y-%m-%d %H:%M:%S") if start else "-"
    end_text = end.strftime("%Y-%m-%d %H:%M:%S") if end else "-"
    controls = f"""
    <form class="form-row" method="get" action="/">
      <label>最近 N 个交易日<input name="days" type="number" min="1" max="365" value="{days}"></label>
      <label>盈利前 N 名<input name="top" type="number" min="1" max="{MAX_LIMIT}" value="{top_n}"></label>
      <label>交易日日期<input name="date" type="date" value="{html.escape(trade_date)}"></label>
      <button type="submit">筛选</button>
      <a class="btn secondary" href="/download?{query}">下载 CSV</a>
    </form>"""
    table = render_table(
        FIELDS,
        rows,
        {"balance", "volume_sum_open", "volume_sum_close", "vol_diff", "profit_sum", "volume_floating", "profit_floating"},
        "暂无结果",
    )
    hint = f"查询区间：<b>{html.escape(start_text)}</b> 到 <b>{html.escape(end_text)}</b>。页面打开和 tab 切换不会查库；点击筛选后才会查询。"
    return render_shell("rank", "盈利排名", controls, table, len(rows), hint, error)


def render_shell(active: str, title: str, controls: str, table_html: str, count: int, hint: str, error: str = "") -> str:
    rank_active = " active" if active == "rank" else ""
    trades_active = " active" if active == "trades" else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{BASE_STYLE}</style>
</head>
<body>
<header>
  <h1>AC 数据分析工具</h1>
  <nav class="tabs">
    <a class="tab{rank_active}" href="/">盈利排名</a>
    <a class="tab{trades_active}" href="/trades">交易记录</a>
  </nav>
</header>
<main>
  <section class="panel">
    {controls}
    <div class="hint">{hint}</div>
    {f"<div class='error'>{html.escape(error)}</div>" if error else ""}
  </section>
  <section class="panel">
    <div class="hint">结果：{count} 行</div>
    {table_html}
  </section>
</main>
<div id="pageLoading" class="page-loading">查询中，请稍等...</div>
<script>
document.addEventListener('submit', event => {{
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  const loading = document.getElementById('pageLoading');
  if (loading) loading.style.display = 'block';
  const button = form.querySelector('button[type="submit"]');
  if (button) {{
    button.disabled = true;
    button.textContent = '查询中...';
  }}
}});
</script>
</body>
</html>"""


def render_rank_page_ui(days: int, top_n: int, start: datetime | None, rows: list[dict] | None, error: str = "", end: datetime | None = None, trade_date: str = "") -> str:
    rows = rows or []
    query = urlencode({"days": days, "top": top_n, "date": trade_date} if trade_date else {"days": days, "top": top_n})
    start_text = start.strftime("%Y-%m-%d %H:%M:%S") if start else "-"
    end_text = end.strftime("%Y-%m-%d %H:%M:%S") if end else "-"
    controls = f"""
    <form class="form-row" method="get" action="/">
      <label>最近 N 个交易日<input name="days" type="number" min="1" max="365" value="{days}"></label>
      <label>盈利前 N 名<input name="top" type="number" min="1" max="{MAX_LIMIT}" value="{top_n}"></label>
      <label>交易日日期<input name="date" type="date" value="{html.escape(trade_date)}"></label>
      <button type="submit">筛选</button>
      <a class="btn secondary" href="/download?{query}">下载 CSV</a>
    </form>"""
    table = render_table(
        FIELDS,
        rows,
        {"balance", "volume_sum_open", "volume_sum_close", "vol_diff", "profit_sum", "volume_floating", "profit_floating"},
        "暂无结果",
    )
    hint = f"查询区间：<b>{html.escape(start_text)}</b> 到 <b>{html.escape(end_text)}</b>。日期为空时按最近 N 个交易日；填 2026-07-09 表示 7.8 21:00 到 7.9 20:59:59。"
    return render_shell("rank", "盈利排名", controls, table, len(rows), hint, error)


def render_trade_page_ui(login: str, start_date: str, end_date: str, limit: int, rows: list[dict], error: str = "") -> str:
    query = urlencode({"login": login, "start": start_date, "end": end_date, "limit": limit})
    default_rank_date = previous_trading_date()
    controls = f"""
    <form class="form-row" method="get" action="/trades">
      <label>Login<input name="login" value="{html.escape(login)}" placeholder="例如 32087"></label>
      <label>开始日期<input name="start" type="date" value="{html.escape(start_date)}"></label>
      <label>结束日期<input name="end" type="date" value="{html.escape(end_date)}"></label>
      <label>最多行数<input name="limit" type="number" min="1" max="5000" value="{limit}"></label>
      <button type="submit">查询</button>
      <a class="btn secondary" href="/trades/download?{query}">下载 CSV</a>
    </form>"""
    table = render_table(
        TRADE_FIELDS,
        rows,
        {"login", "ticket", "order_id", "position_id", "volume", "price_open", "price_close", "profit", "storage", "commission", "fee"},
        "请输入 login 查询",
    )
    hint = "自动查询 MT4、主 MT5、Int MT5、MT5 live3。按交易时间倒序显示，适合快速回看指定账号历史交易。"
    return render_shell("trades", "交易记录", controls, table, len(rows), hint, error)


def render_shell(active: str, title: str, controls: str, table_html: str, count: int, hint: str, error: str = "") -> str:
    rank_active = " active" if active == "rank" else ""
    trades_active = " active" if active == "trades" else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{BASE_STYLE}</style>
</head>
<body>
<header>
  <h1>AC 数据分析工具</h1>
  <nav class="tabs">
    <a class="tab{rank_active}" href="/">盈利排名</a>
    <a class="tab{trades_active}" href="/trades">交易记录</a>
  </nav>
</header>
<main>
  <section class="panel">
    {controls}
    <div class="hint">{hint}</div>
    {f"<div class='error'>{html.escape(error)}</div>" if error else ""}
  </section>
  <section class="panel">
    <div class="hint">结果：{count} 行</div>
    {table_html}
  </section>
</main>
</body>
</html>"""


def render_rank_page_ui(days: int, top_n: int, start: datetime | None, rows: list[dict] | None, error: str = "", end: datetime | None = None, trade_date: str = "") -> str:
    rows = rows or []
    query = urlencode({"days": days, "top": top_n, "date": trade_date} if trade_date else {"days": days, "top": top_n})
    start_text = start.strftime("%Y-%m-%d %H:%M:%S") if start else "-"
    end_text = end.strftime("%Y-%m-%d %H:%M:%S") if end else "-"
    controls = f"""
    <form class="form-row" method="get" action="/">
      <label>最近 N 个交易日<input name="days" type="number" min="1" max="365" value="{days}"></label>
      <label>盈利前 N 名<input name="top" type="number" min="1" max="{MAX_LIMIT}" value="{top_n}"></label>
      <label>交易日日期<input name="date" type="date" value="{html.escape(trade_date)}"></label>
      <button type="submit">筛选</button>
      <a class="btn secondary" href="/download?{query}">下载 CSV</a>
    </form>"""
    table = render_table(
        FIELDS,
        rows,
        {"balance", "volume_sum_open", "volume_sum_close", "vol_diff", "profit_sum", "volume_floating", "profit_floating"},
        "暂无结果",
    )
    hint = f"查询区间：<b>{html.escape(start_text)}</b> 到 <b>{html.escape(end_text)}</b>。日期为空时先不查库；填 2026-07-09 表示 7.8 21:00 到 7.9 20:59:59。"
    return render_shell("rank", "盈利排名", controls, table, len(rows), hint, error)


def render_trade_page_ui(login: str, start_date: str, end_date: str, limit: int, rows: list[dict], error: str = "") -> str:
    query = urlencode({"login": login, "start": start_date, "end": end_date, "limit": limit})
    controls = f"""
    <form class="form-row" method="get" action="/trades">
      <label>Login<input name="login" value="{html.escape(login)}" placeholder="例如 32087"></label>
      <label>开始日期<input name="start" type="date" value="{html.escape(start_date)}"></label>
      <label>结束日期<input name="end" type="date" value="{html.escape(end_date)}"></label>
      <label>最多行数<input name="limit" type="number" min="1" max="5000" value="{limit}"></label>
      <button type="submit">查询</button>
      <a class="btn secondary" href="/trades/download?{query}">下载 CSV</a>
    </form>"""
    table = render_table(
        TRADE_FIELDS,
        rows,
        {"login", "ticket", "order_id", "position_id", "volume", "price_open", "price_close", "profit", "storage", "commission", "fee"},
        "请输入 login 查询",
    )
    hint = "自动查询 MT4、主 MT5、Int MT5、MT5 live3。按交易时间倒序显示，适合快速回看指定账号历史交易。"
    return render_shell("trades", "交易记录", controls, table, len(rows), hint, error)


def render_trade_page_ui(login: str, start_date: str, end_date: str, limit: int, rows: list[dict], error: str = "") -> str:
    query = urlencode({"login": login, "start": start_date, "end": end_date, "limit": limit})
    controls = f"""
    <form class="form-row" method="get" action="/trades">
      <label>Login<input name="login" value="{html.escape(login)}" placeholder="例如 32087"></label>
      <label>开始日期<input name="start" type="date" value="{html.escape(start_date)}"></label>
      <label>结束日期<input name="end" type="date" value="{html.escape(end_date)}"></label>
      <label>最多行数<input name="limit" type="number" min="1" max="5000" value="{limit}"></label>
      <button type="submit">查询单账号</button>
      <a class="btn secondary" href="/trades/download?{query}">下载 CSV</a>
    </form>
    <div class="split"></div>
    <div class="form-row">
      <label>排名交易日<input id="batchDate" type="date" value="{html.escape(default_rank_date)}"></label>
      <label>最近 N 个交易日<input id="batchDays" type="number" min="1" max="365" value="1"></label>
      <label>交易开始<input id="batchStart" type="date" value="{html.escape(start_date)}"></label>
      <label>交易结束<input id="batchEnd" type="date" value="{html.escape(end_date)}"></label>
      <label>每账号最多交易<input id="batchLimit" type="number" min="1" max="1000" value="100"></label>
    </div>
    <div class="batch-actions">
      <button id="batchStartBtn" type="button">一键分析前100盈利账户</button>
      <button id="batchNextBtn" type="button" class="btn secondary" disabled>下一批 25 个</button>
      <span id="batchStatus" class="loading"></span>
    </div>"""
    single_table = render_table(
        TRADE_FIELDS,
        rows,
        {"login", "ticket", "order_id", "position_id", "volume", "price_open", "price_close", "profit", "storage", "commission", "fee"},
        "请输入 login 查询，或使用上方一键分析批量查看。",
    )
    batch_html = """
    <div id="batchResults"></div>
    <script>
    const tradeFields = %s;
    let batchOffset = 0;
    let batchBusy = false;
    const batchStartBtn = document.getElementById('batchStartBtn');
    const batchNextBtn = document.getElementById('batchNextBtn');
    const batchStatus = document.getElementById('batchStatus');
    const batchResults = document.getElementById('batchResults');
    function htmlEscape(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    function renderMiniTable(rows) {
      if (!rows.length) return '<div class="empty">这个账号在所选日期内暂无交易</div>';
      const head = tradeFields.map(field => `<th>${htmlEscape(field)}</th>`).join('');
      const body = rows.map(row => `<tr>${tradeFields.map(field => `<td>${htmlEscape(row[field])}</td>`).join('')}</tr>`).join('');
      return `<div class="mini-table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    }
    function appendAccounts(accounts) {
      const html = accounts.map(account => `
        <details class="account-card">
          <summary>序号 ${account.seq} ｜ login ${htmlEscape(account.login)} ｜ ${htmlEscape(account.name)} ｜ 盈利 ${htmlEscape(account.profit_sum)} ｜ 交易 ${account.trade_count} 条</summary>
          <div class="account-body">${renderMiniTable(account.trades || [])}</div>
        </details>
      `).join('');
      batchResults.insertAdjacentHTML('beforeend', html);
    }
    async function loadBatch(reset) {
      if (batchBusy) return;
      batchBusy = true;
      if (reset) {
        batchOffset = 0;
        batchResults.innerHTML = '';
      }
      batchStartBtn.disabled = true;
      batchNextBtn.disabled = true;
      batchStatus.textContent = `加载中：第 ${batchOffset + 1} 到 ${batchOffset + 25} 个账号...`;
      const query = new URLSearchParams({
        offset: String(batchOffset),
        batch: '25',
        top: '100',
        days: document.getElementById('batchDays').value || '1',
        date: document.getElementById('batchDate').value || '',
        start: document.getElementById('batchStart').value,
        end: document.getElementById('batchEnd').value,
        limit: document.getElementById('batchLimit').value || '100'
      });
      try {
        const response = await fetch(`/api/analyze-batch?${query.toString()}`, {cache: 'no-store'});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || '批量分析失败');
        appendAccounts(payload.accounts || []);
        batchOffset = payload.next_offset || batchOffset + 25;
        batchNextBtn.disabled = !payload.has_next;
        batchStatus.textContent = payload.has_next ? `已加载到第 ${batchOffset} 个，可继续下一批。` : '前100账号已加载完。';
      } catch (error) {
        batchStatus.textContent = error instanceof Error ? error.message : '批量分析失败';
      } finally {
        batchBusy = false;
        batchStartBtn.disabled = false;
      }
    }
    batchStartBtn.addEventListener('click', () => loadBatch(true));
    batchNextBtn.addEventListener('click', () => loadBatch(false));
    </script>
    """ % json.dumps(TRADE_FIELDS, ensure_ascii=False)
    hint = "切换 tab 不会查数据库。单账号查询只查输入的 login；一键分析会按盈利排名前100的顺序，每25个账号一批加载，并可逐个折叠查看。"
    return render_shell("trades", "交易记录", controls, single_table + batch_html, len(rows), hint, error)


def render_shell(active: str, title: str, controls: str, table_html: str, count: int, hint: str, error: str = "") -> str:
    rank_active = " active" if active == "rank" else ""
    trades_active = " active" if active == "trades" else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{BASE_STYLE}</style>
</head>
<body>
<header>
  <h1>AC 数据分析工具</h1>
  <nav class="tabs">
    <a class="tab{rank_active}" href="/">盈利排名</a>
    <a class="tab{trades_active}" href="/trades">交易记录</a>
  </nav>
</header>
<main>
  <section class="panel">
    {controls}
    <div class="hint">{hint}</div>
    {f"<div class='error'>{html.escape(error)}</div>" if error else ""}
  </section>
  <section class="panel">
    <div class="hint">结果：{count} 行</div>
    {table_html}
  </section>
</main>
<div id="pageLoading" class="page-loading">查询中，请稍等...</div>
<script>
document.addEventListener('submit', event => {{
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  const loading = document.getElementById('pageLoading');
  if (loading) loading.style.display = 'block';
  const button = form.querySelector('button[type="submit"]');
  if (button) {{
    button.disabled = true;
    button.textContent = '查询中...';
  }}
}});
</script>
</body>
</html>"""


def render_shell(active: str, title: str, controls: str, table_html: str, count: int, hint: str, error: str = "") -> str:
    rank_active = " active" if active == "rank" else ""
    trades_active = " active" if active == "trades" else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{BASE_STYLE}</style>
</head>
<body>
<header>
  <h1>AC 数据分析工具</h1>
  <nav class="tabs">
    <a class="tab{rank_active}" href="/">盈利排名</a>
    <a class="tab{trades_active}" href="/trades">交易记录</a>
  </nav>
</header>
<main>
  <section class="panel">
    {controls}
    <div class="hint">{hint}</div>
    {f"<div class='error'>{html.escape(error)}</div>" if error else ""}
  </section>
  <section class="panel">
    <div class="hint">结果：{count} 行</div>
    {table_html}
  </section>
</main>
<div id="pageLoading" class="page-loading">查询中，请稍等...</div>
<script>
document.addEventListener('submit', event => {{
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  const loading = document.getElementById('pageLoading');
  if (loading) loading.style.display = 'block';
  const button = form.querySelector('button[type="submit"]');
  if (button) {{
    button.disabled = true;
    button.textContent = '查询中...';
  }}
}});
</script>
</body>
</html>"""


def render_rank_page_ui(days: int, top_n: int, start: datetime | None, rows: list[dict] | None, error: str = "", end: datetime | None = None, trade_date: str = "") -> str:
    rows = rows or []
    trade_date = trade_date or previous_trading_date()
    query = urlencode({"days": days, "top": top_n, "date": trade_date})
    start_text = start.strftime("%Y-%m-%d %H:%M:%S") if start else "-"
    end_text = end.strftime("%Y-%m-%d %H:%M:%S") if end else "-"
    controls = f"""
    <form class="form-row" method="get" action="/">
      <label>最近 N 个交易日<input name="days" type="number" min="1" max="365" value="{days}"></label>
      <label>盈利前 N 名<input name="top" type="number" min="1" max="{MAX_LIMIT}" value="{top_n}"></label>
      <label>交易日日期<input name="date" type="date" value="{html.escape(trade_date)}"></label>
      <button type="submit">筛选</button>
      <a class="btn secondary" href="/download?{query}">下载 CSV</a>
    </form>"""
    table = render_table(
        FIELDS,
        rows,
        {"balance", "volume_sum_open", "volume_sum_close", "vol_diff", "profit_sum", "volume_floating", "profit_floating"},
        "点击筛选后显示结果",
    )
    hint = f"默认选择前一天、前100名。交易日按 21:00 开盘计算，例如 2026-07-09 表示 7.8 21:00 到 7.9 收盘。当前查询区间：<b>{html.escape(start_text)}</b> 到 <b>{html.escape(end_text)}</b>。"
    return render_shell("rank", "盈利排名", controls, table, len(rows), hint, error)


def render_trade_page_ui(login: str, start_date: str, end_date: str, limit: int, rows: list[dict], error: str = "") -> str:
    query = urlencode({"login": login, "start": start_date, "end": end_date, "limit": limit})
    default_rank_date = previous_trading_date()
    controls = f"""
    <form class="form-row" method="get" action="/trades">
      <label>Login<input name="login" value="{html.escape(login)}" placeholder="例如 32087"></label>
      <label>开始日期<input name="start" type="date" value="{html.escape(start_date)}"></label>
      <label>结束日期<input name="end" type="date" value="{html.escape(end_date)}"></label>
      <label>最多行数<input name="limit" type="number" min="1" max="5000" value="{limit}"></label>
      <button type="submit">查询单账号</button>
      <a class="btn secondary" href="/trades/download?{query}">下载 CSV</a>
    </form>
    <div class="split"></div>
    <div class="form-row">
      <label>排名交易日<input id="batchDate" type="date" value="{html.escape(default_rank_date)}"></label>
      <label>最近 N 个交易日<input id="batchDays" type="number" min="1" max="365" value="1"></label>
      <label>交易开始<input id="batchStart" type="date" value="{html.escape(start_date)}"></label>
      <label>交易结束<input id="batchEnd" type="date" value="{html.escape(end_date)}"></label>
      <label>每账号最多交易<input id="batchLimit" type="number" min="1" max="1000" value="100"></label>
    </div>
    <div class="batch-actions">
      <button id="batchStartBtn" type="button">一键分析前100盈利账户</button>
      <button id="batchNextBtn" type="button" class="btn secondary" disabled>下一批 25 个</button>
      <span id="batchStatus" class="loading"></span>
    </div>"""
    single_table = render_table(
        TRADE_FIELDS,
        rows,
        {"login", "ticket", "order_id", "position_id", "volume", "price_open", "price_close", "profit", "storage", "commission", "fee"},
        "请输入 login 查询，或使用上方一键分析批量查看。",
    )
    batch_html = """
    <div id="batchResults"></div>
    <script>
    const tradeFields = %s;
    let batchOffset = 0;
    let batchBusy = false;
    const batchStartBtn = document.getElementById('batchStartBtn');
    const batchNextBtn = document.getElementById('batchNextBtn');
    const batchStatus = document.getElementById('batchStatus');
    const batchResults = document.getElementById('batchResults');
    function htmlEscape(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    function renderMiniTable(rows) {
      if (!rows.length) return '<div class="empty">这个账号在所选日期内暂无交易</div>';
      const head = tradeFields.map(field => `<th>${htmlEscape(field)}</th>`).join('');
      const body = rows.map(row => `<tr>${tradeFields.map(field => `<td>${htmlEscape(row[field])}</td>`).join('')}</tr>`).join('');
      return `<div class="mini-table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    }
    function appendAccounts(accounts) {
      const html = accounts.map(account => `
        <details class="account-card">
          <summary>序号 ${account.seq} - login ${htmlEscape(account.login)} - ${htmlEscape(account.name)} - 盈利 ${htmlEscape(account.profit_sum)} - 交易 ${account.trade_count} 条</summary>
          <div class="account-body">${renderMiniTable(account.trades || [])}</div>
        </details>
      `).join('');
      batchResults.insertAdjacentHTML('beforeend', html);
    }
    async function loadBatch(reset) {
      if (batchBusy) return;
      batchBusy = true;
      if (reset) {
        batchOffset = 0;
        batchResults.innerHTML = '';
      }
      batchStartBtn.disabled = true;
      batchNextBtn.disabled = true;
      batchStatus.textContent = `加载中：第 ${batchOffset + 1} 到 ${batchOffset + 25} 个账号...`;
      const query = new URLSearchParams({
        offset: String(batchOffset),
        batch: '25',
        top: '100',
        days: document.getElementById('batchDays').value || '1',
        date: document.getElementById('batchDate').value || '',
        start: document.getElementById('batchStart').value,
        end: document.getElementById('batchEnd').value,
        limit: document.getElementById('batchLimit').value || '100'
      });
      try {
        const response = await fetch(`/api/analyze-batch?${query.toString()}`, {cache: 'no-store'});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || '批量分析失败');
        appendAccounts(payload.accounts || []);
        batchOffset = payload.next_offset || batchOffset + 25;
        batchNextBtn.disabled = !payload.has_next;
        batchStatus.textContent = payload.has_next ? `已加载到第 ${batchOffset} 个，可继续下一批。` : '前100账号已加载完。';
      } catch (error) {
        batchStatus.textContent = error instanceof Error ? error.message : '批量分析失败';
      } finally {
        batchBusy = false;
        batchStartBtn.disabled = false;
      }
    }
    batchStartBtn.addEventListener('click', () => loadBatch(true));
    batchNextBtn.addEventListener('click', () => loadBatch(false));
    </script>
    """ % json.dumps(TRADE_FIELDS, ensure_ascii=False)
    hint = "切换 tab 不会查数据库。单账号查询只查输入的 login；一键分析会自动按前一天前100盈利账户顺序，每25个账号一批加载，并且每个账号都可以折叠查看。"
    return render_shell("trades", "交易记录", controls, single_table + batch_html, len(rows), hint, error)


def render_shell(active: str, title: str, controls: str, table_html: str, count: int, hint: str, error: str = "") -> str:
    rank_active = " active" if active == "rank" else ""
    trades_active = " active" if active == "trades" else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{BASE_STYLE}</style>
</head>
<body>
<header>
  <h1>AC 数据分析工具</h1>
  <nav class="tabs">
    <a id="rankTabLink" class="tab{rank_active}" href="/">盈利排名</a>
    <a class="tab{trades_active}" href="/trades">交易记录</a>
  </nav>
</header>
<main>
  <section class="panel">
    {controls}
    <div class="hint">{hint}</div>
    {f"<div class='error'>{html.escape(error)}</div>" if error else ""}
  </section>
  <section class="panel">
    <div class="hint">结果：{count} 行</div>
    {table_html}
  </section>
</main>
<div id="pageLoading" class="page-loading">查询中，请稍等...</div>
<script>
const rankTabLink = document.getElementById('rankTabLink');
const currentUrl = new URL(window.location.href);
if (currentUrl.pathname === '/' && currentUrl.search) {{
  localStorage.setItem('acProfitRankLastUrl', currentUrl.pathname + currentUrl.search);
}}
if (rankTabLink) {{
  const savedRankUrl = localStorage.getItem('acProfitRankLastUrl');
  if (savedRankUrl) rankTabLink.href = savedRankUrl;
}}
document.addEventListener('submit', event => {{
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  const loading = document.getElementById('pageLoading');
  if (loading) loading.style.display = 'block';
  const button = form.querySelector('button[type="submit"]');
  if (button) {{
    button.disabled = true;
    button.textContent = '查询中...';
  }}
}});
</script>
</body>
</html>"""


def render_trade_page_ui(login: str, start_date: str, end_date: str, limit: int, rows: list[dict], error: str = "") -> str:
    query = urlencode({"login": login, "start": start_date, "end": end_date, "limit": limit})
    default_rank_date = previous_trading_date()
    controls = f"""
    <form class="form-row" method="get" action="/trades">
      <label>Login<input name="login" value="{html.escape(login)}" placeholder="例如 32087"></label>
      <label>开始日期<input name="start" type="date" value="{html.escape(start_date)}"></label>
      <label>结束日期<input name="end" type="date" value="{html.escape(end_date)}"></label>
      <label>最多行数<input name="limit" type="number" min="1" max="5000" value="{limit}"></label>
      <button type="submit">查询单账号</button>
      <a class="btn secondary" href="/trades/download?{query}">下载 CSV</a>
    </form>
    <div class="split"></div>
    <div class="form-row">
      <label>排名交易日<input id="batchDate" type="date" value="{html.escape(default_rank_date)}"></label>
      <label>最近 N 个交易日<input id="batchDays" type="number" min="1" max="365" value="1"></label>
      <label>交易开始<input id="batchStart" type="date" value="{html.escape(start_date)}"></label>
      <label>交易结束<input id="batchEnd" type="date" value="{html.escape(end_date)}"></label>
      <label>每账号最多交易<input id="batchLimit" type="number" min="1" max="1000" value="100"></label>
    </div>
    <div class="batch-actions">
      <button id="batchStartBtn" type="button">一键分析前100盈利账户</button>
      <button id="batchNextBtn" type="button" class="btn secondary" disabled>下一批 25 个</button>
      <span id="batchStatus" class="loading"></span>
    </div>"""
    single_table = render_table(
        TRADE_FIELDS,
        rows,
        {"login", "ticket", "order_id", "position_id", "volume", "price_open", "price_close", "profit", "storage", "commission", "fee"},
        "请输入 login 查询，或使用上方一键分析批量查看。",
    )
    batch_html = """
    <div id="batchResults"></div>
    <script>
    const tradeFields = %s;
    let batchOffset = 0;
    let batchBusy = false;
    const batchStartBtn = document.getElementById('batchStartBtn');
    const batchNextBtn = document.getElementById('batchNextBtn');
    const batchStatus = document.getElementById('batchStatus');
    const batchResults = document.getElementById('batchResults');
    function htmlEscape(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    function renderMiniTable(rows) {
      if (!rows.length) return '<div class="empty">这个账号在所选日期内暂无交易</div>';
      const head = tradeFields.map(field => `<th>${htmlEscape(field)}</th>`).join('');
      const body = rows.map(row => `<tr>${tradeFields.map(field => `<td>${htmlEscape(row[field])}</td>`).join('')}</tr>`).join('');
      return `<div class="mini-table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    }
    function appendAccount(account) {
      const html = `
        <details class="account-card">
          <summary>序号 ${account.seq} - login ${htmlEscape(account.login)} - ${htmlEscape(account.name)} - 盈利 ${htmlEscape(account.profit_sum)} - 交易 ${account.trade_count} 条</summary>
          <div class="account-body">${renderMiniTable(account.trades || [])}</div>
        </details>`;
      batchResults.insertAdjacentHTML('beforeend', html);
      const latest = batchResults.lastElementChild;
      if (latest) latest.scrollIntoView({block: 'nearest'});
    }
    function setBatchButtons(done, hasNext) {
      batchBusy = false;
      batchStartBtn.disabled = false;
      batchNextBtn.disabled = !hasNext;
      if (done && !hasNext) batchNextBtn.disabled = true;
    }
    async function loadBatch(reset) {
      if (batchBusy) return;
      batchBusy = true;
      if (reset) {
        batchOffset = 0;
        batchResults.innerHTML = '';
      }
      batchStartBtn.disabled = true;
      batchNextBtn.disabled = true;
      batchStatus.textContent = `准备排名：第 ${batchOffset + 1} 到 ${batchOffset + 25} 个账号...`;
      const query = new URLSearchParams({
        offset: String(batchOffset),
        batch: '25',
        top: '100',
        days: document.getElementById('batchDays').value || '1',
        date: document.getElementById('batchDate').value || '',
        start: document.getElementById('batchStart').value,
        end: document.getElementById('batchEnd').value,
        limit: document.getElementById('batchLimit').value || '100'
      });
      let processed = 0;
      try {
        const response = await fetch(`/api/analyze-stream?${query.toString()}`, {cache: 'no-store'});
        if (!response.ok || !response.body) throw new Error('批量分析启动失败');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const {value, done} = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, {stream: true});
          const lines = buffer.split('\\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (!line.trim()) continue;
            const event = JSON.parse(line);
            if (event.type === 'status') {
              batchStatus.textContent = event.message || '处理中...';
            } else if (event.type === 'account') {
              processed += 1;
              appendAccount(event.account);
              batchStatus.textContent = `已输出 ${processed}/25 个，本批正在继续...`;
            } else if (event.type === 'done') {
              batchOffset = event.next_offset || batchOffset + processed;
              batchStatus.textContent = event.has_next ? `本批完成，已到第 ${batchOffset} 个，可继续下一批。` : '前100账号已加载完。';
              setBatchButtons(true, Boolean(event.has_next));
            } else if (event.type === 'error') {
              throw new Error(event.message || '批量分析失败');
            }
          }
        }
      } catch (error) {
        batchStatus.textContent = error instanceof Error ? error.message : '批量分析失败';
        setBatchButtons(false, false);
      } finally {
        if (batchBusy) setBatchButtons(false, processed > 0);
      }
    }
    batchStartBtn.addEventListener('click', () => loadBatch(true));
    batchNextBtn.addEventListener('click', () => loadBatch(false));
    </script>
    """ % json.dumps(TRADE_FIELDS, ensure_ascii=False)
    hint = "一键分析现在会流式输出：先取前100盈利账户排名，然后每个账号交易记录查完就立刻显示，不用等整批25个全部结束。"
    return render_shell("trades", "交易记录", controls, batch_html + single_table, len(rows), hint, error)


MT5_SOURCES = [
    ("mt5_live", "sass_crm_ac_mt5_live"),
    ("int_mt5_live_new", "int_sass_crm_ac_mt5_live_new"),
    ("mt5_live3", "sass_crm_ac_mt5_live3"),
]


def previous_trading_date() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def as_decimal(value) -> Decimal:
    return Decimal("0") if value is None else Decimal(str(value))


def placeholders(values: list[str]) -> str:
    return ",".join(["%s"] * len(values))


def daily_cutoff_epoch(date_text: str) -> int:
    cutoff = datetime.strptime(f"{date_text} 20:59:59", "%Y-%m-%d %H:%M:%S")
    return calendar.timegm(cutoff.timetuple())


def optimized_rank_rows_from_deals(cur, start_text: str, end_text: str, top_n: int) -> list[tuple[str, Decimal]]:
    profits: dict[str, Decimal] = {}
    for _label, schema in MT5_SOURCES:
        cur.execute(
            f"""
            SELECT d.Login AS login,
                   SUM((d.Profit + d.Storage + d.Commission + d.Fee) / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS profit_sum
            FROM {schema}.mt5_deals d
            LEFT JOIN {schema}.mt5_users_view u ON u.Login = d.Login
            WHERE d.Time >= %s AND d.Time <= %s
              AND d.Action IN (0, 1)
              AND d.Entry IN (1, 3)
              AND u.`Group` NOT LIKE '%%Test%%'
              AND u.`Group` NOT LIKE 'ACFIX%%'
              AND COALESCE(u.Status, '') <> 'zzz'
            GROUP BY d.Login
            """,
            (start_text, end_text),
        )
        for row in cur.fetchall():
            login = str(row["login"])
            profits[login] = profits.get(login, Decimal("0")) + as_decimal(row["profit_sum"])

    cur.execute(
        """
        SELECT t.LOGIN AS login, SUM(t.PROFIT + t.SWAPS + t.COMMISSION) AS profit_sum
        FROM mt4_export_syc.mt4_trades t
        LEFT JOIN mt4_export_syc.mt4_users_view u ON u.LOGIN = t.LOGIN
        WHERE t.CLOSE_TIME >= %s AND t.CLOSE_TIME <= %s
          AND t.CMD IN (0, 1)
          AND (u.`GROUP` LIKE 'PXM-%%' OR u.`GROUP` LIKE 'TO-%%')
        GROUP BY t.LOGIN
        """,
        (start_text, end_text),
    )
    for row in cur.fetchall():
        login = str(row["login"])
        profits[login] = profits.get(login, Decimal("0")) + as_decimal(row["profit_sum"])
    return sorted(profits.items(), key=lambda item: item[1], reverse=True)[:top_n]


def optimized_rank_rows(cur, start_text: str, end_text: str, top_n: int) -> list[tuple[str, Decimal]]:
    first_daily_date = (datetime.strptime(start_text[:10], "%Y-%m-%d").date() + timedelta(days=1))
    last_daily_date = datetime.strptime(end_text[:10], "%Y-%m-%d").date()
    daily_dates = []
    current_daily_date = first_daily_date
    while current_daily_date <= last_daily_date:
        daily_dates.append(current_daily_date.isoformat())
        current_daily_date += timedelta(days=1)
    if not daily_dates:
        return optimized_rank_rows_from_deals(cur, start_text, end_text, top_n)
    daily_epochs = [daily_cutoff_epoch(day) for day in daily_dates]
    epoch_ph = placeholders([str(epoch) for epoch in daily_epochs])
    time_ph = placeholders(daily_dates)
    daily_times = [f"{day} 20:59:59" for day in daily_dates]
    profits: dict[str, Decimal] = {}

    for _label, schema in MT5_SOURCES:
        cur.execute(
            f"""
            SELECT Login AS login,
                   SUM((DailyProfit + DailyStorage) / CASE WHEN `Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS profit_sum
            FROM {schema}.mt5_daily_view
            WHERE Datetime IN ({epoch_ph})
              AND `Group` NOT LIKE '%%Test%%'
              AND `Group` NOT LIKE 'ACFIX%%'
              AND `Group` NOT LIKE '%%GIVEUP%%'
            GROUP BY Login
            """,
            tuple(daily_epochs),
        )
        for row in cur.fetchall():
            login = str(row["login"])
            profits[login] = profits.get(login, Decimal("0")) + as_decimal(row["profit_sum"])

    cur.execute(
        """
        SELECT LOGIN AS login, SUM(PROFIT_CLOSED) AS profit_sum
        FROM mt4_export_syc.mt4_daily
        WHERE TIME IN ({time_ph})
          AND (`GROUP` LIKE 'PXM-%%' OR `GROUP` LIKE 'TO-%%')
          AND `GROUP` NOT LIKE '%%GIVEUP%%'
        GROUP BY LOGIN
        """.format(time_ph=time_ph),
        tuple(daily_times),
    )
    for row in cur.fetchall():
        login = str(row["login"])
        profits[login] = profits.get(login, Decimal("0")) + as_decimal(row["profit_sum"])

    ranked = sorted(profits.items(), key=lambda item: item[1], reverse=True)[:top_n]
    if ranked:
        return ranked
    return optimized_rank_rows_from_deals(cur, start_text, end_text, top_n)


def new_account_bucket(login: str, profit: Decimal) -> dict:
    return {
        "login": login,
        "balance": Decimal("0"),
        "REGDATE": "",
        "name": "",
        "group_name": "",
        "status": "",
        "open_symbols": set(),
        "volume_sum_open": Decimal("0"),
        "close_symbols": set(),
        "volume_sum_close": Decimal("0"),
        "profit_sum": profit,
        "floating_symbols": set(),
        "volume_floating": Decimal("0"),
        "profit_floating": Decimal("0"),
    }


def add_symbol(bucket: dict, key: str, symbol) -> None:
    symbol = str(symbol or "").strip()
    if symbol:
        bucket[key].add(symbol)


def apply_user_info(cur, accounts: dict[str, dict], logins: list[str]) -> None:
    if not logins:
        return
    ph = placeholders(logins)
    for _label, schema in MT5_SOURCES:
        cur.execute(
            f"SELECT Login AS login, Balance AS balance, Registration AS REGDATE, Name AS name, `Group` AS group_name, COALESCE(Status, '') AS status FROM {schema}.mt5_users_view WHERE Login IN ({ph})",
            tuple(logins),
        )
        for row in cur.fetchall():
            bucket = accounts.get(str(row["login"]))
            if bucket and not bucket["group_name"]:
                bucket.update(row)
    cur.execute(
        f"SELECT LOGIN AS login, BALANCE AS balance, REGDATE AS REGDATE, NAME AS name, `GROUP` AS group_name, COALESCE(STATUS, '') AS status FROM mt4_export_syc.mt4_users_view WHERE LOGIN IN ({ph})",
        tuple(logins),
    )
    for row in cur.fetchall():
        bucket = accounts.get(str(row["login"]))
        if bucket and not bucket["group_name"]:
            bucket.update(row)


def apply_deal_symbol_aggregates(cur, accounts: dict[str, dict], logins: list[str], start_text: str, end_text: str, entry_mode: str) -> None:
    if not logins:
        return
    ph = placeholders(logins)
    if entry_mode == "open":
        mt5_entry = "d.Entry = 0"
        mt4_time = "t.OPEN_TIME"
        symbol_key = "open_symbols"
        volume_key = "volume_sum_open"
    else:
        mt5_entry = "d.Entry IN (1, 3)"
        mt4_time = "t.CLOSE_TIME"
        symbol_key = "close_symbols"
        volume_key = "volume_sum_close"

    for _label, schema in MT5_SOURCES:
        cur.execute(
            f"""
            SELECT d.Login AS login, d.Symbol AS symbol,
                   SUM(d.Volume / 10000 / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS lots
            FROM {schema}.mt5_deals d
            LEFT JOIN {schema}.mt5_users_view u ON u.Login = d.Login
            WHERE d.Login IN ({ph})
              AND d.Time >= %s AND d.Time <= %s
              AND d.Action IN (0, 1)
              AND {mt5_entry}
              AND u.`Group` NOT LIKE '%%Test%%'
              AND u.`Group` NOT LIKE 'ACFIX%%'
              AND COALESCE(u.Status, '') <> 'zzz'
            GROUP BY d.Login, d.Symbol
            """,
            tuple(logins) + (start_text, end_text),
        )
        for row in cur.fetchall():
            bucket = accounts[str(row["login"])]
            add_symbol(bucket, symbol_key, row["symbol"])
            bucket[volume_key] += as_decimal(row["lots"])

    cur.execute(
        f"""
        SELECT t.LOGIN AS login, t.SYMBOL AS symbol, SUM(t.VOLUME) / 100 AS lots
        FROM mt4_export_syc.mt4_trades t
        LEFT JOIN mt4_export_syc.mt4_users_view u ON u.LOGIN = t.LOGIN
        WHERE t.LOGIN IN ({ph})
          AND {mt4_time} >= %s AND {mt4_time} <= %s
          AND t.CMD IN (0, 1)
          AND (u.`GROUP` LIKE 'PXM-%%' OR u.`GROUP` LIKE 'TO-%%')
        GROUP BY t.LOGIN, t.SYMBOL
        """,
        tuple(logins) + (start_text, end_text),
    )
    for row in cur.fetchall():
        bucket = accounts[str(row["login"])]
        add_symbol(bucket, symbol_key, row["symbol"])
        bucket[volume_key] += as_decimal(row["lots"])


def apply_floating_fast(cur, accounts: dict[str, dict], logins: list[str]) -> None:
    if not logins:
        return
    ph = placeholders(logins)
    for _label, schema in MT5_SOURCES:
        cur.execute(
            f"""
            SELECT p.Login AS login, p.Symbol AS symbol,
                   SUM(p.Volume / 10000 / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS lots,
                   SUM((p.Profit + p.Storage) / CASE WHEN u.`Group` LIKE '%%Cent%%' THEN 100 ELSE 1 END) AS profit
            FROM {schema}.mt5_positions p
            LEFT JOIN {schema}.mt5_users_view u ON u.Login = p.Login
            WHERE p.Login IN ({ph})
              AND u.`Group` NOT LIKE '%%Test%%'
              AND u.`Group` NOT LIKE 'ACFIX%%'
              AND COALESCE(u.Status, '') <> 'zzz'
            GROUP BY p.Login, p.Symbol
            """,
            tuple(logins),
        )
        for row in cur.fetchall():
            bucket = accounts[str(row["login"])]
            add_symbol(bucket, "floating_symbols", row["symbol"])
            bucket["volume_floating"] += as_decimal(row["lots"])
            bucket["profit_floating"] += as_decimal(row["profit"])
    cur.execute(
        f"""
        SELECT t.LOGIN AS login, t.SYMBOL AS symbol, SUM(t.VOLUME) / 100 AS lots,
               SUM(t.PROFIT + t.SWAPS + t.COMMISSION) AS profit
        FROM mt4_export_syc.mt4_trades t
        LEFT JOIN mt4_export_syc.mt4_users_view u ON u.LOGIN = t.LOGIN
        WHERE t.LOGIN IN ({ph})
          AND t.CLOSE_TIME = '1970-01-01 00:00:00'
          AND t.CMD IN (0, 1)
          AND (u.`GROUP` LIKE 'PXM-%%' OR u.`GROUP` LIKE 'TO-%%')
        GROUP BY t.LOGIN, t.SYMBOL
        """,
        tuple(logins),
    )
    for row in cur.fetchall():
        bucket = accounts[str(row["login"])]
        add_symbol(bucket, "floating_symbols", row["symbol"])
        bucket["volume_floating"] += as_decimal(row["lots"])
        bucket["profit_floating"] += as_decimal(row["profit"])


def run_query(days: int, top_n: int, trade_date: str | None = None) -> tuple[datetime, datetime, list[dict]]:
    start, end = trading_window(days, trade_date)
    start_text = start.strftime("%Y-%m-%d %H:%M:%S")
    end_text = end.strftime("%Y-%m-%d %H:%M:%S")
    with db_conn() as conn:
        with conn.cursor() as cur:
            ranked = optimized_rank_rows(cur, start_text, end_text, top_n)
            logins = [login for login, _profit in ranked]
            accounts = {login: new_account_bucket(login, profit) for login, profit in ranked}
            apply_user_info(cur, accounts, logins)
            apply_deal_symbol_aggregates(cur, accounts, logins, start_text, end_text, "open")
            apply_deal_symbol_aggregates(cur, accounts, logins, start_text, end_text, "close")
            apply_floating_fast(cur, accounts, logins)
    rows = []
    for login in logins:
        b = accounts[login]
        rows.append({
            "login": login,
            "balance": round(as_decimal(b.get("balance")), 2),
            "REGDATE": b.get("REGDATE") or "",
            "name": b.get("name") or "",
            "group_name": b.get("group_name") or "",
            "status": b.get("status") or "",
            "open_symbols": ",".join(sorted(b["open_symbols"])) or "NULL",
            "volume_sum_open": f"{b['volume_sum_open']:.4f}",
            "close_symbols": ",".join(sorted(b["close_symbols"])) or "NULL",
            "volume_sum_close": f"{b['volume_sum_close']:.4f}",
            "vol_diff": f"{(b['volume_sum_close'] - b['volume_sum_open']):.4f}",
            "profit_sum": f"{b['profit_sum']:.4f}",
            "floating_symbols": ",".join(sorted(b["floating_symbols"])) or "NULL",
            "volume_floating": f"{b['volume_floating']:.2f}",
            "profit_floating": f"{b['profit_floating']:.2f}",
        })
    return start, end, rows


def run_rank_summary(days: int, top_n: int, trade_date: str | None = None) -> tuple[datetime, datetime, list[dict]]:
    start, end = trading_window(days, trade_date)
    start_text = start.strftime("%Y-%m-%d %H:%M:%S")
    end_text = end.strftime("%Y-%m-%d %H:%M:%S")
    with db_conn() as conn:
        with conn.cursor() as cur:
            ranked = optimized_rank_rows(cur, start_text, end_text, top_n)
            logins = [login for login, _profit in ranked]
            accounts = {login: new_account_bucket(login, profit) for login, profit in ranked}
            apply_user_info(cur, accounts, logins)
    rows = []
    for login in logins:
        bucket = accounts[login]
        rows.append({
            "login": login,
            "name": bucket.get("name") or "",
            "group_name": bucket.get("group_name") or "",
            "profit_sum": f"{bucket['profit_sum']:.4f}",
        })
    return start, end, rows


def stream_json_line(handler: BaseHTTPRequestHandler, payload: dict) -> None:
    handler.wfile.write((json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
    handler.wfile.flush()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/api/analyze-stream":
            today = date.today()
            days = parse_positive_int(params.get("days", ["1"])[0], 1, 365)
            top_n = parse_positive_int(params.get("top", ["100"])[0], 100, 100)
            rank_date = (params.get("date", [""])[0] or "").strip()
            try:
                offset = int(params.get("offset", ["0"])[0] or "0")
            except ValueError:
                offset = 0
            offset = min(max(offset, 0), 99)
            batch_size = parse_positive_int(params.get("batch", ["25"])[0], 25, 25)
            trade_limit = parse_positive_int(params.get("limit", ["100"])[0], 100, 1000)
            default_start = (today - timedelta(days=30)).isoformat()
            try:
                start_date = parse_date_text(params.get("start", [default_start])[0] or default_start)
                end_date = parse_date_text(params.get("end", [today.isoformat()])[0] or today.isoformat())
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-store, no-transform")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                stream_json_line(self, {"type": "status", "message": "正在获取前100盈利账户排名..."})
                rank_start, rank_end, rank_rows = run_rank_summary(days, top_n, rank_date or None)
                selected = rank_rows[offset:offset + batch_size]
                total_selected = len(selected)
                stream_json_line(self, {
                    "type": "status",
                    "message": f"排名已获取，开始分析本批 {total_selected} 个账号...",
                    "rank_start": rank_start,
                    "rank_end": rank_end,
                    "offset": offset,
                })
                for index, account in enumerate(selected, start=offset + 1):
                    login = str(account.get("login", "")).strip()
                    stream_json_line(self, {"type": "status", "message": f"正在分析序号 {index} / login {login}..."})
                    trades = run_trade_query(login, start_date, end_date, trade_limit) if login else []
                    stream_json_line(self, {
                        "type": "account",
                        "account": {
                            "seq": index,
                            "login": login,
                            "name": account.get("name", ""),
                            "group_name": account.get("group_name", ""),
                            "profit_sum": account.get("profit_sum", ""),
                            "trade_count": len(trades),
                            "trades": trades,
                        },
                    })
                next_offset = offset + total_selected
                stream_json_line(self, {
                    "type": "done",
                    "offset": offset,
                    "next_offset": next_offset,
                    "has_next": next_offset < min(top_n, len(rank_rows)),
                })
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                try:
                    if not self.wfile.closed:
                        stream_json_line(self, {"type": "error", "message": str(exc)})
                except Exception:
                    pass
            return

        if parsed.path == "/api/analyze-batch":
            today = date.today()
            days = parse_positive_int(params.get("days", ["1"])[0], 1, 365)
            top_n = parse_positive_int(params.get("top", ["100"])[0], 100, 100)
            rank_date = (params.get("date", [""])[0] or "").strip()
            try:
                offset = int(params.get("offset", ["0"])[0] or "0")
            except ValueError:
                offset = 0
            offset = min(max(offset, 0), 99)
            batch_size = parse_positive_int(params.get("batch", ["25"])[0], 25, 25)
            trade_limit = parse_positive_int(params.get("limit", ["100"])[0], 100, 1000)
            default_start = (today - timedelta(days=30)).isoformat()
            start_date = parse_date_text(params.get("start", [default_start])[0] or default_start)
            end_date = parse_date_text(params.get("end", [today.isoformat()])[0] or today.isoformat())
            try:
                rank_start, rank_end, rank_rows = run_query(days, top_n, rank_date or None)
                selected = rank_rows[offset:offset + batch_size]
                accounts = []
                for index, account in enumerate(selected, start=offset + 1):
                    login = str(account.get("login", "")).strip()
                    trades = run_trade_query(login, start_date, end_date, trade_limit) if login else []
                    accounts.append({
                        "seq": index,
                        "login": login,
                        "name": account.get("name", ""),
                        "group_name": account.get("group_name", ""),
                        "profit_sum": account.get("profit_sum", ""),
                        "trade_count": len(trades),
                        "trades": trades,
                    })
                json_response(self, {
                    "accounts": accounts,
                    "offset": offset,
                    "next_offset": offset + len(selected),
                    "has_next": offset + len(selected) < min(top_n, len(rank_rows)),
                    "rank_start": rank_start,
                    "rank_end": rank_end,
                    "fields": TRADE_FIELDS,
                })
            except Exception as exc:
                json_response(self, {"error": str(exc)}, 500)
            return

        if parsed.path in {"/trades", "/trades/download"}:
            login = (params.get("login", [""])[0] or "").strip()
            today = date.today()
            default_start = (today - timedelta(days=30)).isoformat()
            start_date = parse_date_text(params.get("start", [default_start])[0] or default_start)
            end_date = parse_date_text(params.get("end", [today.isoformat()])[0] or today.isoformat())
            limit = parse_positive_int(params.get("limit", ["500"])[0], 500, 5000)
            try:
                rows = run_trade_query(login, start_date, end_date, limit) if login else []
                if parsed.path == "/trades/download":
                    csv_response_for(self, rows, f"trades_{login}_{start_date}_{end_date}.csv", TRADE_FIELDS)
                    return
                html_response(self, render_trade_page_ui(login, start_date, end_date, limit, rows))
            except Exception as exc:
                html_response(self, render_trade_page_ui(login, start_date, end_date, limit, [], str(exc)), 500)
            return

        if parsed.path == "/" and not parsed.query:
            html_response(self, render_rank_page_ui(1, 100, None, []))
            return

        days = parse_positive_int(params.get("days", ["7"])[0], 7, 365)
        top_n = parse_positive_int(params.get("top", ["50"])[0], 50, MAX_LIMIT)
        trade_date = (params.get("date", [""])[0] or "").strip()
        try:
            start, end, rows = run_query(days, top_n, trade_date or None)
            if parsed.path == "/download":
                csv_response(self, rows, f"ac_profit_top_{top_n}_{days}d.csv")
                return
            html_response(self, render_rank_page_ui(days, top_n, start, rows, end=end, trade_date=trade_date))
        except Exception as exc:
            html_response(self, render_rank_page_ui(days, top_n, None, [], str(exc), trade_date=trade_date), 500)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"AC profit ranker running: http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
