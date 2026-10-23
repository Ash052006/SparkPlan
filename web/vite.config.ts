import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.SPARKPLAN_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": apiTarget,
      "/v2": apiTarget,
      "/plan": apiTarget,
      "/runs": apiTarget,
      "/catalog": apiTarget,
      "/health": apiTarget,
      "/healthz": apiTarget,
    },
  },
  build: { outDir: "dist" },
});
