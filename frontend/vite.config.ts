import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// ``uv run dev`` starts this server next to Flask and passes the address Flask
// ended up on, so the proxy below and the backend can never disagree.
// ``npm run dev`` on its own keeps working: the default is the port Flask
// prefers (see ``web.port`` in config.toml).
const DEFAULT_BACKEND = "http://127.0.0.1:5001";

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, ".", "SPOTM3U_");
  const backend = env.SPOTM3U_DEV_BACKEND || DEFAULT_BACKEND;

  return {
    // Flask serves the production build under ``/app`` (see spotm3u/frontend.py),
    // so the emitted asset URLs are absolute and keep working on a deep link.
    // The dev server keeps serving from ``/``, which is what ``uv run dev`` and
    // the docs point at.
    base: command === "build" ? "/app/" : "/",
    plugins: [react(), tailwindcss()],
    server: {
      // Fixed, strict port so the address in use never changes silently while
      // the backend is started alongside the dev server.
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      // Development only. The proxy exists because Vite and Flask are two
      // origins while developing; ``/api`` stays same-origin by design, so a
      // production build needs no proxy and no backend address.
      proxy: {
        // Frontend code always calls ``/api/...`` (see src/lib/api.ts and
        // docs/api.md) and never learns the backend host or port. The path is
        // forwarded unchanged: the API lives under ``/api`` on both sides.
        "/api": {
          target: backend,
        },
      },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
  };
});
