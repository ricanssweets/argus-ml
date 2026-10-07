"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV: { href: string; label: string }[] = [
  { href: "/", label: "Dashboard" },
  { href: "/market-scanner", label: "Market Scanner" },
  { href: "/stock/NVDA", label: "Stock Analysis" },
  { href: "/etf", label: "ETF Analysis" },
  { href: "/predictions", label: "Predictions" },
  { href: "/signals", label: "Signals" },
  { href: "/portfolio", label: "Portfolio" },
  { href: "/backtesting", label: "Backtesting" },
  { href: "/models", label: "AI Models" },
  { href: "/market-regime", label: "Market Regime" },
  { href: "/news-sentiment", label: "News & Sentiment" },
  { href: "/alerts", label: "Alerts" },
  { href: "/research-lab", label: "Research Lab" },
  { href: "/settings", label: "Settings" },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/stock/NVDA") return pathname.startsWith("/stock");
  return pathname === href || pathname.startsWith(href + "/");
}

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="fixed inset-y-0 left-0 flex w-60 flex-col border-r border-ink-700 bg-ink-900">
      <div className="border-b border-ink-700 px-5 py-5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-sky-600 font-bold text-white">
            A
          </div>
          <div>
            <p className="text-base font-bold tracking-wide text-white">ARGUS</p>
            <p className="text-[10px] uppercase tracking-widest text-slate-500">
              Prediction Platform
            </p>
          </div>
        </div>
      </div>
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        <ul className="space-y-0.5">
          {NAV.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <li key={item.label}>
                <Link
                  href={item.href}
                  className={`block rounded-lg px-3 py-2 text-sm transition-colors ${
                    active
                      ? "bg-sky-600/20 font-medium text-sky-300"
                      : "text-slate-400 hover:bg-ink-800 hover:text-slate-200"
                  }`}
                >
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      <div className="border-t border-ink-700 px-5 py-4">
        <p className="text-[10px] uppercase tracking-widest text-slate-500">
          Engine
        </p>
        <p className="mt-1 font-mono text-xs text-slate-300">ARGUS-EQ-1.0</p>
      </div>
    </aside>
  );
}
