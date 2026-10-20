import type { Asset, AssetGroup, Snapshot } from "./market";
const END = "2026-10-16T20:00:00Z";
// Deterministic illustration for interface evaluation. These are NOT market observations.
const rows: [string, string, AssetGroup, number, number, number][] = [
  ["SPY", "S&P 500", "Equities", 682.34, 0.64, 1.86],
  ["QQQ", "Nasdaq 100", "Equities", 612.78, 0.41, 1.22],
  ["IWM", "Russell 2000", "Equities", 242.65, -0.27, 0.83],
  ["RSP", "S&P 500 · equal weight", "Equities", 196.42, 0.93, 2.67],
  ["IEF", "7–10 year Treasuries", "Rates & credit", 97.82, 0.22, 0.74],
  ["TLT", "20+ year Treasuries", "Rates & credit", 91.36, 0.58, 1.94],
  ["LQD", "Investment-grade credit", "Rates & credit", 110.74, 0.16, 0.52],
  ["HYG", "High-yield credit", "Rates & credit", 80.28, -0.18, -0.42],
  ["GLD", "Gold", "Gold", 356.48, 0.82, 2.14],
  ["BTC", "Bitcoin", "Crypto", 107842.6, 1.26, 3.28],
  ["ETH", "Ethereum", "Crypto", 3892.41, -0.84, -1.46],
  ["SGOV", "0–3 month Treasuries", "Cash", 100.51, 0.01, 0.05],
];
const indicators = (p: number, i: number): Asset["indicators"] => ({
  ema20: p * (i === 7 || i === 10 ? 1.012 : 0.985),
  ema50: p * (i === 7 || i === 10 ? 1.026 : 0.966),
  ema200: p * (i === 7 || i === 10 ? 1.05 : 0.92),
  rsi14: Math.round((i === 7 || i === 10 ? 42 : 54) + Math.sin(i) * 8),
  adx14: 22 + (i % 8),
  atr14: p * 0.012,
  relativeVolume: 1.02 + (i % 4) * 0.13,
  volatility: 12 + i * 1.3,
});
export const demo: Snapshot = {
  id: "illustrative-2026-10-16",
  mode: "demo",
  generatedAt: END,
  commonCloseAt: END,
  assets: rows.map(([symbol, name, group, p, d, w], i) => {
    const dates: string[] = [];
    let at = new Date(END);
    while (dates.length < 65) {
      if (at.getUTCDay() !== 0 && at.getUTCDay() !== 6)
        dates.unshift(at.toISOString());
      at = new Date(at.getTime() - 86400000);
    }
    const history = dates.map((time, j) => ({
      time,
      value:
        p *
        (0.93 +
          (0.07 * j) / 64 +
          Math.sin(j * 0.63 + i) * 0.006 +
          Math.sin(j * 0.19 + i) * 0.009),
    }));
    history[64].value = p;
    history[63].value = p / (1 + d / 100);
    history[59].value = p / (1 + w / 100);
    const q = { price: p, change1d: d, change1w: w, asOf: END };
    return {
      ...q,
      symbol,
      name,
      group,
      sourceId: "illustrative",
      status: "demo" as const,
      note: "Illustrative prices, history and indicators. Not observed market data; these values are for interface evaluation only.",
      commonClose: q,
      history,
      indicators: indicators(p, i),
    };
  }),
  sources: [
    {
      id: "illustrative",
      name: "Design dataset",
      status: "demo",
      lastObservation: END,
      lastFetch: null,
      cadence: "Static illustration",
      detail:
        "All displayed figures in demo mode are synthetic. No live feed is represented.",
      url: "",
    },
  ],
};
