import pg from "pg";

const { Pool } = pg;

export const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl:
    process.env.NODE_ENV === "production"
      ? { rejectUnauthorized: false }
      : false
});

export const DEFAULT_CONFIG = {
  env: "DEMO",
  symbol: "BTCUSDT",
  baseInterval: "15",
  auxInterval: "60",
  klineLimit: 300,

  leverage: 3,
  stakePct: 10,
  fixedQty: 0.001,

  tpPct: 1,
  slPct: 0.8,
  tpAmountUsdt: 0,
  slAmountUsdt: 0,
  reserveUtaUsdt: 0,

  tradingThreshold: 0.6,
  holdThreshold: 0.57,
  adxThreshold: 25,

  autoEnabled: false,
  allowRealOrders: false,
  forceEntry: false,

  loopSeconds: 60,
  cooldownMinutes: 60,

  lastTradeHour: -1,
  lastTradeAt: null
};

export async function initDb() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS app_config (
      id INTEGER PRIMARY KEY DEFAULT 1,
      data JSONB NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      CONSTRAINT only_one_config CHECK (id = 1)
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS run_logs (
      id BIGSERIAL PRIMARY KEY,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      level TEXT NOT NULL DEFAULT 'INFO',
      message TEXT NOT NULL,
      details JSONB
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS snapshots (
      id BIGSERIAL PRIMARY KEY,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      symbol TEXT NOT NULL,
      decision TEXT NOT NULL,
      final_decision TEXT NOT NULL,
      price DOUBLE PRECISION,
      details JSONB
    );
  `);

  const existing = await pool.query("SELECT id FROM app_config WHERE id = 1");
  if (existing.rowCount === 0) {
    await pool.query(
      "INSERT INTO app_config (id, data) VALUES (1, $1)",
      [DEFAULT_CONFIG]
    );
  }
}

export async function getConfig() {
  const result = await pool.query("SELECT data FROM app_config WHERE id = 1");
  const stored = result.rows[0]?.data || {};
  return { ...DEFAULT_CONFIG, ...stored };
}

export async function saveConfig(partial) {
  const current = await getConfig();
  const next = { ...current, ...partial };
  await pool.query(
    `
    INSERT INTO app_config (id, data, updated_at)
    VALUES (1, $1, NOW())
    ON CONFLICT (id)
    DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
    `,
    [next]
  );
  return next;
}

export async function addLog(level, message, details = null) {
  await pool.query(
    "INSERT INTO run_logs (level, message, details) VALUES ($1, $2, $3)",
    [level, message, details]
  );
}

export async function getLogs(limit = 100) {
  const result = await pool.query(
    "SELECT * FROM run_logs ORDER BY id DESC LIMIT $1",
    [limit]
  );
  return result.rows;
}

export async function saveSnapshot(snapshot) {
  await pool.query(
    `
    INSERT INTO snapshots (symbol, decision, final_decision, price, details)
    VALUES ($1, $2, $3, $4, $5)
    `,
    [
      snapshot.symbol,
      snapshot.decision,
      snapshot.finalDecision,
      snapshot.price ?? null,
      snapshot.details ?? {}
    ]
  );
}

export async function getLatestSnapshot() {
  const result = await pool.query(
    "SELECT * FROM snapshots ORDER BY id DESC LIMIT 1"
  );
  return result.rows[0] || null;
}
