import "dotenv/config";
import { initDb, getConfig, addLog } from "./db.js";
import { runOnce } from "./engine.js";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

await initDb();

console.log("panic-trader background worker started");

while (true) {
  let cfg = null;

  try {
    cfg = await getConfig();

    if (!cfg.autoEnabled) {
      await sleep(Math.max(15, Number(cfg.loopSeconds || 60)) * 1000);
      continue;
    }

    await runOnce({ placeOrder: true, source: "worker" });
  } catch (err) {
    console.error("worker error:", err);
    await addLog("ERROR", err.message, {
      stack: err.stack
    }).catch(() => null);
  }

  const seconds = Math.max(15, Number(cfg?.loopSeconds || 60));
  await sleep(seconds * 1000);
}
