import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Use a repo-local extraction when this host cannot install Playwright's
// Debian packages system-wide. Hosts with system dependencies skip it.
const localLibraryPath = fileURLToPath(
  new URL("./.playwright-system-deps/usr/lib/x86_64-linux-gnu", import.meta.url),
);
if (existsSync(localLibraryPath)) {
  process.env.LD_LIBRARY_PATH = [localLibraryPath, process.env.LD_LIBRARY_PATH]
    .filter(Boolean)
    .join(":");
}

const baseURL = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:8766";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ["list"],
    ["html", { open: "never" }],
  ],
  use: {
    baseURL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-chromium",
      use: { ...devices["Pixel 7"] },
    },
  ],
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command:
          ".venv/bin/python -m errgrind.web --host 127.0.0.1 --port 8766 --db /tmp/errgrind-playwright.db",
        cwd: "..",
        env: {
          ...process.env,
          XDG_CONFIG_HOME: "/tmp/errgrind-playwright-config",
          XDG_STATE_HOME: "/tmp/errgrind-playwright-state",
        },
        url: baseURL,
        reuseExistingServer: false,
        timeout: 30_000,
        gracefulShutdown: { signal: "SIGTERM", timeout: 1_000 },
      },
});
