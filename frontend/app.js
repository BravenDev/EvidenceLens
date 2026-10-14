"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  documents: [],
  history: [],
  scope: null,
  mode: "evidence",
  busy: false,
  aiReady: false,
  pendingDelete: null,
  lastResult: null,
  indexJob: null,
  indexTimer: null,
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.setAttribute("aria-hidden", "true");
  svg.append(use);
  return svg;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers:
      options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" },
    signal: options.signal || AbortSignal.timeout(300000),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(
      "The server returned an unreadable response. Check the VS Code terminal.",
    );
  }
  if (!response.ok)
    throw new Error(
      data.error?.message || `Request failed (${response.status}).`,
    );
  return data;
}

function notice(message, type = "") {
  $("notice").textContent = message;
  $("notice").className = `notice ${type}`;
  $("notice").hidden = !message;
}

function failure(error) {
  const message = ["TimeoutError", "AbortError"].includes(error.name)
    ? "The request timed out. Check the server terminal and local model, then try again."
    : error.message === "Failed to fetch"
      ? "Cannot reach the application. Check that the server is running in VS Code."
      : error.message;
  notice(message, "error");
}

function busy(value) {
  state.busy = value;
  for (const id of [
    "ask-button",
    "search-button",
    "upload-button",
    "sample-button",
    "index-button",
    "refresh-status",
    "delete-confirm",
  ])
    $(id).disabled = value;
  $("file-input").disabled = value;
  $("index-button").disabled = value || Boolean(state.indexJob);
  $("results").setAttribute("aria-busy", String(value));
}

function clearResults() {
  state.lastResult = null;
  $("results").replaceChildren();
  $("results").hidden = true;
  $("empty-state").hidden = false;
  document.body.classList.remove("has-results");
}

