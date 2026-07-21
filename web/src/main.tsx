import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// Production only. In development Vite serves modules unhashed and reloads
// them constantly; a worker caching those would fight HMR and produce stale
// modules that survive a refresh — a genuinely confusing failure to debug.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Registration failing costs the offline shell and nothing else, so it
      // must never take the app down with it.
    });
  });
}
