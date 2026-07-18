import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const enabled = Boolean(process.env.SCRCPYGATE_E2E_BASE_URL || process.env.SCRCPYGATE_E2E_RUN);
const e2eUsername = process.env.SCRCPYGATE_E2E_USERNAME;
const e2ePassword = process.env.SCRCPYGATE_E2E_PASSWORD;

test.describe("ScrcpyGate accessibility and layout gates", () => {
  test.skip(!enabled, "Set SCRCPYGATE_E2E_BASE_URL and start ScrcpyGate to run browser gates");

  async function assertA11y(page, label) {
    const results = await new AxeBuilder({ page }).analyze();
    const blocking = results.violations.filter((violation) => ["critical", "serious"].includes(violation.impact));
    expect(blocking, `${label}: axe violations`).toEqual([]);
  }

  async function assertNoHorizontalOverflow(page, label) {
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    expect(overflow, `${label}: horizontal overflow`).toBe(false);
  }

  async function signIn(page) {
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.locator("#username").fill(e2eUsername);
    await page.locator("#password").fill(e2ePassword);
    await page.locator("#loginSubmit").click();
    await page.waitForURL((url) => !url.pathname.endsWith("/login"));
  }

  test("login page passes axe and supports keyboard password toggle", async ({ page }) => {
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await expect(page.locator("html")).toHaveAttribute("lang", /^(zh-CN|en-US)$/);
    await assertA11y(page, "login");
    await assertNoHorizontalOverflow(page, "login");

    await page.locator("#username").focus();
    await page.keyboard.press("Tab");
    await expect(page.locator("#password")).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.locator("#passwordToggle")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("#password")).toHaveAttribute("type", "text");
  });

  test("login page keeps dark and light themes readable", async ({ browser }) => {
    for (const theme of ["dark", "light"]) {
      const context = await browser.newContext({ colorScheme: theme });
      const page = await context.newPage();
      await page.addInitScript((value) => window.localStorage.setItem("scrcpygate:theme", value), theme);
      await page.goto("/login", { waitUntil: "domcontentloaded" });
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await assertA11y(page, `login-${theme}`);
      await context.close();
    }
  });

  test("authenticated workspaces pass axe and overflow gates when credentials are provided", async ({ page }) => {
    test.skip(!e2eUsername || !e2ePassword, "Set SCRCPYGATE_E2E_USERNAME and SCRCPYGATE_E2E_PASSWORD for authenticated gates");
    await signIn(page);

    for (const path of ["/", "/admin", "/alas/embed/"]) {
      await page.goto(path, { waitUntil: "domcontentloaded" });
      await assertA11y(page, path);
      await assertNoHorizontalOverflow(page, path);
    }
  });

  test("mobile device menu keeps focus and primary actions inside the visual viewport", async ({ page }, testInfo) => {
    test.skip(!["mobile", "mobile-landscape"].includes(testInfo.project.name), "Mobile projects only");
    test.skip(!e2eUsername || !e2ePassword, "Set authenticated E2E credentials");
    await signIn(page);
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.locator("#devices .device-card").first()).toBeVisible();

    await page.locator("#menuBtn").click();
    await expect(page.locator("#sidebar")).toHaveClass(/open/);
    await expect(page.locator("#deviceSearch")).not.toBeFocused();

    const geometry = await page.evaluate(() => {
      const viewport = window.visualViewport;
      const actions = document.querySelector(".side-actions").getBoundingClientRect();
      const buttons = Array.from(document.querySelectorAll(".side-actions button")).map((button) => {
        const rect = button.getBoundingClientRect();
        return { width: rect.width, height: rect.height, top: rect.top, bottom: rect.bottom };
      });
      return {
        height: viewport ? viewport.height : innerHeight,
        actions: { top: actions.top, bottom: actions.bottom },
        buttons,
      };
    });
    expect(geometry.actions.bottom).toBeLessThanOrEqual(geometry.height + 1);
    expect(geometry.actions.top).toBeGreaterThanOrEqual(0);
    for (const button of geometry.buttons) {
      expect(button.width).toBeGreaterThanOrEqual(44);
      expect(button.height).toBeGreaterThanOrEqual(44);
      expect(button.bottom).toBeLessThanOrEqual(geometry.height + 1);
    }

    await expect(page.locator("#sidebar")).toHaveClass(/open/);
  });
});
