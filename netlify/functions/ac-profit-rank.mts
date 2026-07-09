import type { Config, Context } from "@netlify/functions";
import mysql from "mysql2/promise";

const FIELDS = [
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
  ROUND(COALESCE(u.Balance, a.Balance, 0), 2) AS balance,
  u.Registration AS REGDATE,
  u.Name AS name,
  u.\`Group\` AS group_name,
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
  WHERE Time >= ?
    AND Action IN (0, 1)
    AND Entry IN (1, 3)
  GROUP BY Login
  ORDER BY profit_sum DESC
  LIMIT ?
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
    WHERE Time >= ?
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
    WHERE Time >= ?
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
`;

export default async (req: Request, _context: Context) => {
  const url = new URL(req.url);
  const days = parsePositiveInt(url.searchParams.get("days"), 1, 365);
  const top = parsePositiveInt(url.searchParams.get("top"), 20, 1000);
  const isCsv = url.pathname.endsWith(".csv") || url.searchParams.get("format") === "csv";

  let connection: mysql.Connection | undefined;
  try {
    connection = await getConnection();

    const [startRows] = await connection.execute<mysql.RowDataPacket[]>(
      `SELECT DATE_FORMAT(
        DATE_SUB(
          IF(
            TIME(NOW()) >= '21:00:00',
            TIMESTAMP(CURDATE(), '21:00:00'),
            TIMESTAMP(DATE_SUB(CURDATE(), INTERVAL 1 DAY), '21:00:00')
          ),
          INTERVAL ? DAY
        ),
        '%Y-%m-%d %H:%i:%s'
      ) AS start_time`,
      [days - 1],
    );

    const start = String(startRows[0]?.start_time || "");
    const [rows] = await connection.execute<mysql.RowDataPacket[]>(query, [start, top, start, start]);

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
      { fields: FIELDS, rows, start, days, top },
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
