import type { ReactNode } from "react";

const STYLES: Record<string, string> = {
  default: "bg-ink-700 text-slate-200 border-ink-700",
  demo: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  bull: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  bear: "bg-rose-500/15 text-rose-300 border-rose-500/40",
  neutral: "bg-slate-500/15 text-slate-300 border-slate-500/40",
  warn: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  critical: "bg-rose-500/20 text-rose-200 border-rose-500/50",
  watch: "bg-bronze-500/15 text-bronze-300 border-bronze-500/40",
  info: "bg-bronze-500/15 text-bronze-300 border-bronze-500/40",
  champion: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  challenger: "bg-violet-500/15 text-violet-300 border-violet-500/40",
  retired: "bg-slate-600/20 text-slate-400 border-slate-600/40",
};

export function Badge({
  tone = "default",
  children,
  className = "",
}: {
  tone?: keyof typeof STYLES;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
        STYLES[tone] ?? STYLES.default
      } ${className}`}
    >
      {children}
    </span>
  );
}
