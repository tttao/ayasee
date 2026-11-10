import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "./",
  build: {
    outDir: "dist",
    // The page ships a strict CSP (default-src 'self'): no inline scripts.
    modulePreload: { polyfill: false },
  },
  test: {
    environment: "jsdom",
    include: ["test/**/*.test.ts"],
  },
});