function renderDocuments() {
  const filter = $("document-filter").value.toLocaleLowerCase();
  const list = $("document-list");
  list.replaceChildren();
  $("sidebar-count").textContent = state.documents.length;
  $("all-count").textContent = state.documents.length;
  $("all-documents").classList.toggle("active", !state.scope);
  const selected = state.documents.find((doc) => doc.id === state.scope);
  $("scope-label").textContent = selected
    ? `Searching ${selected.filename}`
    : "Searching all documents";
  const filtered = state.documents.filter((doc) =>
    doc.filename.toLocaleLowerCase().includes(filter),
  );
  if (!filtered.length)
    list.append(
      element(
        "p",
        "library-empty",
        state.documents.length
          ? "No documents match this filter."
          : "Your library is empty. Add a document or try the sample collection.",
      ),
    );
  for (const doc of filtered) {
    const row = element(
      "div",
      `document-row${state.scope === doc.id ? " selected" : ""}`,
    );
    const select = element("button", "document-select");
    select.type = "button";
    select.title = `Search within ${doc.filename}`;
    select.setAttribute("aria-pressed", String(state.scope === doc.id));
    const meta = element("span", "document-meta");
    meta.append(element("span", "document-name", doc.filename));
    const detail = `${doc.kind.toUpperCase()} · ${doc.chunk_count} passage${doc.chunk_count === 1 ? "" : "s"}${doc.embedded_chunks === doc.chunk_count ? " · indexed" : ""}`;
    meta.append(element("span", "document-detail", detail));
    select.append(icon("file"), meta);
    select.addEventListener("click", () => {
      state.scope = state.scope === doc.id ? null : doc.id;
      renderDocuments();
    });
    const remove = element("button", "icon-button document-delete");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${doc.filename}`);
    remove.append(icon("trash"));
    remove.addEventListener("click", () => {
      if (state.busy) return;
      state.pendingDelete = doc;
      $("delete-name").textContent = doc.filename;
      $("delete-dialog").showModal();
    });
    row.append(select, remove);
    list.append(row);
  }
}

async function refresh() {
  const [library, status, history] = await Promise.all([
    api("/api/documents"),
    api("/api/status"),
    api("/api/history"),
  ]);
  state.documents = library.documents;
  state.history = history.runs;
  renderHistory();
  if (!state.documents.some((doc) => doc.id === state.scope))
    state.scope = null;
  renderDocuments();
  const counts = status.storage;
  $("library-summary").textContent = counts.documents
    ? `${counts.documents} document${counts.documents === 1 ? "" : "s"} · ${counts.chunks} searchable passages`
    : "Your research workspace is ready.";
  state.aiReady = status.ai.chat_ready;
  $("ai-dot").classList.toggle("ready", state.aiReady);
  $("ai-status").textContent = state.aiReady
    ? "Local AI is ready"
    : status.ai.available
      ? "Chat model not installed"
      : "Local AI is off";
  $("ai-detail").textContent = state.aiReady
    ? status.chat_model
    : "Evidence search is ready";
  $("model-commands").textContent =
    `ollama pull ${status.chat_model}\nollama pull ${status.embedding_model}`;
}

async function uploadFiles(files) {
  if (!files.length || state.busy) return;
  busy(true);
  let added = 0,
    duplicates = 0;
  const warnings = [];
  try {
    for (const file of files) {
      notice(`Adding ${file.name}…`);
      if (file.size > 10 * 1024 * 1024)
        throw new Error(`${file.name} exceeds the 10 MB document limit.`);
      const form = new FormData();
      form.append("file", file);
      const result = await api("/api/documents", {
        method: "POST",
        body: form,
      });
      result.duplicate ? duplicates++ : added++;
      if (result.warning) warnings.push(result.warning);
    }
    clearResults();
    notice(
      `${added} document${added === 1 ? "" : "s"} added${duplicates ? `; ${duplicates} duplicate${duplicates === 1 ? "" : "s"} skipped` : ""}.${warnings.length ? ` ${warnings.join(" ")}` : ""}`,
      "success",
    );
  } catch (error) {
    failure(error);
  } finally {
    try {
      await refresh();
    } catch (error) {
      failure(error);
    }
    $("file-input").value = "";
    busy(false);
  }
}

async function loadSamples() {
  if (state.busy) return;
  busy(true);
  notice("Adding the fictional sample collection…");
  try {
    const result = await api("/api/demo", { method: "POST", body: "{}" });
    await refresh();
    clearResults();
    notice(
      result.loaded
        ? `${result.loaded} sample documents added. Try one of the suggested questions.`
        : "The sample documents are already in your library.",
      "success",
    );
  } catch (error) {
    failure(error);
  } finally {
    busy(false);
  }
}

function appendAnswer(container, text, sources) {
  const map = new Map(sources.map((item) => [item.chunk_id, item]));
  const pattern = /\[S(\d+)\]/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    container.append(document.createTextNode(text.slice(last, match.index)));
    const found = map.get(Number(match[1]));
    if (found) {
      const link = element("button", "source-ref", match[0]);
      link.type = "button";
      link.setAttribute(
        "aria-label",
        `Inspect source ${found.label} in ${found.filename}`,
      );
      link.addEventListener("click", () => inspectSource(found));
      container.append(link);
    } else container.append(document.createTextNode(match[0]));
    last = match.index + match[0].length;
  }
  container.append(document.createTextNode(text.slice(last)));
}

function excerptNearQuestion(text, query) {
  if (text.length <= 290) return text;
  const ignored = new Set(
    "what who how when where why is are the a an for to of do does in on can my".split(
      " ",
    ),
  );
  const terms = [
    ...new Set(
      (query.toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu) || []).filter(
        (word) => word.length > 1 && !ignored.has(word),
      ),
    ),
  ];
  const lowered = text.toLocaleLowerCase();
  let bestStart = 0,
    bestScore = 0;
  for (const term of terms) {
    let offset = 0;
    for (let count = 0; count < 40; count++) {
      const position = lowered.indexOf(term, offset);
      if (position < 0) break;
      const start = Math.max(0, position - 65);
      const window = lowered.slice(start, start + 290);
      const score = terms.filter((word) => window.includes(word)).length;
      if (score > bestScore) {
        bestScore = score;
        bestStart = start;
      }
      offset = position + term.length;
    }
  }
  const end = Math.min(text.length, bestStart + 290);
  return `${bestStart ? "…" : ""}${text.slice(bestStart, end).trim()}${end < text.length ? "…" : ""}`;
}

function sourceCard(item, cited, mode, query) {
  const card = element("button", "source-card");
  card.type = "button";
  card.setAttribute(
    "aria-label",
    `Inspect ${item.filename}, ${item.location}, source ${item.label}`,
  );
  const head = element("span", "source-card-head");
  head.append(
    element("span", "source-tag", item.label),
    element("span", "source-filename", item.filename),
    icon("arrow"),
  );
  const excerpt = item.excerpt || excerptNearQuestion(item.text, query);
  const footer = element("span", "source-card-footer");
  footer.append(
    element("span", "", item.location),
    element(
      "span",
      "",
      mode === "ai"
        ? cited
          ? "Cited in answer"
          : "Retrieved passage"
        : "Inspect source ↗",
    ),
  );
  card.append(head, element("span", "source-excerpt", excerpt), footer);
  card.addEventListener("click", () => inspectSource(item));
  return card;
}

function renderResult(data, action) {
  state.lastResult = data;
  const root = $("results");
  root.replaceChildren();
  root.hidden = false;
  $("empty-state").hidden = true;
  document.body.classList.add("has-results");
  if (window.innerWidth <= 760) setLibraryCollapsed(true);
  const heading = element("div", "result-heading");
  const headerActions = element("div", "result-header-actions");
  const exported = element("button", "export-button", "Export notes");
  exported.type = "button";
  exported.prepend(icon("download"));
  exported.addEventListener("click", () => exportNotes(data, action));
  headerActions.append(
    element(
      "span",
      "result-meta",
      `${data.retrieval === "hybrid" ? "Hybrid" : "Keyword"} · ${data.elapsed_ms} ms`,
    ),
    exported,
  );
  heading.append(element("h2", "", data.query), headerActions);
  root.append(heading);
  if (action === "ask") {
    const panel = element(
      "div",
      `answer-panel${data.mode === "ai" ? " ai" : ""}`,
    );
    const top = element("div", "answer-top");
    top.append(
      element(
        "span",
        "answer-label",
        data.abstained
          ? "INSUFFICIENT EVIDENCE"
          : data.mode === "ai"
            ? "LOCAL AI ANSWER"
            : "EVIDENCE MATCHES",
      ),
    );
    const copy = element("button", "icon-button");
    copy.type = "button";
    copy.setAttribute("aria-label", "Copy research result");
    copy.append(icon("copy"));
    copy.addEventListener("click", async () => {
      try {
        const references = (data.citations || [])
          .map((s) => `[${s.label}] ${s.filename}, ${s.location}`)
          .join("\n");
        await navigator.clipboard.writeText(
          `${data.query}\n\n${data.answer}\n\n${references}`.trim(),
        );
        notice("Research result copied.", "success");
      } catch {
        notice(
          "Clipboard access is unavailable. Select the answer text to copy it.",
        );
      }
    });
    top.append(copy);
    const answer = element("div", "answer-text");
    appendAnswer(answer, data.answer, data.sources);
    panel.append(top, answer);
    if (data.mode === "ai" && !data.abstained)
      panel.append(
        element(
          "div",
          "answer-note",
          "Verify the passages before relying on the answer. A valid source reference does not guarantee a correct interpretation.",
        ),
      );
    root.append(panel);
  }
  const sources = data.sources || [];
  if (sources.length) {
    const sourceHeading = element("div", "sources-heading");
    sourceHeading.append(
      element("span", "", "Retrieved evidence"),
      element(
        "span",
        "",
        `${sources.length} passage${sources.length === 1 ? "" : "s"} · ${new Set(sources.map((s) => s.document_id)).size} document${new Set(sources.map((s) => s.document_id)).size === 1 ? "" : "s"}`,
      ),
    );
    const grid = element("div", "sources-grid");
    const cited = new Set((data.citations || []).map((s) => s.chunk_id));
    for (const item of sources)
      grid.append(
        sourceCard(item, cited.has(item.chunk_id), data.mode, data.query),
      );
    root.append(sourceHeading, grid);
  } else if (action === "search")
    root.append(
      element(
        "div",
        "answer-panel",
        "No matching passages were found. Try different search terms or another document.",
      ),
    );
}

async function runResearch(action) {
  if (state.busy) return;
  const query = $("question").value.trim();
  if (!query) {
    notice("Enter a question or a few search terms.");
    $("question").focus();
    return;
  }
  if (!state.documents.length) {
    notice("Add a document or load the sample collection first.");
    return;
  }
  busy(true);
  notice("");
  $("empty-state").hidden = true;
  const loading = element("div", "loading-state");
  loading.append(
    element("span", "spinner"),
    element(
      "span",
      "",
      action === "ask" && state.mode === "ai"
        ? "Retrieving evidence and preparing a local AI answer…"
        : "Finding relevant passages…",
    ),
  );
  $("results").replaceChildren(loading);
  $("results").hidden = false;
  const request = {
    query,
    retrieval: $("retrieval").value,
    document_id: state.scope,
    limit: 6,
  };
  if (action === "ask") request.mode = state.mode;
  try {
    const data = await api(action === "ask" ? "/api/ask" : "/api/search", {
      method: "POST",
      body: JSON.stringify(request),
    });
    renderResult(data, action);
    // A failed sidebar refresh should not discard a successful research result.
    try {
      await refreshHistory();
    } catch {
      notice(
        "Your result is ready, but the history list could not refresh. Reload to try again.",
      );
    }
  } catch (error) {
    clearResults();
    failure(error);
  } finally {
    busy(false);
  }
}

async function inspectSource(item) {
  try {
    const data = await api(item.url);
    $("source-title").textContent = data.filename;
    $("source-location").textContent = `${data.label} · ${data.location}`;
    const pre = $("source-text");
    const highlight = element(
      "mark",
      "",
      data.page_text.slice(data.start, data.end),
    );
    pre.replaceChildren(
      document.createTextNode(data.page_text.slice(0, data.start)),
      highlight,
      document.createTextNode(data.page_text.slice(data.end)),
    );
    $("source-download").href = `/api/documents/${data.document_id}/download`;
    $("source-dialog").showModal();
    requestAnimationFrame(() => highlight.scrollIntoView({ block: "center" }));
  } catch (error) {
    failure(error);
  }
}

function chooseMode(mode) {
  if (mode === "ai" && !state.aiReady) {
    $("help-dialog").showModal();
    return;
  }
  setMode(mode);
}

function setMode(mode) {
  state.mode = mode;
  for (const name of ["evidence", "ai"]) {
    $(`${name}-mode`).classList.toggle("selected", mode === name);
    $(`${name}-mode`).setAttribute("aria-pressed", String(mode === name));
  }
}

$("upload-button").addEventListener("click", () => $("file-input").click());
$("file-input").addEventListener("change", (event) =>
  uploadFiles(Array.from(event.target.files)),
);
for (const event of ["dragenter", "dragover"])
  $("drop-zone").addEventListener(event, (e) => {
    e.preventDefault();
    $("drop-zone").classList.add("dragover");
  });
for (const event of ["dragleave", "drop"])
  $("drop-zone").addEventListener(event, (e) => {
    e.preventDefault();
    $("drop-zone").classList.remove("dragover");
  });
$("drop-zone").addEventListener("drop", (e) =>
  uploadFiles(Array.from(e.dataTransfer.files)),
);
$("document-filter").addEventListener("input", renderDocuments);
$("all-documents").addEventListener("click", () => {
  state.scope = null;
  renderDocuments();
});
$("sample-button").addEventListener("click", loadSamples);
$("ask-button").addEventListener("click", () => runResearch("ask"));
$("search-button").addEventListener("click", () => runResearch("search"));
$("question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    runResearch("ask");
  }
});
$("evidence-mode").addEventListener("click", () => chooseMode("evidence"));
$("ai-mode").addEventListener("click", () => chooseMode("ai"));
for (const chip of document.querySelectorAll("[data-prompt]"))
  chip.addEventListener("click", () => {
    $("question").value = chip.dataset.prompt;
    $("question").focus();
  });
$("source-close").addEventListener("click", () => $("source-dialog").close());
$("help-button").addEventListener("click", () => $("help-dialog").showModal());
$("help-close").addEventListener("click", () => $("help-dialog").close());
$("refresh-status").addEventListener("click", async () => {
  try {
    await refresh();
    if (state.aiReady) {
      $("help-dialog").close();
      notice("Local AI is ready. Select AI answer to use it.", "success");
    } else
      notice(
        "The chat model is not ready yet. Keep Ollama running and check the model download.",
      );
  } catch (error) {
    failure(error);
  }
});
$("index-button").addEventListener("click", async () => {
  if (state.busy || state.indexJob) return;
  $("index-button").disabled = true;
  try {
    const job = await api("/api/index-jobs", {
      method: "POST",
      body: JSON.stringify({ document_id: null, force: false }),
    });
    state.indexJob = job.id;
    sessionStorage.setItem("evidencelens-index-job", job.id);
    $("index-progress").hidden = false;
    $("index-progress").scrollIntoView({
      block: "nearest",
      behavior: "smooth",
    });
    await pollIndex();
  } catch (error) {
    failure(error);
  } finally {
    $("index-button").disabled = state.busy || Boolean(state.indexJob);
  }
});
$("delete-cancel").addEventListener("click", () => $("delete-dialog").close());
$("delete-confirm").addEventListener("click", async () => {
  if (!state.pendingDelete || state.busy) return;
  const id = state.pendingDelete.id;
  busy(true);
  $("delete-dialog").close();
  try {
    await api(`/api/documents/${id}`, { method: "DELETE" });
    clearResults();
    await refresh();
    notice("Document removed from your workspace.", "success");
  } catch (error) {
    failure(error);
  } finally {
    state.pendingDelete = null;
    busy(false);
  }
});

refresh().catch(failure);

function renderHistory() {
  const recent = $("recent-history");
  const full = $("history-list");
  recent.replaceChildren();
  full.replaceChildren();
  $("history-button").textContent = state.history.length
    ? `View all (${state.history.length})`
    : "View all";
  if (!state.history.length) {
    recent.append(
      element("p", "history-empty", "Your research will appear here."),
    );
    full.append(
      element(
        "p",
        "history-empty",
        "Run a search or ask a question to save your first result.",
      ),
    );
  }
  for (const [index, run] of state.history.entries()) {
    function entry(compact) {
      const button = element(
        "button",
        compact ? "history-entry compact" : "history-entry",
      );
      button.type = "button";
      const when = new Date(run.created_at).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
      button.append(
        element("span", "history-query", run.query),
        element(
          "span",
          "history-meta",
          `${run.action === "ask" ? "Question" : "Search"} · ${when}`,
        ),
      );
      button.addEventListener("click", () => openHistory(run.id));
      return button;
    }
    if (index < 3) recent.append(entry(true));
    const row = element("div", "history-row");
    const remove = element("button", "icon-button");
    remove.type = "button";
    remove.setAttribute("aria-label", `Delete saved research: ${run.query}`);
    remove.append(icon("trash"));
    remove.addEventListener("click", async () => {
      try {
        await api(`/api/history/${run.id}`, { method: "DELETE" });
        await refreshHistory();
      } catch (error) {
        failure(error);
      }
    });
    row.append(entry(false), remove);
    full.append(row);
  }
  $("clear-history").disabled = !state.history.length;
}

async function refreshHistory() {
  const data = await api("/api/history");
  state.history = data.runs;
  renderHistory();
}

async function openHistory(id) {
  if (state.busy) return;
  try {
    const data = await api(`/api/history/${id}`);
    $("history-dialog").close();
    $("question").value = data.query;
    state.scope = data.document_id;
    $("retrieval").value = data.retrieval;
    // Saved AI answers can be inspected even when the local model is offline.
    if (data.action === "ask") setMode(data.mode);
    renderDocuments();
    renderResult(data, data.action);
    $("results").scrollIntoView({ block: "start", behavior: "smooth" });
    notice("Opened a saved result. No model request was made.");
  } catch (error) {
    failure(error);
  }
}

function markdownText(text) {
  return String(text).replace(/[\\`*_{}\[\]<>()!#+]/g, "\\$&");
}
function fenced(text) {
  const runs = String(text).match(/`+/g) || [];
  const fence = "`".repeat(Math.max(3, ...runs.map((run) => run.length + 1)));
  return `${fence}text\n${text}\n${fence}`;
}
function exportNotes(data, action) {
  const lines = [
    "# BraveTrace research notes",
    "",
    `- Question: ${markdownText(data.query)}`,
    `- Mode: ${action === "search" ? "Search" : data.mode === "ai" ? "Local AI answer" : "Evidence excerpts"}`,
    `- Retrieval: ${data.retrieval}`,
    `- Saved: ${data.created_at || new Date().toISOString()}`,
  ];
  if (data.model) lines.push(`- Model: ${markdownText(data.model)}`);
  if (data.answer) lines.push("", "## Result", "", fenced(data.answer));
  lines.push("", "## Source passages", "");
  const cited = new Set((data.citations || []).map((item) => item.chunk_id));
  for (const item of data.sources || []) {
    lines.push(
      `### [${item.label}] ${markdownText(item.filename)} — ${markdownText(item.location)}`,
      "",
      data.mode === "ai"
        ? cited.has(item.chunk_id)
          ? "Cited in the answer."
          : "Retrieved for inspection."
        : "Retrieved evidence.",
      "",
      fenced(item.text),
      "",
    );
  }
  if (!data.sources.length)
    lines.push("No matching source passages were found.");
  const blob = new Blob([lines.join("\n")], {
    type: "text/markdown;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = element("a");
  link.href = url;
  link.download = `BraveTrace_${(data.created_at || new Date().toISOString()).slice(0, 10)}.md`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function pollIndex() {
  clearTimeout(state.indexTimer);
  if (!state.indexJob) return;
  const jobId = state.indexJob;
  try {
    const job = await api(`/api/index-jobs/${jobId}`);
    // A late poll must not restore a finished job or overwrite a newer build.
    if (state.indexJob !== jobId) return;
    $("index-progress").hidden = false;
    $("index-progress-count").textContent =
      `${job.processed} / ${job.total} passages · ${job.percent}%`;
    $("index-progress-bar").value = job.percent;
    $("cancel-index").disabled = job.status === "cancelling";
    $("index-progress-detail").textContent =
      job.status === "cancelling"
        ? "Stopping after the current model batch. Completed passages stay saved."
        : "You can continue researching while the index builds.";
    if (["completed", "cancelled", "failed"].includes(job.status)) {
      state.indexJob = null;
      sessionStorage.removeItem("evidencelens-index-job");
      $("index-progress").hidden = true;
      await refresh();
      if (job.status === "completed")
        notice(
          `Semantic index ready. ${job.indexed} new passages indexed. Select Hybrid search to use it.`,
          "success",
        );
      else if (job.status === "cancelled")
        notice(
          `Indexing stopped. ${job.indexed} completed passages were saved. Start indexing again to resume.`,
        );
      else
        notice(
          job.error?.message || "The semantic index could not be built.",
          "error",
        );
      $("index-button").disabled = state.busy;
    } else state.indexTimer = setTimeout(pollIndex, 700);
  } catch (error) {
    if (state.indexJob && state.indexJob !== jobId) return;
    state.indexJob = null;
    sessionStorage.removeItem("evidencelens-index-job");
    $("index-progress").hidden = true;
    $("index-button").disabled = state.busy;
    failure(error);
  }
}

$("cancel-index").addEventListener("click", async () => {
  if (!state.indexJob) return;
  try {
    await api(`/api/index-jobs/${state.indexJob}/cancel`, {
      method: "POST",
      body: "{}",
    });
    await pollIndex();
  } catch (error) {
    failure(error);
  }
});
$("history-button").addEventListener("click", () =>
  $("history-dialog").showModal(),
);
$("history-close").addEventListener("click", () => $("history-dialog").close());
$("clear-history").addEventListener("click", () => {
  $("history-dialog").close();
  $("clear-history-dialog").showModal();
});
$("clear-history-cancel").addEventListener("click", () =>
  $("clear-history-dialog").close(),
);
$("clear-history-confirm").addEventListener("click", async () => {
  try {
    await api("/api/history", { method: "DELETE" });
    await refreshHistory();
    $("clear-history-dialog").close();
    notice("Research history cleared.", "success");
  } catch (error) {
    failure(error);
  }
});

function setLibraryCollapsed(collapsed) {
  document.querySelector(".sidebar").classList.toggle("collapsed", collapsed);
  $("library-toggle").setAttribute("aria-expanded", String(!collapsed));
}
$("library-toggle").addEventListener("click", () =>
  setLibraryCollapsed(
    !document.querySelector(".sidebar").classList.contains("collapsed"),
  ),
);
document.addEventListener("keydown", (event) => {
  if (
    event.key === "/" &&
    !event.ctrlKey &&
    !event.metaKey &&
    !document.querySelector("dialog[open]") &&
    !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)
  ) {
    event.preventDefault();
    $("question").focus();
  }
});
state.indexJob = sessionStorage.getItem("evidencelens-index-job");
if (state.indexJob) {
  $("index-button").disabled = true;
  pollIndex();
}
