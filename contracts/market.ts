import type { MarketContext } from "./context";
/** Percent changes use percent units: 1.25 means +1.25%, never 125%. */
export type DataStatus = "demo" | "live" | "delayed" | "stale" | "unavailable";
export type AssetGroup =
  | "Equities"
  | "Rates & credit"
  | "Gold"
  | "Crypto"
  | "Cash";
export type Point = { time: string; value: number };
export type TechnicalEvidence = {
  asOf: string;
  basis: "venue-daily-bars" | "provider-adjusted-daily-bars";
  windowUnit: "calendar-days" | "sessions";
  return4w: number | null;
  return13w: number | null;
  ema20Slope5Pct: number | null;
  ema50Slope5Pct: number | null;
  ema200Slope5Pct: number | null;
  atr14Pct: number | null;
  structure: {
    state:
      | "higher-highs-higher-lows"
      | "lower-highs-lower-lows"
      | "mixed"
      | "flat"
      | "unavailable";
    highs: (Point & { confirmedAt: string })[];
    lows: (Point & { confirmedAt: string })[];
    lookbackBars: number;
    confirmationBars: 2;
  };
  classification: "uptrend" | "downtrend" | "range" | "unclear";
  reason: string;
  methodVersion: "trend-v1";
};
export type Quote = {
  price: number | null;
  change1d: number | null;
  change1w: number | null;
  asOf: string | null;
};
export type Asset = Quote & {
  technicals?: TechnicalEvidence | null;
  symbol: string;
  name: string;
  group: AssetGroup;
  sourceId: string;
  status: DataStatus;
  note: string;
  commonClose: Quote | null;
  history: Point[];
  indicators: {
    ema20: number | null;
    ema50: number | null;
    ema200: number | null;
    rsi14: number | null;
    adx14: number | null;
    atr14: number | null;
    relativeVolume: number | null;
    volatility: number | null;
  };
};
export type Source = {
  id: string;
  name: string;
  status: DataStatus;
  lastObservation: string | null;
  lastFetch: string | null;
  cadence: string;
  detail: string;
  url: string;
};
export type Snapshot = {
  id: string;
  mode: "demo" | "connected";
  generatedAt: string;
  commonCloseAt: string | null;
  assets: Asset[];
  sources: Source[];
  context?: MarketContext;
};
