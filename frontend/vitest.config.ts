import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // Mirrors tsconfig.json's "@/*" -> "./src/*" path mapping — vitest
      // doesn't read tsconfig paths on its own.
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    /*
      Pin a non-UTC zone for the suite.

      This container (and CI) runs UTC, where a timezone bug is invisible:
      `new Date("2026-08-18T03:07:00")` and the correct UTC parse produce the
      same instant, so an assertion about offset-less timestamps passes whether
      or not the code is right. The backend serialises every timestamp without
      a `Z` (utcnow() is deliberately naive — see backend/app/time_utils.py),
      and ECMA-262 parses that form as LOCAL time, so this is a live hazard
      rather than a theoretical one.

      Los Angeles specifically: a negative offset that also crosses a date
      boundary for the early-morning pipeline timestamps this app displays, so
      a mis-parse changes the rendered DAY and not just the hour.
    */
    env: { TZ: "America/Los_Angeles" },
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.d.ts", "src/app/**/layout.tsx", "src/app/**/page.tsx"],
    },
  },
});
