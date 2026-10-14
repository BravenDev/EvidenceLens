"use strict";

// This test owns its server, temporary data, and simulated Ollama HTTP service.
// It checks integration and UI behavior, not a real model's answer quality.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const http = require("node:http");
const { spawn } = require("node:child_process");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const python =
  process.env.EVIDENCELENS_PYTHON ||
  path.join(
    root,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function waitUntil(predicate, message) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(message);
}

async function simulatedOllama() {
  const state = {
    installed: false,
    holdEmbeddings: false,
    release: [],
    chatCalls: 0,
    embedCalls: 0,
    repairs: 0,
  };
  const server = http.createServer(async (request, response) => {
    function send(data) {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify(data));
    }
    try {
      if (request.url === "/api/tags") {
        send({
          models: state.installed
            ? [{ name: "browser-chat" }, { name: "browser-embed" }]
            : [],
        });
        return;
      }
      let body = "";
      for await (const chunk of request) body += chunk;
      const data = JSON.parse(body);
      if (request.url === "/api/embed") {
        state.embedCalls++;
        if (state.holdEmbeddings)
          await new Promise((resolve) => state.release.push(resolve));
        send({ embeddings: data.input.map(() => [1, 1, 1]) });
      } else if (request.url === "/api/chat") {
        state.chatCalls++;
        const repair = data.messages[0].content.includes(
          "previous attempt failed",
        );
        if (repair) state.repairs++;
        const sources = JSON.parse(data.messages[1].content).evidence;
        const source = sources.find(
          (item) =>
            item.document === "security-policy.md" &&
            item.text.includes("30 days"),
        );
        assert.ok(
          source,
          "The real retriever must supply the policy passage to the adapter.",
        );
        const id = repair ? source.chunk_id : 999999;
        send({
          message: {
            content: JSON.stringify({
              answer: `Operational audit logs are retained for 30 days [S${id}].`,
              citations: [id],
              abstained: false,
            }),
          },
        });
      } else {
        response.writeHead(404);
        response.end();
      }
    } catch (error) {
      response.writeHead(500, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: error.message }));
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { state, server, url: `http://127.0.0.1:${server.address().port}` };
}

