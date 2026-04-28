import "dotenv/config";
import express from "express";
import path from "path";
import { fileURLToPath } from "url";
import { initDb, getConfig, saveConfig, getLogs, getLatestSnapshot } from "./db.js";
import { runOnce } from "./engine.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function requireAdmin(req, res, next) {
  const expected = process.env.ADMIN_TOKEN;
  if (!expected) {
    return res.status(500).json({ error: "ADMIN_TOKEN is not configured" });
  }

  const got =
    req.headers["x-admin-token"] ||
    req.query.token ||
    req.body?.token;

  if (got !== expected) {
    return res.status(401).json({ error: "Unauthorized" });
  }

  next();
}

await initDb();

const app = express();
app.use(express.json({ limit: "1mb" }));
app.use(express.static(path.join(__dirname, "../public")));

app.get("/health", (_, res) => {
  res.json({ ok: true, service: "panic-trader-web" });
});

app.get("/api/state", requireAdmin, async (_, res) => {
  const [config, latest, logs] = await Promise.all([
    getConfig(),
    getLatestSnapshot(),
    getLogs(80)
  ]);

  res.json({
    config,
    latest,
    logs
  });
});

app.post("/api/config", requireAdmin, async (req, res) => {
  const allowed = [
    "env",
    "symbol",
    "baseInterval",
    "auxInterval",
    "klineLimit",
    "leverage",
    "stakePct",
    "fixedQty",
    "tpPct",
    "slPct",
    "tpAmountUsdt",
    "slAmountUsdt",
    "reserveUtaUsdt",
    "tradingThreshold",
    "holdThreshold",
    "adxThreshold",
    "autoEnabled",
    "allowRealOrders",
    "forceEntry",
    "loopSeconds",
    "cooldownMinutes"
  ];

  const patch = {};
  for (const k of allowed) {
    if (Object.prototype.hasOwnProperty.call(req.body, k)) {
      patch[k] = req.body[k];
    }
  }

  if (patch.symbol) patch.symbol = String(patch.symbol).trim().toUpperCase();
  if (patch.env && !["DEMO", "PROD"].includes(patch.env)) patch.env = "DEMO";

  const numericKeys = [
    "klineLimit",
    "leverage",
    "stakePct",
    "fixedQty",
    "tpPct",
    "slPct",
    "tpAmountUsdt",
    "slAmountUsdt",
    "reserveUtaUsdt",
    "tradingThreshold",
    "holdThreshold",
    "adxThreshold",
    "loopSeconds",
    "cooldownMinutes"
  ];

  for (const k of numericKeys) {
    if (k in patch) patch[k] = Number(patch[k]);
  }

  for (const k of ["autoEnabled", "allowRealOrders", "forceEntry"]) {
    if (k in patch) patch[k] = Boolean(patch[k]);
  }

  const config = await saveConfig(patch);
  res.json({ ok: true, config });
});

app.post("/api/run-once", requireAdmin, async (_, res) => {
  try {
    const result = await runOnce({ placeOrder: false, source: "manual" });
    res.json({ ok: true, result });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.post("/api/run-order", requireAdmin, async (_, res) => {
  try {
    const result = await runOnce({ placeOrder: true, source: "manual_order" });
    res.json({ ok: true, result });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

const port = Number(process.env.PORT || 3000);
app.listen(port, () => {
  console.log(`panic-trader web listening on :${port}`);
});
