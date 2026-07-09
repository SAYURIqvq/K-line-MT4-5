import type { Config, Context } from "@netlify/functions";
import mysql from "mysql2/promise";

const FIELDS = [
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
] as const;

type Row = Record<(typeof FIELDS)[number], unknown>;

function env(name: string, fallback = "") {
  return Netlify.env.get(name) || fallback;
}

function parsePositiveInt(value: string | null, fallback: number, max: number) {
  const parsed = Number.parseInt(value || "", 10);
  if (!Number.isFinite(parsed) || parsed < 1) return fallback;
  return Math.min(parsed, max);
}

function csvEscape(value: unknown) {
  const text = value == null ? "" : String(value);
  if (/[",\r\n]/.test(text)) return `"${text.replaceAll('"', '""')}"`;
  return text;
}

function toCsv(rows: Row[]) {
  const lines = [FIELDS.join(",")];
  for (const row of rows) {
    lines.push(FIELDS.map((field) => csvEscape(row[field])).join(","));
  }
  return "\ufeff" + lines.join("\r\n") + "\r\n";
}

function explicitWindow(days: number, tradeDate: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(tradeDate)) return null;
  const [year, month, day] = tradeDate.split("-").map((value) => Number.parseInt(value, 10));
  const target = new Date(Date.UTC(year, month - 1, day));
  if (Number.isNaN(target.getTime())) return null;
  const start = new Date(target);
  start.setUTCDate(start.getUTCDate() - days);
  const startText = `${start.getUTCFullYear()}-${String(start.getUTCMonth() + 1).padStart(2, "0")}-${String(start.getUTCDate()).padStart(2, "0")} 21:00:00`;
  return { start: startText, end: `${tradeDate} 20:59:59` };
}

async function getConnection() {
  const password = env("AC_DB_PASSWORD");
  if (!password) {
    throw new Error("Missing AC_DB_PASSWORD environment variable.");
  }

  return mysql.createConnection({
    host: env("AC_DB_HOST", "rm-3nsv8k160ht47x44uio.mysql.rds.aliyuncs.com"),
    port: Number.parseInt(env("AC_DB_PORT", "3306"), 10),
    user: env("AC_DB_USER", "intern"),
    password,
    charset: "utf8mb4",
    connectTimeout: 10000,
  });
}

const query = `
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
           SUM((d.Profit + d.Storage + d.Commission + d.Fee) / CASE WHEN u.\`Group\` LIKE '%Cent%' THEN 100 ELSE 1 END) AS profit_sum
    FROM sass_crm_ac_mt5_live.mt5_deals d
    LEFT JOIN sass_crm_ac_mt5_live.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= ?
      AND d.Time <= ?
      AND d.Action IN (0, 1)
      AND d.Entry IN (1, 3)
      AND u.\`Group\` NOT LIKE '%Test%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login
    UNION ALL
    SELECT d.Login AS login,
           SUM((d.Profit + d.Storage + d.Commission + d.Fee) / CASE WHEN u.\`Group\` LIKE '%Cent%' THEN 100 ELSE 1 END) AS profit_sum
    FROM int_sass_crm_ac_mt5_live_new.mt5_deals d
    LEFT JOIN int_sass_crm_ac_mt5_live_new.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= ?
      AND d.Time <= ?
      AND d.Action IN (0, 1)
      AND d.Entry IN (1, 3)
      AND u.\`Group\` NOT LIKE '%Test%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login
    UNION ALL
    SELECT t.LOGIN AS login, SUM(t.PROFIT + t.SWAPS + t.COMMISSION) AS profit_sum
    FROM mt4_export_syc.mt4_trades t
    LEFT JOIN mt4_export_syc.mt4_users_view u ON u.LOGIN = t.LOGIN
    WHERE t.CLOSE_TIME >= ?
      AND t.CLOSE_TIME <= ?
      AND t.CMD IN (0, 1)
      AND u.\`GROUP\` LIKE 'PXM-%'
    GROUP BY t.LOGIN
  ) close_union
  GROUP BY login
  ORDER BY profit_sum DESC
  LIMIT ?
) ranked
LEFT JOIN (
  SELECT Login AS login, Balance AS balance, Registration AS REGDATE, Name AS name, \`Group\` AS group_name, COALESCE(Status, '') AS status
  FROM sass_crm_ac_mt5_live.mt5_users_view
  UNION ALL
  SELECT Login AS login, Balance AS balance, Registration AS REGDATE, Name AS name, \`Group\` AS group_name, COALESCE(Status, '') AS status
  FROM int_sass_crm_ac_mt5_live_new.mt5_users_view
  UNION ALL
  SELECT LOGIN AS login, BALANCE AS balance, REGDATE AS REGDATE, NAME AS name, \`GROUP\` AS group_name, COALESCE(STATUS, '') AS status
  FROM mt4_export_syc.mt4_users_view
) u ON u.login = ranked.login
LEFT JOIN (
  SELECT
    login,
    GROUP_CONCAT(Symbol ORDER BY Symbol ASC SEPARATOR ',') AS open_symbols,
    SUM(lots) AS volume_sum_open
  FROM (
    SELECT d.Login AS login, d.Symbol AS Symbol,
           SUM(d.Volume / 10000 / CASE WHEN u.\`Group\` LIKE '%Cent%' THEN 100 ELSE 1 END) AS lots
    FROM sass_crm_ac_mt5_live.mt5_deals d
    LEFT JOIN sass_crm_ac_mt5_live.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= ?
      AND d.Time <= ?
      AND d.Action IN (0, 1)
      AND d.Entry = 0
      AND u.\`Group\` NOT LIKE '%Test%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login, d.Symbol
    UNION ALL
    SELECT d.Login AS login, d.Symbol AS Symbol,
           SUM(d.Volume / 10000 / CASE WHEN u.\`Group\` LIKE '%Cent%' THEN 100 ELSE 1 END) AS lots
    FROM int_sass_crm_ac_mt5_live_new.mt5_deals d
    LEFT JOIN int_sass_crm_ac_mt5_live_new.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= ?
      AND d.Time <= ?
      AND d.Action IN (0, 1)
      AND d.Entry = 0
      AND u.\`Group\` NOT LIKE '%Test%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login, d.Symbol
    UNION ALL
    SELECT t.LOGIN AS login, t.SYMBOL AS Symbol, SUM(t.VOLUME) / 100 AS lots
    FROM mt4_export_syc.mt4_trades t
    LEFT JOIN mt4_export_syc.mt4_users_view u ON u.LOGIN = t.LOGIN
    WHERE t.OPEN_TIME >= ?
      AND t.OPEN_TIME <= ?
      AND t.CMD IN (0, 1)
      AND u.\`GROUP\` LIKE 'PXM-%'
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
      SUM(d.Volume / 10000 / CASE WHEN u.\`Group\` LIKE '%Cent%' THEN 100 ELSE 1 END) AS lots,
      SUM((d.Profit + d.Storage + d.Commission + d.Fee) / CASE WHEN u.\`Group\` LIKE '%Cent%' THEN 100 ELSE 1 END) AS profit
    FROM sass_crm_ac_mt5_live.mt5_deals d
    LEFT JOIN sass_crm_ac_mt5_live.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= ?
      AND d.Time <= ?
      AND d.Action IN (0, 1)
      AND d.Entry IN (1, 3)
      AND u.\`Group\` NOT LIKE '%Test%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login, d.Symbol
    UNION ALL
    SELECT
      d.Login AS login,
      d.Symbol AS Symbol,
      SUM(d.Volume / 10000 / CASE WHEN u.\`Group\` LIKE '%Cent%' THEN 100 ELSE 1 END) AS lots,
      SUM((d.Profit + d.Storage + d.Commission + d.Fee) / CASE WHEN u.\`Group\` LIKE '%Cent%' THEN 100 ELSE 1 END) AS profit
    FROM int_sass_crm_ac_mt5_live_new.mt5_deals d
    LEFT JOIN int_sass_crm_ac_mt5_live_new.mt5_users_view u ON u.Login = d.Login
    WHERE d.Time >= ?
      AND d.Time <= ?
      AND d.Action IN (0, 1)
      AND d.Entry IN (1, 3)
      AND u.\`Group\` NOT LIKE '%Test%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY d.Login, d.Symbol
    UNION ALL
    SELECT t.LOGIN AS login, t.SYMBOL AS Symbol,
           SUM(t.VOLUME) / 100 AS lots,
           SUM(t.PROFIT + t.SWAPS + t.COMMISSION) AS profit
    FROM mt4_export_syc.mt4_trades t
    LEFT JOIN mt4_export_syc.mt4_users_view u ON u.LOGIN = t.LOGIN
    WHERE t.CLOSE_TIME >= ?
      AND t.CLOSE_TIME <= ?
      AND t.CMD IN (0, 1)
      AND u.\`GROUP\` LIKE 'PXM-%'
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
      SUM(p.Volume / 10000 / CASE WHEN u.\`Group\` LIKE '%Cent%' THEN 100 ELSE 1 END) AS lots,
      SUM((p.Profit + p.Storage) / CASE WHEN u.\`Group\` LIKE '%Cent%' THEN 100 ELSE 1 END) AS profit_floating
    FROM sass_crm_ac_mt5_live.mt5_positions p
    LEFT JOIN sass_crm_ac_mt5_live.mt5_users_view u ON u.Login = p.Login
    WHERE u.\`Group\` NOT LIKE '%Test%'
      AND COALESCE(u.Status, '') <> 'zzz'
    GROUP BY p.Login, p.Symbol
    UNION ALL
    SELECT
      p.Login AS login,
      p.Symbol AS Symbol,
      SUM(p.Volume / 10000 / CASE WHEN u.\`Group\` LIKE '%Cent%' THEN 100 ELSE 1 END) AS lots,
      SUM((p.Profit + p.Storage) / CASE WHEN u.\`Group\` LIKE '%Cent%' THEN 100 ELSE 1 END) AS profit_floating
    FROM int_sass_crm_ac_mt5_live_new.mt5_positions p
    LEFT JOIN int_sass_crm_ac_mt5_live_new.mt5_users_view u ON u.Login = p.Login
    WHERE u.\`Group\` NOT LIKE '%Test%'
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
      AND u.\`GROUP\` LIKE 'PXM-%'
    GROUP BY t.LOGIN, t.SYMBOL
  ) floating_by_symbol
  GROUP BY login
) f ON f.login = ranked.login
ORDER BY profit_sum DESC;
`;

export default async (req: Request, _context: Context) => {
  const url = new URL(req.url);
  const days = parsePositiveInt(url.searchParams.get("days"), 1, 365);
  const top = parsePositiveInt(url.searchParams.get("top"), 20, 1000);
  const tradeDate = url.searchParams.get("date") || "";
  const isCsv = url.pathname.endsWith(".csv") || url.searchParams.get("format") === "csv";

  let connection: mysql.Connection | undefined;
  try {
    connection = await getConnection();

    let window = explicitWindow(days, tradeDate);
    if (!window) {
      const [startRows] = await connection.execute<mysql.RowDataPacket[]>(
        `SELECT
          DATE_FORMAT(
            DATE_SUB(
              IF(
                TIME(NOW()) >= '21:00:00',
                TIMESTAMP(CURDATE(), '21:00:00'),
                TIMESTAMP(DATE_SUB(CURDATE(), INTERVAL 1 DAY), '21:00:00')
              ),
              INTERVAL ? DAY
            ),
            '%Y-%m-%d %H:%i:%s'
          ) AS start_time,
          DATE_FORMAT(NOW(), '%Y-%m-%d %H:%i:%s') AS end_time`,
        [days - 1],
      );
      window = { start: String(startRows[0]?.start_time || ""), end: String(startRows[0]?.end_time || "") };
    }

    const { start, end } = window;
    const [rows] = await connection.execute<mysql.RowDataPacket[]>(query, [
      start,
      end,
      start,
      end,
      start,
      end,
      top,
      start,
      end,
      start,
      end,
      start,
      end,
      start,
      end,
      start,
      end,
      start,
      end,
    ]);

    if (isCsv) {
      return new Response(toCsv(rows as Row[]), {
        headers: {
          "Cache-Control": "no-store",
          "Content-Disposition": `attachment; filename="ac_profit_top_${top}_${days}d.csv"`,
          "Content-Type": "text/csv; charset=utf-8",
        },
      });
    }

    return Response.json(
      { fields: FIELDS, rows, start, end, days, top },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return Response.json({ error: message }, { status: 500 });
  } finally {
    await connection?.end();
  }
};

export const config: Config = {
  path: ["/api/ac-profit-rank", "/api/ac-profit-rank.csv"],
};
