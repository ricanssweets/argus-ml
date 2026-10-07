import type { ReactNode } from "react";

export function Table({
  head,
  children,
  className = "",
}: {
  head: ReactNode[];
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`overflow-x-auto ${className}`}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-ink-700 text-left text-[11px] uppercase tracking-wider text-slate-500">
            {head.map((h, i) => (
              <th key={i} className="px-3 py-2 font-medium first:pl-0 last:pr-0">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-800 text-slate-200">
          {children}
        </tbody>
      </table>
    </div>
  );
}

export function Td({
  children,
  className = "",
  align = "left",
}: {
  children: ReactNode;
  className?: string;
  align?: "left" | "right" | "center";
}) {
  const a =
    align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left";
  return (
    <td className={`px-3 py-2.5 first:pl-0 last:pr-0 ${a} ${className}`}>
      {children}
    </td>
  );
}
