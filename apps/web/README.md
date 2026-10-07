# ARGUS Web — Institutional AI Stock & ETF Prediction Platform

Next.js 14 (App Router) + TypeScript + Tailwind CSS + Recharts.

Design authority: `~/workspace/argus/docs/` (ARCHITECTURE.md, DATABASE_SCHEMA.md,
API_SPEC.md, MODEL_EVALUATION.md). This app implements the dashboard information
architecture from ARCHITECTURE.md §9 and the display rules from §4.

## Prerequisites

- Node.js 24 (check with `node --version`)

## Setup

```bash
cd ~/workspace/argus/apps/web
npm install
```

## Development

```bash
npm run dev
# → http://localhost:3000
```

## Production build

```bash
npm run build
npm start
```

## API wiring

The app talks to the ARGUS API at `NEXT_PUBLIC_ARGUS_API_URL` (default
`http://localhost:8000`, base path `/api/v1`). Every response follows the
envelope contract:

```json
{ "data": …, "meta": { "as_of": "…", "data_status": "CONFIRMED", "request_id": "…" } }
```

If the API is unreachable or returns an error, the typed client in
`lib/api.ts` falls back to **clearly-marked synthetic demo data**
(`lib/demo-data.ts`, deterministic seeded PRNG — nothing here is real market
data) and the UI renders a prominent **"DEMO DATA · synthetic"** badge on
every affected page.

## Display rules (locked law)

- Probabilities → whole percents (`73%`, never `73.42%`); expected returns →
  one decimal (`+6.4%`).
- Every prediction surface shows the disclaimer:
  *"Probabilistic estimate. Not a guarantee of future performance."*
  plus data as-of timestamps and `data_status`.
- Confidence (setup quality) is displayed separately from probability
  (likelihood of outcome) — never conflated.

## Structure

```
app/
  page.tsx                 Dashboard (regime banner, opportunities, model health, locked predictions)
  stock/[ticker]/page.tsx  Stock Analysis (AI score, multi-horizon table, price chart + MA + band, explanation)
  models/page.tsx          AI Models observatory (calibration curve, Brier/ROC-AUC, degradation flags)
  backtesting/page.tsx     Backtest config form + equity curve vs SPY + metrics
  market-regime/page.tsx   Current regime, drivers, 6-month history
  …                        9 stub sections (Phase-marked, clean placeholders)
components/
  ui/                      card, badge, table, button (hand-written, no shadcn CLI)
  charts/                  Recharts client components
  Sidebar.tsx / DemoBadge.tsx / StubPage.tsx
lib/
  types.ts                 Domain types mirroring API_SPEC.md
  api.ts                   Typed fetch client + demo fallback
  demo-data.ts             SYNTHETIC deterministic demo data
  format.ts                Whole-percent / one-decimal formatters + disclaimer
```

## Notes

- `/stock/[ticker]` is dynamically rendered; no static params are generated.
- Recharts components are client components (`"use client"`) inside server
  pages — no hydration workarounds needed.
