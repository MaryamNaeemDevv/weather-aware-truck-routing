import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  worker: { format: "es" },
  server: {
    proxy: { "/api": process.env.VITE_PROXY_TARGET ?? "http://127.0.0.1:8000" },
  },
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: { manualChunks: (id) => (id.includes("maplibre-gl") ? "maplibre" : undefined) },
    },
  },
});
