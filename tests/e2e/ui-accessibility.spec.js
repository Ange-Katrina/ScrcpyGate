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

  test("mobile login does not summon the keyboard or steal password-toggle focus", async ({ page }, testInfo) => {
    test.skip(!["mobile", "mobile-landscape"].includes(testInfo.project.name), "Mobile projects only");
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await expect(page.locator("#username")).not.toBeFocused();
    await expect(page.locator("#password")).not.toBeFocused();
    await page.locator("#passwordToggle").click();
    await expect(page.locator("#password")).not.toBeFocused();
    await expect(page.locator("#password")).toHaveAttribute("type", "text");
  });

  test("language picker matches the theme control and keeps keyboard focus", async ({ page }) => {
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    const localeTrigger = page.locator(".ui-locale-trigger");
    const localeMenu = page.locator(".ui-locale-menu");
    await expect(localeTrigger).toBeVisible();
    const pickerGeometry = await page.evaluate(() => {
      const inspect = (selector) => {
        const element = document.querySelector(selector);
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return {
          height: rect.height,
          backgroundColor: style.backgroundColor,
          borderColor: style.borderColor,
          borderRadius: style.borderRadius,
          fontSize: style.fontSize,
        };
      };
      return {
        locale: inspect(".ui-locale-picker"),
        theme: inspect(".ui-theme-picker"),
      };
    });
    expect(pickerGeometry.locale).toEqual(pickerGeometry.theme);

    await localeTrigger.click();
    await expect(localeMenu).toBeVisible();
    await expect(localeTrigger).toHaveAttribute("aria-expanded", "true");
    await expect(localeMenu.getByRole("menuitemradio")).toHaveCount(2);
    await page.keyboard.press("Escape");
    await expect(localeMenu).toBeHidden();
    await expect(localeTrigger).toBeFocused();
    await expect(localeTrigger).toHaveAttribute("aria-expanded", "false");
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

  test("mobile admin navigation and editor drawers keep actions reachable", async ({ page }, testInfo) => {
    test.skip(!["mobile", "mobile-landscape"].includes(testInfo.project.name), "Mobile projects only");
    test.skip(!e2eUsername || !e2ePassword, "Set authenticated E2E credentials");
    await signIn(page);
    await page.goto("/admin", { waitUntil: "domcontentloaded" });
    await page.locator("#adminNavToggle").click();
    await expect(page.locator("#adminNav")).toHaveClass(/is-open/);
    await expect(page.locator("#adminNav")).toHaveAttribute("role", "dialog");
    await expect(page.locator("#adminNav")).toHaveAttribute("aria-modal", "true");
    await page.keyboard.press("Escape");
    await expect(page.locator("#adminNavToggle")).toBeFocused();
    await expect(page.locator("#adminNav")).not.toHaveClass(/is-open/);
    await expect(page.locator("#adminNavBackdrop")).toBeHidden();

    await page.locator("#adminNavToggle").click();
    await page.locator("#adminTabDevices").click();
    await page.locator("#openDeviceDrawer").click();
    await expect(page.locator("#deviceDrawer")).toHaveClass(/is-open/);
    await expect(page.locator("#deviceDrawerClose")).toBeFocused();
    await expect(page.locator("#deviceAddress")).not.toBeFocused();
    const drawerGeometry = await page.evaluate(() => {
      const viewportHeight = window.visualViewport ? window.visualViewport.height : innerHeight;
      const actions = document.querySelector("#deviceDrawer .drawer-actions").getBoundingClientRect();
      const buttons = Array.from(document.querySelectorAll("#deviceDrawer .drawer-actions button")).map((button) => {
        const rect = button.getBoundingClientRect();
        return { width: rect.width, height: rect.height };
      });
      return { viewportHeight, actionsBottom: actions.bottom, buttons };
    });
    expect(drawerGeometry.actionsBottom).toBeLessThanOrEqual(drawerGeometry.viewportHeight + 1);
    for (const button of drawerGeometry.buttons) {
      expect(button.width).toBeGreaterThanOrEqual(44);
      expect(button.height).toBeGreaterThanOrEqual(44);
    }
  });

  test("admin editor drawers keep compact fields and fixed actions", async ({ page }) => {
    test.skip(!e2eUsername || !e2ePassword, "Set authenticated E2E credentials");
    await signIn(page);
    await page.goto("/admin", { waitUntil: "domcontentloaded" });

    const openDrawer = async (tabSelector, triggerSelector, drawerSelector) => {
      const navToggle = page.locator("#adminNavToggle");
      if (await navToggle.isVisible()) {
        await navToggle.click();
      }
      await page.locator(tabSelector).evaluate((element) => element.click());
      await expect(page.locator(triggerSelector)).toBeVisible();
      await page.locator(triggerSelector).click();
      await expect(page.locator(drawerSelector)).toHaveClass(/is-open/);
    };

    const assertDrawerGeometry = async (drawerSelector) => {
      const geometry = await page.locator(drawerSelector).evaluate((drawer) => {
        const heading = drawer.querySelector(".drawer-heading");
        const body = drawer.querySelector(".drawer-body");
        const actions = drawer.querySelector(".drawer-actions");
        const visibleFields = Array.from(drawer.querySelectorAll(".drawer-form > div"))
          .filter((field) => !field.hidden && field.getClientRects().length)
          .map((field) => field.getBoundingClientRect())
          .sort((left, right) => left.top - right.top || left.left - right.left);
        const rowGaps = [];
        let previousRow = null;
        for (const rect of visibleFields) {
          if (!previousRow || Math.abs(rect.top - previousRow.top) <= 1) {
            previousRow = previousRow
              ? { top: previousRow.top, bottom: Math.max(previousRow.bottom, rect.bottom) }
              : { top: rect.top, bottom: rect.bottom };
            continue;
          }
          rowGaps.push(rect.top - previousRow.bottom);
          previousRow = { top: rect.top, bottom: rect.bottom };
        }
        const headingRect = heading.getBoundingClientRect();
        const bodyRect = body.getBoundingClientRect();
        const actionsRect = actions.getBoundingClientRect();
        const anchorsBeforeScroll = { headingTop: headingRect.top, actionsBottom: actionsRect.bottom };
        const canScroll = body.scrollHeight > body.clientHeight + 1;
        if (canScroll) body.scrollTop = body.scrollHeight;
        const anchorsAfterScroll = {
          headingTop: heading.getBoundingClientRect().top,
          actionsBottom: actions.getBoundingClientRect().bottom,
        };
        return {
          rowGaps,
          bodyTop: bodyRect.top,
          bodyBottom: bodyRect.bottom,
          headingBottom: headingRect.bottom,
          actionsTop: actionsRect.top,
          actionsBottom: actionsRect.bottom,
          viewportHeight: window.visualViewport ? window.visualViewport.height : innerHeight,
          canScroll,
          anchorsBeforeScroll,
          anchorsAfterScroll,
        };
      });
      expect(geometry.rowGaps.every((gap) => gap >= 0 && gap <= 32)).toBe(true);
      expect(geometry.bodyTop).toBeGreaterThanOrEqual(geometry.headingBottom - 1);
      expect(geometry.bodyBottom).toBeLessThanOrEqual(geometry.actionsTop + 1);
      expect(geometry.actionsBottom).toBeLessThanOrEqual(geometry.viewportHeight + 1);
      if (geometry.canScroll) {
        expect(Math.abs(geometry.anchorsBeforeScroll.headingTop - geometry.anchorsAfterScroll.headingTop)).toBeLessThanOrEqual(1);
        expect(Math.abs(geometry.anchorsBeforeScroll.actionsBottom - geometry.anchorsAfterScroll.actionsBottom)).toBeLessThanOrEqual(1);
      }
    };

    await openDrawer("#adminTabDevices", "#openDeviceDrawer", "#deviceDrawer");
    await assertDrawerGeometry("#deviceDrawer");
    await page.keyboard.press("Escape");

    await openDrawer("#adminTabUsers", "#openUserDrawer", "#userDrawer");
    await assertDrawerGeometry("#userDrawer");
  });

  test("mobile ALAS shell keeps return action and locale control visible", async ({ page }, testInfo) => {
    test.skip(!["mobile", "mobile-landscape"].includes(testInfo.project.name), "Mobile projects only");
    test.skip(!e2eUsername || !e2ePassword, "Set authenticated E2E credentials");
    await signIn(page);
    await page.goto("/alas/embed/", { waitUntil: "domcontentloaded" });
    const returnLink = page.locator('.alas-shell-toolbar > .alas-shell-button[href="/"]');
    const localeTrigger = page.locator(".alas-shell-toolbar .ui-locale-trigger");
    await expect(returnLink).toBeVisible();
    await expect(localeTrigger).toBeVisible();
    const shellGeometry = await page.evaluate(() => {
      const toolbar = document.querySelector(".alas-shell-toolbar");
      const toolbarRect = toolbar.getBoundingClientRect();
      const returnRect = document.querySelector('.alas-shell-toolbar > .alas-shell-button[href="/"]').getBoundingClientRect();
      const localeRect = document.querySelector(".alas-shell-toolbar .ui-locale-trigger").getBoundingClientRect();
      return {
        toolbarOverflow: toolbar.scrollWidth > toolbar.clientWidth + 1,
        returnRight: returnRect.right,
        localeHeight: localeRect.height,
        viewportWidth: document.documentElement.clientWidth,
        toolbarBottom: toolbarRect.bottom,
      };
    });
    expect(shellGeometry.toolbarOverflow).toBe(false);
    expect(shellGeometry.returnRight).toBeLessThanOrEqual(shellGeometry.viewportWidth + 1);
    expect(shellGeometry.localeHeight).toBeGreaterThanOrEqual(44);
  });
});
