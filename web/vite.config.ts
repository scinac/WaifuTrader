import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backend = "http://127.0.0.1:8000";

// Proxying keeps the app same-origin, which the strict session cookie requires.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": backend,
      "/ws": { target: backend, ws: true },
    },
  },
});
