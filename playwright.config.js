import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.SCRCPYGATE_E2E_BASE_URL || "http://127.0.0.1:5077";
const browserChannel = process.env.SCRCPYGATE_E2E_BROWSER_CHANNEL;
const chromium = {
  browserName: "chromium",
  ...(browserChannel ? { channel: browserChannel } : {}),
};

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? "line" : "list",
  use: {
    ...chromium,
    baseURL,
    colorScheme: "dark",
    reducedMotion: "reduce",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "desktop-compact",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } },
    },
    {
      name: "tablet",
      use: { ...devices["iPad Mini"], viewport: { width: 768, height: 1024 }, hasTouch: true },
    },
    {
      name: "mobile",
      use: { ...devices["iPhone 13"], viewport: { width: 390, height: 844 }, hasTouch: true },
    },
    {
      name: "mobile-landscape",
      use: { ...devices["iPhone 13"], viewport: { width: 844, height: 390 }, hasTouch: true },
    },
  ],
});
