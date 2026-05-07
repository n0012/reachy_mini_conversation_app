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

function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-pane").forEach(p => p.classList.toggle("active", p.id === `tab-${name}`));
  try { localStorage.setItem("activeTab", name); } catch {}
}

async function init() {
  const loading = document.getElementById("loading");
  show(loading, true);

  const setupPanel = document.getElementById("setup-panel");
  const tabBar = document.getElementById("tab-bar");
  const monitorChip = document.getElementById("monitor-chip");
  const cameraFeed = document.getElementById("camera-feed");
  const cameraToggle = document.getElementById("camera-toggle");
  if (cameraToggle) {
    cameraToggle.addEventListener("change", () => {
      if (cameraToggle.checked) {
        cameraFeed.src = "/stream/camera";
        cameraFeed.style.display = "";
      } else {
        cameraFeed.src = "";
        cameraFeed.style.display = "none";
      }
    });
  }
  const unifiedFeed = document.getElementById("unified-feed");
  const feedEmpty = document.getElementById("feed-empty");
  const setupStatusEl = document.getElementById("setup-status");
  const setupSubmitBtn = document.getElementById("setup-submit");
  const setupVoice = document.getElementById("setup-voice");
  const setupVoiceStyle = document.getElementById("setup-voice-style");
  const setupPreviewBtn = document.getElementById("setup-preview-btn");
  const setupLocation = document.getElementById("setup-gcp-location");
  if (setupLocation && !setupLocation.value) setupLocation.value = "us-central1";

  const st = await waitForStatus(30000);

  // If the server didn't respond within 30s the app is still starting up —
  // auto-reload rather than showing the setup wizard (which only confuses
  // users who are already configured).
  if (!st) {
    const loadingText = document.querySelector("#loading p");
    if (loadingText) loadingText.textContent = "Starting up… reloading shortly";
    setTimeout(() => window.location.reload(), 4000);
    return;
  }

  // Show version in header pill
  const versionPill = document.getElementById("version-pill");
  if (versionPill && st.app_version) versionPill.textContent = `v${st.app_version}`;

  const voices = await getVoices();
  const styles = await getVoiceStyles();

  // --- Setup wizard (no tabs) ---
  if (!st.setup_complete) {
    show(setupPanel, true);

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

    // ── Compact memories: dry-run preview → apply ──────────────────────────
    const compactBtn = document.getElementById("compact-memory-btn");
    const compactResult = document.getElementById("compact-result");
    const compactSummary = document.getElementById("compact-summary");
    const compactList = document.getElementById("compact-actions-list");
    const compactApplyBar = document.getElementById("compact-apply-bar");
    const compactApplyBtn = document.getElementById("compact-apply-btn");
    const compactCancelBtn = document.getElementById("compact-cancel-btn");
    const compactApplyStatus = document.getElementById("compact-apply-status");

    let pendingCompactPlan = null;

    function updateCompactSummary(actions) {
      const accepted = actions.filter(a => a._accepted).length;
      compactSummary.textContent = `${accepted} of ${actions.length} action${actions.length === 1 ? "" : "s"} selected`;
    }

    function renderCompactPlan(result) {
      compactList.innerHTML = "";
      const actions = result.proposed || [];
      // Attach acceptance state to each action
      actions.forEach(a => { a._accepted = true; a._editedFact = null; });
      pendingCompactPlan = actions;

      if (!actions.length) {
        compactSummary.textContent = `Analyzed ${result.analyzed} memories — nothing to compact.`;
        compactApplyBar.classList.add("hidden");
        return;
      }
      updateCompactSummary(actions);

      const isDelete = a => a.type === "delete";
      const accentColor = a => isDelete(a) ? "#e57373" : "#80cbc4";
      const dimColor = a => isDelete(a) ? "#ef9a9a" : "#80cbc4";

      for (const a of actions) {
        const row = document.createElement("div");
        row.style.cssText = `padding:8px 10px;border-left:2px solid ${accentColor(a)};background:rgba(255,255,255,0.03);border-radius:4px`;

        // Header row
        const head = document.createElement("div");
        head.style.cssText = `display:flex;align-items:center;justify-content:space-between;gap:8px`;
        const label = document.createElement("span");
        label.style.cssText = `font-weight:600;text-transform:uppercase;font-size:10px;letter-spacing:0.5px;color:${dimColor(a)}`;
        label.textContent = `${a.type} — ${a.reason || ""}`;
        head.appendChild(label);

        // Accept / Skip buttons
        const btns = document.createElement("span");
        btns.style.cssText = "display:flex;gap:4px;flex-shrink:0";
        const acceptBtn = document.createElement("button");
        acceptBtn.style.cssText = "margin:0;padding:2px 8px;font-size:11px;border-radius:4px";
        const skipBtn = document.createElement("button");
        skipBtn.style.cssText = "margin:0;padding:2px 8px;font-size:11px;border-radius:4px";

        function refreshBtns() {
          if (a._accepted) {
            acceptBtn.textContent = "✓ Accept";
            acceptBtn.style.opacity = "1";
            skipBtn.textContent = "Skip";
            skipBtn.className = "ghost";
            skipBtn.style.opacity = "0.5";
          } else {
            acceptBtn.textContent = "Accept";
            acceptBtn.className = "ghost";
            acceptBtn.style.opacity = "0.5";
            skipBtn.textContent = "✗ Skipped";
            skipBtn.style.opacity = "1";
          }
        }
        refreshBtns();
        acceptBtn.addEventListener("click", () => { a._accepted = true; refreshBtns(); updateCompactSummary(actions); });
        skipBtn.addEventListener("click", () => { a._accepted = false; refreshBtns(); updateCompactSummary(actions); });
        btns.appendChild(acceptBtn);
        btns.appendChild(skipBtn);
        head.appendChild(btns);
        row.appendChild(head);

        // Source facts
        for (const f of (a.facts || [])) {
          const li = document.createElement("div");
          li.style.cssText = "color:var(--muted);margin-top:3px;font-size:12px";
          li.textContent = "• " + f;
          row.appendChild(li);
        }

        // Merge result (editable)
        if (a.new_fact) {
          const mergeRow = document.createElement("div");
          mergeRow.style.cssText = "display:flex;align-items:flex-start;gap:6px;margin-top:5px";
          const mergePreview = document.createElement("div");
          mergePreview.style.cssText = "color:var(--accent-2);font-size:12px;flex:1";
          mergePreview.textContent = "→ " + (a._editedFact ?? a.new_fact);
          const editBtn = document.createElement("button");
          editBtn.className = "ghost";
          editBtn.style.cssText = "margin:0;padding:2px 7px;font-size:11px;flex-shrink:0";
          editBtn.textContent = "✏️";
          editBtn.title = "Edit merged fact";
          mergeRow.appendChild(mergePreview);
          mergeRow.appendChild(editBtn);
          row.appendChild(mergeRow);

          const ta = document.createElement("textarea");
          ta.style.cssText = "display:none;width:100%;margin-top:5px;font-size:12px;border-radius:6px;padding:6px 8px;background:rgba(0,0,0,0.3);border:1px solid var(--border);color:var(--text);resize:vertical;box-sizing:border-box";
          ta.rows = 3;
          ta.value = a.new_fact;
          ta.addEventListener("input", () => {
            a._editedFact = ta.value;
            mergePreview.textContent = "→ " + ta.value;
            a._accepted = true; refreshBtns(); updateCompactSummary(actions);
          });
          editBtn.addEventListener("click", () => {
            const open = ta.style.display === "none";
            ta.style.display = open ? "block" : "none";
            editBtn.textContent = open ? "✏️ done" : "✏️";
            if (open) ta.focus();
          });
          row.appendChild(ta);
        }

        compactList.appendChild(row);
      }
      compactApplyBar.classList.remove("hidden");
    }

    compactBtn.addEventListener("click", async () => {
      compactBtn.disabled = true;
      compactBtn.textContent = "Analyzing…";
      compactResult.classList.remove("hidden");
      compactSummary.textContent = "Asking Gemini to review your memories…";
      compactList.innerHTML = "";
      compactApplyBar.classList.add("hidden");
      try {
        const resp = await fetch("/memory/compact", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dry_run: true, ...memScope() }),
        });
        const data = await resp.json();
        if (data.error) {
          compactSummary.textContent = "Error: " + data.error;
        } else {
          renderCompactPlan(data);
        }
      } catch (e) {
        compactSummary.textContent = "Failed: " + (e.message || "unknown");
      } finally {
        compactBtn.disabled = false;
        compactBtn.textContent = "🧹 Compact memories";
      }
    });

    compactCancelBtn.addEventListener("click", () => {
      compactResult.classList.add("hidden");
    });

    compactApplyBtn.addEventListener("click", async () => {
      if (!pendingCompactPlan) { compactApplyStatus.textContent = "Nothing to apply."; return; }
      // Only send accepted actions; substitute edited merge facts
      const selected = pendingCompactPlan
        .filter(a => a._accepted)
        .map(a => {
          if (a._editedFact !== null && a._editedFact !== undefined) {
            return { ...a, new_fact: a._editedFact };
          }
          return a;
        });
      if (!selected.length) { compactApplyStatus.textContent = "Nothing selected — toggle some ✓ to apply."; return; }
      compactApplyBtn.disabled = true;
      compactApplyStatus.textContent = `Applying ${selected.length} action${selected.length === 1 ? "" : "s"}…`;
      try {
        const resp = await fetch("/memory/compact", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dry_run: false, plan: selected, ...memScope() }),
        });
        const data = await resp.json();
        if (data.error) {
          compactApplyStatus.textContent = "Error: " + data.error;
        } else {
          // Clear the panel — actions are done
          compactResult.classList.add("hidden");
          compactList.innerHTML = "";
          compactApplyBar.classList.add("hidden");
          compactApplyStatus.textContent = "";
          pendingCompactPlan = null;
          compactBtn.textContent = `🧹 Compact memories — ${data.applied} updated`;
          setTimeout(() => { compactBtn.textContent = "🧹 Compact memories"; }, 4000);
          refreshMemoryCount();
        }
      } catch (e) {
        compactApplyStatus.textContent = "Failed: " + (e.message || "unknown");
      } finally {
        compactApplyBtn.disabled = false;
      }
    });

    // Configuration panel
    const cfgStatus = document.getElementById("cfg-status");
    const autoSleepInput = document.getElementById("cfg-auto-sleep");
    try {
      const cfgResp = await fetchWithTimeout("/config", {}, 3000);
      if (cfgResp.ok) {
        const cfg = await cfgResp.json();
        document.getElementById("cfg-gcp-project").value = cfg.gcp_project_id || "";
        document.getElementById("cfg-gcp-location").value = cfg.gcp_location || "us-central1";
        document.getElementById("cfg-engine-id").value = cfg.vertex_reasoning_engine_id || "";
        if (Array.isArray(cfg.auto_sleep_minutes_range) && cfg.auto_sleep_minutes_range.length === 2) {
          autoSleepInput.min = cfg.auto_sleep_minutes_range[0];
          autoSleepInput.max = cfg.auto_sleep_minutes_range[1];
        }
        autoSleepInput.value = cfg.auto_sleep_minutes ?? 10;
      }
    } catch {}

    document.getElementById("cfg-behavior-save").addEventListener("click", async () => {
      const status = document.getElementById("cfg-behavior-status");
      const minutes = parseInt(autoSleepInput.value, 10);
      const lo = parseInt(autoSleepInput.min, 10) || 1;
      const hi = parseInt(autoSleepInput.max, 10) || 60;
      if (!Number.isFinite(minutes) || minutes < lo || minutes > hi) {
        setStatusMessage(status, `Enter a number between ${lo} and ${hi}.`, "warn"); return;
      }
      setStatusMessage(status, "Saving…");
      try {
        const resp = await fetch("/config/behavior", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ auto_sleep_minutes: minutes }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.error || "save_failed");
        autoSleepInput.value = data.auto_sleep_minutes ?? minutes;
        setStatusMessage(status, "Saved.", "ok");
      } catch (e) {
        setStatusMessage(status, `Failed: ${e.message || "unknown error"}`, "error");
      }
    });
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

    // Sleep / Wake controls
    const wakeBtn = document.getElementById("wake-btn");
    const sleepBtn = document.getElementById("sleep-btn");
    let currentProfile = st.setup_complete ? (await fetchWithTimeout("/session/status", {}, 3000).then(r => r.json()).catch(() => ({}))) : {};
    currentProfile = currentProfile.profile || "reachy-gemini";

    async function updateSleepState() {
      try {
        const resp = await fetchWithTimeout("/session/status", {}, 2000);
        if (!resp.ok) return;
        const data = await resp.json();
        currentProfile = data.profile || currentProfile;
        const sleeping = !!data.sleeping;
        monitorChip.textContent = sleeping ? "😴 Sleeping" : "Live";
        monitorChip.className = sleeping ? "chip" : "chip chip-ok";
        show(wakeBtn, sleeping);
        show(sleepBtn, !sleeping);
      } catch {}
    }

    wakeBtn.addEventListener("click", async () => {
      await fetch("/session/wake", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({profile: currentProfile}) });
      setTimeout(updateSleepState, 1000);
    });
    sleepBtn.addEventListener("click", async () => {
      await fetch("/session/sleep", { method: "POST" });
      setTimeout(updateSleepState, 500);
    });

    // Poll sleep state every 15s
    updateSleepState();
    setInterval(updateSleepState, 15000);

    // ── Show tabs + restore last active tab ────────────────────────────────────
    show(tabBar, true);
    document.querySelectorAll(".tab-btn").forEach(btn => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });
    const savedTab = (() => { try { return localStorage.getItem("activeTab") || "monitor"; } catch { return "monitor"; } })();
    switchTab(savedTab);

    // ── Memory tab: populate user chip + stats from session status ──────────────
    const memUserChip = document.getElementById("memory-user-chip");
    const memStatCount = document.getElementById("mem-stat-count");
    const memStatProfile = document.getElementById("mem-stat-profile");
    let activeUser = "";
    try {
      const sessResp = await fetchWithTimeout("/session/status", {}, 3000);
      if (sessResp.ok) {
        const sess = await sessResp.json();
        activeUser = sess.active_user || sess.user_id || "";
        const profile = sess.profile || "—";
        if (memUserChip) memUserChip.textContent = activeUser || "—";
        if (memStatProfile) memStatProfile.textContent = `profile: ${profile}`;
      }
    } catch {}
    // Memory count — reusable so compact/settings can refresh it
    async function refreshMemoryCount() {
      try {
        const mResp = await fetchWithTimeout("/memory/list", {}, 3000);
        if (mResp.ok) {
          const mData = await mResp.json();
          const count = mData.total ?? (Array.isArray(mData.memories) ? mData.memories.length : null);
          if (count !== null && memStatCount) memStatCount.textContent = `${count} memories`;
        }
      } catch {}
    }
    refreshMemoryCount();

    // ── Memory scope selectors ─────────────────────────────────────────────────
    const memProfileSelect = document.getElementById("mem-profile-select");
    const memUserSelect = document.getElementById("mem-user-select");
    const memScopeRefresh = document.getElementById("mem-scope-refresh");

    // Populate profile dropdown from already-loaded personalities
    async function initMemProfileSelect() {
      try {
        const resp = await fetchWithTimeout("/personalities", {}, 3000);
        if (resp.ok) {
          const data = await resp.json();
          const profiles = data.choices || [];
          const activeProf = data.current || "";
          memProfileSelect.innerHTML = "";
          profiles.filter(p => p !== "(built-in default)").forEach(p => {
            const opt = document.createElement("option");
            opt.value = p; opt.textContent = p;
            if (p === activeProf) opt.selected = true;
            memProfileSelect.appendChild(opt);
          });
        }
      } catch {}
    }

    // Populate user dropdown by fetching unique user_ids for selected profile
    async function refreshUserSelect() {
      const profile = memProfileSelect.value;
      if (!profile) return;
      try {
        const resp = await fetch(`/memory/users?profile=${encodeURIComponent(profile)}`);
        const data = await resp.json();
        const users = data.users || [];
        const prev = memUserSelect.value;
        memUserSelect.innerHTML = "";
        users.forEach(u => {
          const opt = document.createElement("option");
          opt.value = u; opt.textContent = u;
          if (u === prev || u === activeUser) opt.selected = true;
          memUserSelect.appendChild(opt);
        });
        if (!memUserSelect.value && users.length) memUserSelect.value = users[0];
      } catch {}
      refreshMemoryCount();
    }

    function memScope() {
      return { profile: memProfileSelect.value, user_id: memUserSelect.value };
    }

    await initMemProfileSelect();
    await refreshUserSelect();
    memProfileSelect.addEventListener("change", () => refreshUserSelect());
    memUserSelect.addEventListener("change", () => {
      refreshMemoryCount();
      if (memSearchResults) memSearchResults.innerHTML = "";
      if (memSearchStatus) memSearchStatus.textContent = "";
    });
    if (memScopeRefresh) memScopeRefresh.addEventListener("click", refreshUserSelect);

    // ── Memory browser: search / add / edit / delete ───────────────────────────
    const memSearchInput = document.getElementById("mem-search-input");
    const memSearchBtn = document.getElementById("mem-search-btn");
    const memSearchStatus = document.getElementById("mem-search-status");
    const memSearchResults = document.getElementById("mem-search-results");
    const memAddInput = document.getElementById("mem-add-input");
    const memAddBtn = document.getElementById("mem-add-btn");
    const memAddStatus = document.getElementById("mem-add-status");

    async function deleteMemory(memoryId, rowEl, onError) {
      try {
        const resp = await fetch("/memory/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ memory_id: memoryId }),
        });
        const data = await resp.json();
        if (data.ok) {
          rowEl.style.transition = "opacity 0.2s";
          rowEl.style.opacity = "0";
          setTimeout(() => { rowEl.remove(); refreshMemoryCount(); }, 200);
        } else {
          if (onError) onError("Delete failed: " + (data.error || "unknown"));
        }
      } catch (e) {
        if (onError) onError("Delete failed: " + e.message);
      }
    }

    function renderMemoryResult(m) {
      const row = document.createElement("div");
      row.className = "mem-result-row";

      // Fact display (truncated, hover for full)
      const factEl = document.createElement("div");
      factEl.className = "feed-recall-row";
      factEl.style.cssText = "flex:1;min-width:0";
      const display = m.fact.length > 70 ? m.fact.slice(0, 70) + "…" : m.fact;
      factEl.textContent = display;
      factEl.dataset.full = m.fact;

      const age = m.age_days != null ? `${Math.round(m.age_days)}d ago` : "";
      const ageEl = document.createElement("span");
      ageEl.style.cssText = "font-size:11px;color:var(--muted);flex-shrink:0";
      ageEl.textContent = age;

      // Edit / Delete buttons
      const editBtn = document.createElement("button");
      editBtn.className = "ghost";
      editBtn.style.cssText = "margin:0;padding:3px 8px;font-size:11px;flex-shrink:0";
      editBtn.textContent = "Edit";
      const delBtn = document.createElement("button");
      delBtn.className = "ghost";
      delBtn.style.cssText = "margin:0;padding:3px 8px;font-size:11px;flex-shrink:0;color:#ef9a9a;border-color:#ef9a9a44";
      delBtn.textContent = "Delete";

      const controls = document.createElement("div");
      controls.style.cssText = "display:flex;gap:4px;align-items:center;flex-shrink:0";
      controls.appendChild(ageEl);
      controls.appendChild(editBtn);
      controls.appendChild(delBtn);

      const top = document.createElement("div");
      top.style.cssText = "display:flex;gap:8px;align-items:center";
      top.appendChild(factEl);
      top.appendChild(controls);
      row.appendChild(top);

      // Inline edit area (hidden by default)
      const editArea = document.createElement("div");
      editArea.style.display = "none";
      const ta = document.createElement("textarea");
      ta.rows = 3;
      ta.value = m.fact;
      ta.style.cssText = "width:100%;margin-top:6px;font-size:12px;box-sizing:border-box";
      const saveEditBtn = document.createElement("button");
      saveEditBtn.style.cssText = "margin:6px 0 0;padding:5px 12px;font-size:12px";
      saveEditBtn.textContent = "Save edit";
      const cancelEditBtn = document.createElement("button");
      cancelEditBtn.className = "ghost";
      cancelEditBtn.style.cssText = "margin:6px 0 0 6px;padding:5px 10px;font-size:12px";
      cancelEditBtn.textContent = "Cancel";
      editArea.appendChild(ta);
      editArea.appendChild(saveEditBtn);
      editArea.appendChild(cancelEditBtn);
      row.appendChild(editArea);

      editBtn.addEventListener("click", () => {
        const open = editArea.style.display === "none";
        editArea.style.display = open ? "block" : "none";
        editBtn.textContent = open ? "Close" : "Edit";
        if (open) ta.focus();
      });
      cancelEditBtn.addEventListener("click", () => { editArea.style.display = "none"; editBtn.textContent = "Edit"; });

      saveEditBtn.addEventListener("click", async () => {
        const newFact = ta.value.trim();
        if (!newFact || newFact === m.fact) { editArea.style.display = "none"; editBtn.textContent = "Edit"; return; }
        saveEditBtn.disabled = true;
        saveEditBtn.textContent = "Saving…";
        try {
          // Delete old, add new
          await fetch("/memory/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ memory_id: m.memory_id }) });
          await fetch("/memory/add", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fact: newFact, ...memScope() }) });
          factEl.textContent = newFact.length > 70 ? newFact.slice(0, 70) + "…" : newFact;
          factEl.dataset.full = newFact;
          m.fact = newFact;
          editArea.style.display = "none";
          editBtn.textContent = "Edit";
          refreshMemoryCount();
        } catch (e) { alert("Save failed: " + e.message); }
        saveEditBtn.disabled = false;
        saveEditBtn.textContent = "Save edit";
      });

      // Two-click delete: first click → "Confirm?" button, second → delete
      let deleteConfirmTimer = null;
      delBtn.addEventListener("click", async () => {
        if (delBtn.dataset.confirming) {
          // Second click — execute
          clearTimeout(deleteConfirmTimer);
          delBtn.textContent = "…";
          delBtn.disabled = true;
          await deleteMemory(m.memory_id, row, (err) => {
            delBtn.disabled = false;
            delBtn.textContent = "Delete";
            delete delBtn.dataset.confirming;
            delBtn.style.background = "";
          });
        } else {
          // First click — show confirm state
          delBtn.dataset.confirming = "1";
          delBtn.textContent = "Confirm?";
          delBtn.style.background = "#ef9a9a33";
          delBtn.style.color = "#fff";
          // Auto-reset after 5s
          deleteConfirmTimer = setTimeout(() => {
            if (delBtn.dataset.confirming) {
              delete delBtn.dataset.confirming;
              delBtn.textContent = "Delete";
              delBtn.style.background = "";
              delBtn.style.color = "";
            }
          }, 5000);
        }
      });

      return row;
    }

    if (memSearchBtn) {
      async function runSearch() {
        const q = memSearchInput.value.trim();
        if (!q) return;
        memSearchBtn.disabled = true;
        memSearchStatus.textContent = "Searching…";
        memSearchResults.innerHTML = "";
        try {
          const resp = await fetch("/memory/search", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: q, ...memScope() }),
          });
          const data = await resp.json();
          if (data.error) { memSearchStatus.textContent = "Error: " + data.error; return; }
          const matches = data.matches || [];
          memSearchStatus.textContent = `${matches.length} match${matches.length === 1 ? "" : "es"}`;
          matches.forEach(m => memSearchResults.appendChild(renderMemoryResult(m)));
        } catch (e) { memSearchStatus.textContent = "Failed: " + e.message; }
        memSearchBtn.disabled = false;
      }
      memSearchBtn.addEventListener("click", runSearch);
      memSearchInput.addEventListener("keydown", e => { if (e.key === "Enter") runSearch(); });
    }

    if (memAddBtn) {
      memAddBtn.addEventListener("click", async () => {
        const fact = memAddInput.value.trim();
        if (!fact) return;
        memAddBtn.disabled = true;
        setStatusMessage(memAddStatus, "Adding…");
        try {
          const resp = await fetch("/memory/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ fact, ...memScope() }),
          });
          const data = await resp.json();
          if (data.ok) {
            setStatusMessage(memAddStatus, "Added.", "ok");
            memAddInput.value = "";
            refreshMemoryCount();
          } else {
            setStatusMessage(memAddStatus, data.error || "Error", "error");
          }
        } catch (e) { setStatusMessage(memAddStatus, "Failed: " + e.message, "error"); }
        memAddBtn.disabled = false;
      });
    }

    // ── Retrieval settings ─────────────────────────────────────────────────────
    const topKSlider = document.getElementById("mem-top-k");
    const topKVal = document.getElementById("mem-top-k-val");
    const recencySlider = document.getElementById("mem-recency");
    const recencyVal = document.getElementById("mem-recency-val");
    const memSaveBtn = document.getElementById("mem-settings-save");
    const memSaveStatus = document.getElementById("mem-settings-status");

    const recencyLabel = v => {
      const n = parseFloat(v);
      if (n <= 0.02) return "Semantic only";
      if (n <= 0.08) return "Mostly semantic";
      if (n <= 0.18) return "Balanced";
      if (n <= 0.3) return "Prefer recent";
      return "Strongly recent";
    };

    if (topKSlider) {
      topKSlider.addEventListener("input", () => { topKVal.textContent = topKSlider.value; });
    }
    if (recencySlider) {
      recencySlider.addEventListener("input", () => { recencyVal.textContent = recencyLabel(recencySlider.value); });
    }

    // Load current retrieval settings
    try {
      const rResp = await fetchWithTimeout("/config/memory", {}, 3000);
      if (rResp.ok) {
        const rData = await rResp.json();
        if (topKSlider && rData.top_k) { topKSlider.value = rData.top_k; topKVal.textContent = rData.top_k; }
        if (recencySlider && rData.recency_alpha !== undefined) {
          recencySlider.value = rData.recency_alpha;
          recencyVal.textContent = recencyLabel(rData.recency_alpha);
        }
      }
    } catch {}

    if (memSaveBtn) {
      memSaveBtn.addEventListener("click", async () => {
        memSaveBtn.disabled = true;
        setStatusMessage(memSaveStatus, "Saving…");
        try {
          const resp = await fetch("/config/memory", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              top_k: parseInt(topKSlider.value),
              recency_alpha: parseFloat(recencySlider.value),
            }),
          });
          const data = await resp.json();
          if (data.ok) {
            setStatusMessage(memSaveStatus, "Saved — takes effect next session.", "ok");
            refreshMemoryCount();
          } else {
            setStatusMessage(memSaveStatus, data.error || "Error", "error");
          }
        } catch (e) {
          setStatusMessage(memSaveStatus, "Failed: " + e.message, "error");
        } finally {
          memSaveBtn.disabled = false;
        }
      });
    }

    // ── Unified feed ───────────────────────────────────────────────────────────

    // Pending tool names — flushed as a grouped pill row when a non-tool event arrives
    let pendingTools = [];
    let toolFlushTimer = null;

    function scrollFeed() {
      unifiedFeed.scrollTop = unifiedFeed.scrollHeight;
    }

    function flushTools() {
      clearTimeout(toolFlushTimer);
      toolFlushTimer = null;
      if (!pendingTools.length) return;
      const tools = pendingTools.splice(0);
      feedEmpty.style.display = "none";
      const row = document.createElement("div");
      row.className = "feed-tools";
      row.innerHTML = tools.map(t => `<span class="feed-tool-chip">🛠 ${t}</span>`).join("");
      unifiedFeed.appendChild(row);
      scrollFeed();
    }

    function appendFeedEvent({ role, content, detail }) {
      if (!content || !content.trim()) return;

      if (role === "tool") {
        // Accumulate tools and flush after a short pause (groups rapid consecutive calls)
        pendingTools.push(content.replace(/\(.*\)$/, ""));
        clearTimeout(toolFlushTimer);
        toolFlushTimer = setTimeout(flushTools, 400);
        return;
      }

      // Any non-tool event: flush pending tools first
      flushTools();
      feedEmpty.style.display = "none";

      if (role === "user" || role === "assistant") {
        const div = document.createElement("div");
        div.className = `transcript-msg ${role}`;
        const label = document.createElement("div");
        label.className = "transcript-role";
        label.textContent = role === "user" ? "You" : "Reachy";
        const text = document.createElement("div");
        text.textContent = content;
        div.appendChild(label);
        div.appendChild(text);
        unifiedFeed.appendChild(div);

      } else if (role === "memory") {
        // Expandable memory chip — shows count/user, expands to full context
        const chip = document.createElement("div");
        chip.className = "feed-session-chip";
        const hasDetail = detail && detail.trim();
        chip.innerHTML = `<span class="feed-chip-label">${content}</span>${hasDetail ? ' <span class="feed-chip-expand">▸ show</span>' : ''}`;
        if (hasDetail) {
          const pre = document.createElement("pre");
          pre.className = "feed-memory-detail hidden";
          pre.textContent = detail;
          chip.appendChild(pre);
          chip.querySelector(".feed-chip-expand").addEventListener("click", (e) => {
            e.stopPropagation();
            const open = !pre.classList.contains("hidden");
            pre.classList.toggle("hidden", open);
            chip.querySelector(".feed-chip-expand").textContent = open ? "▸ show" : "▾ hide";
          });
        }
        unifiedFeed.appendChild(chip);

      } else if (role === "recall_result") {
        // Recall card: header + one row per fact, hover to see full text
        const card = document.createElement("div");
        card.className = "feed-recall-card";
        const header = document.createElement("div");
        header.className = "feed-recall-header";
        header.textContent = content;
        card.appendChild(header);
        if (detail && detail.trim()) {
          // Parse lines: first is "query: ..." rest are "N. fact · Xd ago"
          const lines = detail.split("\n").slice(1); // skip query line
          const list = document.createElement("div");
          list.className = "feed-recall-list";
          lines.forEach(line => {
            if (!line.trim()) return;
            const row = document.createElement("div");
            row.className = "feed-recall-row";
            // Truncate at 55 chars for display; full text in data attr for hover
            const dotIdx = line.indexOf(". ");
            const factPart = dotIdx >= 0 ? line.slice(dotIdx + 2) : line;
            const display = factPart.length > 55 ? factPart.slice(0, 55) + "…" : factPart;
            row.textContent = display;
            row.dataset.full = factPart;
            list.appendChild(row);
          });
          card.appendChild(list);
        }
        unifiedFeed.appendChild(card);

      } else if (role === "search") {
        const chip = document.createElement("div");
        chip.className = "feed-session-chip";
        chip.innerHTML = `<span class="feed-chip-label">🔍 ${content}</span>`;
        unifiedFeed.appendChild(chip);

      } else {
        // sleep/awake and other session events
        const chip = document.createElement("div");
        chip.className = "feed-session-chip";
        chip.innerHTML = `<span class="feed-chip-label">${content}</span>`;
        unifiedFeed.appendChild(chip);
      }

      scrollFeed();
    }

    const es = new EventSource("/stream/transcript");
    es.onmessage = (event) => { try { appendFeedEvent(JSON.parse(event.data)); } catch {} };
    es.onopen = () => {
      monitorChip.textContent = "Live"; monitorChip.className = "chip chip-ok";
      // After replay finishes rendering, snap to bottom
      setTimeout(() => { unifiedFeed.scrollTop = unifiedFeed.scrollHeight; }, 100);
    };
    es.onerror = () => { monitorChip.textContent = "Reconnecting…"; monitorChip.className = "chip"; };

  } catch (e) {
    console.error("UI init failed:", e);
  } finally {
    show(loading, false);
  }
}

window.addEventListener("DOMContentLoaded", init);
