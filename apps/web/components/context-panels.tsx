"use client";
import { useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Check,
  Database,
  ExternalLink,
  FileUp,
  RefreshCw,
} from "lucide-react";
import type {
  Briefing,
  DeskAlert,
  FlowCategory,
  MarketContext,
  ReportSummary,
} from "../../../contracts/context";
import type { Snapshot } from "../../../contracts/market";
import { dateOnly, dollars, isReportSummary, yieldChange } from "@/lib/context";
import { isSnapshot, percent, stamp } from "@/lib/market";
import { IS_PAGES, researchPath, sitePath } from "@/lib/deployment";

async function api(path: string, data?: unknown) {
  const response = await fetch(
    researchPath(path, data !== undefined),
    data === undefined
      ? { cache: "no-store" }
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        },
  );
  const body = await response.json();
  if (!response.ok)
    throw new Error(body.error ?? "The research action failed.");
  return body;
}
function download(value: unknown, name: string, plainText = false) {
  const url = URL.createObjectURL(
    new Blob([plainText ? String(value) : JSON.stringify(value, null, 2)], {
      type: plainText ? "text/markdown;charset=utf-8" : "application/json",
    }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
const categories: [FlowCategory, string, string][] = [
  ["named-etf", "Named ETFs", "Individual funds"],
  ["bitcoin-etfs", "US spot Bitcoin ETFs", "US issuer basket"],
  ["ethereum-etfs", "US spot Ethereum ETFs", "US issuer basket"],
  ["gold-etfs", "Global gold ETFs", "Global fund basket"],
  ["fund-category", "ICI fund categories", "Published category totals"],
];

export function MacroPanel({
  context,
  isDemo = false,
}: {
  context?: MarketContext;
  isDemo?: boolean;
}) {
  const rows = context?.macro.series ?? [];
  return (
    <section className="panel macro-panel">
      <div className="panel-heading">
        <div>
          <h2>The rates backdrop</h2>
          <span className="subheading">
            Official daily observations · dates remain separate from prices
          </span>
        </div>
        <span className="quiet-count">Treasury</span>
      </div>
      <div className="macro-list">
        {rows.length ? (
          rows.map((s) => (
            <details className="macro-item" key={s.id}>
              <summary>
                <span>
                  {s.label}
                  <small>
                    {s.observedOn
                      ? `${dateOnly(s.observedOn)} · ${s.status}`
                      : "No source observation"}
                  </small>
                </span>
                <span className="macro-values num">
                  {s.value === null
                    ? "—"
                    : `${s.value.toFixed(2)}${s.unit === "percent" ? "%" : " bp"}`}
                  <small>{yieldChange(s.change, s.unit)}</small>
                </span>
              </summary>
              <div className="evidence-note">
                <p>{s.detail}</p>
                <p>
                  Publication time:{" "}
                  {s.publishedAt
                    ? stamp(s.publishedAt)
                    : "not supplied by this source"}
                  . Fetched {stamp(s.fetchedAt)} SGT.
                </p>
                <a href={s.sourceUrl} target="_blank" rel="noreferrer">
                  Open Treasury source <ExternalLink size={12} />
                </a>
              </div>
            </details>
          ))
        ) : (
          <p className="context-empty">
            {isDemo
              ? "This design dataset does not contain macro observations."
              : "Macro observations are not available in this snapshot."}
          </p>
        )}
      </div>
      <p className="context-footnote">
        Credit spreads, DXY and positioning remain unavailable. A successful
        fetch does not establish a publication timestamp.
      </p>
    </section>
  );
}

export function AlertsPanel({
  context,
  locked,
  onRefresh,
}: {
  context?: MarketContext;
  locked: boolean;
  onRefresh: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null),
    [error, setError] = useState("");
  const [acknowledged, setAcknowledged] = useState<string[]>([]);
  const alerts = context?.alerts ?? [];
  useEffect(() => setAcknowledged([]), [context]);
  async function acknowledge(a: DeskAlert) {
    setBusy(a.id);
    setError("");
    try {
      await api(`alerts/${a.id}/acknowledge`, {});
      setAcknowledged((ids) => [...ids, a.id]);
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to acknowledge.");
    } finally {
      setBusy(null);
    }
  }
  return (
    <section className="panel alerts-panel">
      <div className="panel-heading">
        <div>
          <h2>Research watch</h2>
          <span className="subheading">
            State changes, with the evidence attached
          </span>
        </div>
        <span className="quiet-count">
          {alerts.filter((a) => a.state === "active").length} active
        </span>
      </div>
      {error && (
        <p role="alert" className="context-error">
          {error}
        </p>
      )}
      {!alerts.length ? (
        <p className="context-empty">
          Connect a source snapshot to evaluate research watches.
        </p>
      ) : (
        alerts.map((a) => (
          <details
            className={`desk-alert ${a.state}`}
            key={a.id}
            open={a.state === "active"}
          >
            <summary>
              <span>{a.title}</span>
              <span className="alert-state">{a.state}</span>
            </summary>
            <div className="alert-body">
              <p>{a.detail}</p>
              {a.evidence.length > 0 && (
                <ul>
                  {a.evidence.map((e, i) => (
                    <li key={i}>
                      {e.label}: <span className="num">{e.value}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="alert-meta">
                <span>
                  Changed {stamp(a.changedAt)} SGT · {a.transitions} state
                  changes
                </span>
                {a.state === "active" && !IS_PAGES && (
                  <button
                    className="text-button"
                    disabled={
                      locked ||
                      busy !== null ||
                      !!a.acknowledgedAt ||
                      acknowledged.includes(a.id)
                    }
                    onClick={() => void acknowledge(a)}
                  >
                    {a.acknowledgedAt || acknowledged.includes(a.id) ? (
                      <>
                        <Check size={13} />
                        Acknowledged
                      </>
                    ) : busy === a.id ? (
                      "Saving…"
                    ) : (
                      "Acknowledge"
                    )}
                  </button>
                )}
              </div>
            </div>
          </details>
        ))
      )}
      <p className="context-footnote">
        Research thresholds are provisional. Invalid inputs suppress analytical
        watches; these states do not place orders.
      </p>
    </section>
  );
}

export function FlowPreview({
  context,
  onView,
}: {
  context?: MarketContext;
  onView: () => void;
}) {
  const series = context?.flows.series ?? [];
  return (
    <section className="panel flow-preview">
      <div className="panel-heading">
        <h2>Capital flows</h2>
        <button className="inline-link" onClick={onView}>
          Open workspace <ArrowUpRight size={12} />
        </button>
      </div>
      <div className="flow-placeholder">
        {series.length
          ? series.slice(0, 3).map((s) => (
              <div className="flow-preview-row" key={s.id}>
                <span>
                  {s.label}
                  <small>{s.scope}</small>
                </span>
                <span className="flow-line" />
                <span className="num">{dollars(s.windows[0].netFlowUsd)}</span>
                <span className="muted">{s.windows[0].status}</span>
              </div>
            ))
          : [
              ["Equity ETFs", "Individual funds"],
              ["Digital assets", "US spot ETF baskets"],
              ["Gold", "Global ETF holdings"],
            ].map(([name, scope]) => (
              <div className="flow-preview-row" key={name}>
                <span>
                  {name}
                  <small>{scope}</small>
                </span>
                <span className="flow-line" />
                <span className="num">—</span>
                <span className="muted">Not connected</span>
              </div>
            ))}
      </div>
      <div className="flow-note">
        <span className="tiny-square" />
        Published weekly periods. Overlapping scopes remain separate.
      </div>
    </section>
  );
}

type ImportPreview = {
  committed: boolean;
  inserted: number;
  unchanged: number;
  series: string[];
  incomplete: number;
};
export function FlowWorkspace({
  context,
  isDemo,
  archived,
  onRefresh,
}: {
  context?: MarketContext;
  isDemo: boolean;
  archived: boolean;
  onRefresh: () => void;
}) {
  const [weeks, setWeeks] = useState<1 | 4 | 13>(1),
    [document, setDocument] = useState<unknown>(null),
    [preview, setPreview] = useState<ImportPreview | null>(null);
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const series = context?.flows.series ?? [];
  async function select(file: File) {
    setError("");
    setMessage("");
    setPreview(null);
    setDocument(null);
    if (file.size > 900_000) {
      setError("Choose a JSON file smaller than 900 KB.");
      return;
    }
    setBusy(true);
    try {
      const parsed = JSON.parse(await file.text());
      const result = await api("flows/import", {
        document: parsed,
        commit: false,
      });
      setDocument(parsed);
      setPreview(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to read this file.");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }
  async function apply() {
    if (!document) return;
    setBusy(true);
    setError("");
    try {
      const result = await api("flows/import", { document, commit: true });
      setMessage(
        `${result.inserted} records imported; ${result.unchanged} unchanged. Revisions are retained.`,
      );
      setPreview(null);
      setDocument(null);
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flow-workspace">
      <section className="panel flows-view">
        <div className="panel-heading">
          <div>
            <h2>Follow the published record</h2>
            <span className="subheading">
              Comparable scope first. Complete periods only.
            </span>
          </div>
          <div className="pair-tabs">
            {([1, 4, 13] as const).map((w) => (
              <button
                key={w}
                className={weeks === w ? "selected" : ""}
                aria-pressed={weeks === w}
                onClick={() => setWeeks(w)}
              >
                {w} {w === 1 ? "week" : "weeks"}
              </button>
            ))}
          </div>
        </div>
        <div className="flow-table-scroll">
          <table className="flow-table">
            <thead>
              <tr>
                <th>Dataset / scope</th>
                <th>Period ending</th>
                <th className="numeric">Net flow / USD</th>
                <th className="numeric">% of starting AUM</th>
                <th>Coverage</th>
              </tr>
            </thead>
            <tbody>
              {series.map((s) => {
                const w = s.windows.find((w) => w.weeks === weeks)!;
                return (
                  <tr key={s.id}>
                    <td>
                      <strong>{s.label}</strong>
                      <small>{s.scope}</small>
                    </td>
                    <td>
                      {dateOnly(w.periodEnd)}
                      <small>
                        {weeks} published {weeks === 1 ? "week" : "weeks"}
                      </small>
                    </td>
                    <td className="numeric num">{dollars(w.netFlowUsd)}</td>
                    <td className="numeric num">{percent(w.normalizedPct)}</td>
                    <td>
                      {w.status}
                      <small>
                        {w.coveredPeriods}/{w.expectedPeriods} complete periods
                      </small>
                    </td>
                  </tr>
                );
              })}
              {categories
                .filter(([id]) => !series.some((s) => s.category === id))
                .map(([id, label, scope]) => (
                  <tr key={id}>
                    <td>
                      <strong>{label}</strong>
                      <small>{scope}</small>
                    </td>
                    <td>Not supplied</td>
                    <td className="numeric num">—</td>
                    <td className="numeric num">—</td>
                    <td>Unavailable</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        {!series.length && (
          <div className="empty-explanation">
            <Database size={20} />
            <div>
              <h3>No sourced flow records yet.</h3>
              <p>
                Import a permitted provider export to populate the view. Partial
                baskets and missing weeks do not become zero; scopes are never
                added together.
              </p>
            </div>
          </div>
        )}
        {series.map((s) => (
          <details className="flow-evidence" key={s.id}>
            <summary>
              {s.label} · evidence and revisions{" "}
              <span>revision {s.revision}</span>
            </summary>
            <div>
              <p>{s.windows.find((w) => w.weeks === weeks)?.reason}</p>
              <p>
                Constituents: {s.constituents.join(", ")}. Published{" "}
                {stamp(s.latestPublishedAt)} SGT; imported {stamp(s.importedAt)}{" "}
                SGT.
              </p>
              <p>{s.rightsNote}</p>
              <a href={s.sourceUrl} target="_blank" rel="noreferrer">
                {s.sourceName} <ExternalLink size={12} />
              </a>
            </div>
          </details>
        ))}
      </section>
      <section className="panel import-panel">
        <div className="panel-heading">
          <div>
            <h2>{IS_PAGES ? "Flow coverage" : "Add a published dataset"}</h2>
            <span className="subheading">
              {IS_PAGES
                ? "Published evidence only; no uploads on this website."
                : "Review the file before importing. Existing revisions are preserved."}
            </span>
          </div>
          <FileUp size={18} />
        </div>
        <p>
          Use the JSON template with your source’s published weekly net flows,
          exact constituents and permission to use the data. Beginning AUM is
          optional; normalization stays unavailable without it.
        </p>
        {isDemo && (
          <p className="context-footnote">
            Switch to connected data to import records into the research store.
          </p>
        )}
        {archived && (
          <p className="context-footnote">
            This snapshot is frozen. Refresh current data before importing.
          </p>
        )}
        {IS_PAGES && (
          <p className="context-footnote">
            Automatic fund-flow collection is not connected. Missing flows
            remain unavailable; price or AUM changes are not substitutes.
          </p>
        )}
        {!IS_PAGES && (
          <div className="import-actions">
            <a
              className="text-button"
              href={sitePath("/flow-import-template.json")}
              download
            >
              <ArrowDownToLine size={14} />
              Download template
            </a>
            <button
              className="primary-button"
              disabled={busy || isDemo || archived}
              onClick={() => fileRef.current?.click()}
            >
              {busy ? "Validating…" : "Choose JSON file"}
            </button>
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              aria-label="Flow import file"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void select(file);
              }}
            />
          </div>
        )}
        {error && (
          <p role="alert" className="context-error">
            {error}
          </p>
        )}
        {message && (
          <p role="status" className="import-result">
            {message}
          </p>
        )}
        {preview && (
          <div className="import-review">
            <h3>Ready to review</h3>
            <p>
              {preview.inserted} new records · {preview.unchanged} unchanged ·{" "}
              {preview.incomplete} incomplete.
            </p>
            <p>Series: {preview.series.join(", ")}</p>
            <p>
              Imported values are supplied by you; validation checks structure
              and consistency, not the publisher’s accuracy or your data rights.
            </p>
            <div className="import-actions">
              <button
                className="text-button"
                disabled={busy}
                onClick={() => {
                  setPreview(null);
                  setDocument(null);
                }}
              >
                Cancel
              </button>
              <button
                className="primary-button"
                disabled={busy}
                onClick={() => void apply()}
              >
                Import {preview.inserted} records <ArrowRight size={14} />
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

type FullReport = ReportSummary & { snapshot: Snapshot; briefing: Briefing };
function isFullReport(value: unknown): value is FullReport {
  const report = value as Partial<FullReport> | null;
  return (
    !!report &&
    isReportSummary(value) &&
    isSnapshot(report.snapshot) &&
    typeof report.briefing?.title === "string" &&
    Array.isArray(report.briefing.facts) &&
    report.briefing.facts.every(
      (f) =>
        f &&
        typeof f.text === "string" &&
        Array.isArray(f.evidence) &&
        f.evidence.every(
          (e) =>
            e &&
            typeof e.label === "string" &&
            typeof e.value === "string" &&
            typeof e.recordId === "string",
        ),
    ) &&
    Array.isArray(report.briefing.gaps) &&
    report.briefing.gaps.every((g) => typeof g === "string") &&
    (report.briefing.monitorMarkdown === undefined ||
      (typeof report.briefing.monitorMarkdown === "string" &&
        report.briefing.monitorMarkdown.length <= 200000))
  );
}
export function ReportArchive({
  snapshot,
  onOpen,
}: {
  snapshot: Snapshot;
  onOpen: (snapshot: Snapshot) => void;
}) {
  const [reports, setReports] = useState<ReportSummary[]>([]),
    [active, setActive] = useState<FullReport | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function refresh() {
    try {
      const result = await api("reports");
      if (
        !Array.isArray(result.reports) ||
        !result.reports.every(isReportSummary)
      )
        throw Error("The report index could not be validated.");
      setReports(result.reports);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reports unavailable.");
    }
  }
  useEffect(() => {
    void refresh();
  }, []);
  async function open(id: string) {
    setBusy(true);
    setError("");
    try {
      const data = await api(`reports/${id}`);
      if (!isFullReport(data))
        throw Error("The stored report could not be validated.");
      setActive(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Report unavailable.");
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      const report = await api("reports", { snapshotId: snapshot.id });
      if (!isFullReport(report))
        throw Error("The archived report could not be validated.");
      setActive(report);
      await refresh();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Unable to archive this snapshot.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="report-archive">
      <div className="panel-heading">
        <div>
          <h2>{IS_PAGES ? "Published reports" : "Durable research archive"}</h2>
          <span className="subheading">
            {IS_PAGES
              ? "Up to 30 recent captures · download reports you want to keep"
              : "Saved on this server · immutable snapshots and factual briefings"}
          </span>
        </div>
        <button
          className="text-button"
          disabled={busy}
          aria-label="Refresh report archive"
          onClick={() => void refresh()}
        >
          <RefreshCw size={14} />
        </button>
      </div>
      <div className="archive-actions">
        <p>
          {reports.length
            ? `${reports.length} recent reports available.`
            : IS_PAGES
              ? "No published reports available."
              : "No server reports saved yet."}
        </p>
        {!IS_PAGES && (
          <button
            className="primary-button"
            disabled={busy || snapshot.mode !== "connected"}
            onClick={() => void save()}
          >
            {busy ? "Working…" : "Archive source snapshot"}
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="context-error">
          {error}
        </p>
      )}
      <div className="report-list">
        {reports.map((r) => (
          <button
            className={`report-row ${active?.id === r.id ? "selected" : ""}`}
            key={r.id}
            onClick={() => void open(r.id)}
            disabled={busy}
          >
            <span>
              <strong>{r.title}</strong>
              <small>
                {r.kind === "daily" ? "Daily capture" : "Manual archive"} ·{" "}
                {stamp(r.createdAt)} SGT
              </small>
            </span>
            <ArrowUpRight size={14} />
          </button>
        ))}
      </div>
      {active && (
        <article className="saved-briefing">
          <div className="panel-heading">
            <h3>{active.briefing.title}</h3>
            <button
              className="text-button"
              onClick={() =>
                download(active, `rotation-report-${active.id}.json`)
              }
            >
              <ArrowDownToLine size={13} />
              Export report
            </button>
          </div>
          <p className="briefing-date">
            Captured {stamp(active.createdAt)} SGT. This is the evidence
            available at capture time.
          </p>
          <p className="briefing-date">
            Jev: disabled; connection approval pending. Source-based research
            only.
          </p>
          {active.briefing.monitorMarkdown && (
            <button
              className="text-button"
              onClick={() =>
                download(
                  active.briefing.monitorMarkdown,
                  `rotation-monitor-${active.id}.md`,
                  true,
                )
              }
            >
              <ArrowDownToLine size={13} />
              Export monitor report
            </button>
          )}
          <ol>
            {active.briefing.facts.map((f, i) => (
              <li key={i}>
                <p>{f.text}</p>
                <details>
                  <summary>Evidence references</summary>
                  {f.evidence.map((e, j) => (
                    <div className="briefing-reference" key={j}>
                      {e.label}: {e.value}
                      <code>{e.recordId}</code>
                    </div>
                  ))}
                </details>
              </li>
            ))}
          </ol>
          <h4>Evidence gaps</h4>
          <ul>
            {active.briefing.gaps.map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
          <div className="briefing-actions">
            <button
              className="primary-button"
              onClick={() => onOpen(active.snapshot)}
            >
              Open frozen snapshot <ArrowRight size={14} />
            </button>
            <span className="muted">
              {active.previousId
                ? "Linked to the preceding archive entry."
                : "First archive entry."}
            </span>
          </div>
        </article>
      )}
      <p className="context-footnote">
        {IS_PAGES
          ? "The publisher retains up to 30 reports while its cache survives. Save snapshot stores a separate copy in this browser. Downloads remain yours if the website changes."
          : "The archive persists across browser reloads and service restarts. Backups are still required to survive loss of the environment."}
      </p>
    </div>
  );
}
