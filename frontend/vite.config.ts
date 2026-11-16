import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vitest/config";

// In dev, Vite proxies the API to the backend instance chosen by FINANCE360_PORT.
const apiPort = process.env.FINANCE360_PORT || "8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    proxy: {
      "/health": `http://localhost:${apiPort}`,
      "/api": `http://localhost:${apiPort}`,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
