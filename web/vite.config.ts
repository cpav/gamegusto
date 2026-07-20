import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// The design tokens live at the repo root (design/tokens.css) and are the single
// source of truth for both this client and the spec, so the CSS imports them
// directly rather than keeping a drifting copy here.
const repoRoot = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    fs: { allow: [repoRoot] },
    // The API runs separately (uvicorn on 8000). Proxying keeps the client
    // same-origin in dev, so SSE needs no CORS negotiation.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
