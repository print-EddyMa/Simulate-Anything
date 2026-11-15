import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const api = process.env.API_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: { proxy: { "/api": { target: api, changeOrigin: true } } },
  build: {
    outDir: "../create_anything/web/dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1600,
  },
});
