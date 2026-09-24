import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The React frontend is developed against the Flask backend, which runs
// separately (``uv run dev``). Task 103 wires the ``/api`` development proxy
// between the two; until then this config only builds and serves the app.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Fixed, strict port so the address in use never changes silently while
    // the backend is started alongside the dev server.
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
