"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { LogoLockup } from "./Logo";

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

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <ul className="space-y-0.5">
      {NAV.map((item) => {
        const active = isActive(pathname, item.href);
        return (
          <li key={item.label}>
            <Link
              href={item.href}
              onClick={onNavigate}
              className={`block rounded-lg px-3 py-2.5 text-[15px] transition-colors ${
                active
                  ? "bg-bronze-600/20 font-medium text-bronze-300"
                  : "text-slate-400 hover:bg-ink-800 hover:text-stone-200"
              }`}
            >
              {item.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function EngineFooter() {
  return (
    <div className="border-t border-ink-700 px-5 py-4">
      <p className="text-[10px] uppercase tracking-widest text-slate-500">
        Engine
      </p>
      <p className="mt-1 font-mono text-xs text-bronze-300">ARGUS-EQ-1.1</p>
    </div>
  );
}

export function Sidebar() {
  const [open, setOpen] = useState(false);

  return (
    <>
      {/* Mobile top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-ink-700 bg-ink-900/95 px-4 py-3 backdrop-blur lg:hidden">
        <LogoLockup />
        <button
          onClick={() => setOpen(true)}
          aria-label="Open menu"
          className="flex h-10 w-10 items-center justify-center rounded-lg border border-ink-700 bg-ink-800 text-stone-200"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path
              d="M3 5.5h14M3 10h14M3 14.5h14"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </header>

      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 hidden w-60 flex-col border-r border-ink-700 bg-ink-900 lg:flex">
        <div className="border-b border-ink-700 px-5 py-5">
          <LogoLockup />
        </div>
        <nav className="flex-1 overflow-y-auto px-3 py-4">
          <NavList />
        </nav>
        <EngineFooter />
      </aside>

      {/* Mobile drawer */}
      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 animate-fade bg-black/70"
            onClick={() => setOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] animate-slide-in flex-col border-r border-ink-700 bg-ink-900">
            <div className="flex items-center justify-between border-b border-ink-700 px-5 py-4">
              <LogoLockup />
              <button
                onClick={() => setOpen(false)}
                aria-label="Close menu"
                className="flex h-10 w-10 items-center justify-center rounded-lg border border-ink-700 bg-ink-800 text-stone-200"
              >
                <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                  <path
                    d="M4 4l10 10M14 4L4 14"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            </div>
            <nav className="flex-1 overflow-y-auto px-3 py-4">
              <NavList onNavigate={() => setOpen(false)} />
            </nav>
            <EngineFooter />
          </aside>
        </div>
      )}
    </>
  );
}
