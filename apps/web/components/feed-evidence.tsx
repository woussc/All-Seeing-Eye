"use client";
import type { Publication } from "../../../contracts/publication";
import type { Snapshot } from "@/lib/market";
import { stamp } from "@/lib/market";

export function FreshnessStrip({
  snapshot,
  publication,
  online,
  nextCheck,
}: {
  snapshot: Snapshot;
  publication: Publication | null;
  online: boolean;
  nextCheck: number;
}) {
  const populated = snapshot.assets.filter((a) => a.price !== null);
  const needsAttention = snapshot.assets.filter(
    (a) => a.status === "stale" || a.status === "unavailable",
  ).length;
  return (
    <div className="feed-strip" aria-label="Feed freshness">
      <strong>
        {online ? "Published evidence" : "Offline · retained view"}
      </strong>
      <span>Collected {stamp(snapshot.generatedAt)} SGT</span>
      <span>
        {populated.length}/12 observations · {needsAttention} need attention
      </span>
      <span>
        {publication
          ? `Collection cadence: ${publication.cadenceSeconds / 3600}h`
          : "Publication details unavailable"}
      </span>
      <span>
        {online && nextCheck > Date.now()
          ? `Next eligible check ${stamp(new Date(nextCheck).toISOString())} SGT`
          : "Checks pause when hidden or offline"}
      </span>
    </div>
  );
}

export function FeedEvidence({ publication }: { publication: Publication }) {
  const quota = publication.quota;
  return (
    <section className="panel feed-evidence">
      <div className="panel-heading">
        <div>
          <h2>Collection budget</h2>
          <span className="subheading">
            Application ceilings · captured with this publication
          </span>
        </div>
        <span className="quiet-count num">
          {quota?.globalUsed ?? "Unknown"} / {quota?.globalLimit ?? "—"}
        </span>
      </div>
      <p className="context-footnote">
        These are application request budgets, not purchased API allowances.
        Unknown accounting is never reported as zero. Source activation requires
        current verification and a persistent ledger.
      </p>
      {quota ? (
        <>
          <p className="context-footnote">
            Ledger {quota.state} at {stamp(quota.asOf)} SGT · rolling 24 hours.
          </p>
          <div className="budget-grid">
            {quota.sources.map((s) => (
              <div className="budget-source" key={s.id}>
                <strong>{s.id}</strong>
                <span className="num">
                  {s.used ?? "Unknown"} / {s.limit}
                </span>
                <span>
                  {!s.enabled
                    ? "Disabled"
                    : !s.verified || s.verificationRequired
                      ? "Verification required"
                      : s.blockedUntil
                        ? "Cooling down"
                        : "Verified policy"}
                </span>
                {s.blockedUntil && (
                  <small>Cooldown until {stamp(s.blockedUntil)} SGT</small>
                )}
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="context-empty">
          No collection accounting accompanied this capture.
        </p>
      )}
      <h3>Changes since prior publication</h3>
      <p className="context-footnote">
        Recorded quote, source-status and macro changes. Price movements do not
        establish capital flows.
      </p>
      {publication.changes.length ? (
        <ol className="feed-changes">
          {publication.changes.map((c, i) => (
            <li key={`${c.kind}-${i}`}>
              <strong>{c.label}</strong>
              <span>
                <span className="num">{c.before}</span> →{" "}
                <span className="num">{c.after}</span>
              </span>
              <small>
                {stamp(c.asOf)} SGT ·{" "}
                {c.kind === "price"
                  ? "quote observation"
                  : "recorded at capture"}
              </small>
            </li>
          ))}
        </ol>
      ) : (
        <p className="context-empty">
          No comparable material record changes. This can be the first
          publication or an unchanged capture.
        </p>
      )}
    </section>
  );
}