async function main() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "evidencelens-browser-"));
  const port = await freePort();
  const url = `http://127.0.0.1:${port}`;
  const model = await simulatedOllama();
  let serverError,
    startupLog = "";
  const server = spawn(
    python,
    [
      "-m",
      "uvicorn",
      "backend.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
    ],
    {
      cwd: root,
      env: {
        ...process.env,
        EVIDENCELENS_DATA_DIR: temp,
        OLLAMA_BASE_URL: model.url,
        EVIDENCELENS_CHAT_MODEL: "browser-chat",
        EVIDENCELENS_EMBEDDING_MODEL: "browser-embed",
      },
      stdio: ["ignore", "ignore", "pipe"],
    },
  );
  server.on("error", (error) => {
    serverError = error;
  });
  server.stderr.on("data", (chunk) => {
    startupLog = (startupLog + chunk).slice(-4000);
  });
  let browser;
  const checks = [];
  async function readApi(route) {
    const response = await fetch(url + route);
    assert.ok(response.ok, `GET ${route} returned ${response.status}`);
    return response.json();
  }
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      if (serverError) throw serverError;
      try {
        if ((await fetch(`${url}/api/health`)).ok) {
          ready = true;
          break;
        }
      } catch {}
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!ready)
      throw new Error(
        `The isolated server did not start. Check the Python interpreter.\n${startupLog}`,
      );
    browser = await chromium.launch({
      headless: true,
      executablePath: process.env.EVIDENCELENS_BROWSER_EXECUTABLE || undefined,
      args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    });
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1050 },
    });
    async function capture(name) {
      if (!process.env.EVIDENCELENS_SCREENSHOT_DIR) return;
      fs.mkdirSync(process.env.EVIDENCELENS_SCREENSHOT_DIR, {
        recursive: true,
      });
      await page.screenshot({
        path: path.join(process.env.EVIDENCELENS_SCREENSHOT_DIR, `${name}.png`),
        fullPage: true,
      });
    }
    async function waitIdle() {
      await page.waitForFunction(
        () => !document.querySelector("#ask-button").disabled,
      );
    }
    async function exportResult() {
      const event = page.waitForEvent("download");
      await page
        .getByRole("button", { name: "Export notes", exact: true })
        .click();
      const download = await event;
      assert.match(
        download.suggestedFilename(),
        /^EvidenceLens_\d{4}-\d{2}-\d{2}\.md$/,
      );
      return fs.readFileSync(await download.path(), "utf8");
    }
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(url);
    await page.waitForFunction(
      () =>
        !document.querySelector("#ai-status").textContent.includes("Checking"),
    );
    assert.match(await page.locator("h1").textContent(), /Research with/);
    await capture("empty");
    await page.click("#ask-button");
    await page.getByText("Enter a question or a few search terms.").waitFor();
    checks.push("home and empty-input handling");

    await page.click("#sample-button");
    await page.waitForFunction(
      () => document.querySelectorAll(".document-row").length === 3,
    );
    await waitIdle();
    await page.click("#sample-button");
    await page
      .getByText("The sample documents are already in your library.")
      .waitFor();
    assert.equal(await page.locator(".document-row").count(), 3);
    checks.push("sample collection and duplicate prevention");

    await page.fill("#question", "operational audit logs retained");
    await page.click("#search-button");
    await page.waitForSelector(".source-card");
    assert.match(
      await page.locator(".source-card").first().textContent(),
      /security-policy/,
    );
    await page.locator(".source-card").first().click();
    await page.waitForSelector("#source-dialog[open]");
    assert.match(
      await page.locator("#source-text mark").textContent(),
      /30 days/,
    );
    await capture("source");
    const downloadEvent = page.waitForEvent("download");
    await page.click("#source-download");
    assert.equal(
      (await downloadEvent).suggestedFilename(),
      "security-policy.md",
    );
    await page.keyboard.press("Escape");
    checks.push(
      "ranked search, exact source highlighting, and original download",
    );

    await page.fill("#document-filter", "pilot-brief");
    await page.locator(".document-select").click();
    await page.fill("#document-filter", "");
    await page.fill("#question", "pilot timeline");
    await page.locator("#question").press("Control+Enter");
    await page
      .locator(".answer-label")
      .filter({ hasText: "EVIDENCE MATCHES" })
      .waitFor();
    const scopedNames = await page
      .locator(".source-filename")
      .allTextContents();
    assert.ok(
      scopedNames.length > 0 &&
        scopedNames.every((name) => name === "pilot-brief.md"),
    );
    await capture("research");
    const pilotRun = (await readApi("/api/history")).runs.find(
      (run) => run.query === "pilot timeline",
    );
    assert.ok(pilotRun);
    const pilotNotes = await exportResult();
    assert.match(pilotNotes, /pilot-brief\.md/);
    assert.match(pilotNotes, /Source passages/);
    assert.match(pilotNotes, /\[S\d+\]/);
    await page.click("#all-documents");
    checks.push(
      "document filter, scope, keyboard submission, and cited Markdown export",
    );

    const text =
      '<img src=x onerror="window.xssHit=true"> Browser spectrometer evidence.\n\n```\nUntrusted Markdown stays inside a text fence.\n```';
    await page.locator("#file-input").setInputFiles({
      name: "browser-smoke.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(text),
    });
    await page.waitForFunction(
      () => document.querySelectorAll(".document-row").length === 4,
    );
    await waitIdle();
    await page.fill("#question", "spectrometer");
    await page.click("#search-button");
    await page.waitForSelector(".source-card");
    assert.equal(await page.locator(".source-excerpt img").count(), 0);
    assert.equal(await page.evaluate(() => window.xssHit), undefined);
    assert.match(await page.locator(".source-excerpt").textContent(), /<img/);
    const untrustedNotes = await exportResult();
    assert.ok(untrustedNotes.includes("````text\n" + text + "\n````"));
    await page.locator("#file-input").setInputFiles({
      name: "renamed.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(text),
    });
    await page.waitForFunction(() =>
      document.querySelector("#notice").textContent.includes("duplicate"),
    );
    assert.equal(await page.locator(".document-row").count(), 4);
    checks.push(
      "uploads, duplicate detection, literal HTML, and safe export fences",
    );

    await waitIdle();
    await page.fill("#question", "pilot telescope");
    await page.click("#ask-button");
    await page.getByText("INSUFFICIENT EVIDENCE").waitFor();
    await page.click("#help-button");
    await page.waitForSelector("#help-dialog[open]");
    assert.match(
      await page.locator("#model-commands").textContent(),
      /ollama pull/,
    );
    model.state.installed = true;
    await page.click("#refresh-status");
    await page.waitForFunction(() =>
      document.querySelector("#ai-status").textContent.includes("ready"),
    );
    await page.waitForSelector("#help-dialog", { state: "hidden" });
    checks.push("weak-match rejection and local AI setup status");

    const fixture = Array.from(
      { length: 90 },
      (_, index) =>
        `Section ${index + 1}. This indexing rehearsal paragraph contains locally stored reference material and enough text to exercise multiple embedding batches.`,
    ).join("\n\n");
    await page.locator("#file-input").setInputFiles({
      name: "index-fixture.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(fixture),
    });
    await page.waitForFunction(
      () => document.querySelectorAll(".document-row").length === 5,
    );
    await waitIdle();
    model.state.holdEmbeddings = true;
    await page.click("#index-button");
    await page.waitForSelector("#index-progress", { state: "visible" });
    await waitUntil(
      () => model.state.release.length > 0,
      "The simulated embedding batch did not start.",
    );
    const before = (await readApi("/api/health")).storage;
    assert.ok(before.chunks > 8);
    // Keyword search remains available while an actual HTTP embedding request waits.
    await page.fill("#question", "audit logs");
    await page.click("#search-button");
    await page.waitForSelector(".source-card");
    assert.equal(await page.locator("#index-button").isDisabled(), true);
    await page.reload();
    await page.waitForSelector("#index-progress", { state: "visible" });
    await page.click("#cancel-index");
    await page.waitForFunction(() =>
      document
        .querySelector("#index-progress-detail")
        .textContent.includes("Stopping"),
    );
    model.state.holdEmbeddings = false;
    model.state.release.splice(0).forEach((resolve) => resolve());
    await page.waitForFunction(() =>
      document
        .querySelector("#notice")
        .textContent.includes("Indexing stopped"),
    );
    const stopped = (await readApi("/api/health")).storage;
    assert.equal(stopped.embedded_chunks, 8);
    assert.equal(await page.locator("#index-button").isDisabled(), false);
    await page.click("#index-button");
    await page.waitForFunction(() =>
      document
        .querySelector("#notice")
        .textContent.includes("Semantic index ready"),
    );
    const done = (await readApi("/api/health")).storage;
    assert.equal(done.embedded_chunks, done.chunks);
    checks.push(
      "background progress, concurrent search, reload, cancellation, and cached resume",
    );

    await page.selectOption("#retrieval", "hybrid");
    await page.click("#ai-mode");
    const aiQuestion = "How long are audit logs retained?";
    await page.fill("#question", aiQuestion);
    await page.click("#ask-button");
    await page.getByText("LOCAL AI ANSWER", { exact: true }).waitFor();
    await waitIdle();
    assert.equal(model.state.chatCalls, 2);
    assert.equal(model.state.repairs, 1);
    assert.match(
      await page.locator(".answer-text").textContent(),
      /30 days \[S\d+\]/,
    );
    await page.locator(".answer-text .source-ref").click();
    await page.waitForSelector("#source-dialog[open]");
    assert.match(
      await page.locator("#source-text mark").textContent(),
      /30 days/,
    );
    await page.keyboard.press("Escape");
    await capture("ai-answer");
    checks.push(
      "real HTTP adapter, hybrid integration, format repair, and clickable AI citations (simulated model)",
    );

    const aiRun = (await readApi("/api/history")).runs.find(
      (run) => run.query === aiQuestion,
    );
    assert.equal((await readApi(`/api/history/${aiRun.id}`)).repaired, true);
    await page.reload();
    await page.waitForSelector("#recent-history .history-entry");
    model.state.installed = false;
    await page.click("#help-button");
    await page.click("#refresh-status");
    await page.waitForFunction(() =>
      document
        .querySelector("#ai-status")
        .textContent.includes("not installed"),
    );
    await page.click("#help-close");
    const callsBefore = [model.state.chatCalls, model.state.embedCalls];
    await page.click("#history-button");
    await page
      .locator("#history-list .history-entry")
      .filter({ hasText: aiQuestion })
      .click();
    await page.getByText("LOCAL AI ANSWER", { exact: true }).waitFor();
    assert.equal(
      await page.locator("#ai-mode").getAttribute("aria-pressed"),
      "true",
    );
    assert.equal(await page.locator("#retrieval").inputValue(), "hybrid");
    assert.deepEqual(
      [model.state.chatCalls, model.state.embedCalls],
      callsBefore,
    );
    const aiNotes = await exportResult();
    assert.match(aiNotes, /30 days \[S\d+\]/);
    assert.match(aiNotes, /security-policy\.md/);
    assert.match(aiNotes, /Cited in the answer/);
    checks.push(
      "saved AI answer reopens offline without inference and exports full sources",
    );

    await page
      .getByRole("button", { name: "Remove browser-smoke.txt", exact: true })
      .click();
    await page.click("#delete-confirm");
    await page.waitForFunction(
      () => document.querySelectorAll(".document-row").length === 4,
    );
    await page.reload();
    await page.waitForFunction(
      () => document.querySelectorAll(".document-row").length === 4,
    );
    const saved = (await readApi("/api/history")).runs;
    assert.ok(saved.some((run) => run.id === pilotRun.id));
    assert.ok(!saved.some((run) => run.query === "spectrometer"));
    checks.push(
      "document deletion cleans related history and preserves unrelated saved research",
    );

    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    await page.click("#evidence-mode");
    await page.selectOption("#retrieval", "keyword");
    await page.fill("#question", "security exceptions");
    await page.click("#ask-button");
    await page.waitForSelector(".source-card");
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    assert.match(
      await page.locator(".source-excerpt").first().textContent(),
      /Security exceptions require written approval/,
    );
    assert.equal(
      await page.locator("#library-toggle").getAttribute("aria-expanded"),
      "false",
    );
    await page.locator("#results").scrollIntoViewIfNeeded();
    await capture("mobile");
    await page.click("#library-toggle");
    assert.equal(
      await page.locator("#library-toggle").getAttribute("aria-expanded"),
      "true",
    );
    await page.click("#history-button");
    await page
      .locator("#history-list")
      .getByRole("button", {
        name: `Delete saved research: ${aiQuestion}`,
        exact: true,
      })
      .click();
    await page.waitForFunction(
      (query) =>
        !Array.from(
          document.querySelectorAll("#history-list .history-query"),
        ).some((node) => node.textContent === query),
      aiQuestion,
    );
    await page.click("#clear-history");
    await page.click("#clear-history-confirm");
    await page.waitForFunction(() =>
      document.querySelector("#notice").textContent.includes("history cleared"),
    );
    assert.equal((await readApi("/api/history")).runs.length, 0);
    assert.equal((await readApi("/api/documents")).documents.length, 4);
    assert.deepEqual(errors, []);
    checks.push(
      "mobile library toggle, saved-result deletion, clear history, and zero JavaScript errors",
    );
    console.log(
      JSON.stringify(
        {
          status: "passed",
          checks,
          browser: browser.version(),
          model: "simulated HTTP service; no real inference",
        },
        null,
        2,
      ),
    );
  } finally {
    model.state.holdEmbeddings = false;
    model.state.release.splice(0).forEach((resolve) => resolve());
    if (browser) await browser.close();
    if (server.exitCode === null) {
      await new Promise((resolve) => {
        const timer = setTimeout(() => {
          server.kill("SIGKILL");
          resolve();
        }, 2500);
        server.once("exit", () => {
          clearTimeout(timer);
          resolve();
        });
        server.kill();
      });
    }
    await new Promise((resolve) => model.server.close(resolve));
    fs.rmSync(temp, {
      recursive: true,
      force: true,
      maxRetries: 3,
      retryDelay: 100,
    });
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
