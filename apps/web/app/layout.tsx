import type { Metadata } from "next";
import { Sidebar } from "@/components/Sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: "ARGUS — Vision Builds Better Decisions",
  description:
    "Analytics / Insights / Action. Institutional-grade calibrated predictions for stocks and ETFs.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-ink-950 text-slate-200 antialiased">
        <Sidebar />
        <main className="ml-60 min-h-screen">
          <div className="mx-auto max-w-7xl px-6 py-6">{children}</div>
        </main>
      </body>
    </html>
  );
}
