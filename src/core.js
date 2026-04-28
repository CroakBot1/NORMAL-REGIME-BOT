export const TradeDecision = {
  BUY: "BUY",
  SELL: "SELL",
  HOLD: "HOLD"
};

export const FinalDecision = {
  ALLOW_LONG: "ALLOW_LONG",
  ALLOW_SHORT: "ALLOW_SHORT",
  HOLD: "HOLD"
};

function safeDiv(a, b, fallback = 0) {
  return Math.abs(b) < 1e-12 ? fallback : a / b;
}

function avg(values) {
  if (!values.length) return 0;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function ema(values, period) {
  if (!values.length) return [];
  const k = 2 / (period + 1);
  const out = [];
  let prev = values[0];
  for (const v of values) {
    prev = v * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

function rma(values, period) {
  if (!values.length) return [];
  const out = [];
  let prev = avg(values.slice(0, period));
  for (let i = 0; i < values.length; i++) {
    if (i < period) {
      out.push(avg(values.slice(0, i + 1)));
    } else {
      prev = (prev * (period - 1) + values[i]) / period;
      out.push(prev);
    }
  }
  return out;
}

export function computeIndicators(candles) {
  const closes = candles.map((c) => c.close);
  const highs = candles.map((c) => c.high);
  const lows = candles.map((c) => c.low);
  const volumes = candles.map((c) => c.volume || 0);

  const emaFast = ema(closes, 12);
  const emaSlow = ema(closes, 26);
  const macd = closes.map((_, i) => (emaFast[i] || 0) - (emaSlow[i] || 0));
  const macdSignal = ema(macd, 9);
  const macdHist = macd.map((v, i) => v - (macdSignal[i] || 0));

  const gains = [];
  const losses = [];
  for (let i = 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    gains.push(Math.max(0, diff));
    losses.push(Math.max(0, -diff));
  }

  const avgGain = rma(gains, 14);
  const avgLoss = rma(losses, 14);
  const rsi = closes.map((_, i) => {
    if (i === 0) return 50;
    const g = avgGain[i - 1] || 0;
    const l = avgLoss[i - 1] || 0;
    if (l === 0) return 100;
    const rs = g / l;
    return 100 - 100 / (1 + rs);
  });

  const tr = [];
  for (let i = 0; i < candles.length; i++) {
    if (i === 0) {
      tr.push(highs[i] - lows[i]);
    } else {
      tr.push(
        Math.max(
          highs[i] - lows[i],
          Math.abs(highs[i] - closes[i - 1]),
          Math.abs(lows[i] - closes[i - 1])
        )
      );
    }
  }
  const atr = rma(tr, 14);

  const bbMid = [];
  const bbStd = [];
  const bbUpper = [];
  const bbLower = [];
  const bbPos = [];
  const bbWidth = [];

  for (let i = 0; i < closes.length; i++) {
    const start = Math.max(0, i - 19);
    const slice = closes.slice(start, i + 1);
    const m = avg(slice);
    const variance = avg(slice.map((v) => (v - m) ** 2));
    const sd = Math.sqrt(variance);
    const up = m + 2 * sd;
    const lo = m - 2 * sd;
    bbMid.push(m);
    bbStd.push(sd);
    bbUpper.push(up);
    bbLower.push(lo);
    bbPos.push(safeDiv(closes[i] - lo, up - lo, 0.5));
    bbWidth.push(safeDiv(up - lo, closes[i], 0));
  }

  const logRet1 = closes.map((c, i) =>
    i === 0 ? 0 : Math.log(safeDiv(c, closes[i - 1], 1))
  );

  const volumeMean = avg(volumes.slice(-30));
  const volumeStd = Math.sqrt(avg(volumes.slice(-30).map((v) => (v - volumeMean) ** 2))) || 1;
  const volZ = volumes.map((v) => (v - volumeMean) / volumeStd);

  const i = candles.length - 1;
  const latest = candles[i];

  return {
    emaFast: emaFast[i],
    emaSlow: emaSlow[i],
    emaFastRel: safeDiv(emaFast[i] - latest.close, latest.close),
    emaSlowRel: safeDiv(emaSlow[i] - latest.close, latest.close),
    rsi: rsi[i],
    macdRel: safeDiv(macd[i], latest.close),
    macdSignalRel: safeDiv(macdSignal[i], latest.close),
    macdHistRel: safeDiv(macdHist[i], latest.close),
    atr: atr[i],
    atrRel: safeDiv(atr[i], latest.close),
    bbPos: bbPos[i],
    bbWidth: bbWidth[i],
    logRet1: logRet1[i],
    hlPct: safeDiv(latest.high - latest.low, latest.close),
    ocPct: safeDiv(latest.close - latest.open, latest.open),
    volZ: volZ[i]
  };
}

export function rawStrategyDecision(baseCandles, auxCandles, cfg) {
  const base = computeIndicators(baseCandles);
  const aux = computeIndicators(auxCandles);
  const latest = baseCandles[baseCandles.length - 1];

  let buyScore = 0;
  let sellScore = 0;

  if (base.emaFast > base.emaSlow) buyScore += 0.22;
  else sellScore += 0.22;

  if (aux.emaFast > aux.emaSlow) buyScore += 0.18;
  else sellScore += 0.18;

  if (base.rsi < 35) buyScore += 0.2;
  if (base.rsi > 65) sellScore += 0.2;

  if (base.macdHistRel > 0) buyScore += 0.14;
  if (base.macdHistRel < 0) sellScore += 0.14;

  if (base.bbPos < 0.25) buyScore += 0.12;
  if (base.bbPos > 0.75) sellScore += 0.12;

  if (latest.close > latest.open) buyScore += 0.08;
  if (latest.close < latest.open) sellScore += 0.08;

  if (base.volZ > 0.75 && latest.close > latest.open) buyScore += 0.06;
  if (base.volZ > 0.75 && latest.close < latest.open) sellScore += 0.06;

  const maxScore = Math.max(buyScore, sellScore);
  let decision = TradeDecision.HOLD;
  if (cfg.forceEntry || maxScore >= cfg.tradingThreshold) {
    decision = buyScore > sellScore ? TradeDecision.BUY : TradeDecision.SELL;
  }

  if (!cfg.forceEntry && maxScore < cfg.holdThreshold) {
    decision = TradeDecision.HOLD;
  }

  const regime = evaluateRegime(base, aux);

  if (!cfg.forceEntry && regime !== "NORMAL") {
    decision = TradeDecision.HOLD;
  }

  return {
    decision,
    buyScore,
    sellScore,
    confidence: maxScore,
    regime,
    indicators: { base, aux }
  };
}

export function evaluateRegime(base, aux) {
  if (base.atrRel > 0.05 || aux.bbWidth > 0.12) return "PANIC";
  if (base.rsi > 80 || base.rsi < 20) return "EXTREME";
  return "NORMAL";
}

export function computeAdx(candles, period = 14) {
  if (candles.length < period * 2 + 1) {
    return { adx: 0, plusDi: 0, minusDi: 0, isValid: false };
  }

  const trs = [];
  const plusDMs = [];
  const minusDMs = [];

  for (let i = 1; i < candles.length; i++) {
    const curr = candles[i];
    const prev = candles[i - 1];

    const tr = Math.max(
      curr.high - curr.low,
      Math.abs(curr.high - prev.close),
      Math.abs(curr.low - prev.close)
    );

    const upMove = curr.high - prev.high;
    const downMove = prev.low - curr.low;

    trs.push(tr);
    plusDMs.push(upMove > downMove && upMove > 0 ? upMove : 0);
    minusDMs.push(downMove > upMove && downMove > 0 ? downMove : 0);
  }

  const trRma = rma(trs, period);
  const plusRma = rma(plusDMs, period);
  const minusRma = rma(minusDMs, period);

  const dx = trRma.map((tr, i) => {
    const plusDi = safeDiv(100 * plusRma[i], tr);
    const minusDi = safeDiv(100 * minusRma[i], tr);
    return safeDiv(100 * Math.abs(plusDi - minusDi), plusDi + minusDi);
  });

  const adxSeries = rma(dx, period);
  const last = dx.length - 1;

  return {
    adx: adxSeries[last],
    plusDi: safeDiv(100 * plusRma[last], trRma[last]),
    minusDi: safeDiv(100 * minusRma[last], trRma[last]),
    isValid: true
  };
}

export function adxGate(proposedDecision, candles, threshold = 25) {
  if (proposedDecision === TradeDecision.HOLD) {
    return { decision: TradeDecision.HOLD, gate: "HOLD", metrics: computeAdx(candles) };
  }

  const metrics = computeAdx(candles);
  if (!metrics.isValid || metrics.adx < threshold) {
    return { decision: TradeDecision.HOLD, gate: "ADX_BLOCK", metrics };
  }

  if (proposedDecision === TradeDecision.BUY && metrics.plusDi > metrics.minusDi) {
    return { decision: TradeDecision.BUY, gate: "ALLOW_LONG", metrics };
  }

  if (proposedDecision === TradeDecision.SELL && metrics.minusDi > metrics.plusDi) {
    return { decision: TradeDecision.SELL, gate: "ALLOW_SHORT", metrics };
  }

  return { decision: TradeDecision.HOLD, gate: "ADX_DIRECTION_BLOCK", metrics };
}

export function computeMarketStructure(candles, pivotSize = 2) {
  if (candles.length < pivotSize * 2 + 1) {
    return neutralStructure();
  }

  const swingHighs = [];
  const swingLows = [];

  for (let i = pivotSize; i < candles.length - pivotSize; i++) {
    const currentHigh = candles[i].high;
    const currentLow = candles[i].low;

    let isSwingHigh = true;
    let isSwingLow = true;

    for (let j = 1; j <= pivotSize; j++) {
      if (candles[i - j].high > currentHigh || candles[i + j].high > currentHigh) {
        isSwingHigh = false;
      }
      if (candles[i - j].low < currentLow || candles[i + j].low < currentLow) {
        isSwingLow = false;
      }
    }

    if (isSwingHigh) swingHighs.push(currentHigh);
    if (isSwingLow) swingLows.push(currentLow);
  }

  if (swingHighs.length < 2 || swingLows.length < 2) {
    return neutralStructure();
  }

  const lastClose = candles[candles.length - 1].close;
  const shLast = swingHighs[swingHighs.length - 1];
  const slLast = swingLows[swingLows.length - 1];
  const shPrev = swingHighs[swingHighs.length - 2];
  const slPrev = swingLows[swingLows.length - 2];

  const bullish = shLast > shPrev && slLast > slPrev;
  const bearish = shLast < shPrev && slLast < slPrev;
  const range = !bullish && !bearish;

  const bosUp = lastClose > shLast;
  const bosDown = lastClose < slLast;

  return {
    isBullishStructure: bullish,
    isBearishStructure: bearish,
    isRangeStructure: range,
    distToLastSwingHigh: safeDiv(shLast - lastClose, lastClose),
    distToLastSwingLow: safeDiv(lastClose - slLast, lastClose),
    bosUp,
    bosDown,
    swingHighs,
    swingLows
  };
}

function neutralStructure() {
  return {
    isBullishStructure: false,
    isBearishStructure: false,
    isRangeStructure: true,
    distToLastSwingHigh: 0,
    distToLastSwingLow: 0,
    bosUp: false,
    bosDown: false,
    swingHighs: [],
    swingLows: []
  };
}

export function marketStructureGate(proposedDecision, candles, minBoundaryDist = 0.002) {
  if (proposedDecision === TradeDecision.HOLD) {
    return { decision: TradeDecision.HOLD, structure: computeMarketStructure(candles) };
  }

  const s = computeMarketStructure(candles);

  if (proposedDecision === TradeDecision.BUY) {
    const pass =
      (s.isBullishStructure || s.isRangeStructure) &&
      !s.bosUp &&
      !s.bosDown &&
      s.distToLastSwingHigh > minBoundaryDist;

    return { decision: pass ? TradeDecision.BUY : TradeDecision.HOLD, structure: s };
  }

  if (proposedDecision === TradeDecision.SELL) {
    const pass =
      (s.isBearishStructure || s.isRangeStructure) &&
      !s.bosUp &&
      !s.bosDown &&
      s.distToLastSwingLow > minBoundaryDist;

    return { decision: pass ? TradeDecision.SELL : TradeDecision.HOLD, structure: s };
  }

  return { decision: TradeDecision.HOLD, structure: s };
}

function candleFeatures(candles) {
  return candles.map((c, index) => {
    const body = Math.abs(c.close - c.open);
    const range = Math.max(1e-9, Math.abs(c.high - c.low));
    const upperWick = c.high - Math.max(c.open, c.close);
    const lowerWick = Math.min(c.open, c.close) - c.low;

    return {
      index,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      body,
      range,
      upperWick,
      lowerWick,
      bullish: c.close > c.open,
      bearish: c.close < c.open,
      bodyPct: body / range,
      upperWickPct: upperWick / range,
      lowerWickPct: lowerWick / range
    };
  });
}

function hasLookback(f, i, required) {
  return i - required + 1 >= 0;
}

function isShortUptrend(f, i, lookback = 3) {
  if (!hasLookback(f, i, lookback + 1)) return false;
  const currentClose = f[i].close;
  const priorClose = f[i - lookback].close;
  let higherCloses = 0;
  for (let x = i - lookback + 1; x <= i; x++) {
    if (f[x].close > f[x - 1].close) higherCloses++;
  }
  return currentClose > priorClose && higherCloses >= Math.floor(lookback / 2) + 1;
}

function isShortDowntrend(f, i, lookback = 3) {
  if (!hasLookback(f, i, lookback + 1)) return false;
  const currentClose = f[i].close;
  const priorClose = f[i - lookback].close;
  let lowerCloses = 0;
  for (let x = i - lookback + 1; x <= i; x++) {
    if (f[x].close < f[x - 1].close) lowerCloses++;
  }
  return currentClose < priorClose && lowerCloses >= Math.floor(lookback / 2) + 1;
}

function bullishContext(f, i) {
  return isShortDowntrend(f, i, 3) || isShortDowntrend(f, i, 5);
}

function bearishContext(f, i) {
  return isShortUptrend(f, i, 3) || isShortUptrend(f, i, 5);
}

function averageBody(f, i, lookback = 10) {
  if (i < 0) return 0;
  const start = Math.max(0, i - lookback + 1);
  return avg(f.slice(start, i + 1).map((x) => x.body));
}

function meaningfulBody(c) {
  return c.bodyPct >= 0.35;
}

const PatternStrength = {
  WEAK: "WEAK",
  MEDIUM: "MEDIUM",
  STRONG: "STRONG"
};

const CandlestickBias = {
  BULLISH: "BULLISH",
  BEARISH: "BEARISH",
  NEUTRAL: "NEUTRAL",
  CONFLICT: "CONFLICT",
  NONE: "NONE"
};

const PATTERN_META = {
  BULLISH_ENGULFING: ["Bullish Engulfing", CandlestickBias.BULLISH, PatternStrength.STRONG, 1],
  BEARISH_ENGULFING: ["Bearish Engulfing", CandlestickBias.BEARISH, PatternStrength.STRONG, 2],
  MORNING_STAR: ["Morning Star", CandlestickBias.BULLISH, PatternStrength.STRONG, 3],
  EVENING_STAR: ["Evening Star", CandlestickBias.BEARISH, PatternStrength.STRONG, 4],
  THREE_WHITE_SOLDIERS: ["Three White Soldiers", CandlestickBias.BULLISH, PatternStrength.STRONG, 7],
  THREE_BLACK_CROWS: ["Three Black Crows", CandlestickBias.BEARISH, PatternStrength.STRONG, 8],
  PIERCING_PATTERN: ["Piercing Pattern", CandlestickBias.BULLISH, PatternStrength.MEDIUM, 13],
  DARK_CLOUD_COVER: ["Dark Cloud Cover", CandlestickBias.BEARISH, PatternStrength.MEDIUM, 14],
  TWEEZER_BOTTOM: ["Tweezer Bottom", CandlestickBias.BULLISH, PatternStrength.MEDIUM, 15],
  TWEEZER_TOP: ["Tweezer Top", CandlestickBias.BEARISH, PatternStrength.MEDIUM, 16],
  BULLISH_HARAMI: ["Bullish Harami", CandlestickBias.BULLISH, PatternStrength.MEDIUM, 18],
  BEARISH_HARAMI: ["Bearish Harami", CandlestickBias.BEARISH, PatternStrength.MEDIUM, 18],
  HAMMER: ["Hammer", CandlestickBias.BULLISH, PatternStrength.MEDIUM, 19],
  INVERTED_HAMMER: ["Inverted Hammer", CandlestickBias.BULLISH, PatternStrength.MEDIUM, 20],
  HANGING_MAN: ["Hanging Man", CandlestickBias.BEARISH, PatternStrength.MEDIUM, 21],
  SHOOTING_STAR: ["Shooting Star", CandlestickBias.BEARISH, PatternStrength.MEDIUM, 22],
  DRAGONFLY_DOJI: ["Dragonfly Doji", CandlestickBias.BULLISH, PatternStrength.WEAK, 23],
  GRAVESTONE_DOJI: ["Gravestone Doji", CandlestickBias.BEARISH, PatternStrength.WEAK, 24],
  LONG_LEGGED_DOJI: ["Long-Legged Doji", CandlestickBias.NEUTRAL, PatternStrength.WEAK, 25],
  DOJI: ["Doji", CandlestickBias.NEUTRAL, PatternStrength.WEAK, 26],
  BULLISH_MARUBOZU: ["Bullish Marubozu", CandlestickBias.BULLISH, PatternStrength.WEAK, 27],
  BEARISH_MARUBOZU: ["Bearish Marubozu", CandlestickBias.BEARISH, PatternStrength.WEAK, 27],
  SPINNING_TOP: ["Spinning Top", CandlestickBias.NEUTRAL, PatternStrength.WEAK, 28],
  RISING_THREE_METHODS: ["Rising Three Methods", CandlestickBias.BULLISH, PatternStrength.WEAK, 29],
  FALLING_THREE_METHODS: ["Falling Three Methods", CandlestickBias.BEARISH, PatternStrength.WEAK, 30],
  BULLISH_BELT_HOLD: ["Bullish Belt Hold", CandlestickBias.BULLISH, PatternStrength.MEDIUM, 38],
  BEARISH_BELT_HOLD: ["Bearish Belt Hold", CandlestickBias.BEARISH, PatternStrength.MEDIUM, 38],
  BULLISH_COUNTERATTACK_LINE: ["Bullish Counterattack Line", CandlestickBias.BULLISH, PatternStrength.MEDIUM, 39],
  BEARISH_COUNTERATTACK_LINE: ["Bearish Counterattack Line", CandlestickBias.BEARISH, PatternStrength.MEDIUM, 39],
  MATCHING_LOW: ["Matching Low", CandlestickBias.BULLISH, PatternStrength.WEAK, 46],
  MATCHING_HIGH: ["Matching High", CandlestickBias.BEARISH, PatternStrength.WEAK, 47]
};

function hit(type, i) {
  const [displayName, bias, strength, priority] = PATTERN_META[type];
  return { type, displayName, bias, strength, priority, triggeredAtIndex: i };
}

export function evaluateCandlestickPatterns(candles) {
  if (candles.length < 10) {
    return {
      latestIndex: candles.length - 1,
      hits: [],
      bullishHits: [],
      bearishHits: [],
      neutralHits: [],
      topPriorityHit: null
    };
  }

  const f = candleFeatures(candles);
  const i = f.length - 1;
  const c = f[i];
  const hits = [];

  const doji = c.bodyPct <= 0.1;
  const longLeggedDoji = doji && c.upperWickPct >= 0.4 && c.lowerWickPct >= 0.4;
  const dragonflyDoji = doji && c.lowerWickPct >= 0.7 && c.upperWickPct <= 0.1;
  const gravestoneDoji = doji && c.upperWickPct >= 0.7 && c.lowerWickPct <= 0.1;
  const spinningTop = c.bodyPct <= 0.25 && c.upperWickPct >= 0.25 && c.lowerWickPct >= 0.25;

  const bodyBottom = Math.min(c.open, c.close);
  const bodyTop = Math.max(c.open, c.close);

  const hammerShape =
    c.lowerWick >= 2.5 * c.body &&
    c.upperWick <= c.body * 0.3 &&
    safeDiv(bodyBottom - c.low, c.range) > 0.6;

  const invertedShape =
    c.upperWick >= 2.5 * c.body &&
    c.lowerWick <= c.body * 0.3 &&
    safeDiv(c.high - bodyTop, c.range) > 0.6;

  if (hammerShape && bullishContext(f, i)) hits.push(hit("HAMMER", i));
  if (invertedShape && bullishContext(f, i)) hits.push(hit("INVERTED_HAMMER", i));
  if (hammerShape && bearishContext(f, i)) hits.push(hit("HANGING_MAN", i));
  if (invertedShape && bearishContext(f, i)) hits.push(hit("SHOOTING_STAR", i));

  if (dragonflyDoji) hits.push(hit("DRAGONFLY_DOJI", i));
  else if (gravestoneDoji) hits.push(hit("GRAVESTONE_DOJI", i));
  else if (longLeggedDoji) hits.push(hit("LONG_LEGGED_DOJI", i));
  else if (doji) hits.push(hit("DOJI", i));

  if (spinningTop) hits.push(hit("SPINNING_TOP", i));

  const avgBody = averageBody(f, i - 1);
  const significant = c.body >= avgBody * 2;
  if (
    c.bullish &&
    significant &&
    c.bodyPct >= 0.9 &&
    c.upperWickPct <= 0.05 &&
    c.lowerWickPct <= 0.05
  ) {
    hits.push(hit("BULLISH_MARUBOZU", i));
  }

  if (
    c.bearish &&
    significant &&
    c.bodyPct >= 0.9 &&
    c.upperWickPct <= 0.05 &&
    c.lowerWickPct <= 0.05
  ) {
    hits.push(hit("BEARISH_MARUBOZU", i));
  }

  if (hasLookback(f, i, 2)) {
    const p = f[i - 1];

    const bullishEngulfing =
      p.bearish &&
      c.bullish &&
      c.open <= p.close &&
      c.close >= p.open &&
      meaningfulBody(p) &&
      meaningfulBody(c) &&
      c.body >= averageBody(f, i - 1) * 1.3 &&
      bullishContext(f, i);

    const bearishEngulfing =
      p.bullish &&
      c.bearish &&
      c.open >= p.close &&
      c.close <= p.open &&
      meaningfulBody(p) &&
      meaningfulBody(c) &&
      c.body >= averageBody(f, i - 1) * 1.3 &&
      bearishContext(f, i);

    if (bullishEngulfing) hits.push(hit("BULLISH_ENGULFING", i));
    if (bearishEngulfing) hits.push(hit("BEARISH_ENGULFING", i));

    const piercing =
      p.bearish &&
      c.bullish &&
      c.open < p.low &&
      c.close > (p.open + p.close) / 2 &&
      c.close < p.open &&
      bullishContext(f, i);

    const darkCloud =
      p.bullish &&
      c.bearish &&
      c.open > p.high &&
      c.close < (p.open + p.close) / 2 &&
      c.close > p.open &&
      bearishContext(f, i);

    if (piercing) hits.push(hit("PIERCING_PATTERN", i));
    if (darkCloud) hits.push(hit("DARK_CLOUD_COVER", i));

    const lowTol = Math.abs(c.low - p.low) / c.close <= 0.002;
    const highTol = Math.abs(c.high - p.high) / c.close <= 0.002;

    if (p.bearish && c.bullish && lowTol && bullishContext(f, i)) {
      hits.push(hit("TWEEZER_BOTTOM", i));
    }

    if (p.bullish && c.bearish && highTol && bearishContext(f, i)) {
      hits.push(hit("TWEEZER_TOP", i));
    }

    const bullishHarami =
      p.bearish &&
      c.bullish &&
      p.bodyPct >= 0.5 &&
      c.bodyPct <= 0.45 &&
      c.open > p.close &&
      c.close < p.open &&
      bullishContext(f, i);

    const bearishHarami =
      p.bullish &&
      c.bearish &&
      p.bodyPct >= 0.5 &&
      c.bodyPct <= 0.45 &&
      c.open < p.close &&
      c.close > p.open &&
      bearishContext(f, i);

    if (bullishHarami) hits.push(hit("BULLISH_HARAMI", i));
    if (bearishHarami) hits.push(hit("BEARISH_HARAMI", i));

    if (
      p.bearish &&
      c.bearish &&
      Math.abs(c.close - p.close) / c.close <= 0.002
    ) {
      hits.push(hit("MATCHING_LOW", i));
    }

    if (
      p.bullish &&
      c.bullish &&
      Math.abs(c.close - p.close) / c.close <= 0.002
    ) {
      hits.push(hit("MATCHING_HIGH", i));
    }

    if (
      p.bearish &&
      c.bullish &&
      Math.abs(c.close - p.close) / c.close <= 0.002 &&
      bullishContext(f, i)
    ) {
      hits.push(hit("BULLISH_COUNTERATTACK_LINE", i));
    }

    if (
      p.bullish &&
      c.bearish &&
      Math.abs(c.close - p.close) / c.close <= 0.002 &&
      bearishContext(f, i)
    ) {
      hits.push(hit("BEARISH_COUNTERATTACK_LINE", i));
    }
  }

  if (hasLookback(f, i, 3)) {
    const a = f[i - 2];
    const b = f[i - 1];

    const morningStar =
      a.bearish &&
      b.bodyPct <= 0.35 &&
      c.bullish &&
      b.close < a.close &&
      c.close > (a.open + a.close) / 2 &&
      bullishContext(f, i - 2);

    const eveningStar =
      a.bullish &&
      b.bodyPct <= 0.35 &&
      c.bearish &&
      b.close > a.close &&
      c.close < (a.open + a.close) / 2 &&
      bearishContext(f, i - 2);

    if (morningStar) hits.push(hit("MORNING_STAR", i));
    if (eveningStar) hits.push(hit("EVENING_STAR", i));

    const threeWhite =
      a.bullish &&
      b.bullish &&
      c.bullish &&
      meaningfulBody(a) &&
      meaningfulBody(b) &&
      meaningfulBody(c) &&
      b.close > a.close &&
      c.close > b.close &&
      bullishContext(f, i - 2);

    const threeBlack =
      a.bearish &&
      b.bearish &&
      c.bearish &&
      meaningfulBody(a) &&
      meaningfulBody(b) &&
      meaningfulBody(c) &&
      b.close < a.close &&
      c.close < b.close &&
      bearishContext(f, i - 2);

    if (threeWhite) hits.push(hit("THREE_WHITE_SOLDIERS", i));
    if (threeBlack) hits.push(hit("THREE_BLACK_CROWS", i));
  }

  if (hasLookback(f, i, 5)) {
    if (
      f[i - 4].bullish &&
      f[i - 3].bearish &&
      f[i - 2].bearish &&
      f[i - 1].bearish &&
      f[i].bullish &&
      f[i].close > f[i - 4].close &&
      isShortUptrend(f, i - 4)
    ) {
      hits.push(hit("RISING_THREE_METHODS", i));
    }

    if (
      f[i - 4].bearish &&
      f[i - 3].bullish &&
      f[i - 2].bullish &&
      f[i - 1].bullish &&
      f[i].bearish &&
      f[i].close < f[i - 4].close &&
      isShortDowntrend(f, i - 4)
    ) {
      hits.push(hit("FALLING_THREE_METHODS", i));
    }
  }

  if (
    c.bullish &&
    c.bodyPct >= 0.7 &&
    safeDiv(c.open - c.low, c.range) <= 0.05 &&
    bullishContext(f, i)
  ) {
    hits.push(hit("BULLISH_BELT_HOLD", i));
  }

  if (
    c.bearish &&
    c.bodyPct >= 0.7 &&
    safeDiv(c.high - c.open, c.range) <= 0.05 &&
    bearishContext(f, i)
  ) {
    hits.push(hit("BEARISH_BELT_HOLD", i));
  }

  hits.sort((a, b) => a.priority - b.priority);

  return {
    latestIndex: i,
    hits,
    bullishHits: hits.filter((x) => x.bias === CandlestickBias.BULLISH),
    bearishHits: hits.filter((x) => x.bias === CandlestickBias.BEARISH),
    neutralHits: hits.filter((x) => x.bias === CandlestickBias.NEUTRAL),
    topPriorityHit: hits[0] || null
  };
}

export function scoreCandlestickEvaluation(evaluation) {
  let bullishScore = 0;
  let bearishScore = 0;
  let neutralScore = 0;

  for (const h of evaluation.hits) {
    const weight =
      h.strength === PatternStrength.STRONG
        ? 1
        : h.strength === PatternStrength.MEDIUM
          ? 0.7
          : 0.4;

    if (h.bias === CandlestickBias.BULLISH) bullishScore += weight;
    else if (h.bias === CandlestickBias.BEARISH) bearishScore += weight;
    else if (h.bias === CandlestickBias.NEUTRAL) neutralScore += weight;
  }

  const hasConflict = bullishScore > 0 && bearishScore > 0;

  let dominantBias = CandlestickBias.NONE;
  if (hasConflict) dominantBias = CandlestickBias.CONFLICT;
  else if (bullishScore > bearishScore && bullishScore > neutralScore) dominantBias = CandlestickBias.BULLISH;
  else if (bearishScore > bullishScore && bearishScore > neutralScore) dominantBias = CandlestickBias.BEARISH;
  else if (neutralScore > 0) dominantBias = CandlestickBias.NEUTRAL;

  return { bullishScore, bearishScore, neutralScore, dominantBias, hasConflict };
}

export function candlestickGate(proposedDecision, candles) {
  const evaluation = evaluateCandlestickPatterns(candles);
  const score = scoreCandlestickEvaluation(evaluation);

  let finalDecision = FinalDecision.HOLD;

  if (
    proposedDecision === TradeDecision.BUY &&
    score.dominantBias === CandlestickBias.BULLISH &&
    !score.hasConflict
  ) {
    finalDecision = FinalDecision.ALLOW_LONG;
  }

  if (
    proposedDecision === TradeDecision.SELL &&
    score.dominantBias === CandlestickBias.BEARISH &&
    !score.hasConflict
  ) {
    finalDecision = FinalDecision.ALLOW_SHORT;
  }

  return {
    proposedDecision,
    finalDecision,
    patternEvaluation: evaluation,
    scoreSummary: score,
    logMessage: `Candle gate proposed=${proposedDecision} final=${finalDecision} bias=${score.dominantBias}`
  };
}

export function buildOrderPlan({ decision, price, cfg, balance }) {
  if (decision !== FinalDecision.ALLOW_LONG && decision !== FinalDecision.ALLOW_SHORT) {
    return null;
  }

  const side = decision === FinalDecision.ALLOW_LONG ? "BUY" : "SELL";
  const stakeUsdt = cfg.fixedQty > 0
    ? cfg.fixedQty * price
    : Math.max(0, balance - cfg.reserveUtaUsdt) * (cfg.stakePct / 100);

  const qty = cfg.fixedQty > 0 ? cfg.fixedQty : safeDiv(stakeUsdt * cfg.leverage, price);

  const takeProfitPrice =
    side === "BUY"
      ? price * (1 + cfg.tpPct / 100)
      : price * (1 - cfg.tpPct / 100);

  const stopLossPrice =
    side === "BUY"
      ? price * (1 - cfg.slPct / 100)
      : price * (1 + cfg.slPct / 100);

  return {
    side,
    qty,
    takeProfitPrice,
    stopLossPrice,
    stakeUsdt
  };
}
