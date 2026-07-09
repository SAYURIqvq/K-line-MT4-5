from __future__ import annotations

import csv
import html
import io
import json
import os
from datetime import datetime, time, timedelta
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
    "open_symb",
    "volume_sum",
    "close_symb",
    "volume_sum_close",
    "vol_diff",
    "profit_sum",
    "floating_s",
    "volume_fl",
    "profit_floating",
]


def trading_window_start(days: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    today_21 = datetime.combine(now.date(), time(21, 0, 0))
    current_session_start = today_21 if now >= today_21 else today_21 - timedelta(days=1)
    return current_session_start - timedelta(days=max(days, 1) - 1)


def parse_positive_int(value: str | None, default: int, max_value: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return default
    return min(max(parsed, 1), max_value)


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
        read_timeout=90,
        write_timeout=30,
    )


QUERY = """
SELECT
  ranked.login,
  ROUND(COALESCE(u.Balance, a.Balance, 0), 2) AS balance,
  u.Registration AS REGDATE,
  u.Name AS name,
  u.`Group` AS group_name,
  u.Status AS status,
  COALESCE(o.open_symb, '') AS open_symb,
  ROUND(COALESCE(o.volume_sum, 0), 2) AS volume_sum,
  COALESCE(c.close_symb, '') AS close_symb,
  ROUND(COALESCE(c.volume_sum_close, 0), 2) AS volume_sum_close,
  ROUND(COALESCE(o.volume_sum, 0) - COALESCE(c.volume_sum_close, 0), 2) AS vol_diff,
  ROUND(COALESCE(c.profit_sum, 0), 2) AS profit_sum,
  COALESCE(f.floating_s, '') AS floating_s,
  ROUND(COALESCE(f.volume_fl, 0), 2) AS volume_fl,
  ROUND(COALESCE(f.profit_floating, 0), 2) AS profit_floating
FROM (
  SELECT Login AS login, SUM(Profit + Storage + Commission + Fee) AS profit_sum
  FROM sass_crm_ac_mt5_live.mt5_deals
  WHERE Time >= %s
    AND Action IN (0, 1)
    AND Entry IN (1, 3)
  GROUP BY Login
  ORDER BY profit_sum DESC
  LIMIT %s
) ranked
LEFT JOIN sass_crm_ac_mt5_live.mt5_users_view u ON u.Login = ranked.login
LEFT JOIN sass_crm_ac_mt5_live.mt5_accounts a ON a.Login = ranked.login
LEFT JOIN (
  SELECT
    Login,
    GROUP_CONCAT(CONCAT(Symbol, ':', ROUND(lots, 2)) ORDER BY lots DESC SEPARATOR '; ') AS open_symb,
    SUM(lots) AS volume_sum
  FROM (
    SELECT Login, Symbol, SUM(Volume) / 10000 AS lots
    FROM sass_crm_ac_mt5_live.mt5_deals
    WHERE Time >= %s
      AND Action IN (0, 1)
      AND Entry = 0
    GROUP BY Login, Symbol
  ) open_by_symbol
  GROUP BY Login
) o ON o.Login = ranked.login
LEFT JOIN (
  SELECT
    Login,
    GROUP_CONCAT(CONCAT(Symbol, ':', ROUND(lots, 2)) ORDER BY lots DESC SEPARATOR '; ') AS close_symb,
    SUM(lots) AS volume_sum_close,
    SUM(profit) AS profit_sum
  FROM (
    SELECT
      Login,
      Symbol,
      SUM(Volume) / 10000 AS lots,
      SUM(Profit + Storage + Commission + Fee) AS profit
    FROM sass_crm_ac_mt5_live.mt5_deals
    WHERE Time >= %s
      AND Action IN (0, 1)
      AND Entry IN (1, 3)
    GROUP BY Login, Symbol
  ) close_by_symbol
  GROUP BY Login
) c ON c.Login = ranked.login
LEFT JOIN (
  SELECT
    Login,
    GROUP_CONCAT(CONCAT(Symbol, ':', ROUND(lots, 2)) ORDER BY lots DESC SEPARATOR '; ') AS floating_s,
    SUM(lots) AS volume_fl,
    SUM(profit_floating) AS profit_floating
  FROM (
    SELECT
      Login,
      Symbol,
      SUM(Volume) / 10000 AS lots,
      SUM(Profit + Storage) AS profit_floating
    FROM sass_crm_ac_mt5_live.mt5_positions
    GROUP BY Login, Symbol
  ) floating_by_symbol
  GROUP BY Login
) f ON f.Login = ranked.login
ORDER BY profit_sum DESC;
"""


def run_query(days: int, top_n: int) -> tuple[datetime, list[dict]]:
    start = trading_window_start(days)
    start_text = start.strftime("%Y-%m-%d %H:%M:%S")
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY, (start_text, top_n, start_text, start_text))
            rows = cur.fetchall()
    return start, rows


def html_response(handler: BaseHTTPRequestHandler, body: str, status: int = 200) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
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


def render_page(days: int, top_n: int, start: datetime | None, rows: list[dict] | None, error: str = "") -> str:
    rows = rows or []
    query = urlencode({"days": days, "top": top_n})
    table_rows = []
    for row in rows:
        cells = []
        for field in FIELDS:
            value = row.get(field, "")
            if isinstance(value, datetime):
                value = value.strftime("%Y-%m-%d %H:%M:%S")
            cls = "num" if field in {"balance", "volume_sum", "volume_sum_close", "vol_diff", "profit_sum", "volume_fl", "profit_floating"} else ""
            cells.append(f"<td class='{cls}'>{html.escape(str(value or ''))}</td>")
        table_rows.append("<tr>" + "".join(cells) + "</tr>")
    body_rows = "\n".join(table_rows) or f"<tr><td colspan='{len(FIELDS)}' class='empty'>暂无结果</td></tr>"
    headers = "".join(f"<th>{html.escape(field)}</th>" for field in FIELDS)
    start_text = start.strftime("%Y-%m-%d %H:%M:%S") if start else "-"
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        days = parse_positive_int(params.get("days", ["7"])[0], 7, 365)
        top_n = parse_positive_int(params.get("top", ["50"])[0], 50, MAX_LIMIT)
        try:
            start, rows = run_query(days, top_n)
            if parsed.path == "/download":
                csv_response(self, rows, f"ac_profit_top_{top_n}_{days}d.csv")
                return
            html_response(self, render_page(days, top_n, start, rows))
        except Exception as exc:
            html_response(self, render_page(days, top_n, None, [], str(exc)), 500)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"AC profit ranker running: http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
