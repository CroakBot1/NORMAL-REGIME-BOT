import crypto from "crypto";

export const BYBIT_BASES = {
  PROD: "https://api.bybit.com",
  DEMO: "https://api-demo.bybit.com"
};

function cleanNumber(value, scale = 3) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0";
  const factor = 10 ** scale;
  return (Math.floor(n * factor) / factor).toFixed(scale).replace(/\.?0+$/, "");
}

function hmacSha256Hex(secret, msg) {
  return crypto.createHmac("sha256", secret).update(msg).digest("hex");
}

function requireKeys() {
  const apiKey = process.env.BYBIT_API_KEY;
  const apiSecret = process.env.BYBIT_API_SECRET;
  if (!apiKey || !apiSecret) {
    throw new Error("Missing BYBIT_API_KEY or BYBIT_API_SECRET env vars");
  }
  return { apiKey, apiSecret };
}

export class BybitApi {
  constructor(env = "DEMO") {
    this.env = env === "PROD" ? "PROD" : "DEMO";
    this.baseUrl = BYBIT_BASES[this.env];
    const keys = requireKeys();
    this.apiKey = keys.apiKey;
    this.apiSecret = keys.apiSecret;
    this.recvWindow = "20000";
  }

  async publicGet(path, params = {}) {
    const url = new URL(this.baseUrl + path);
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }

    const resp = await fetch(url, { method: "GET" });
    const text = await resp.text();
    if (!resp.ok) throw new Error(`Bybit public GET HTTP ${resp.status}: ${text}`);
    const json = JSON.parse(text);
    if (json.retCode !== 0) {
      throw new Error(`Bybit public retCode=${json.retCode} msg=${json.retMsg}`);
    }
    return json;
  }

  signedHeaders(payload) {
    const ts = Date.now().toString();
    const prehash = ts + this.apiKey + this.recvWindow + payload;
    const sign = hmacSha256Hex(this.apiSecret, prehash);

    return {
      "X-BAPI-API-KEY": this.apiKey,
      "X-BAPI-SIGN": sign,
      "X-BAPI-SIGN-TYPE": "2",
      "X-BAPI-TIMESTAMP": ts,
      "X-BAPI-RECV-WINDOW": this.recvWindow
    };
  }

  async signedGet(path, params = {}) {
    const url = new URL(this.baseUrl + path);
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }

    const query = url.searchParams.toString();
    const headers = this.signedHeaders(query);

    const resp = await fetch(url, { method: "GET", headers });
    const text = await resp.text();
    if (!resp.ok) throw new Error(`Bybit signed GET HTTP ${resp.status}: ${text}`);
    const json = JSON.parse(text);
    if (json.retCode !== 0) {
      throw new Error(`Bybit signed retCode=${json.retCode} msg=${json.retMsg}`);
    }
    return json;
  }

  async signedPost(path, bodyObj = {}) {
    const body = JSON.stringify(bodyObj);
    const headers = {
      ...this.signedHeaders(body),
      "Content-Type": "application/json"
    };

    const resp = await fetch(this.baseUrl + path, {
      method: "POST",
      headers,
      body
    });

    const text = await resp.text();
    if (!resp.ok) throw new Error(`Bybit signed POST HTTP ${resp.status}: ${text}`);
    const json = JSON.parse(text);
    if (json.retCode !== 0) {
      throw new Error(`Bybit signed retCode=${json.retCode} msg=${json.retMsg}`);
    }
    return json;
  }

  async getKlines(symbol, interval, limit = 300) {
    const json = await this.publicGet("/v5/market/kline", {
      category: "linear",
      symbol,
      interval,
      limit
    });

    const rows = json.result?.list || [];

    return rows
      .map((r) => ({
        startTimeMs: Number(r[0]),
        open: Number(r[1]),
        high: Number(r[2]),
        low: Number(r[3]),
        close: Number(r[4]),
        volume: Number(r[5] || 0)
      }))
      .filter((c) =>
        Number.isFinite(c.startTimeMs) &&
        Number.isFinite(c.open) &&
        Number.isFinite(c.high) &&
        Number.isFinite(c.low) &&
        Number.isFinite(c.close)
      )
      .sort((a, b) => a.startTimeMs - b.startTimeMs);
  }

  async setLeverage(symbol, leverage) {
    return this.signedPost("/v5/position/set-leverage", {
      category: "linear",
      symbol,
      buyLeverage: String(leverage),
      sellLeverage: String(leverage)
    });
  }

  async getWalletBalanceUsdt() {
    const json = await this.signedGet("/v5/account/wallet-balance", {
      accountType: "UNIFIED",
      coin: "USDT"
    });

    const account = json.result?.list?.[0];
    const usdt = account?.coin?.find((c) => c.coin === "USDT");
    return Number(
      usdt?.availableToWithdraw ??
      usdt?.walletBalance ??
      account?.totalWalletBalance ??
      0
    );
  }

  async getOpenPositionInfo(symbol) {
    const json = await this.signedGet("/v5/position/list", {
      category: "linear",
      symbol
    });

    const pos = json.result?.list?.[0];
    const size = Number(pos?.size || 0);
    return size > 0 ? pos : null;
  }

  async placeMarketOrder({
    symbol,
    side,
    qty,
    takeProfitPrice,
    stopLossPrice,
    reduceOnly = false
  }) {
    const body = {
      category: "linear",
      symbol,
      side: side === "BUY" ? "Buy" : "Sell",
      orderType: "Market",
      qty: cleanNumber(qty, 3),
      timeInForce: "IOC"
    };

    if (takeProfitPrice) body.takeProfit = cleanNumber(takeProfitPrice, 1);
    if (stopLossPrice) body.stopLoss = cleanNumber(stopLossPrice, 1);
    if (reduceOnly) body.reduceOnly = true;

    return this.signedPost("/v5/order/create", body);
  }

  async transferUtaToFunding(amount) {
    return this.signedPost("/v5/asset/transfer/inter-transfer", {
      transferId: crypto.randomUUID(),
      coin: "USDT",
      amount: cleanNumber(amount, 4),
      fromAccountType: "UNIFIED",
      toAccountType: "FUND"
    });
  }
}
