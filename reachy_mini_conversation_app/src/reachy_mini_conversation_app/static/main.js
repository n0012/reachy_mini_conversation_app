const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchWithTimeout(url, options = {}, timeoutMs = 2000) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(id);
  }
}

async function waitForStatus(timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (true) {
    try {
      const url = new URL("/status", window.location.origin);
      url.searchParams.set("_", Date.now().toString());
      const resp = await fetchWithTimeout(url, {}, 2000);
      if (resp.ok) return await resp.json();
    } catch (e) {}
    if (Date.now() >= deadline) return null;
    await sleep(500);
  }
}

async function waitForPersonalityData(timeoutMs = 15000) {
  const loadingText = document.querySelector("#loading p");
  let attempts = 0;
  const deadline = Date.now() + timeoutMs;
  while (true) {
    attempts += 1;
    try {
      const url = new URL("/personalities", window.location.origin);
      url.searchParams.set("_", Date.now().toString());
      const resp = await fetchWithTimeout(url, {}, 2000);
      if (resp.ok) return await resp.json();
    } catch (e) {}
    if (loadingText) loadingText.textContent = attempts > 8 ? "Starting backend…" : "Loading…";
    if (Date.now() >= deadline) return null;
    await sleep(500);
  }
}

// ---------- Personalities API ----------
async function loadPersonality(name) {
  const url = new URL("/personalities/load", window.location.origin);
  url.searchParams.set("name", name);
  url.searchParams.set("_", Date.now().toString());
  const resp = await fetchWithTimeout(url, {}, 3000);
  if (!resp.ok) throw new Error("load_failed");
  return await resp.json();
}

async function savePersonality(payload) {
  const saveUrl = new URL("/personalities/save", window.location.origin);
  saveUrl.searchParams.set("_", Date.now().toString());
  let resp = await fetchWithTimeout(saveUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }, 5000);
  if (resp.ok) return await resp.json();
  try {
    const form = new URLSearchParams();
    form.set("name", payload.name || "");
    form.set("instructions", payload.instructions || "");
    form.set("tools_text", payload.tools_text || "");
    form.set("voice", payload.voice || "");
    const url = new URL("/personalities/save_raw", window.location.origin);
    url.searchParams.set("_", Date.now().toString());
    resp = await fetchWithTimeout(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: form.toString(),
    }, 5000);
    if (resp.ok) return await resp.json();
  } catch {}
  const data = await resp.json().catch(() => ({}));
  throw new Error(data.error || "save_failed");
}

async function applyPersonality(name, { persist = false } = {}) {
  const url = new URL("/personalities/apply", window.location.origin);
  url.searchParams.set("name", name || "");
  if (persist) url.searchParams.set("persist", "1");
  url.searchParams.set("_", Date.now().toString());
  const resp = await fetchWithTimeout(url, { method: "POST" }, 5000);
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || "apply_failed");
  }
  return await resp.json();
}

async function applyVoice(voice) {
  const url = new URL("/voices/apply", window.location.origin);
  url.searchParams.set("voice", voice || "");
  url.searchParams.set("_", Date.now().toString());
  const resp = await fetchWithTimeout(url, { method: "POST" }, 20000);
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || "apply_voice_failed");
  }
  return await resp.json();
}

async function previewVoice(voice) {
  const url = new URL("/voices/preview", window.location.origin);
  url.searchParams.set("voice", voice || "");
  const resp = await fetchWithTimeout(url, { method: "GET" }, 15000);
  if (!resp.ok) throw new Error("preview_failed");
  const blob = await resp.blob();
  const audio = new Audio(URL.createObjectURL(blob));
  audio.play();
}

async function getVoices() {
  try {
    const url = new URL("/voices", window.location.origin);
    url.searchParams.set("_", Date.now().toString());
    const resp = await fetchWithTimeout(url, {}, 3000);
    if (!resp.ok) throw new Error("voices_failed");
    return await resp.json();
  } catch (e) { return []; }
}

async function getVoiceStyles() {
  try {
    const url = new URL("/voices/styles", window.location.origin);
    url.searchParams.set("_", Date.now().toString());
    const resp = await fetchWithTimeout(url, {}, 3000);
    if (!resp.ok) return {};
    return await resp.json();
  } catch (e) { return {}; }
}

