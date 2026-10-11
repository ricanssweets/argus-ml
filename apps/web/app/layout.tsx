import type { Metadata } from "next";
import { Marcellus, Inter } from "next/font/google";
import { Sidebar } from "@/components/Sidebar";
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
          <div className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:py-6">
            {children}
          </div>
        </main>
      </body>
    </html>
  );
}
