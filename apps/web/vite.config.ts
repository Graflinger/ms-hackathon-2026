import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api/v1": {
        target: process.env.GOLDENLOOP_API_PROXY || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  preview: { host: "127.0.0.1" },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: true,
    restoreMocks: true,
  },
});
