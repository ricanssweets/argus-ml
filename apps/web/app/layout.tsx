import type { Metadata } from "next";
import { Marcellus, Inter } from "next/font/google";
import { Sidebar } from "@/components/Sidebar";
import { API_CONFIG_ERROR } from "@/lib/config";
import "./globals.css";

const display = Marcellus({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-display",
});

const body = Inter({
  subsets: ["latin"],
  variable: "--font-body",
});

export const metadata: Metadata = {
  title: "ARGUS — Vision Builds Better Decisions",
  description:
    "Analytics / Insights / Action. Institutional-grade calibrated predictions for stocks and ETFs.",
  icons: { icon: "/brand/favicon.png" },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`dark ${display.variable} ${body.variable}`}
    >
      <body className="bg-ink-950 font-body text-slate-200 antialiased">
        <Sidebar />
        <main className="min-h-screen lg:ml-60">
          {API_CONFIG_ERROR && (
            <div
              role="alert"
              className="border-b-2 border-rose-500/60 bg-rose-500/15 px-4 py-3 text-center text-rose-100 lg:px-6"
            >
              <p className="text-xs font-bold uppercase tracking-[0.14em]">
                Configuration error — no API backend configured
              </p>
              <p className="mt-1 text-xs">
                {API_CONFIG_ERROR.message} Every figure in this build is{" "}
                <span className="font-bold">DEMO DATA · synthetic</span>{" "}
                (fabricated by lib/demo-data.ts), not market data. Set{" "}
                <code className="font-mono">NEXT_PUBLIC_ARGUS_API_URL</code>{" "}
                and rebuild to load live records.
              </p>
            </div>
          )}
          <div className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:py-6">
            {children}
          </div>
        </main>
      </body>
    </html>
  );
}
