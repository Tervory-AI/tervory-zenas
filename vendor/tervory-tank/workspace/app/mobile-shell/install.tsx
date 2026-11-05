"use client";

import { useEffect } from "react";

/** Installation has no new screen or prompt; use the browser's native install UI. */
export function InstallShell() {
  useEffect(() => {
    if ("serviceWorker" in navigator && window.isSecureContext) {
      void navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(() => undefined);
    }
  }, []);
  return null;
}
