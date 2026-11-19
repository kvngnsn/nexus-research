import { $, escapeHtml as esc, downloadJson } from "/shared/utils.js";

let token = "",
  snapshot = null,
  selected = "",
  selectedRun = "";
let currentRun = null,
  targets = [],
  polling = false;
const number = (id) => Number($(id).value);
const fmt = (value) => Number(value || 0).toLocaleString();

async function request(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Nexus-Token": token,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.error || "The local service could not complete this request.");
  return data;
}

function saveText(name, text) {
  if (window.webkit?.messageHandlers?.save) {
    window.webkit.messageHandlers.save.postMessage({ name, text });
    return;
  }
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function addTarget() {
  const text = $("target").value.trim();
  if (!text) throw new Error("Paste a target's SMILES structure first.");
  if (targets.length >= 12) throw new Error("A run supports up to twelve targets.");
  const response = await fetch(`/api/structure?smiles=${encodeURIComponent(text)}`);
  if (!response.ok) throw new Error((await response.json()).error);
  if (!targets.includes(text)) targets.push(text);
  $("target").value = "";
  renderTargets();
}

function renderTargets() {
  $("targets").innerHTML = targets
    .map(
      (text, i) =>
        `<li><code>${esc(text)}</code><img alt="Target ${i + 1} structure" src="/api/structure?smiles=${encodeURIComponent(text)}" /><button type="button" data-remove="${i}" aria-label="Remove target ${i + 1}">Remove</button></li>`,
    )
    .join("");
}

function connection() {
  return snapshot?.connections.find((item) => item.id === selected);
}

function renderConnections() {
  const query = $("connectionSearch").value.toLowerCase();
  const items = snapshot.connections.filter((x) =>
    [x.name, x.model, x.kind].join(" ").toLowerCase().includes(query),
  );
  $("connections").innerHTML =
    `<legend class="sr-only">Choose one model connection</legend>` +
    (items.length
      ? items
          .map(
            (item) =>
              `<label class="connection-option"><input type="radio" name="connection" value="${esc(item.id)}" ${item.id === selected ? "checked" : ""} ${item.status === "Not installed" || item.status === "Needs API key" ? "disabled" : ""} /><span>${esc(item.name)}</span><span>${esc(item.model)}</span><span>${esc(item.kind === "local" ? "Local API" : ["codex", "claude"].includes(item.kind) ? "CLI / adapter" : "API")}<small>${esc(item.status)}</small></span></label>`,
          )
          .join("")
      : `<p class="hint">No matching connections. Add one in Manage connections.</p>`);
  updateConnectionHelp();
}

function updateConnectionHelp() {
  const item = connection(),
    cli = item && ["codex", "claude"].includes(item.kind);
  $("apiBudget").hidden = !item || cli || item.kind === "local";
  $("connectionHint").textContent = !item
    ? "Choose a connection. Selecting it does not make a model call."
    : cli
      ? "Uses your existing CLI login. No shell, file, browser or MCP tools are provided to the model."
      : item.kind === "local"
        ? "Uses your existing local server. No model weights are downloaded or bundled."
        : "Uses the named Keychain credential. Research is sent to this provider when you start; its charges and data terms apply.";
  if (item)
    $("connectionHint").textContent = `Selected: ${item.name}. ` + $("connectionHint").textContent;
  $("budgetCaveat").textContent = cli
    ? "CLI limits apply between calls. An in-flight call can exceed token limits or continue billing after cancellation. Dollar caps are unavailable."
    : "API calls reserve input and allowed output before generation. Provider usage controls subsequent calls; local-server input counts are estimates. In-flight requests may still finish after cancellation.";
}

function renderSavedConnections() {
  $("savedConnections").innerHTML =
    snapshot.connections
      .map(
        (x) =>
          `<div class="saved-connection"><span>${esc(x.name)}<br /><span class="hint">${esc(x.kind)} / ${esc(x.model)}</span></span><button data-delete="${esc(x.id)}" ${snapshot.active ? "disabled" : ""}>Remove</button></div>`,
      )
      .join("") || `<p>No connections saved.</p>`;
}

function connectorFields() {
  const kind = $("connectionKind").value,
    cli = ["codex", "claude"].includes(kind);
  $("keyField").hidden = cli || kind === "local";
  $("endpointField").hidden = kind !== "local";
  $("connectionKey").required = !cli && kind !== "local";
  $("connectionKey").value = "";
  $("connectionEndpoint").required = kind === "local";
  $("connectionModel").value = cli ? "default" : "";
  $("connectorHelp").textContent = cli
    ? "Install and sign in to this CLI outside Nexus first. Use default for its built-in default, or enter an exact model ID for reproducible selection. The resolved version is recorded when reported."
    : kind === "local"
      ? "Start an OpenAI-compatible server yourself, such as llama-server. Only a localhost HTTP /v1 endpoint is accepted. Bundled local model installation is in progress."
      : "Enter the exact model ID available to your provider account. No model substitution or automatic paid connection test.";
}

function renderHistory() {
  const signature = snapshot.runs.map((x) => `${x.id}:${x.state}`).join("|");
  if ($("history").dataset.signature !== signature) {
    $("history").innerHTML =
      snapshot.runs
        .map(
          (x) =>
            `<option value="${esc(x.id)}">${esc(new Date(x.created * 1000).toLocaleString())} / ${esc(x.state)} / ${esc(x.config.connection.name)}</option>`,
        )
        .join("") || `<option value="">No saved runs</option>`;
    $("history").dataset.signature = signature;
  }
  if (selectedRun) $("history").value = selectedRun;
}

function renderRun(run) {
  currentRun = run;
  const active = Boolean(snapshot.active),
    thisActive = snapshot.active === run?.id;
  $("start").disabled = active;
  $("pause").disabled = !thisActive || ["pausing", "stopping"].includes(run?.state);
  $("stop").disabled = !thisActive || run?.state === "stopping";
  $("resume").disabled =
    active ||
    !run ||
    !["paused", "interrupted", "output_unavailable", "database_disconnected"].includes(run.state) ||
    run.usage_uncertain ||
    Date.now() / 1000 >= run.deadline;
  $("exportJson").disabled = $("exportReport").disabled = !run;
  if (!run) {
    $("status").textContent = "Ready. Findings save automatically to your chosen folder.";
    return;
  }
  $("status").textContent = `${run.state}: ${run.detail}`;
  const elapsed = Math.max(0, (thisActive ? Date.now() / 1000 : run.updated) - run.created);
  const usage = run.usage_uncertain
    ? "Usage incomplete; new calls stopped"
    : `${fmt(run.input_tokens + run.output_tokens)} / ${fmt(run.config.total_tokens)} tokens`;
  $("metrics").textContent =
    `${Math.floor(elapsed / 60)} min elapsed / ${Math.round(run.config.runtime_seconds / 60)} max · ${usage} · ${run.calls} calls · ${run.findings.length} findings · ${fmt(run.duplicate_pathways)} duplicate pathways skipped · Estimated spend ${run.spend_estimate == null ? "unavailable" : "$" + run.spend_estimate.toFixed(4)}`;
  $("runDetails").textContent =
    `Run ${run.id}\nOriginal deadline ${new Date(run.deadline * 1000).toLocaleString()}\nInput ${fmt(run.input_tokens)} / Output ${fmt(run.output_tokens)}\nCached ${fmt(run.cached_tokens)} (included in input)\nReasoning ${fmt(run.reasoning_tokens)} (included in output where reported)\nProductive work ${Math.round(run.productive_seconds)} seconds\nModel requested ${run.config.connection.model}\nModel reported ${run.responses.at(-1)?.model || "Unavailable"}\nFindings folder: ${run.output_directory || "Legacy run; use Export to save a copy."}\nGraph-checked routes are hypotheses; no experimental or commercial validation.`;
  const signature = `${run.id}:${run.findings.length}:${run.responses.length}`;
  if ($("results").dataset.signature === signature) return;
  $("results").dataset.signature = signature;
  const findings = run.findings
    .map(
      (x) =>
        `<details class="finding"><summary>${esc(x.title)}</summary><small>${esc(x.evidence)} · Cost unknown · Sources not verified</small><p>${esc(x.summary)}</p>${x.steps.length ? `<ol>${x.steps.map((step) => `<li><code>${esc(step.reactants.join(" + "))} → ${esc(step.product)}</code><small>${step.rule_checked ? "Paired reaction graph check passed" : "Transformation not checked"}</small><img alt="Proposed product structure" src="/api/structure?smiles=${encodeURIComponent(step.product)}" /></li>`).join("")}</ol>` : ""}${x.sources.length ? `<p>Sources claimed by model</p><ul>${x.sources.map((url) => `<li><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a></li>`).join("")}</ul>` : `<p class="hint">No source supplied.</p>`}</details>`,
    )
    .join("");
  const responses = run.responses
    .map(
      (x, i) =>
        `<details class="finding"><summary>Visible model response ${i + 1}</summary>${x.duplicates_skipped ? `<p class="hint">${fmt(x.duplicates_skipped)} previously discovered pathways skipped.</p>` : ""}<pre>${esc(x.text)}</pre></details>`,
    )
    .join("");
  $("results").innerHTML =
    findings + responses ||
    `<p class="hint">No results recorded yet. Partial results will appear here as calls finish.</p>`;
}

async function refresh() {
  if (!token || polling) return;
  polling = true;
  try {
    const before = JSON.stringify(snapshot?.connections);
    snapshot = await request("/api/research/status");
    if (!selected && snapshot.connections.length)
      selected = snapshot.connections.find((x) => x.status !== "Not installed")?.id || "";
    if (before !== JSON.stringify(snapshot.connections)) renderConnections();
    if (!selectedRun) selectedRun = snapshot.active || snapshot.runs[0]?.id || "";
    renderHistory();
    $("pathwaySummary").textContent =
      `${fmt(snapshot.pathways.count)} pathways in local history. Known pathways are excluded from new discoveries.`;
    $("storagePath").textContent = snapshot.storage;
    const output = $("outputDirectory");
    if (!output.dataset.loaded || (document.activeElement !== output && !output.dataset.edited)) {
      output.value = snapshot.output.directory;
      output.dataset.loaded = "true";
    }
    $("outputStatus").textContent = `Saving new runs to ${snapshot.output.directory}`;
    $("saveFolder").disabled = $("chooseFolder").disabled = Boolean(snapshot.active);
    $("chooseFolder").hidden = !window.webkit?.messageHandlers?.chooseFolder;
    $("revealData").hidden = !window.webkit?.messageHandlers?.reveal;
    const run = selectedRun
      ? await request(`/api/research/run?id=${encodeURIComponent(selectedRun)}`)
      : null;
    renderRun(run);
  } catch (error) {
    $("error").textContent = error.message;
  } finally {
    polling = false;
  }
}

$("addTarget").addEventListener("click", async () => {
  $("error").textContent = "";
  try {
    await addTarget();
  } catch (error) {
    $("error").textContent = error.message;
  }
});
$("targets").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove]");
  if (button) {
    targets.splice(Number(button.dataset.remove), 1);
    renderTargets();
  }
});
document.querySelectorAll("[data-example]").forEach((button) =>
  button.addEventListener("click", () => {
    $("target").value = button.dataset.example;
    $("target").focus();
  }),
);
$("connectionSearch").addEventListener("input", renderConnections);
$("connections").addEventListener("change", (event) => {
  selected = event.target.value;
  updateConnectionHelp();
});
$("manageConnections").addEventListener("click", () => {
  renderSavedConnections();
  $("connectionDialog").showModal();
});
$("closeConnections").addEventListener("click", () => {
  $("connectionKey").value = "";
  $("connectionDialog").close();
});
$("connectionDialog").addEventListener("close", () => {
  $("connectionKey").value = "";
});
$("connectionKind").addEventListener("change", connectorFields);
$("savedConnections").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete]");
  if (!button) return;
  try {
    await request("/api/research/connection/delete", { id: button.dataset.delete });
    if (selected === button.dataset.delete) selected = "";
    await refresh();
    renderSavedConnections();
  } catch (error) {
    $("connectionError").textContent = error.message;
  }
});
$("connectionForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("connectionError").textContent = "";
  $("saveConnection").disabled = true;
  const key = $("connectionKey").value;
  $("connectionKey").value = "";
  try {
    const item = await request("/api/research/connection", {
      kind: $("connectionKind").value,
      name: $("connectionName").value,
      model: $("connectionModel").value,
      endpoint: $("connectionEndpoint").value,
      key,
    });
    selected = item.id;
    await refresh();
    renderSavedConnections();
    $("connectionForm").reset();
    connectorFields();
  } catch (error) {
    $("connectionError").textContent = error.message;
  } finally {
    $("saveConnection").disabled = false;
  }
});
$("mode").addEventListener("change", () => {
  $("finiteFields").hidden = $("finiteHint").hidden = $("mode").value !== "finite";
});
document.querySelectorAll("[data-preset]").forEach((button) =>
  button.addEventListener("click", () => {
    const values = { quick: [5 / 60, 8000, 12], hour: [1, 50000, 40], day: [24, 250000, 100] }[
      button.dataset.preset
    ];
    $("hours").value = values[0];
    $("tokens").value = values[1];
    $("maxCalls").value = values[2];
  }),
);
$("runForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("error").textContent = "";
  try {
    if ($("target").value.trim()) await addTarget();
    if (!targets.length) throw new Error("Add at least one target structure.");
    if (!selected) throw new Error("Choose a model connection.");
    const api = ["openai", "anthropic"].includes(connection()?.kind);
    const run = await request("/api/research/start", {
      targets,
      connection: selected,
      mode: $("mode").value,
      runtime_seconds: number("hours") * 3600,
      total_tokens: number("tokens"),
      tokens_per_minute: number("tpm"),
      output_tokens: number("outputTokens"),
      max_calls: number("maxCalls"),
      idle_seconds: number("idleMinutes") * 60,
      idle_tokens: number("idleTokens"),
      usd_limit: api ? number("usdLimit") : 0,
      input_rate: api ? number("inputRate") : 0,
      output_rate: api ? number("outputRate") : 0,
    });
    selectedRun = run.id;
    await refresh();
  } catch (error) {
    $("error").textContent = error.message;
  }
});
for (const action of ["pause", "stop", "resume"])
  $(action).addEventListener("click", async () => {
    $("error").textContent = "";
    try {
      await request("/api/research/control", { id: selectedRun, action });
      await refresh();
    } catch (error) {
      $("error").textContent = error.message;
    }
  });
