import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const keysPath = path.resolve(__dirname, "../../data/api_keys.json");
let operator: string | undefined;
try {
  const keys = JSON.parse(fs.readFileSync(keysPath, "utf8")) as Record<
    string,
    { role: string }
  >;
  operator = Object.entries(keys).find(([, v]) => v.role === "operator")?.[0];
} catch {
  // data/api_keys.json is not committed — generate it with
  // tools/make_keys.py to run the RBAC test below; every other test here
  // runs fine against the open (no-key) backend.
}

const routes = [
  "",
  "wall",
  "map",
  "vehicles",
  "alerts",
  "watchlist",
  "zones",
  "traffic",
  "intelligence",
  "assistant",
  "admin",
];

for (const r of routes) {
  test(`console/${r} renders`, async ({ page }) => {
    await page.goto(`/console/${r}`);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });
}

test("landing has live strip and CTA", async ({ page }) => {
  // "load" never fires here: LivePreview embeds a live MJPEG stream
  // (multipart/x-mixed-replace), which by design never completes loading.
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("link", { name: /open console/i }).first()).toBeVisible();
});

test("wall shows an MJPEG tile", async ({ page }) => {
  // The wall's default tile list (TIME_ALIGNED_PRESET, see CameraPicker.tsx)
  // already includes cam13, so it renders without needing to add a camera.
  // Confirm the picker is present and reachable, then assert the tile shows.
  await page.goto("/console/wall/");
  await expect(page.getByRole("button", { name: /add cameras/i })).toBeVisible();
  await expect(page.locator("img[src*='live.mjpg']").first()).toBeVisible({ timeout: 15_000 });
});

test("viewer is gated, operator is not", async ({ page }) => {
  test.skip(!operator, "generate data/api_keys.json with tools/make_keys.py first");
  await page.goto("/console/watchlist/");
  await expect(page.getByRole("button", { name: /add entry/i })).toBeDisabled();
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await page.getByLabel("API key").fill(operator!);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await expect(page.getByText(/operator/i).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /add entry/i })).toBeEnabled();
});

test("map shows camera markers", async ({ page }) => {
  await page.goto("/console/map/");
  // Camera markers are CircleMarkers rendered with a white (#fff) stroke;
  // gap-zone / coverage circles use other stroke colours, so this selector
  // isolates the actual camera dots from the ".leaflet-interactive" paths.
  const markers = page.locator("path.leaflet-interactive[stroke='#fff']");
  await expect(markers.first()).toBeVisible({ timeout: 15_000 });
  const count = await markers.count();
  expect(count).toBeGreaterThanOrEqual(20);
});

test("vehicles trace shows sightings or the honest empty verdict", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/console/vehicles/");
  await page.getByPlaceholder(/registration number/i).fill("GJO1AB1234");
  await page.getByRole("button", { name: /trace registration number/i }).click();
  const result = page.locator("p.mono", { hasText: /sighting|No sightings of this number/i });
  await expect(result).toBeVisible({ timeout: 60_000 });
});

test("?api= override asks before retargeting, and Cancel leaves storage untouched", async ({ page }) => {
  await page.goto("/console/?api=http://localhost:9999");
  await expect(page.getByRole("heading", { name: /connect to a different backend/i })).toBeVisible();
  await expect(page.getByText(/your sign-in key will be sent to this backend/i)).toBeVisible();
  await page.getByRole("button", { name: /^cancel$/i }).click();
  await expect(page.getByRole("heading", { name: /connect to a different backend/i })).toBeHidden();
  const stored = await page.evaluate(() => localStorage.getItem("NETRA_API_BASE"));
  expect(stored).toBeNull();
});
