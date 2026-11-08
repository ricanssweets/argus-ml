/**
 * Build-time API configuration.
 *
 * HONESTY RULE: in a production build we never silently fall back to
 * `http://localhost:8000`. Doing so made every fetch hit the *visitor's* own
 * machine, fail, and get replaced by synthetic demo data with no visible
 * signal. Instead a missing NEXT_PUBLIC_ARGUS_API_URL in production yields
 * `API_BASE === null` plus a machine-readable `API_CONFIG_ERROR` that the root
 * layout renders as a visible configuration warning.
 *
 * `NEXT_PUBLIC_*` values are inlined by Next.js at build time, so this module
 * resolves at build (and is stable at runtime).
 */
import type { DataError } from "./types";

const raw = (process.env.NEXT_PUBLIC_ARGUS_API_URL ?? "").trim();

export const IS_PRODUCTION = process.env.NODE_ENV === "production";

/** Trailing slashes removed; null means "no backend configured". */
export const API_BASE: string | null = raw
  ? raw.replace(/\/+$/, "")
  : IS_PRODUCTION
    ? null
    : "http://localhost:8000";

/** Non-null only when the build has no usable API base. */
export const API_CONFIG_ERROR: DataError | null = API_BASE
  ? null
  : {
      kind: "config",
      message:
        "NEXT_PUBLIC_ARGUS_API_URL was not set when this production build was " +
        "created, so no backend can be reached. Refusing to silently default " +
        "to localhost:8000 (that would query the visitor's own machine).",
    };

/**
 * Cold-starting free-tier backends routinely need more than a few seconds.
 * 2,500 ms produced timeouts that were indistinguishable from "no data";
 * 12,000 ms was the default, but the dashboard's slowest call
 * (GET /predictions?horizon=20&min_score=50) runs the live quant pipeline
 * for the whole universe and measures 18-26 s against the Render free tier,
 * so even 12 s timed out and the public site fell back to the Oct 5 demo
 * snapshot at build time. 60,000 ms covers that call with headroom and can
 * still be overridden per deployment.
 */
const rawTimeout = Number(process.env.NEXT_PUBLIC_ARGUS_TIMEOUT_MS ?? "");
export const TIMEOUT_MS =
  Number.isFinite(rawTimeout) && rawTimeout >= 1000 && rawTimeout <= 120000
    ? rawTimeout
    : 60000;
