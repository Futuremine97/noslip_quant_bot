import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

const rootDirectory = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  resolve: {
    alias: {
      "@": rootDirectory,
    },
  },
  test: {
    include: ["app/**/*.live.test.ts"],
    environment: "node",
  },
});
