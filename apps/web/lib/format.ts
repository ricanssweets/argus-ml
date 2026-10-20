/** Display formatting rules per ARCHITECTURE.md §4:
 *  probabilities → whole percents (73%), expected returns → one decimal (+6.4%).
 *
 * UNIT CONTRACT — the single most common source of 100× display errors:
 *  - `pct`, `ret1`, `ratio1`, `sharpe1` take FRACTIONS (0.064 → "+6.4%").
 *  - `retPct1` takes an ALREADY-PERCENT value. The live API's `change_pct` is
 *    a percent: services/ml/app/routers/assets.py does
 *    `round((price - prev) / prev * 100, 2)`, so +1.23 arrives as `1.23`.
 *    Passing that to `ret1` renders "+123.0%" — never do it.
 * Every formatter maps null / undefined / NaN / ±Infinity to "—", so a missing
 * API field can never throw and can never be plotted as NaN.
 */

export const DISCLAIMER =
  "Probabilistic estimate. Not a guarantee of future performance.";

function missing(x: unknown): boolean {
  return typeof x !== "number" || !Number.isFinite(x);
}

/** 0.73 → "73%" (input is a fraction in [0, 1]). */
export function pct(x: number | null | undefined): string {
  if (missing(x) || Math.abs(x as number) > 1.5) return "—";
  return `${Math.round((x as number) * 100)}%`;
}

/** 0.064 → "+6.4%" (expected returns as FRACTIONS, one decimal). */
export function ret1(x: number | null | undefined): string {
  if (missing(x)) return "—";
  const v = (x as number) * 100;
  // Beyond ±10,000% the value is almost certainly a unit error, not a return.
  if (Math.abs(v) > 10000) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/**
 * 1.23 → "+1.2%" for values the API already sends as a percent
 * (Asset.change_pct). Guards a single-session move beyond ±100% as a unit error.
 */
export function retPct1(x: number | null | undefined): string {
  if (missing(x)) return "—";
  const v = x as number;
  if (Math.abs(v) > 100) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/** 2.83 → "2.8x" */
export function ratio1(x: number | null | undefined): string {
  if (missing(x)) return "—";
  return `${(x as number).toFixed(1)}x`;
}

/** 0–100 score → "82" */
export function score0(x: number | null | undefined): string {
  if (missing(x)) return "—";
  return `${Math.round(x as number)}`;
}

/** 0.212 → "0.212" for model metrics that the API may omit entirely. */
export function metric3(x: number | null | undefined): string {
  if (missing(x)) return "—";
  return (x as number).toFixed(3);
}

/** 2 → "2.00" for non-negative ratios (Sharpe, Sortino, Beta, HHI). */
export function num2(x: number | null | undefined): string {
  if (missing(x)) return "—";
  return (x as number).toFixed(2);
}

/** 12482 → "12,482" for counts the API may omit. */
export function count0(x: number | null | undefined): string {
  if (missing(x)) return "—";
  return Math.round(x as number).toLocaleString("en-US");
}

export function money(x: number | null | undefined): string {
  if (missing(x)) return "—";
  const v = x as number;
  if (Math.abs(v) >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return `$${v.toFixed(2)}`;
}

/** 178.42 → "$178.42"; null → "—" (Asset.price is nullable in the live API). */
export function usd2(x: number | null | undefined): string {
  if (missing(x)) return "—";
  return `$${(x as number).toFixed(2)}`;
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
