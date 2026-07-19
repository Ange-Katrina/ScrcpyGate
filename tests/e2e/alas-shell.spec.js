import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const enabled = Boolean(process.env.SCRCPYGATE_E2E_BASE_URL || process.env.SCRCPYGATE_E2E_RUN);
const e2eUsername = process.env.SCRCPYGATE_E2E_USERNAME;
const e2ePassword = process.env.SCRCPYGATE_E2E_PASSWORD;
const shellProjects = new Set(["desktop", "mobile", "mobile-landscape"]);

async function signIn(page) {
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await page.locator("#username").fill(e2eUsername);
  await page.locator("#password").fill(e2ePassword);
  await page.locator("#loginSubmit").click();
  await page.waitForURL((url) => !url.pathname.endsWith("/login"));
}

async function assertShellA11y(page, label) {
  const results = await new AxeBuilder({ page }).exclude("#alasFrame").analyze();
  const blocking = results.violations.filter((violation) => ["critical", "serious"].includes(violation.impact));
  expect(blocking, `${label}: axe violations`).toEqual([]);
}

async function assertShellGeometry(page, mobile, stateVisible = true) {
  const geometry = await page.evaluate((showingState) => {
    const viewport = window.visualViewport;
    const viewportRect = {
      left: viewport ? viewport.offsetLeft : 0,
      top: viewport ? viewport.offsetTop : 0,
      width: viewport ? viewport.width : window.innerWidth,
      height: viewport ? viewport.height : window.innerHeight,
    };
    const box = (element) => {
      const rect = element.getBoundingClientRect();
      return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height };
    };
    const toolbar = document.querySelector(".alas-shell-toolbar");
    const card = document.querySelector(".alas-shell-state-card");
    const stage = document.querySelector(".alas-shell-stage");
    const frame = document.querySelector("#alasFrame");
    const stageBox = box(stage);
    const frameBox = box(frame);
    const actions = Array.from(document.querySelectorAll(
      ".alas-shell-toolbar .alas-shell-button, .alas-shell-toolbar .ui-locale-trigger"
    )).map(box);
    return {
      viewport: viewportRect,
      pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      toolbarOverflow: toolbar.scrollWidth > toolbar.clientWidth + 1,
      boxes: showingState ? [box(toolbar), box(card)] : [box(toolbar), stageBox, frameBox],
      ready: showingState ? null : { stage: stageBox, frame: frameBox },
      actions,
    };
  }, stateVisible);

  expect(geometry.pageOverflow).toBe(false);
  expect(geometry.toolbarOverflow).toBe(false);
  const viewportRight = geometry.viewport.left + geometry.viewport.width;
  const viewportBottom = geometry.viewport.top + geometry.viewport.height;
  for (const box of [...geometry.boxes, ...geometry.actions]) {
    expect(box.left).toBeGreaterThanOrEqual(geometry.viewport.left - 1);
    expect(box.top).toBeGreaterThanOrEqual(geometry.viewport.top - 1);
    expect(box.right).toBeLessThanOrEqual(viewportRight + 1);
    expect(box.bottom).toBeLessThanOrEqual(viewportBottom + 1);
  }
  if (mobile) {
    for (const action of geometry.actions) {
      expect(action.width).toBeGreaterThanOrEqual(44);
      expect(action.height).toBeGreaterThanOrEqual(44);
    }
  }
  if (geometry.ready) {
    expect(geometry.ready.stage.width).toBeGreaterThan(0);
    expect(geometry.ready.stage.height).toBeGreaterThan(0);
    expect(geometry.ready.frame.width).toBeGreaterThan(0);
    expect(geometry.ready.frame.height).toBeGreaterThan(0);
    expect(Math.abs(geometry.ready.frame.width - geometry.ready.stage.width)).toBeLessThanOrEqual(1);
    expect(Math.abs(geometry.ready.frame.height - geometry.ready.stage.height)).toBeLessThanOrEqual(1);
  }
}

