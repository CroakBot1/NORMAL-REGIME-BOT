import { BybitApi } from "./bybit.js";
import {
  adxGate,
  buildOrderPlan,
  candlestickGate,
  marketStructureGate,
  rawStrategyDecision,
  TradeDecision
} from "./core.js";
import { addLog, getConfig, saveConfig, saveSnapshot } from "./db.js";

function currentHourBucket() {
  return Math.floor(Date.now() / 3600000);
}

function cooldownOk(cfg) {
  if (!cfg.lastTradeAt) return true;
  const last = new Date(cfg.lastTradeAt).getTime();
  const elapsedMs = Date.now() - last;
  return elapsedMs >= cfg.cooldownMinutes * 60 * 1000;
}

export async function runOnce({ placeOrder = false, source = "manual" } = {}) {
  const cfg = await getConfig();
  const api = new BybitApi(cfg.env);

  const baseCandles = await api.getKlines(cfg.symbol, cfg.baseInterval, cfg.klineLimit);
  const auxCandles = await api.getKlines(cfg.symbol, cfg.auxInterval, cfg.klineLimit);

  if (baseCandles.length < 60 || auxCandles.length < 60) {
    throw new Error("Not enough candles returned from Bybit");
  }

  const latest = baseCandles[baseCandles.length - 1];
  const raw = rawStrategyDecision(baseCandles, auxCandles, cfg);

  const ms = marketStructureGate(raw.decision, baseCandles);
  const adx = adxGate(ms.decision, baseCandles, cfg.adxThreshold);
  const candle = candlestickGate(adx.decision, baseCandles);

  let finalDecision = candle.finalDecision;
  let order = null;
  let orderResponse = null;
  let balance = null;
  let blockedReason = null;

  const position = await api.getOpenPositionInfo(cfg.symbol).catch((err) => {
    blockedReason = `Position check failed: ${err.message}`;
    return null;
  });

  if (position) {
    finalDecision = "HOLD";
    blockedReason = "Existing open position detected";
  }

  if (!cooldownOk(cfg) && !cfg.forceEntry) {
    finalDecision = "HOLD";
    blockedReason = "Cooldown active";
  }

  if (finalDecision !== "HOLD") {
    balance = await api.getWalletBalanceUsdt();
    order = buildOrderPlan({
      decision: finalDecision,
      price: latest.close,
      cfg,
      balance
    });
  }

  const shouldPlace =
    placeOrder &&
    cfg.autoEnabled &&
    cfg.allowRealOrders &&
    order &&
    !blockedReason;

  if (shouldPlace) {
    await api.setLeverage(cfg.symbol, cfg.leverage).catch(() => null);

    orderResponse = await api.placeMarketOrder({
      symbol: cfg.symbol,
      side: order.side,
      qty: order.qty,
      takeProfitPrice: order.takeProfitPrice,
      stopLossPrice: order.stopLossPrice
    });

    await saveConfig({
      lastTradeHour: currentHourBucket(),
      lastTradeAt: new Date().toISOString()
    });

    await addLog("TRADE", `Placed ${order.side} ${cfg.symbol}`, {
      order,
      orderResponse
    });
  } else if (order && !cfg.allowRealOrders) {
    blockedReason = "Real orders disabled; scan only";
  } else if (order && !placeOrder) {
    blockedReason = "Manual scan only";
  }

  const snapshot = {
    source,
    symbol: cfg.symbol,
    price: latest.close,
    decision: raw.decision,
    finalDecision,
    order,
    orderResponse,
    blockedReason,
    details: {
      cfg: {
        ...cfg,
        apiKeyHidden: true
      },
      raw,
      marketStructureGate: ms,
      adxGate: adx,
      candlestickGate: candle,
      balance,
      position
    }
  };

  await saveSnapshot(snapshot);
  await addLog("INFO", `Run complete: ${cfg.symbol} final=${finalDecision}`, snapshot);

  return snapshot;
}
