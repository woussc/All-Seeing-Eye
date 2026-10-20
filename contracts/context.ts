export type MacroSeries = {
  id: string;
  label: string;
  unit: "percent" | "basis-points";
  value: number | null;
  previous: number | null;
  change: number | null;
  observedOn: string | null;
  previousOn: string | null;
  publishedAt: string | null;
  fetchedAt: string | null;
  status: "delayed" | "stale" | "unavailable";
  sourceUrl: string;
  detail: string;
  history: { date: string; value: number }[];
};
export type FlowCategory =
  | "named-etf"
  | "bitcoin-etfs"
  | "ethereum-etfs"
  | "gold-etfs"
  | "fund-category";
export type FlowWindow = {
  weeks: 1 | 4 | 13;
  periodStart: string | null;
  periodEnd: string | null;
  netFlowUsd: number | null;
  normalizedPct: number | null;
  coveredPeriods: number;
  expectedPeriods: number;
  status: "complete" | "incomplete" | "stale" | "unavailable";
  reason: string;
  recordIds: string[];
};
export type FlowSeries = {
  id: string;
  label: string;
  category: FlowCategory;
  scope: string;
  sourceName: string;
  sourceUrl: string;
  rightsNote: string;
  latestPublishedAt: string;
  importedAt: string;
  revision: number;
  constituents: string[];
  windows: FlowWindow[];
};
export type Evidence = { label: string; value: string; recordId: string };
export type DeskAlert = {
  id: string;
  ruleId: string;
  title: string;
  detail: string;
  severity: "watch" | "data";
  state: "active" | "resolved" | "suppressed";
  firstSeen: string;
  lastSeen: string;
  changedAt: string;
  acknowledgedAt: string | null;
  evidence: Evidence[];
  transitions: number;
};
export type MarketContext = {
  generatedAt: string;
  macro: { generatedAt: string; series: MacroSeries[] };
  flows: { series: FlowSeries[]; recordCount: number };
  alerts: DeskAlert[];
};
export type ReportSummary = {
  id: string;
  kind: "manual" | "daily";
  reportDate: string;
  createdAt: string;
  snapshotId: string;
  digest: string;
  title: string;
  previousId: string | null;
};
export type Briefing = {
  title: string;
  facts: { text: string; evidence: Evidence[] }[];
  gaps: string[];
  monitorMarkdown?: string;
};
