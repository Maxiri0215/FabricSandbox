import {chromium} from "playwright-core";
import {existsSync} from "node:fs";
import {join} from "node:path";

const targetUrl = new URL(process.env.FABRIC_BASE_URL || "http://127.0.0.1:8000/legacy");
if (targetUrl.pathname === "/") targetUrl.pathname = "/legacy";
const baseUrl = targetUrl.href;
// playwright-core does not install a browser. Reuse a local browser, never download one.
const configuredBrowser = process.env.BROWSER_EXECUTABLE_PATH || process.env.EDGE_PATH;
const platformBrowsers = {
  darwin: [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
  ],
  linux: ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"],
  win32: [
    join(process.env.ProgramFiles || "C:\\Program Files", "Google/Chrome/Application/chrome.exe"),
    join(process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)", "Microsoft/Edge/Application/msedge.exe"),
  ],
};
const executablePath = configuredBrowser || [
  chromium.executablePath(), ...(platformBrowsers[process.platform] || []),
].find((path) => existsSync(path));
if (!executablePath || !existsSync(executablePath)) {
  throw new Error("No local browser found. Set BROWSER_EXECUTABLE_PATH to a Chromium/Chrome/Edge executable (EDGE_PATH is also supported).");
}

const browser = await chromium.launch({headless: true, executablePath});
const page = await browser.newPage({viewport: {width: 1440, height: 1200}});

try {
  await page.goto(baseUrl, {waitUntil: "networkidle"});
  await page.getByRole("button", {name: "加载 3 节点演示"}).click();
  await page.getByText("Fabric 拓扑与放置").waitFor({timeout: 15000});
  await page.getByText("job-multi-01").first().waitFor();
  await page.getByText("rail 对齐").first().waitFor();

  const scenarioResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/runs") &&
      response.request().method() === "POST"
  );
  await page.getByRole("button", {name: "运行故障场景"}).click();
  if (!(await scenarioResponse).ok()) {
    throw new Error("Fault scenario request failed");
  }
  await page.getByText("GPU moved").waitFor({timeout: 15000});

  const comparisonResponses = [];
  const listenForRun = () =>
    page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/runs") &&
        response.request().method() === "POST"
    );
  comparisonResponses.push(listenForRun());
  comparisonResponses.push(listenForRun());
  await page.getByRole("button", {name: "对比策略"}).click();
  for (const responsePromise of comparisonResponses) {
    const response = await responsePromise;
    if (!response.ok()) throw new Error("Strategy comparison run failed");
  }
  await page.getByText("策略对比").waitFor({timeout: 15000});

  await page.getByRole("button", {name: "优先级 +10 并创建修订版"}).click();
  await page.getByText("版本差异").waitFor({timeout: 15000});
  await page.getByRole("button", {name: "提交新修订版"}).click();
  await page.getByText("新修订版已提交").waitFor({timeout: 15000});

  await page.screenshot({
    path: "../../tmp/screenshots/workspace.png",
    fullPage: true
  });

  const bodyText = await page.locator("body").innerText();
  for (const expected of [
    "FabricSandbox",
    "放置方案",
    "故障与重规划",
    "链路负载与流量",
    "修订版生命周期",
    "策略对比",
    "版本差异",
    "job-multi-01"
  ]) {
    if (!bodyText.includes(expected)) {
      throw new Error(`Missing rendered text: ${expected}`);
    }
  }

  console.log("UI smoke test passed");
} finally {
  await browser.close();
}