$("history").addEventListener("change", async () => {
  selectedRun = $("history").value;
  await refresh();
});
$("exportJson").addEventListener("click", async () => {
  try {
    downloadJson(
      `nexus-${selectedRun}.json`,
      await request(`/api/research/export?id=${encodeURIComponent(selectedRun)}`),
    );
  } catch (error) {
    $("error").textContent = error.message;
  }
});
$("exportReport").addEventListener("click", () => {
  if (!currentRun) return;
  const r = currentRun;
  saveText(
    `nexus-${r.id}.md`,
    `# Nexus Research research report\n\nRun ${r.id}\nState: ${r.state}\n${r.detail}\nDuplicate pathways skipped: ${r.duplicate_pathways || 0}\n\nResearch hypotheses only. No experimental, market or commercial validation.\n\n` +
      r.findings
        .map(
          (x) =>
            `## ${x.title}\n\n${x.summary}\n\nStatus: ${x.evidence}. Cost unknown.\n\n${x.steps.map((s) => `${s.reactants.join(" + ")} -> ${s.product}`).join("\n")}\n\n${x.sources.join("\n")}\n`,
        )
        .join("\n") +
      "\n## Visible model responses\n\n" +
      r.responses.map((x) => x.text).join("\n\n"),
  );
});
$("revealData").addEventListener("click", () =>
  window.webkit?.messageHandlers?.reveal?.postMessage({ path: snapshot.output.directory }),
);
$("outputDirectory").addEventListener("input", () => {
  $("outputDirectory").dataset.edited = "true";
});
async function saveFolder() {
  $("outputError").textContent = "";
  try {
    const result = await request("/api/research/output", { directory: $("outputDirectory").value });
    $("outputDirectory").value = result.directory;
    delete $("outputDirectory").dataset.edited;
    await refresh();
  } catch (error) {
    $("outputError").textContent = error.message;
  }
}
$("saveFolder").addEventListener("click", saveFolder);
$("chooseFolder").addEventListener("click", () =>
  window.webkit?.messageHandlers?.chooseFolder?.postMessage({ path: snapshot.output.directory }),
);
window.addEventListener("nexus:directory", async (event) => {
  $("outputDirectory").value = event.detail;
  $("outputDirectory").dataset.edited = "true";
  await saveFolder();
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted) location.reload();
});
connectorFields();
try {
  const bootstrap = await request("/api/workspace");
  token = bootstrap.token;
  await refresh();
} catch (error) {
  $("error").textContent = error.message;
}
setInterval(() => {
  if (!document.hidden) refresh();
}, 2000);
