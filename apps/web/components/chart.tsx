"use client";
import { useEffect, useRef } from "react";
declare global {
  interface Window {
    Charts?: {
      line: (
        el: HTMLElement,
        data: Record<string, unknown>,
      ) => { destroy?: () => void };
    };
  }
}
export function Chart({
  labels,
  series,
  ready,
  percent = false,
}: {
  labels: string[];
  series: { name: string; values: number[] }[];
  ready: boolean;
  percent?: boolean;
}) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current || !ready || !window.Charts || !series.length) return;
    const target = el.current;
    const values = series.flatMap((s) => s.values),
      lo = Math.min(...values),
      hi = Math.max(...values);
    const margin = Math.max((hi - lo) * 0.12, 0.1);
    const handle = window.Charts.line(target, {
      labels,
      series,
      min: lo - margin,
      max: hi + margin,
      height: () => target.clientHeight,
      format: (v: number) =>
        percent ? `${(v - 100).toFixed(2)}%` : v.toFixed(4),
      tickFormat: (v: number) =>
        percent
          ? `${v - 100 > 0 ? "+" : ""}${(v - 100).toFixed(hi - lo < 4 ? 1 : 0)}%`
          : v.toFixed(3),
    });
    return () => {
      handle.destroy?.();
      target.replaceChildren();
    };
  }, [labels, series, ready, percent]);
  if (!series.length || !labels.length)
    return (
      <div className="chart-empty">
        <span>No aligned history</span>
        <p>
          Verified observations will appear here when the source is available.
        </p>
      </div>
    );
  return (
    <div ref={el} className="chart" aria-label="Historical comparison chart" />
  );
}
