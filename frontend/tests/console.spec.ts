import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const keysPath = path.resolve(__dirname, "../../data/api_keys.json");
const keys = JSON.parse(fs.readFileSync(keysPath, "utf8")) as Record<
  string,
  { role: string }
>;
const operator = Object.entries(keys).find(([, v]) => v.role === "operator")![0];

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
  await page.goto("/");
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
  await page.goto("/console/watchlist/");
  await expect(page.getByRole("button", { name: /add entry/i })).toBeDisabled();
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await page.getByLabel("API key").fill(operator);
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