test.describe("ALAS shell runtime gates", () => {
  test.skip(!enabled, "Set SCRCPYGATE_E2E_BASE_URL and start ScrcpyGate to run browser gates");

  test("unreachable state, locale popup, retry recovery, and themes stay usable", async ({ page }, testInfo) => {
    test.skip(!shellProjects.has(testInfo.project.name), "ALAS shell target viewports only");
    test.skip(!e2eUsername || !e2ePassword, "Set authenticated E2E credentials");
    await signIn(page);
    await page.evaluate(() => window.localStorage.setItem("scrcpygate:theme", "dark"));

    const proxyRequests = [];
    let releaseFirstResponse;
    let markFirstProxySeen;
    const firstResponseRelease = new Promise((resolve) => { releaseFirstResponse = resolve; });
    const firstProxySeen = new Promise((resolve) => { markFirstProxySeen = resolve; });
    await page.route("**/alas/embed/proxy/**", async (route) => {
      proxyRequests.push(route.request().url());
      if (proxyRequests.length === 1) {
        markFirstProxySeen();
        await firstResponseRelease;
        await route.fulfill({
          status: 502,
          contentType: "application/json",
          body: JSON.stringify({ detail: "ALAS Runtime unreachable" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<!doctype html><html lang=\"en\"><head><title>ALAS mock</title></head><body><main><h1>ALAS ready</h1></main></body></html>",
      });
    });

    try {
      await page.goto("/alas/embed/", { waitUntil: "commit" });
      await firstProxySeen;
      const shellState = page.locator("#alasState");
      const loadStatus = page.locator("#loadStatus");
      const frame = page.locator("#alasFrame");
      await expect(shellState).toHaveClass(/is-visible/);
      await expect(shellState).toHaveAttribute("data-kind", "loading");
      releaseFirstResponse();
      await expect(shellState).toHaveAttribute("data-kind", "unreachable");
      await expect(shellState).toHaveAttribute("role", "alert");
      await expect(loadStatus).toHaveAttribute("data-state", "error");
      await expect(frame).toHaveAttribute("aria-busy", "true");
      await expect(page.locator("#retryFrame")).toBeVisible();

      const mobile = ["mobile", "mobile-landscape"].includes(testInfo.project.name);
      await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
      await assertShellGeometry(page, mobile);
      await assertShellA11y(page, `${testInfo.project.name}-unreachable`);

      const localeTrigger = page.locator(".alas-shell-toolbar .ui-locale-trigger");
      const localeMenu = page.locator(".alas-shell-toolbar .ui-locale-menu");
      await localeTrigger.click();
      await expect(localeMenu).toBeVisible();
      const menuBounds = await localeMenu.boundingBox();
      const viewportBounds = await page.evaluate(() => {
        const viewport = window.visualViewport;
        return {
          left: viewport ? viewport.offsetLeft : 0,
          top: viewport ? viewport.offsetTop : 0,
          right: (viewport ? viewport.offsetLeft + viewport.width : window.innerWidth),
          bottom: (viewport ? viewport.offsetTop + viewport.height : window.innerHeight),
        };
      });
      expect(menuBounds).not.toBeNull();
      expect(menuBounds.x).toBeGreaterThanOrEqual(viewportBounds.left + 7);
      expect(menuBounds.y).toBeGreaterThanOrEqual(viewportBounds.top + 7);
      expect(menuBounds.x + menuBounds.width).toBeLessThanOrEqual(viewportBounds.right - 7);
      expect(menuBounds.y + menuBounds.height).toBeLessThanOrEqual(viewportBounds.bottom - 7);
      await page.keyboard.press("Escape");
      await expect(localeMenu).toBeHidden();
      await expect(localeTrigger).toBeFocused();

      await page.locator("#retryFrame").click();
      await expect(loadStatus).toHaveAttribute("data-state", "ready");
      await expect(frame).toHaveAttribute("aria-busy", "false");
      await expect(shellState).not.toHaveClass(/is-visible/);
      expect(proxyRequests.some((url) => new URL(url).searchParams.has("_scrcpygate_retry"))).toBe(true);
      await assertShellGeometry(page, mobile, false);
      await assertShellA11y(page, `${testInfo.project.name}-ready-dark`);

      await page.evaluate(() => window.localStorage.setItem("scrcpygate:theme", "light"));
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
      await expect(page.locator("#loadStatus")).toHaveAttribute("data-state", "ready");
      await assertShellGeometry(page, mobile, false);
      await assertShellA11y(page, `${testInfo.project.name}-ready-light`);
    } finally {
      releaseFirstResponse();
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });

  test("stalled upstream reaches the timeout recovery state", async ({ page }, testInfo) => {
    test.skip(!shellProjects.has(testInfo.project.name), "ALAS shell target viewports only");
    test.skip(!e2eUsername || !e2ePassword, "Set authenticated E2E credentials");
    await signIn(page);
    await page.clock.install();
    let stalledRoute;
    let markProxySeen;
    const proxySeen = new Promise((resolve) => { markProxySeen = resolve; });
    await page.route("**/alas/embed/proxy/**", (route) => {
      stalledRoute = route;
      markProxySeen();
    });

    try {
      await page.goto("/alas/embed/", { waitUntil: "domcontentloaded" });
      await proxySeen;
      const shellState = page.locator("#alasState");
      await expect(shellState).toHaveClass(/is-visible/);
      await expect(shellState).toHaveAttribute("data-kind", "loading");
      await assertShellA11y(page, `${testInfo.project.name}-loading`);
      await page.clock.fastForward(15_001);
      await expect(shellState).toHaveAttribute("data-kind", "timeout");
      await expect(shellState).toHaveAttribute("role", "alert");
      await expect(page.locator("#loadStatus")).toHaveAttribute("data-state", "error");
      await expect(page.locator("#alasFrame")).toHaveAttribute("aria-busy", "true");
      await expect(page.locator("#retryFrame")).toBeVisible();
      await assertShellGeometry(page, ["mobile", "mobile-landscape"].includes(testInfo.project.name));
      await assertShellA11y(page, `${testInfo.project.name}-timeout`);
    } finally {
      if (stalledRoute) await stalledRoute.abort("failed").catch(() => {});
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });
});
