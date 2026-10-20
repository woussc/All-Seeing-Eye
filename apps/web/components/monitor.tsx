"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Script from "next/script";
import {
  Activity,
  ArrowDown,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Database,
  ExternalLink,
  Eye,
  Layers3,
  Menu,
  RefreshCw,
  Radar,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { demo } from "@/lib/demo";
import {
  IS_PAGES,
  agePublishedSnapshot,
  marketPath,
  publicationOverdue,
  sitePath,
} from "@/lib/deployment";
import {
  condition,
  GROUPS,
  headline,
  isSnapshot,
  percent,
  price,
  quoteFor,
  ratioHistory,
  stamp,
  technicalsFor,
  usableAssets,
  type Asset,
  type AssetGroup,
  type Snapshot,
  type Source,
  type View,
} from "@/lib/market";
import { Chart } from "./chart";
import {
  AlertsPanel,
  FlowPreview,
  FlowWorkspace,
  MacroPanel,
  ReportArchive,
} from "./context-panels";
type Screen = "overview" | "flows" | "research" | "journal" | "sources";
type Saved = { snapshot: Snapshot; view: View; savedAt: string };
const nav = [
  { id: "overview" as const, label: "Overview", icon: Activity },
  { id: "flows" as const, label: "Fund flows", icon: Layers3 },
  { id: "research" as const, label: "Research", icon: Radar },
  { id: "journal" as const, label: "Journal", icon: BookOpen },
  { id: "sources" as const, label: "Sources", icon: Database },
];
const value = (n: number | null, suffix = "") =>
  n == null ? "—" : `${n.toFixed(2)}${suffix}`;
function Dot({ status }: { status: string }) {
  return <span className={`dot ${status}`} aria-hidden="true" />;
}
function Status({ status }: { status: string }) {
  return (
    <span className={`status ${status}`}>
      <Dot status={status} />
      {status === "demo"
        ? "Illustrative"
        : status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}
function IconMark() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <path
        d="M3 16 9 7h14l6 9-6 9H9Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      />
      <path
        d="M9 16 13 11h6l4 5-4 5h-6Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      />
      <path d="M16 12v8M12 16h8" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}
export function Monitor() {
  const [screen, setScreen] = useState<Screen>("overview");
  const [rawSnapshot, setSnapshot] = useState<Snapshot>(demo);
  const [publicationLoaded, setPublicationLoaded] = useState(!IS_PAGES);
  const [view, setView] = useState<View>("close");
  const [filter, setFilter] = useState<AssetGroup | "All assets">("All assets");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<"asset" | "guide" | null>(null);
  const [archived, setArchived] = useState(false);
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saved, setSaved] = useState<Saved[]>([]);
  const [sort, setSort] = useState<"default" | "up" | "down">("default");
  const [chartPair, setChartPair] = useState<"RSP/SPY" | "IWM/SPY" | "ETH/BTC">(
    "RSP/SPY",
  );
  const [range, setRange] = useState<20 | 65>(65);
  const [now, setNow] = useState<string>("");
  const snapshot =
    IS_PAGES && !archived ? agePublishedSnapshot(rawSnapshot) : rawSnapshot;
  const searchEl = useRef<HTMLInputElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);
  const refreshController = useRef<AbortController | null>(null);
  const refreshVersion = useRef(0);
  useEffect(() => {
    const tick = () =>
      setNow(
        new Intl.DateTimeFormat("en-GB", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          timeZone: "Asia/Singapore",
          hour12: false,
        }).format(new Date()),
      );
    tick();
    const id = setInterval(tick, 1000);
    try {
      const raw: unknown = JSON.parse(
        localStorage.getItem("rotation-journal") ?? "[]",
      );
      if (Array.isArray(raw))
        setSaved(
          raw
            .filter(
              (x) =>
                x &&
                isSnapshot(x.snapshot) &&
                ["latest", "close"].includes(x.view) &&
                Number.isFinite(Date.parse(x.savedAt)),
            )
            .slice(0, 12),
        );
    } catch {}
    const keys = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        setScreen("overview");
        searchEl.current?.focus();
      }
      if (e.key === "Escape") setDrawer(null);
    };
    document.addEventListener("keydown", keys);
    return () => {
      refreshController.current?.abort();
      clearInterval(id);
      document.removeEventListener("keydown", keys);
    };
  }, []);
  useEffect(() => {
    if (!drawer) return;
    const previous = document.activeElement as HTMLElement | null;
    drawerRef.current?.focus();
    const keys = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const nodes = drawerRef.current?.querySelectorAll<HTMLElement>(
        'button, a[href], input, [tabindex="0"]',
      );
      if (!nodes?.length) return;
      const first = nodes[0],
        last = nodes[nodes.length - 1];
      if (
        e.shiftKey &&
        (document.activeElement === first ||
          document.activeElement === drawerRef.current)
      ) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", keys);
    const old = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", keys);
      document.body.style.overflow = old;
      previous?.focus();
    };
  }, [drawer]);
  useEffect(() => {
    if (!notice) return;
    const id = setTimeout(() => setNotice(""), 4500);
    return () => clearTimeout(id);
  }, [notice]);
  function cancelRefresh() {
    refreshVersion.current++;
    refreshController.current?.abort();
    refreshController.current = null;
    setLoading(false);
  }
  async function connect(showNotice = true) {
    cancelRefresh();
    const version = refreshVersion.current;
    const controller = new AbortController();
    refreshController.current = controller;
    setArchived(false);
    setLoading(true);
    setError("");
    try {
      const res = await fetch(marketPath(), {
        cache: "no-store",
        signal: AbortSignal.any([
          controller.signal,
          AbortSignal.timeout(30000),
        ]),
      });
      const data = await res.json();
      if (!res.ok || !isSnapshot(data))
        throw Error(data.error ?? "Unable to validate the market snapshot.");
      if (version !== refreshVersion.current) return;
      setSnapshot(data);
      setPublicationLoaded(true);
      if (showNotice)
        setNotice(
          IS_PAGES
            ? "Published snapshot checked. Source collection runs on its daily schedule."
            : "Source check complete. Review coverage and timestamps.",
        );
    } catch (e) {
      if (version === refreshVersion.current)
        setError(e instanceof Error ? e.message : "Source refresh failed.");
    } finally {
      if (version === refreshVersion.current) {
        refreshController.current = null;
        setLoading(false);
      }
    }
  }
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const target = params.get("screen");
    if (
      ["overview", "flows", "research", "journal", "sources"].includes(
        target ?? "",
      )
    )
      setScreen(target as Screen);
    if (params.get("demo") !== "1") void connect(false);
    else setPublicationLoaded(true);
  }, []);
  useEffect(() => {
    if (snapshot.mode !== "connected" || archived) return;
    const id = setInterval(() => {
      void connect(false);
    }, 60000);
    return () => clearInterval(id);
  }, [snapshot.mode, archived]);
  const rows = useMemo(() => {
    const list = snapshot.assets.filter(
      (a) =>
        (filter === "All assets" || a.group === filter) &&
        `${a.symbol} ${a.name}`.toLowerCase().includes(search.toLowerCase()),
    );
    return sort === "default"
      ? list
      : list.sort((a, b) => {
          const av = quoteFor(a, view).change1d,
            bv = quoteFor(b, view).change1d;
          if (av == null) return 1;
          if (bv == null) return -1;
          return sort === "down" ? bv - av : av - bv;
        });
  }, [snapshot, filter, search, sort, view]);
  const asset = snapshot.assets.find((a) => a.symbol === selected);
  const isDemo = snapshot.mode === "demo";
  const count = usableAssets(snapshot, view).length;
  const trendUp = snapshot.assets.filter(
    (a) => condition(a, view) === "Uptrend",
  ).length;
  const cmp = useMemo(() => {
    const [a, b] = chartPair.split("/");
    const ah = snapshot.assets.find((x) => x.symbol === a),
      bh = snapshot.assets.find((x) => x.symbol === b);
    const ratio = ratioHistory(ah, bh)
      .filter(
        (p) =>
          view === "latest" ||
          (snapshot.commonCloseAt != null && p.time <= snapshot.commonCloseAt),
      )
      .slice(-range);
    const aligned = new Set(ratio.map((x) => x.time));
    const series = [ah, bh].flatMap((s) => {
      if (!s) return [];
      const h = s.history.filter((p) => aligned.has(p.time));
      return h.length
        ? [
            {
              name: s.symbol,
              values: h.map((p) => (p.value / h[0].value) * 100),
            },
          ]
        : [];
    });
    return {
      ratio,
      series,
      labels: ratio.map((p) =>
        new Intl.DateTimeFormat("en-GB", {
          day: "2-digit",
          month: "short",
          timeZone: "UTC",
        }).format(new Date(p.time)),
      ),
    };
  }, [snapshot, chartPair, range, view]);
  function save() {
    const next = [
      { snapshot, view, savedAt: new Date().toISOString() },
      ...saved.filter(
        (x) => !(x.snapshot.id === snapshot.id && x.view === view),
      ),
    ].slice(0, 12);
    try {
      localStorage.setItem("rotation-journal", JSON.stringify(next));
      setSaved(next);
      setNotice("Snapshot saved to your local research journal.");
    } catch {
      setError("Browser storage is full. Export this snapshot to keep a copy.");
    }
  }
  function download() {
    const blob = new Blob(
      [
        JSON.stringify(
          { snapshot, view, exportedAt: new Date().toISOString() },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `rotation-${snapshot.id}-${view}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
    setNotice("Snapshot exported with source metadata.");
  }
  function open(a: Asset) {
    setSelected(a.symbol);
    setDrawer("asset");
  }
  const title =
    screen === "overview"
      ? "Market rotation"
      : screen === "flows"
        ? "Fund flows"
        : screen === "research"
          ? "Research watch"
          : screen === "journal"
            ? "Research journal"
            : "Source health";
  if (!publicationLoaded)
    return (
      <main className="main-content" style={{ padding: "3rem", maxWidth: 720 }}>
        <h1>Market Rotation Monitor</h1>
        <p role={error ? "alert" : "status"}>
          {error
            ? "The published data could not be loaded. No sample prices are substituted."
            : "Loading the latest published snapshot…"}
        </p>
        {error && (
          <button
            className="primary-button"
            disabled={loading}
            onClick={() => void connect()}
          >
            {loading ? "Checking…" : "Retry published data"}
          </button>
        )}
      </main>
    );
  return (
    <>
      <Script
        src={sitePath("/vendor/taste-charts.js")}
        onReady={() => setReady(true)}
      />
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="rail" aria-label="Main navigation">
        <button
          className="brand-mark"
          aria-label="Market rotation overview"
          onClick={() => setScreen("overview")}
        >
          <IconMark />
        </button>
        <nav>
          {nav.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              className={screen === id ? "active" : ""}
              aria-label={label}
              aria-current={screen === id ? "page" : undefined}
              title={label}
              onClick={() => {
                setScreen(id);
                setSearch("");
              }}
            >
              <Icon size={18} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <button
          className="rail-help"
          title="Reading the monitor"
          aria-label="Reading the monitor"
          onClick={() => setDrawer("guide")}
        >
          <Eye size={18} />
          <span>Guide</span>
        </button>
      </aside>
      <div className="workspace">
        <header className="masthead">
          <div className="wordmark">
            SHUAI<span>/</span>
            <span className="workspace-name">Market Rotation Monitor</span>
          </div>
          <div className="masthead-right">
            <span className="time num">
              {now || "Singapore"}
              <span> SGT</span>
            </span>
            <span className="private-workspace">
              <span className="small-ring" />
              {IS_PAGES ? "Public research monitor" : "Personal workspace"}
            </span>
          </div>
        </header>
        <main id="main">
          <div className="page-heading">
            <div>
              <div className="breadcrumb">
                Research desk <ChevronRight size={12} />
                <span>{title}</span>
              </div>
              <h1>
                {title}
                <span className="heading-period">.</span>
              </h1>
              <p className="page-description">
                {screen === "overview"
                  ? headline(snapshot, view)
                  : screen === "flows"
                    ? "Follow complete releases, with their scope and publication dates."
                    : screen === "research"
                      ? "Rates, market participation and the evidence behind each state."
                      : screen === "journal"
                        ? "Stored source snapshots and the evidence captured with them."
                        : "Every observation has a source. Every gap stays visible."}
              </p>
            </div>
            <div className="heading-actions">
              <button className="text-button" onClick={download}>
                <ArrowDownToLine size={14} />
                Export
              </button>
              <button className="primary-button" onClick={save}>
                <BookOpen size={14} />
                Save snapshot
              </button>
            </div>
          </div>
          <div className="context-bar">
            <div className="view-tabs" aria-label="Observation window">
              <button
                className={view === "close" ? "selected" : ""}
                aria-pressed={view === "close"}
                onClick={() => setView("close")}
              >
                Common close
              </button>
              <button
                className={view === "latest" ? "selected" : ""}
                aria-pressed={view === "latest"}
                onClick={() => setView("latest")}
              >
                Latest quotes
              </button>
            </div>
            <div className="observation">
              <Clock3 size={13} />
              <span>
                {view === "close"
                  ? `${stamp(snapshot.commonCloseAt, "America/New_York")} New York`
                  : `Fetched ${stamp(snapshot.generatedAt)} SGT`}
              </span>
            </div>
            <div className="mode-controls">
              <span className="dataset-label">
                <Dot status={isDemo ? "demo" : "connected"} />
                {archived
                  ? "Saved snapshot"
                  : isDemo
                    ? "Demo dataset"
                    : IS_PAGES
                      ? "Published snapshot"
                      : "Connected sources"}
              </span>
              <button
                className="refresh"
                disabled={loading}
                onClick={() => {
                  if (isDemo) void connect();
                  else {
                    cancelRefresh();
                    setArchived(false);
                    setSnapshot(demo);
                    setError("");
                  }
                }}
              >
                {loading
                  ? "Checking sources…"
                  : isDemo
                    ? IS_PAGES
                      ? "Load published data"
                      : "Use connected data"
                    : "Explore demo"}
                <ArrowRight size={13} />
              </button>
              {!isDemo && (
                <button
                  className="icon-button"
                  title={
                    IS_PAGES ? "Check published update" : "Refresh sources"
                  }
                  aria-label={
                    IS_PAGES ? "Check published update" : "Refresh sources"
                  }
                  disabled={loading}
                  onClick={() => void connect()}
                >
                  <RefreshCw size={14} className={loading ? "spin" : ""} />
                </button>
              )}
            </div>
          </div>
          {isDemo ? (
            <div className="dataset-notice">
              <span className="notice-marker" />
              Illustrative data for design evaluation. Prices, indicators and
              chart history are synthetic.
              <button onClick={() => setDrawer("guide")}>
                How to read this <ArrowUpRight size={12} />
              </button>
            </div>
          ) : (
            <div className="dataset-notice">
              <span className="notice-marker" />
              {count} of 12 assets have a price in this view. Each source
              retains its actual observation time.
              <button onClick={() => setScreen("sources")}>
                View source health <ArrowUpRight size={12} />
              </button>
            </div>
          )}
          {IS_PAGES && !isDemo && (
            <div
              className={
                publicationOverdue(snapshot) ? "error-banner" : "dataset-notice"
              }
              role="status"
            >
              {archived
                ? "Frozen report"
                : publicationOverdue(snapshot)
                  ? "Update overdue"
                  : "Daily publication"}
              {" · "}Collected {stamp(snapshot.generatedAt)} SGT. Scheduled
              around 08:17 SGT; timing can vary. Quotes and watch states
              describe that capture. Reload checks for a published update.
            </div>
          )}
          {error && (
            <div role="alert" className="error-banner">
              {error}
              <button onClick={() => void connect()}>Retry</button>
            </div>
          )}
          {screen === "overview" && (
            <>
              <section className="compass" aria-label="Market conditions">
                {[
                  {
                    name: "Equity participation",
                    symbol: "RSP",
                    detail: "Equal weight · daily change",
                  },
                  {
                    name: "Duration / credit",
                    symbol: "TLT",
                    detail: "Long Treasuries · daily change",
                  },
                  {
                    name: "Gold",
                    symbol: "GLD",
                    detail: "Gold ETF · daily change",
                  },
                  {
                    name: "Digital assets",
                    symbol: "BTC",
                    detail: "Bitcoin · reported daily change",
                  },
                ].map(({ name, symbol, detail }) => {
                  const a = snapshot.assets.find((x) => x.symbol === symbol)!;
                  const q = quoteFor(a, view);
                  return (
                    <button
                      key={name}
                      className="compass-item"
                      onClick={() => open(a)}
                    >
                      <span className="compass-label">
                        {name}
                        <ArrowUpRight size={13} />
                      </span>
                      <span className="compass-value num">
                        {percent(q.change1d)}
                      </span>
                      <span className="compass-detail">{detail}</span>
                    </button>
                  );
                })}
              </section>
              <div className="context-links" aria-label="Research coverage">
                <button onClick={() => setScreen("research")}>
                  <span>Rates backdrop</span>
                  <strong>
                    {snapshot.context?.macro.series.filter(
                      (s) => s.value !== null,
                    ).length ?? 0}{" "}
                    / 4 available
                  </strong>
                  <ArrowUpRight size={12} />
                </button>
                <button onClick={() => setScreen("research")}>
                  <span>Research watch</span>
                  <strong>
                    {snapshot.context?.alerts.filter(
                      (a) => a.state === "active",
                    ).length ?? 0}{" "}
                    active
                  </strong>
                  <ArrowUpRight size={12} />
                </button>
                <button onClick={() => setScreen("flows")}>
                  <span>Published flows</span>
                  <strong>
                    {snapshot.context?.flows.series.length ?? 0} series
                  </strong>
                  <ArrowUpRight size={12} />
                </button>
              </div>
              <div className="overview-grid">
                <section className="panel market-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Cross-asset monitor</h2>
                      <span className="subheading">
                        Twelve references. One research view.
                      </span>
                    </div>
                    <span className="quiet-count num">{rows.length} / 12</span>
                  </div>
                  <div className="table-tools">
                    <label className="search">
                      <Search size={14} />
                      <input
                        ref={searchEl}
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="Find an asset"
                        aria-label="Find an asset"
                      />
                      <kbd>⌘ K</kbd>
                    </label>
                    <label className="filter-control">
                      <SlidersHorizontal size={13} />
                      <select
                        value={filter}
                        aria-label="Asset class"
                        onChange={(e) =>
                          setFilter(e.target.value as AssetGroup | "All assets")
                        }
                      >
                        <option>All assets</option>
                        {GROUPS.map((g) => (
                          <option key={g}>{g}</option>
                        ))}
                      </select>
                      <ChevronDown size={12} />
                    </label>
                  </div>
                  <div className="table-scroll">
                    <table className="asset-table">
                      <thead>
                        <tr>
                          <th>Asset</th>
                          <th className="numeric">Last / USD</th>
                          <th className="numeric">
                            <button
                              onClick={() =>
                                setSort(sort === "down" ? "up" : "down")
                              }
                              aria-label="Sort by daily change"
                            >
                              1 day{" "}
                              {sort !== "default" && (
                                <ArrowDown
                                  size={10}
                                  style={{
                                    transform:
                                      sort === "up"
                                        ? "rotate(180deg)"
                                        : undefined,
                                  }}
                                />
                              )}
                            </button>
                          </th>
                          <th className="numeric weekly">1 week</th>
                          <th className="trend-col">Condition</th>
                          <th className="row-tail">
                            <span className="sr-only">Details</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((a) => {
                          const q = quoteFor(a, view);
                          const state = condition(a, view);
                          return (
                            <tr
                              key={a.symbol}
                              className={
                                selected === a.symbol ? "current-row" : ""
                              }
                            >
                              <td>
                                <button
                                  className="asset-identity"
                                  onClick={() => open(a)}
                                >
                                  <span
                                    className={`asset-token ${a.group === "Crypto" ? "crypto-token" : ""}`}
                                  >
                                    {a.symbol === "BTC"
                                      ? "₿"
                                      : a.symbol === "ETH"
                                        ? "Ξ"
                                        : a.symbol.slice(0, 1)}
                                  </span>
                                  <span>
                                    <strong>{a.symbol}</strong>
                                    <span className="asset-name">{a.name}</span>
                                  </span>
                                </button>
                              </td>
                              <td className="numeric num">
                                {price(q.price)}
                                <span className="mobile-delta">
                                  {percent(q.change1w)} / week
                                </span>
                              </td>
                              <td
                                className={`numeric num daily ${q.change1d == null ? "muted" : q.change1d < 0 ? "negative" : q.change1d > 0 ? "positive" : ""}`}
                              >
                                {percent(q.change1d)}
                              </td>
                              <td className="numeric num weekly">
                                {percent(q.change1w)}
                              </td>
                              <td className="trend-col">
                                <span
                                  className={`trend ${state === "Uptrend" ? "trend-up" : state === "Downtrend" ? "trend-down" : ""}`}
                                >
                                  <span />
                                  {state}
                                </span>
                              </td>
                              <td className="row-tail">
                                <button
                                  aria-label={`Inspect ${a.symbol}`}
                                  onClick={() => open(a)}
                                >
                                  <ChevronRight size={13} />
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    {rows.length === 0 && (
                      <div className="no-results">
                        No assets match “{search}”.
                        <button
                          onClick={() => {
                            setSearch("");
                            setFilter("All assets");
                          }}
                        >
                          Clear filters
                        </button>
                      </div>
                    )}
                  </div>
                  <div className="table-foot">
                    <span>
                      <span className="tiny-square" />
                      {isDemo
                        ? "Illustrative observations"
                        : "Per-source timestamps apply"}
                    </span>
                    <span>
                      {trendUp} uptrend{trendUp !== 1 ? "s" : ""}{" "}
                      <span className="foot-divider">/</span> {count} available
                    </span>
                  </div>
                </section>
                <div className="side-stack">
                  <section className="panel relative-panel">
                    <div className="panel-heading">
                      <div>
                        <h2>Under the surface</h2>
                        <span className="subheading">
                          Is participation broadening?
                        </span>
                      </div>
                      <ArrowUpRight size={15} className="muted" />
                    </div>
                    <div className="pair-tabs">
                      {(["RSP/SPY", "IWM/SPY", "ETH/BTC"] as const).map((p) => (
                        <button
                          key={p}
                          className={chartPair === p ? "selected" : ""}
                          onClick={() => setChartPair(p)}
                        >
                          {p.replace("/", " / ")}
                        </button>
                      ))}
                    </div>
                    <div className="relative-head">
                      <div>
                        <span className="relative-number num">
                          {cmp.ratio.length
                            ? cmp.ratio[cmp.ratio.length - 1].value.toFixed(4)
                            : "—"}
                        </span>
                        <span className="relative-caption">
                          Latest aligned ratio
                        </span>
                      </div>
                      <div className="chart-range">
                        <button
                          className={range === 20 ? "selected" : ""}
                          onClick={() => setRange(20)}
                        >
                          20D
                        </button>
                        <button
                          className={range === 65 ? "selected" : ""}
                          onClick={() => setRange(65)}
                        >
                          65D
                        </button>
                      </div>
                    </div>
                    <div className="chart-legend">
                      {chartPair.split("/").map((p, i) => (
                        <span key={p}>
                          <i className={`legend-line series-${i}`} />
                          {p}
                        </span>
                      ))}
                      <span className="legend-unit">
                        Return from first shown close
                      </span>
                    </div>
                    <Chart
                      ready={ready}
                      labels={cmp.labels}
                      series={cmp.series}
                      percent
                    />
                    <div className="chart-bottom">
                      <span>
                        {isDemo ? "Illustrative" : "Verified"} daily closes
                      </span>
                      <span>{cmp.labels.length} aligned observations</span>
                    </div>
                  </section>
                  <section className="panel research-panel">
                    <div className="panel-heading">
                      <h2>On the research desk</h2>
                      <span className="quiet-count">3 prompts</span>
                    </div>
                    <Research
                      snapshot={snapshot}
                      view={view}
                      onOpen={(s) => {
                        const a = snapshot.assets.find((a) => a.symbol === s);
                        if (a) open(a);
                      }}
                    />
                  </section>
                </div>
              </div>
              <div className="bottom-grid">
                <FlowPreview
                  context={snapshot.context}
                  onView={() => setScreen("flows")}
                />
                <section className="panel coverage-panel">
                  <div className="panel-heading">
                    <h2>Evidence coverage</h2>
                    <button
                      className="inline-link"
                      onClick={() => setScreen("sources")}
                    >
                      Inspect sources <ArrowUpRight size={12} />
                    </button>
                  </div>
                  <div className="coverage-row">
                    <span>Prices in this view</span>
                    <span className="num">{count} / 12</span>
                  </div>
                  <div className="coverage-track">
                    <span style={{ width: `${(count / 12) * 100}%` }} />
                  </div>
                  <div className="coverage-row">
                    <span>Fund-level flows</span>
                    <span className="coverage-state">
                      {snapshot.context?.flows.series.length
                        ? `${snapshot.context.flows.series.length} imported series`
                        : "Not connected"}
                    </span>
                  </div>
                  <div className="coverage-row">
                    <span>Macro observations</span>
                    <span className="coverage-state">
                      {snapshot.context?.macro.series.filter(
                        (s) => s.value !== null,
                      ).length ?? 0}{" "}
                      / 4 available
                    </span>
                  </div>
                  <p>
                    {isDemo
                      ? "Demo coverage describes the illustration only. Switch datasets to inspect actual feed availability."
                      : "A successful refresh does not make an old observation current. Source dates remain visible."}
                  </p>
                </section>
              </div>
            </>
          )}
          {screen === "research" && (
            <div className="context-grid research-view">
              <MacroPanel context={snapshot.context} isDemo={isDemo} />
              <AlertsPanel
                context={snapshot.context}
                locked={archived || isDemo || IS_PAGES}
                onRefresh={() => void connect()}
              />
            </div>
          )}
          {screen === "flows" && (
            <FlowWorkspace
              context={snapshot.context}
              isDemo={isDemo}
              archived={archived}
              onRefresh={() => void connect()}
            />
          )}
          {screen === "sources" && (
            <div className="source-layout">
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>Observation sources</h2>
                    <span className="subheading">
                      Last checked {stamp(snapshot.generatedAt)} SGT
                    </span>
                  </div>
                  <button
                    className="text-button"
                    disabled={loading}
                    onClick={() => void connect()}
                  >
                    <RefreshCw size={14} />
                    {loading
                      ? "Checking…"
                      : IS_PAGES
                        ? "Check published update"
                        : "Check sources"}
                  </button>
                </div>
                <div className="source-list">
                  {snapshot.sources.map((s) => (
                    <SourceRow key={s.id} source={s} />
                  ))}
                </div>
              </section>
              <section className="panel source-policy">
                <h2>A timestamp is evidence</h2>
                <p>
                  Observation time is when a value was measured. Fetch time is
                  when this monitor received it.
                </p>
                <p>
                  US sessions and crypto run on different clocks. Common close
                  shows only values with a verified matching cutoff.
                </p>
                <h3>Additional datasets</h3>
                <ul>
                  <li>Flows and AUM can be imported with source evidence</li>
                  <li>
                    Treasury yields have a direct adapter; availability is shown
                    below
                  </li>
                  <li>
                    Credit spreads, DXY and positioning remain unconnected
                  </li>
                  <li>Macro releases and earnings calendars</li>
                </ul>
                <p>
                  These panels stay unavailable until their sources and
                  definitions are verified.
                </p>
                <MacroPanel context={snapshot.context} isDemo={isDemo} />
              </section>
            </div>
          )}
          {screen === "journal" && (
            <section className="panel journal">
              <div className="panel-heading">
                <div>
                  <h2>Saved observations</h2>
                  <span className="subheading">
                    Stored in this browser · up to 12 snapshots
                  </span>
                </div>
                <span className="quiet-count num">{saved.length}</span>
              </div>
              {!saved.length ? (
                <div className="journal-empty">
                  <BookOpen size={26} />
                  <h3>Start with what you can verify.</h3>
                  <p>
                    Save a snapshot to preserve the dataset, source status and
                    observation window you are reviewing.
                  </p>
                  <button className="primary-button" onClick={save}>
                    Save this snapshot <ArrowRight size={14} />
                  </button>
                </div>
              ) : (
                saved.map((s) => (
                  <div
                    className="journal-row"
                    key={`${s.snapshot.id}-${s.view}`}
                  >
                    <div className="journal-mark">
                      <BookOpen size={17} />
                    </div>
                    <div>
                      <h3>
                        {s.view === "close" ? "Common close" : "Latest quotes"}{" "}
                        · {stamp(s.snapshot.generatedAt)}
                      </h3>
                      <p>
                        {s.snapshot.mode === "demo"
                          ? "Illustrative dataset"
                          : "Source snapshot"}{" "}
                        · Saved {stamp(s.savedAt)} SGT
                      </p>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => {
                        cancelRefresh();
                        setArchived(true);
                        setSnapshot(s.snapshot);
                        setView(s.view);
                        setScreen("overview");
                        setNotice(
                          "Saved snapshot opened. Automatic refresh is paused.",
                        );
                      }}
                    >
                      Open <ArrowUpRight size={14} />
                    </button>
                  </div>
                ))
              )}
              <ReportArchive
                snapshot={snapshot}
                onOpen={(s) => {
                  cancelRefresh();
                  setSnapshot(s);
                  setArchived(true);
                  setView("latest");
                  setScreen("overview");
                  setNotice(
                    "Archived report opened. Automatic refresh is paused.",
                  );
                }}
              />
            </section>
          )}
          <footer className="footer">
            <span>
              <span className="footer-mark">S</span>Evidence before conviction.
            </span>
            <span>
              {isDemo
                ? "Design preview · no live market data"
                : "Research preview · source coverage varies"}
              <span className="foot-divider">/</span>Times in Singapore unless
              labeled
            </span>
          </footer>
        </main>
      </div>
      {notice && (
        <div role="status" className="toast">
          <Check size={15} />
          {notice}
        </div>
      )}
      {drawer && (
        <div className="drawer-backdrop" onClick={() => setDrawer(null)}>
          <div
            className="drawer"
            ref={drawerRef}
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-labelledby="drawer-title"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="drawer-close icon-button"
              aria-label="Close details"
              onClick={() => setDrawer(null)}
            >
              <X size={20} />
            </button>
            {drawer === "asset" && asset ? (
              <AssetDetail
                asset={asset}
                snapshot={snapshot}
                view={view}
                ready={ready}
              />
            ) : (
              <>
                <div className="drawer-kicker">Reading the monitor</div>
                <h2 id="drawer-title">Start with the evidence.</h2>
                <p className="drawer-intro">
                  This desk compares prices, participation and the quality of
                  the data behind them.
                </p>
                <div className="guide-block">
                  <h3>Two views, two questions</h3>
                  <p>
                    <strong>Common close</strong> asks what assets looked like
                    at the same completed US session. Missing aligned crypto
                    marks stay unavailable.
                  </p>
                  <p>
                    <strong>Latest quotes</strong> shows the most recent
                    verified observation from each source. Different timestamps
                    and daily windows can apply.
                  </p>
                </div>
                <div className="guide-block">
                  <h3>Demo is an illustration</h3>
                  <p>
                    Prices, history and indicators in the demo dataset are
                    synthetic. They demonstrate the interface, not a market
                    conclusion.
                  </p>
                </div>
                <div className="guide-block">
                  <h3>A gap carries information</h3>
                  <p>
                    A dash means unavailable; zero means zero. Indicators remain
                    unavailable until their data is sufficient. Flow data
                    requires a complete, comparable published period.
                  </p>
                </div>
                <div className="guide-block">
                  <h3>Inspect before interpreting</h3>
                  <p>
                    Select any asset to see its observation time, price basis
                    and source status. This preview does not submit trades.
                  </p>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
function Research({
  snapshot,
  view,
  onOpen,
}: {
  snapshot: Snapshot;
  view: View;
  onOpen: (s: string) => void;
}) {
  const get = (s: string) => snapshot.assets.find((a) => a.symbol === s)!;
  const q = (s: string) => quoteFor(get(s), view);
  const rs = q("RSP").change1w,
    sp = q("SPY").change1w;
  const relative =
    rs == null || sp == null
      ? null
      : ((1 + rs / 100) / (1 + sp / 100) - 1) * 100;
  const topics = [
    {
      title: "Participation beyond the leaders",
      symbol: "RSP",
      fact:
        relative == null
          ? "Aligned weekly returns are unavailable."
          : `RSP vs SPY: ${percent(relative)} over the reported week.`,
      next: "Check small-cap participation before calling it broad.",
    },
    {
      title: "Duration and credit tell different stories",
      symbol: "TLT",
      fact: `TLT ${percent(q("TLT").change1w)} · HYG ${percent(q("HYG").change1w)} over their reported week.`,
      next: "Credit spreads are needed to test the interpretation.",
    },
    {
      title: "Read crypto through the pair",
      symbol: "ETH",
      fact: `ETH ${percent(q("ETH").change1w)} · BTC ${percent(q("BTC").change1w)} over their reported week.`,
      next: "A complete ETF-flow period would add independent evidence.",
    },
  ];
  return (
    <div className="research-list">
      {topics.map((t, i) => (
        <button
          className="research-item"
          key={t.title}
          onClick={() => onOpen(t.symbol)}
        >
          <span>
            <strong>{t.title}</strong>
            <span className="research-fact">{t.fact}</span>
            <span className="research-next">{t.next}</span>
          </span>
          <ArrowUpRight size={13} />
        </button>
      ))}
    </div>
  );
}
function SourceRow({ source: s }: { source: Source }) {
  return (
    <div className="source-row">
      <div className="source-row-title">
        <h3>{s.name}</h3>
        <Status status={s.status} />
      </div>
      <p>{s.detail}</p>
      <div className="source-meta">
        <span>
          Observed <strong className="num">{stamp(s.lastObservation)}</strong>
        </span>
        <span>
          Fetched <strong className="num">{stamp(s.lastFetch)}</strong>
        </span>
        <span>{s.cadence}</span>
        {/^https:\/\//.test(s.url) && (
          <a href={s.url} target="_blank" rel="noreferrer">
            Source <ExternalLink size={11} />
          </a>
        )}
      </div>
    </div>
  );
}
function AssetDetail({
  asset: a,
  snapshot,
  view,
  ready,
}: {
  asset: Asset;
  snapshot: Snapshot;
  view: View;
  ready: boolean;
}) {
  const q = quoteFor(a, view);
  const technicals = technicalsFor(a, view);
  const indicatorTime = a.history.at(-1)?.time ?? null;
  const showIndicators =
    view === "latest" || (q.asOf !== null && indicatorTime === q.asOf);
  const history = useMemo(
    () =>
      a.history.filter(
        (p) =>
          view === "latest" ||
          (snapshot.commonCloseAt != null && p.time <= snapshot.commonCloseAt),
      ),
    [a, view, snapshot.commonCloseAt],
  );
  const series = useMemo(
    () =>
      history.length
        ? [
            {
              name: a.symbol,
              values: history.map((p) => (p.value / history[0].value) * 100),
            },
          ]
        : [],
    [a.symbol, history],
  );
  const labels = useMemo(
    () =>
      history.map((p) =>
        new Intl.DateTimeFormat("en-GB", {
          day: "2-digit",
          month: "short",
          timeZone: "UTC",
        }).format(new Date(p.time)),
      ),
    [history],
  );
  const s = snapshot.sources.find((s) => s.id === a.sourceId);
  return (
    <>
      <div className="drawer-kicker">
        {a.group} / {view === "close" ? "Common close" : "Latest quote"}
      </div>
      <h2 id="drawer-title">
        {a.symbol}
        <span>{a.name}</span>
      </h2>
      <div className="asset-detail-price num">
        {q.price == null ? "—" : `$${price(q.price)}`}
      </div>
      <div className="asset-detail-delta">
        <span className="num">{percent(q.change1d)}</span>
        <span>reported daily change</span>
      </div>
      <Status status={q.price == null ? "unavailable" : a.status} />
      <div className="detail-chart">
        <Chart labels={labels} series={series} ready={ready} percent />
      </div>
      <div className="detail-metadata">
        <div>
          <span>Observed</span>
          <strong className="num">
            {stamp(q.asOf)} {q.asOf ? "SGT" : ""}
          </strong>
        </div>
        <div>
          <span>Common-close cutoff</span>
          <strong className="num">
            {stamp(snapshot.commonCloseAt, "America/New_York")} NY
          </strong>
        </div>
        <div>
          <span>Price basis</span>
          <strong>USD · source-defined</strong>
        </div>
        <div>
          <span>Reported weekly change</span>
          <strong className="num">{percent(q.change1w)}</strong>
        </div>
      </div>
      <div className="guide-block">
        <h3>Technical conditions</h3>
        <p className="detail-sub">
          Completed daily bars ·{" "}
          {snapshot.mode === "demo"
            ? "illustrative"
            : stamp(showIndicators ? indicatorTime : null)}
        </p>
        <dl className="indicators">
          {[
            ["EMA 20", a.indicators.ema20],
            ["EMA 50", a.indicators.ema50],
            ["EMA 200", a.indicators.ema200],
            ["RSI 14", a.indicators.rsi14],
            ["ADX 14", a.indicators.adx14],
            ["ATR 14", a.indicators.atr14],
            ["Relative volume", a.indicators.relativeVolume],
            ["Realized vol. %", a.indicators.volatility],
          ].map(([label, n]) => (
            <div key={label as string}>
              <dt>{label}</dt>
              <dd className="num">
                {value(
                  showIndicators ? (n as number | null) : null,
                  label === "Relative volume" ? "×" : "",
                )}
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="guide-block">
        <h3>Trend evidence</h3>
        {technicals ? (
          <>
            <p className="detail-sub">
              {condition(a, view)} · {stamp(technicals.asOf)} SGT.{" "}
              {technicals.basis === "provider-adjusted-daily-bars"
                ? "Provider-adjusted completed bars"
                : "Venue-specific UTC daily bars"}
              .
            </p>
            <dl className="indicators">
              {(
                [
                  ["4-week return", technicals.return4w],
                  ["13-week return", technicals.return13w],
                  ["EMA20 / 5-bar move", technicals.ema20Slope5Pct],
                  ["EMA50 / 5-bar move", technicals.ema50Slope5Pct],
                  ["EMA200 / 5-bar move", technicals.ema200Slope5Pct],
                  ["ATR14 / close", technicals.atr14Pct],
                ] as const
              ).map(([label, n]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd className="num">
                    {label === "ATR14 / close" ? value(n, "%") : percent(n)}
                  </dd>
                </div>
              ))}
            </dl>
            <p>
              {a.status === "stale"
                ? "The source is stale; trend confirmation is withheld. "
                : ""}
              {technicals.reason}
            </p>
            <p className="detail-sub">
              {technicals.windowUnit === "sessions"
                ? "4/13 weeks = 20/65 sessions; adjusted-price context differs from the raw-price 1d/1w columns."
                : "4/13 weeks = 28/91 calendar days; this venue's UTC bars do not match the US session cutoff."}
            </p>
            <details>
              <summary>
                Confirmed price structure:{" "}
                {technicals.structure.state.replaceAll("-", " ")}
              </summary>
              <p>
                Strict pivots use two completed bars on each side within the
                last {technicals.structure.lookbackBars} bars. The most recent
                two bars cannot yet be pivots.
              </p>
              {[
                ...technicals.structure.highs.map((p) => ({
                  ...p,
                  kind: "High",
                })),
                ...technicals.structure.lows.map((p) => ({
                  ...p,
                  kind: "Low",
                })),
              ].map((p) => (
                <p className="detail-sub" key={`${p.kind}-${p.time}`}>
                  {p.kind}: {price(p.value)} · {stamp(p.time)} SGT; confirmed{" "}
                  {stamp(p.confirmedAt)} SGT.
                </p>
              ))}
            </details>
            <p className="detail-sub">
              Trend-v1 is a descriptive research rule, not a tested trading
              edge. EMA levels are not investor cost bases or guaranteed
              support.
            </p>
          </>
        ) : (
          <p>
            Detailed trend evidence is unavailable for this snapshot and
            selected cutoff. Older archives are preserved without adding later
            calculations.
          </p>
        )}
      </div>
      <div className="guide-block">
        <h3>Evidence & limitations</h3>
        <p>{a.note}</p>
        {s && (
          <>
            <p className="detail-source">
              {s.name} · {s.cadence}
            </p>
            {/^https:\/\//.test(s.url) && (
              <a
                className="inline-link"
                href={s.url}
                target="_blank"
                rel="noreferrer"
              >
                Open source <ExternalLink size={12} />
              </a>
            )}
          </>
        )}
      </div>
    </>
  );
}
