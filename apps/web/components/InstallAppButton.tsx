"use client";

import { useEffect, useState } from "react";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

// Installs ARGUS as a standalone app (PWA) so the home-screen icon is
// the bronze A alone — no browser badge (Firefox/Chrome shortcut badge).
// The button is ALWAYS visible (Firefox Android never fires
// beforeinstallprompt, so hiding it until then would leave Firefox
// users with no way in). On click: use the install prompt when the
// browser offers it, otherwise show the manual steps for that browser.
// Registers the service worker on mount.
export function InstallAppButton() {
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
    if (window.matchMedia("(display-mode: standalone)").matches) {
      setInstalled(true);
    }
    const onPrompt = (e: Event) => {
      e.preventDefault();
      setDeferred(e as BeforeInstallPromptEvent);
    };
    const onInstalled = () => {
      setInstalled(true);
      setDeferred(null);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (installed) return null;

  return (
    <div>
      <button
        type="button"
        onClick={async () => {
          if (deferred) {
            await deferred.prompt();
            const choice = await deferred.userChoice;
            if (choice.outcome === "accepted") setDeferred(null);
          } else {
            setShowHelp((v) => !v);
          }
        }}
        className="w-full rounded-lg bg-bronze-600 px-3 py-2.5 text-sm font-medium text-white transition-colors hover:bg-bronze-500"
      >
        Instalar app
      </button>
      {showHelp && (
        <p className="mt-2 text-xs leading-relaxed text-slate-400">
          En Firefox: menú ⋮ → Instalar o Añadir a pantalla de inicio. En
          Chrome: menú ⋮ → Instalar app. Borra primero el acceso viejo, que
          ese sí lleva el símbolo de Firefox.
        </p>
      )}
    </div>
  );
}
