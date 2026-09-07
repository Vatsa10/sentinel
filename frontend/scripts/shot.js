const { chromium } = require("playwright");
const path = process.argv[2];
const url = process.argv[3];
const w = Number(process.argv[4] || 1440);
const h = Number(process.argv[5] || 900);
(async () => {
  const browser = await chromium.launch({
    executablePath: "C:/Users/Vatsa/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe",
  });
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  await page.goto(url, { waitUntil: "networkidle", timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(4500);
  await page.screenshot({ path, fullPage: false });
  await browser.close();
})();
