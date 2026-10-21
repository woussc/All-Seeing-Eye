export type QuotaStatus = {
  state: "available" | "unavailable";
  asOf: string;
  windowSeconds: number;
  globalLimit: number;
  globalUsed: number | null;
  sources: {
    id: string;
    enabled: boolean;
    verified: boolean;
    used: number | null;
    limit: number;
    remaining: number | null;
    nextCapacityAt: string | null;
    blockedUntil: string | null;
    verificationRequired: boolean;
  }[];
};
export type Publication = {
  version: 2;
  generatedAt: string;
  cadenceSeconds: number;
  overdueAfterSeconds: number;
  schedule: string;
  expectedCadence: string;
  availableAssets: number;
  freshAssetsAtCapture: number;
  reportCount: number;
  retentionLimit: number;
  quota: QuotaStatus | null;
  changes: {
    kind: "price" | "health" | "macro";
    label: string;
    before: string;
    after: string;
    asOf: string;
  }[];
};
export type FeedManifest = {
  version: 1;
  generation: string;
  generatedAt: string;
  snapshotPath: string;
  snapshotSha256: string;
  publicationPath: string;
  publicationSha256: string;
  pollAfterSeconds: number;
};
