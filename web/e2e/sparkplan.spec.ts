import { expect, Page, Route, test } from "@playwright/test";
import { ARTIFACTS, CATALOG, REFUSAL_RUN, RUN } from "./fixtures";

type MockState = {
  planCalls: number;
  runPolls: number;
};

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockSuccess(page: Page): Promise<MockState> {
  const state: MockState = { planCalls: 0, runPolls: 0 };

  await page.route("**/catalog", async (route) => {
    await json(route, CATALOG);
  });

  await page.route("**/plan", async (route) => {
    state.planCalls += 1;
    await json(route, { run_id: "run_demo" }, 202);
  });

  await page.route("**/runs/run_demo/files/**", async (route) => {
    const filename = new URL(route.request().url()).pathname.split("/").pop() ?? "";
    const artifact = ARTIFACTS[filename];
    if (!artifact) {
      await route.fulfill({ status: 404, body: "not found" });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: artifact.contentType,
      body: artifact.body,
    });
  });

  await page.route(/\/runs\/run_demo$/, async (route) => {
    state.runPolls += 1;
    if (state.runPolls === 1) {
      await json(route, { ...RUN, status: "running", stages: [] });
      return;
    }
    await json(route, RUN);
  });

  return state;
}

async function mockRefusal(page: Page): Promise<MockState> {
  const state: MockState = { planCalls: 0, runPolls: 0 };

  await page.route("**/plan", async (route) => {
    state.planCalls += 1;
    await json(route, { run_id: "run_refused" }, 202);
  });

  await page.route(/\/runs\/run_refused$/, async (route) => {
    state.runPolls += 1;
    await json(route, REFUSAL_RUN);
  });

  return state;
}

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

test.describe("successful build", () => {
  test.use({ viewport: { width: 390, height: 844 }, colorScheme: "light" });

  test("renders a complete, mobile-friendly build", async ({ page, context }, testInfo) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const errors = watchErrors(page);
    const state = await mockSuccess(page);

    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Ideas in. Validated builds out." })).toBeVisible();
    await expect(page.locator("#idea-count")).toHaveText("0/500");

    await page.getByRole("button", { name: /Use example 1:/ }).click();
    await expect(page.getByLabel("Hardware idea")).toHaveValue(/desk weather station/i);

    await page.getByRole("button", { name: "Generate build" }).click();
    await expect(page.getByText("Build ready.", { exact: true })).toBeVisible();

    expect(state.planCalls).toBe(1);
    expect(state.runPolls).toBeGreaterThanOrEqual(2);
    await expect(page.getByRole("heading", { name: "Verification" })).toBeVisible();
    await expect(page.getByText("Not physically built", { exact: true })).toBeVisible();
    await expect(page.getByText("Validated", { exact: true })).toBeVisible();
    await expect(page.getByText("Simulated", { exact: true })).toBeVisible();

    await expect(page.getByRole("heading", { name: "Parts" })).toBeVisible();
    await expect(page.getByText("DHT22 temperature and humidity sensor")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Wiring" })).toBeVisible();
    await expect(page.getByText("DHT22 sensor · DATA")).toBeVisible();
    await expect(page.getByText("MOUNT_CLEARANCE")).toBeVisible();

    await expect(page.getByRole("heading", { name: "Firmware" })).toBeVisible();
    await expect(page.getByText("void setup()")).toBeVisible();
    await page.getByRole("button", { name: "Copy", exact: true }).click();
    await expect(page.getByRole("button", { name: "Copied", exact: true })).toBeVisible();

    await expect(page.locator("#guide-heading")).toBeVisible();
    await expect(page.getByText("Wire the DHT22")).toBeVisible();

    const viewer = page.getByRole("img", { name: /Interactive 3D view/ });
    await expect(viewer).toBeVisible();
    await expect(page.locator(".viewer-error")).toHaveCount(0);
    await page.getByRole("button", { name: "Base", exact: true }).click();
    await expect(page.getByRole("button", { name: "Base", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await page.getByRole("button", { name: "Assembled", exact: true }).click();
    const slider = page.getByLabel("Exploded view distance in millimetres");
    await expect(slider).toBeEnabled();
    await slider.focus();
    await slider.press("End");
    await expect(page.getByText("40 mm", { exact: true })).toBeVisible();
    await viewer.focus();
    await viewer.press("ArrowLeft");
    await viewer.press("+");
    await expect(page.locator(".viewer-error")).toHaveCount(0);

    await expect(page.getByRole("link", { name: "Download bundle" })).toHaveAttribute(
      "href",
      "/runs/run_demo/bundle.zip",
    );
    await expect(page.getByRole("link", { name: "report.json" })).toHaveAttribute(
      "href",
      "/runs/run_demo/files/report.json",
    );
    await expect(page.getByRole("link", { name: "trace.json" })).toHaveAttribute(
      "href",
      "/runs/run_demo/files/trace.json",
    );
    await page.screenshot({ path: testInfo.outputPath("success-390.png"), fullPage: true });

    await page.getByRole("button", { name: "Use dark theme" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(page.getByRole("button", { name: "Use light theme" })).toBeVisible();

    const dimensions = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(dimensions.scrollWidth).toBe(dimensions.clientWidth);
    expect(errors).toEqual([]);
  });
});

test.describe("refused build", () => {
  test.use({ viewport: { width: 390, height: 844 }, colorScheme: "dark" });

  test("renders the refusal reasons without inventing artifacts", async ({ page }, testInfo) => {
    const errors = watchErrors(page);
    const state = await mockRefusal(page);

    await page.goto("/");
    await page.getByLabel("Hardware idea").fill("an autonomous drone");
    await page.getByRole("button", { name: "Generate build" }).click();

    await expect(page.getByRole("heading", { name: "Unsupported idea" })).toBeVisible();
    expect(state.planCalls).toBe(1);
    expect(state.runPolls).toBeGreaterThanOrEqual(1);
    await expect(
      page.getByText("SparkPlan has no flight-control hardware.", { exact: true }).first(),
    ).toBeVisible();
    await expect(page.getByText("flight_control", { exact: true })).toBeVisible();
    await expect(page.getByText("A ground rover using DC motors and a distance sensor.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Download bundle" })).toHaveCount(0);
    await expect(page.getByRole("img", { name: /Interactive 3D view/ })).toHaveCount(0);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    const dimensions = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(dimensions.scrollWidth).toBe(dimensions.clientWidth);
    await page.screenshot({ path: testInfo.outputPath("refusal-390.png"), fullPage: true });

    expect(errors).toEqual([]);
  });
});
