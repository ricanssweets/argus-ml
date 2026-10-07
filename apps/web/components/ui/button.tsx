import type { ButtonHTMLAttributes, ReactNode } from "react";

export function Button({
  children,
  variant = "primary",
  className = "",
  ...rest
}: {
  children: ReactNode;
  variant?: "primary" | "secondary" | "ghost";
  className?: string;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const v =
    variant === "primary"
      ? "bg-bronze-600 text-white hover:bg-bronze-500"
      : variant === "secondary"
        ? "bg-ink-700 text-slate-200 hover:bg-ink-700/70 border border-ink-700"
        : "text-slate-300 hover:text-white hover:bg-ink-800";
  return (
    <button
      className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:opacity-50 ${v} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
