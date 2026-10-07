/** Display formatting rules per ARCHITECTURE.md §4:
 *  probabilities → whole percents (73%), expected returns → one decimal (+6.4%).
 */

export const DISCLAIMER =
  "Probabilistic estimate. Not a guarantee of future performance.";

/** 0.73 → "73%" */
export function pct(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${Math.round(x * 100)}%`;
}

/** 0.064 → "+6.4%" (expected returns, one decimal) */
export function ret1(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const v = x * 100;
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/** 2.83 → "2.8x" */
export function ratio1(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${x.toFixed(1)}x`;
}

/** 0–100 score → "82" */
export function score0(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${Math.round(x)}`;
}

export function money(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  if (Math.abs(x) >= 1e12) return `$${(x / 1e12).toFixed(2)}T`;
  if (Math.abs(x) >= 1e9) return `$${(x / 1e9).toFixed(2)}B`;
  if (Math.abs(x) >= 1e6) return `$${(x / 1e6).toFixed(1)}M`;
  return `$${x.toFixed(2)}`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    hour12: false,
  });
}
