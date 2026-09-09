import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const apiProxy = new URL(
  process.env.GOLDENLOOP_API_PROXY || "http://127.0.0.1:8000",
);
if (
  !["127.0.0.1", "localhost", "[::1]"].includes(apiProxy.hostname) ||
  !["http:", "https:"].includes(apiProxy.protocol) ||
  apiProxy.username ||
  apiProxy.password
) {
  throw new Error(
    "GOLDENLOOP_API_PROXY must target a loopback HTTP(S) backend without credentials.",
  );
}

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: apiProxy.origin,
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