async function getCurrentVoice() {
  try {
    const url = new URL("/voices/current", window.location.origin);
    url.searchParams.set("_", Date.now().toString());
    const resp = await fetchWithTimeout(url, {}, 3000);
    if (!resp.ok) throw new Error();
    const data = await resp.json();
    return typeof data.voice === "string" ? data.voice : "";
  } catch (e) { return ""; }
}

async function submitSetup(payload) {
  const resp = await fetch("/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || "setup_failed");
  return data;
}

function show(el, flag) { el.classList.toggle("hidden", !flag); }

function setStatusMessage(el, text, tone = "") {
  el.textContent = text;
  el.className = tone ? `status ${tone}` : "status";
  el.setAttribute("role", tone === "error" ? "alert" : "status");
  el.setAttribute("aria-live", tone === "error" ? "assertive" : "polite");
  el.setAttribute("aria-atomic", "true");
}

function populateVoiceSelect(select, voices, styles, currentVoice) {
  select.innerHTML = "";
  const defaultVoice = "Sadachbia";
  for (const v of voices) {
    const opt = document.createElement("option");
    opt.value = v;
    const style = styles[v];
    opt.textContent = style ? `${v} — ${style}` : v;
    if (v === (currentVoice || defaultVoice)) opt.selected = true;
    select.appendChild(opt);
  }
}

async function init() {
  const loading = document.getElementById("loading");
  show(loading, true);

  const setupPanel = document.getElementById("setup-panel");
  const personalityPanel = document.getElementById("personality-panel");
  const monitorPanel = document.getElementById("monitor-panel");
  const monitorChip = document.getElementById("monitor-chip");
  const transcriptLog = document.getElementById("transcript-log");
  const transcriptEmpty = document.getElementById("transcript-empty");
  const activityLog = document.getElementById("activity-log");
  const activityEmpty = document.getElementById("activity-empty");
  const setupStatusEl = document.getElementById("setup-status");
  const setupSubmitBtn = document.getElementById("setup-submit");
  const setupVoice = document.getElementById("setup-voice");
  const setupVoiceStyle = document.getElementById("setup-voice-style");
  const setupPreviewBtn = document.getElementById("setup-preview-btn");
  const setupLocation = document.getElementById("setup-gcp-location");
  if (setupLocation && !setupLocation.value) setupLocation.value = "us-central1";

  const st = (await waitForStatus()) || { setup_complete: false };

  const voices = await getVoices();
  const styles = await getVoiceStyles();

  // --- Setup wizard ---
  if (!st.setup_complete) {
    show(setupPanel, true);
    show(personalityPanel, false);
    show(monitorPanel, false);

    populateVoiceSelect(setupVoice, voices, styles, "Sadachbia");

    function updateSetupVoiceStyle() {
      const v = setupVoice.value.split(" — ")[0];
      setupVoiceStyle.textContent = styles[v] ? `Character: ${styles[v]}` : "";
    }
    setupVoice.addEventListener("change", updateSetupVoiceStyle);
    updateSetupVoiceStyle();

    setupPreviewBtn.addEventListener("click", async () => {
      const voice = setupVoice.value.split(" — ")[0];
      setupPreviewBtn.disabled = true;
      setupPreviewBtn.textContent = "…";
      try {
        await previewVoice(voice);
      } catch {
        setStatusMessage(setupStatusEl, "Preview unavailable — configure first.", "warn");
      } finally {
        setupPreviewBtn.disabled = false;
        setupPreviewBtn.textContent = "▶ Preview";
      }
    });

    setupSubmitBtn.addEventListener("click", async () => {
      const geminiKey = (document.getElementById("setup-gemini-key").value || "").trim();
      const gcpProject = (document.getElementById("setup-gcp-project").value || "").trim();
      const gcpLocation = (document.getElementById("setup-gcp-location").value || "us-central1").trim();
      const engineId = (document.getElementById("setup-engine-id").value || "").trim();
      const saJson = (document.getElementById("setup-sa-json").value || "").trim();
      const voice = setupVoice.value.split(" — ")[0];

      if (!geminiKey || !gcpProject || !engineId || !saJson) {
        setStatusMessage(setupStatusEl, "Please fill in all required fields.", "warn");
        return;
      }
      try { JSON.parse(saJson); } catch {
        setStatusMessage(setupStatusEl, "Service Account JSON is not valid JSON.", "error");
        return;
      }

      setStatusMessage(setupStatusEl, "Saving configuration…");
      setupSubmitBtn.disabled = true;
      try {
        await submitSetup({ gemini_api_key: geminiKey, gcp_project_id: gcpProject, gcp_location: gcpLocation,
          vertex_reasoning_engine_id: engineId, service_account_json: saJson, voice });
        setStatusMessage(setupStatusEl, "Setup complete! Loading…", "ok");
        await sleep(800);
        window.location.reload();
      } catch (e) {
        setStatusMessage(setupStatusEl, `Setup failed: ${e.message || "unknown error"}`, "error");
        setupSubmitBtn.disabled = false;
      }
    });

    show(loading, false);
    return;
  }

  // --- Post-setup: personality + monitor ---
  const list = (await waitForPersonalityData()) || { choices: [] };
  if (!list.choices.length) {
    setStatusMessage(document.getElementById("personality-status") || document.body, "Personality endpoints not ready. Retry shortly.", "warn");
    show(loading, false);
    return;
  }

  try {
    const pSelect = document.getElementById("personality-select");
    const pApply = document.getElementById("apply-personality");
    const pPersist = document.getElementById("persist-personality");
    const pNew = document.getElementById("new-personality");
    const pSave = document.getElementById("save-personality");
    const pStartupLabel = document.getElementById("startup-label");
    const pName = document.getElementById("personality-name");
    const pInstr = document.getElementById("instructions-ta");
    const pTools = document.getElementById("tools-ta");
    const pStatus = document.getElementById("personality-status");
    const pVoice = document.getElementById("voice-select");
    const pApplyVoice = document.getElementById("apply-voice");
    const pPreviewVoice = document.getElementById("preview-voice");
    const pVoiceStyle = document.getElementById("voice-style-label");
    const pMemory = document.getElementById("memory-check");
    const pGrounding = document.getElementById("grounding-check");
    const pMcp = document.getElementById("mcp-ta");
    const pAvail = document.getElementById("tools-available");

    const AUTO_WITH = { dance: ["stop_dance"], play_emotion: ["stop_emotion"] };

    const choices = Array.isArray(list.choices) ? list.choices : [];
    const DEFAULT_OPTION = choices[0] || "(built-in default)";
    const startupChoice = choices.includes(list.startup) ? list.startup : DEFAULT_OPTION;

    function setStartupLabel(name) {
      pStartupLabel.textContent = `Launch on start: ${name && name !== DEFAULT_OPTION ? name : "Built-in default"}`;
    }

    pSelect.innerHTML = "";
    for (const n of choices) {
      const opt = document.createElement("option");
      opt.value = n; opt.textContent = n;
      pSelect.appendChild(opt);
    }
    if (choices.length) pSelect.value = choices.includes(startupChoice) ? startupChoice : choices[0];

    let currentVoice = await getCurrentVoice();
    populateVoiceSelect(pVoice, voices, styles, currentVoice);

    function updateVoiceStyle() {
      const v = pVoice.value.split(" — ")[0];
      pVoiceStyle.textContent = styles[v] ? `Character: ${styles[v]}` : "";
    }
    pVoice.addEventListener("change", updateVoiceStyle);
    updateVoiceStyle();

    setStartupLabel(startupChoice);

    function renderToolCheckboxes(available, enabled) {
      pAvail.innerHTML = "";
      const enabledSet = new Set(enabled);
      for (const t of available) {
        const wrap = document.createElement("div"); wrap.className = "chk";
        const id = `tool-${t}`;
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.id = id; cb.value = t; cb.checked = enabledSet.has(t);
        const lab = document.createElement("label"); lab.htmlFor = id; lab.textContent = t;
        wrap.appendChild(cb); wrap.appendChild(lab); pAvail.appendChild(wrap);
      }
    }

    function getSelectedTools() {
      const selected = new Set();
      pAvail.querySelectorAll('input[type="checkbox"]').forEach((el) => { if (el.checked) selected.add(el.value); });
      for (const [main, deps] of Object.entries(AUTO_WITH)) {
        if (selected.has(main)) for (const d of deps) selected.add(d);
      }
      return Array.from(selected);
    }

    function syncToolsTextarea() {
      const selected = getSelectedTools();
      const comments = pTools.value.split("\n").filter((ln) => ln.trim().startsWith("#"));
      pTools.value = (comments.join("\n") + (comments.length ? "\n" : "") + selected.join("\n")).trim() + "\n";
    }

    pAvail.addEventListener("change", (ev) => {
      const target = ev.target;
      if (!(target instanceof HTMLInputElement) || target.type !== "checkbox") return;
      if (AUTO_WITH[target.value]) {
        for (const dep of AUTO_WITH[target.value]) {
          const el = pAvail.querySelector(`input[value="${dep}"]`);
          if (el) el.checked = target.checked || el.checked;
        }
      }
      syncToolsTextarea();
    });

    async function loadSelected() {
      const selected = pSelect.value;
      const data = await loadPersonality(selected);
      pInstr.value = data.instructions || "";
      pTools.value = data.tools_text || "";
      const loadedVoice = voices.includes(data.voice) ? data.voice : (voices[0] || "");
      const activeVoice = voices.includes(currentVoice) ? currentVoice : loadedVoice;
      pVoice.value = data.uses_default_voice ? activeVoice : loadedVoice;
      updateVoiceStyle();
      renderToolCheckboxes(data.available_tools, data.enabled_tools);
      pMemory.checked = !!data.memory;
      pGrounding.checked = !!data.grounding;
      pMcp.value = data.mcp_servers || "";
      const idx = selected.lastIndexOf("/");
      pName.value = idx >= 0 ? selected.slice(idx + 1) : "";
      setStatusMessage(pStatus, `Loaded ${selected}`);
    }

    pSelect.addEventListener("change", loadSelected);
    await loadSelected();
    show(personalityPanel, true);

    pApplyVoice.addEventListener("click", async () => {
      const voice = pVoice.value.split(" — ")[0];
      setStatusMessage(pStatus, "Applying voice…");
      try {
        const res = await applyVoice(voice);
        currentVoice = voice;
        setStatusMessage(pStatus, res.status || `Voice changed to ${voice}.`, "ok");
      } catch (e) {
        setStatusMessage(pStatus, `Failed to apply voice: ${e.message || ""}`, "error");
      }
    });

    pPreviewVoice.addEventListener("click", async () => {
      const voice = pVoice.value.split(" — ")[0];
      pPreviewVoice.disabled = true;
      pPreviewVoice.textContent = "…";
      setStatusMessage(pStatus, `Previewing ${voice}…`);
      try {
        await previewVoice(voice);
        setStatusMessage(pStatus, `Playing ${voice} preview.`, "ok");
      } catch {
        setStatusMessage(pStatus, "Preview unavailable.", "warn");
      } finally {
        pPreviewVoice.disabled = false;
        pPreviewVoice.textContent = "▶ Preview";
      }
    });

    pApply.addEventListener("click", async () => {
      setStatusMessage(pStatus, "Applying…");
      try {
        const res = await applyPersonality(pSelect.value);
        currentVoice = await getCurrentVoice();
        if (res.startup) setStartupLabel(res.startup);
        setStatusMessage(pStatus, res.status || "Applied.", "ok");
      } catch (e) {
        setStatusMessage(pStatus, `Failed to apply: ${e.message || ""}`, "error");
      }
    });

    pPersist.addEventListener("click", async () => {
      setStatusMessage(pStatus, "Saving for startup…");
      try {
        const res = await applyPersonality(pSelect.value, { persist: true });
        currentVoice = await getCurrentVoice();
        if (res.startup) setStartupLabel(res.startup);
        setStatusMessage(pStatus, res.status || "Saved for startup.", "ok");
      } catch (e) {
        setStatusMessage(pStatus, `Failed to persist: ${e.message || ""}`, "error");
      }
    });

    pNew.addEventListener("click", () => {
      pName.value = "";
      pInstr.value = "# Write your instructions here\n";
      pTools.value = "# tools enabled for this profile\n";
      pAvail.querySelectorAll('input[type="checkbox"]').forEach((el) => { el.checked = false; });
      pVoice.value = pVoice.options[0]?.value || "";
      updateVoiceStyle();
      pMemory.checked = false;
      pGrounding.checked = false;
      pMcp.value = "";
      setStatusMessage(pStatus, "Fill in a name and click Save profile.");
    });

    pSave.addEventListener("click", async () => {
      const name = (pName.value || "").trim();
      if (!name) { setStatusMessage(pStatus, "Enter a valid name.", "warn"); return; }
      setStatusMessage(pStatus, "Saving…");
      try {
        syncToolsTextarea();
        const res = await savePersonality({
          name,
          instructions: pInstr.value || "",
          tools_text: pTools.value || "",
          voice: pVoice.value.split(" — ")[0] || pVoice.options[0]?.value || "",
          memory: pMemory.checked,
          grounding: pGrounding.checked,
          mcp_servers: pMcp.value || "",
        });
        pSelect.innerHTML = "";
        for (const n of res.choices) {
          const opt = document.createElement("option");
          opt.value = n; opt.textContent = n;
          if (n === res.value) opt.selected = true;
          pSelect.appendChild(opt);
        }
        setStatusMessage(pStatus, "Saved.", "ok");
        try { await applyPersonality(pSelect.value); } catch {}
      } catch { setStatusMessage(pStatus, "Failed to save.", "error"); }
    });

    // Configuration panel
    const configDetails = document.getElementById("config-details");
    const cfgStatus = document.getElementById("cfg-status");
    show(configDetails, true);
    try {
      const cfgResp = await fetchWithTimeout("/config", {}, 3000);
      if (cfgResp.ok) {
        const cfg = await cfgResp.json();
        document.getElementById("cfg-gcp-project").value = cfg.gcp_project_id || "";
        document.getElementById("cfg-gcp-location").value = cfg.gcp_location || "us-central1";
        document.getElementById("cfg-engine-id").value = cfg.vertex_reasoning_engine_id || "";
      }
    } catch {}
    document.getElementById("cfg-save").addEventListener("click", async () => {
      const geminiKey = (document.getElementById("cfg-gemini-key").value || "").trim();
      const gcpProject = (document.getElementById("cfg-gcp-project").value || "").trim();
      const gcpLocation = (document.getElementById("cfg-gcp-location").value || "us-central1").trim();
      const engineId = (document.getElementById("cfg-engine-id").value || "").trim();
      const saJson = (document.getElementById("cfg-sa-json").value || "").trim();
      if (!gcpProject || !engineId) {
        setStatusMessage(cfgStatus, "Project ID and Engine ID are required.", "warn"); return;
      }
      if (saJson) {
        try { JSON.parse(saJson); } catch {
          setStatusMessage(cfgStatus, "Service Account JSON is not valid JSON.", "error"); return;
        }
      }
      setStatusMessage(cfgStatus, "Saving…");
      try {
        const payload = { gemini_api_key: geminiKey, gcp_project_id: gcpProject,
          gcp_location: gcpLocation, vertex_reasoning_engine_id: engineId,
          service_account_json: saJson || "{}", voice: "" };
        await submitSetup(payload);
        setStatusMessage(cfgStatus, "Configuration updated.", "ok");
        document.getElementById("cfg-gemini-key").value = "";
        document.getElementById("cfg-sa-json").value = "";
      } catch (e) {
        setStatusMessage(cfgStatus, `Failed: ${e.message || "unknown error"}`, "error");
      }
    });

    // Environment variables editor
    const envList = document.getElementById("env-vars-list");
    const envStatus = document.getElementById("env-status");
    const pendingDeletes = new Set();

    function makeEnvRow(key = "", isExisting = false) {
      const row = document.createElement("div");
      row.style.cssText = "display:grid;grid-template-columns:1fr 1fr auto;gap:8px;align-items:center";
      const keyInput = document.createElement("input");
      keyInput.type = "text"; keyInput.placeholder = "VAR_NAME";
      keyInput.value = key; keyInput.readOnly = isExisting;
      keyInput.style.cssText = isExisting ? "opacity:0.7" : "";
      const valInput = document.createElement("input");
      valInput.type = "password"; valInput.placeholder = isExisting ? "••••••• (leave blank to keep)" : "value";
      valInput.dataset.key = key;
      const delBtn = document.createElement("button");
      delBtn.className = "ghost"; delBtn.textContent = "✕";
      delBtn.style.cssText = "margin:0;padding:6px 10px;font-size:12px;color:var(--error)";
      delBtn.addEventListener("click", () => {
        if (isExisting) pendingDeletes.add(key);
        row.remove();
      });
      row.appendChild(keyInput); row.appendChild(valInput); row.appendChild(delBtn);
      row._getEntry = () => ({
        key: keyInput.value.trim(),
        value: valInput.value.trim(),
        delete: false,
      });
      return row;
    }

    try {
      const envResp = await fetchWithTimeout("/config/env", {}, 3000);
      if (envResp.ok) {
        const vars = await envResp.json();
        for (const v of vars) envList.appendChild(makeEnvRow(v.key, true));
      }
    } catch {}

    document.getElementById("env-add-btn").addEventListener("click", () => {
      envList.appendChild(makeEnvRow("", false));
    });

    document.getElementById("env-save-btn").addEventListener("click", async () => {
      const entries = [];
      for (const del of pendingDeletes) entries.push({ key: del, value: "", delete: true });
      for (const row of envList.children) {
        if (row._getEntry) {
          const e = row._getEntry();
          if (e.key) entries.push(e);
        }
      }
      setStatusMessage(envStatus, "Saving…");
      try {
        const resp = await fetch("/config/env", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(entries),
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok) {
          pendingDeletes.clear();
          setStatusMessage(envStatus, "Saved.", "ok");
          // Clear value fields (values are write-only)
          for (const row of envList.children) {
            const inp = row.querySelector('input[type="password"]');
            if (inp) inp.value = "";
          }
        } else {
          setStatusMessage(envStatus, data.error || "Failed.", "error");
        }
      } catch (e) {
        setStatusMessage(envStatus, `Failed: ${e.message}`, "error");
      }
    });

    // Monitor panel
    show(monitorPanel, true);
    const ACTIVITY_ROLES = new Set(["tool", "search", "memory"]);
    function appendTranscriptMessage({ role, content }) {
      if (!content || !content.trim()) return;
      if (ACTIVITY_ROLES.has(role)) {
        // Activity log — truncate tool content to name only (strip args for readability)
        activityEmpty.style.display = "none";
        const row = document.createElement("div");
        row.className = "activity-row";
        const icon = role === "tool" ? "🛠" : role === "search" ? "🔍" : "🧠";
        // For tools: show just the function name, strip args
        const display = role === "tool"
          ? content.replace(/\(.*\)$/, "")
          : content;
        row.title = content; // full content on hover
        row.innerHTML = `<span class="activity-icon">${icon}</span><span class="activity-text">${display}</span>`;
        activityLog.appendChild(row);
        activityLog.scrollTop = activityLog.scrollHeight;
      } else {
        // Conversation log
        transcriptEmpty.style.display = "none";
        const div = document.createElement("div");
        div.className = `transcript-msg ${role === "user" ? "user" : "assistant"}`;
        const label = document.createElement("div"); label.className = "transcript-role";
        label.textContent = role === "user" ? "You" : "Reachy";
        const text = document.createElement("div"); text.textContent = content;
        div.appendChild(label); div.appendChild(text);
        transcriptLog.appendChild(div);
        transcriptLog.scrollTop = transcriptLog.scrollHeight;
      }
    }
    const es = new EventSource("/stream/transcript");
    es.onmessage = (event) => { try { appendTranscriptMessage(JSON.parse(event.data)); } catch {} };
    es.onopen = () => { monitorChip.textContent = "Live"; monitorChip.className = "chip chip-ok"; };
    es.onerror = () => { monitorChip.textContent = "Reconnecting…"; monitorChip.className = "chip"; };

  } catch (e) {
    console.error("UI init failed:", e);
  } finally {
    show(loading, false);
  }
}

window.addEventListener("DOMContentLoaded", init);
