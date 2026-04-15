import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In the Docker Compose network the api is reachable as http://api:8000.
// For host-only dev (no Docker) it's http://localhost:8000.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
