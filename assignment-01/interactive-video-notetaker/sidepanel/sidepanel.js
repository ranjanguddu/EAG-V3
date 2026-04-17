/**
 * Side panel UI: notes, debounced capture, snapshots, export.
 */
const STORAGE_KEY = "ivn_session";

const el = {
  statusLine: document.getElementById("statusLine"),
  autoCapture: document.getElementById("autoCapture"),
  debounceMs: document.getElementById("debounceMs"),
  debounceLabel: document.getElementById("debounceLabel"),
  noteText: document.getElementById("noteText"),
  btnTimestamp: document.getElementById("btnTimestamp"),
  btnCapture: document.getElementById("btnCapture"),
  snapshotList: document.getElementById("snapshotList"),
  btnExportJson: document.getElementById("btnExportJson"),
  btnClear: document.getElementById("btnClear"),
};

/** @type {{ noteText: string, snapshots: object[], autoCapture: boolean, debounceMs: number }} */
let session = {
  noteText: "",
  snapshots: [],
  autoCapture: false,
  debounceMs: 900,
};

let debounceTimer = null;
let autoCaptureWarnShown = false;

function setStatus(text, kind) {
  el.statusLine.textContent = text;
  el.statusLine.classList.remove("ok", "warn");
  if (kind === "ok") el.statusLine.classList.add("ok");
  if (kind === "warn") el.statusLine.classList.add("warn");
}

function formatTime(sec) {
  const n = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  const s = n % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

function buildJumpUrl(href, seconds) {
  const t = Math.max(0, Math.floor(Number(seconds) || 0));
  try {
    const u = new URL(href);
    if (u.hostname === "youtu.be") {
      u.searchParams.set("t", t);
      return u.toString();
    }
    if (u.hostname.replace(/^www\./, "").includes("youtube.com")) {
      u.searchParams.set("t", `${t}s`);
      return u.toString();
    }
    u.hash = `t=${t}`;
    return u.toString();
  } catch {
    return href;
  }
}

async function persist() {
  session.noteText = el.noteText.value;
  session.autoCapture = el.autoCapture.checked;
  session.debounceMs = Number(el.debounceMs.value) || 900;
  await chrome.storage.local.set({ [STORAGE_KEY]: session });
}

async function load() {
  const data = await chrome.storage.local.get(STORAGE_KEY);
  const s = data[STORAGE_KEY];
  if (s && typeof s === "object") {
    session = {
      noteText: typeof s.noteText === "string" ? s.noteText : "",
      snapshots: Array.isArray(s.snapshots) ? s.snapshots : [],
      autoCapture: s.autoCapture === true,
      debounceMs: Number(s.debounceMs) || 900,
    };
  }
  el.noteText.value = session.noteText;
  el.autoCapture.checked = session.autoCapture;
  el.debounceMs.value = String(session.debounceMs);
  el.debounceLabel.textContent = `${session.debounceMs} ms`;
  renderSnapshots();
}

function renderSnapshots() {
  el.snapshotList.innerHTML = "";
  for (const snap of session.snapshots) {
    const li = document.createElement("li");
    li.className = "snapshot-item";
    const img = document.createElement("img");
    img.src = snap.imageDataUrl;
    img.alt = "Frame capture";
    const meta = document.createElement("div");
    meta.className = "snapshot-meta";
    const jump = buildJumpUrl(snap.pageUrl || snap.href || "", snap.seconds);
    const label = formatTime(snap.seconds);
    const a = document.createElement("a");
    a.href = jump;
    a.textContent = `Jump to ${label}`;
    a.title = jump;
    a.addEventListener("click", (e) => {
      e.preventDefault();
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        const id = tabs[0]?.id;
        if (id != null) chrome.tabs.update(id, { url: jump });
      });
    });
    const small = document.createElement("div");
    small.appendChild(a);
    const line2 = document.createElement("div");
    line2.textContent = new Date(snap.createdAt).toLocaleString();
    meta.appendChild(small);
    meta.appendChild(line2);
    li.appendChild(img);
    li.appendChild(meta);
    el.snapshotList.appendChild(li);
  }
}

/**
 * `tabs.query({ lastFocusedWindow })` from the side panel can target the wrong
 * window. `windows.getCurrent({ populate: true })` is the window that hosts
 * the side panel — use its active tab + window id for captureVisibleTab.
 */
function captureVisibleInPanel() {
  return new Promise((resolve) => {
    chrome.windows.getCurrent({ populate: true }, (win) => {
      const wErr = chrome.runtime.lastError;
      if (wErr || !win?.tabs?.length) {
        resolve({
          ok: false,
          reason: "no_window",
          detail: wErr?.message || "No window",
        });
        return;
      }

      const tab = win.tabs.find((t) => t.active) || win.tabs[0];
      if (!tab?.id) {
        resolve({ ok: false, reason: "no_active_tab" });
        return;
      }

      const pageUrl = tab.url || "";
      if (!pageUrl.startsWith("http://") && !pageUrl.startsWith("https://")) {
        resolve({
          ok: false,
          reason: "not_http",
          detail:
            "Select a normal tab (https://…). You can’t capture chrome:// pages or the new-tab page.",
        });
        return;
      }

      const finish = (dataUrl) => {
        chrome.tabs.sendMessage(tab.id, { type: "GET_VIDEO_STATE" }, (state) => {
          void chrome.runtime.lastError;
          resolve({
            ok: true,
            dataUrl,
            tabId: tab.id,
            pageUrl,
            title: tab.title || "",
            ...(state && state.ok ? state : { currentTime: null, href: pageUrl }),
          });
        });
      };

      const attempt = (windowId) => {
        chrome.tabs.captureVisibleTab(windowId, { format: "png" }, (dataUrl) => {
          const capErr = chrome.runtime.lastError;
          if (!capErr && dataUrl) {
            finish(dataUrl);
            return;
          }
          if (windowId != null) {
            chrome.tabs.captureVisibleTab({ format: "png" }, (dataUrl2) => {
              const capErr2 = chrome.runtime.lastError;
              if (!capErr2 && dataUrl2) {
                finish(dataUrl2);
                return;
              }
              resolve({
                ok: false,
                reason: "capture_failed",
                detail:
                  capErr2?.message ||
                  capErr?.message ||
                  "captureVisibleTab returned empty",
              });
            });
            return;
          }
          resolve({
            ok: false,
            reason: "capture_failed",
            detail: capErr?.message || "captureVisibleTab returned empty",
          });
        });
      };

      attempt(win.id);
    });
  });
}

async function requestCapture(options = {}) {
  const { silent = false } = options;
  const res = await captureVisibleInPanel();
  if (!res || !res.ok) {
    if (!silent) {
      let msg = "Could not capture. Click the video tab once, then try again.";
      if (res?.reason === "not_http") {
        msg = res.detail || msg;
      } else if (res?.reason === "capture_failed") {
        msg = res.detail
          ? `Capture failed: ${res.detail}`
          : "Capture failed. Click the YouTube tab, then Capture again.";
      } else if (res?.reason === "no_window") {
        msg = res.detail || "Could not read browser window.";
      } else if (res?.reason === "no_active_tab") {
        msg = "No active tab found in this window.";
      }
      setStatus(msg, "warn");
    }
    return null;
  }
  const seconds =
    res.currentTime != null && !Number.isNaN(res.currentTime)
      ? res.currentTime
      : 0;
  const snap = {
    id:
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`,
    imageDataUrl: res.dataUrl,
    seconds,
    pageUrl: res.href || res.pageUrl || "",
    pageTitle: res.title || "",
    createdAt: Date.now(),
  };
  session.snapshots.unshift(snap);
  if (session.snapshots.length > 80) session.snapshots.length = 80;
  await persist();
  renderSnapshots();
  setStatus(`Saved frame at ${formatTime(seconds)}.`, "ok");
  return snap;
}

function scheduleDebouncedCapture() {
  if (!el.autoCapture.checked) return;
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(async () => {
    debounceTimer = null;
    const snap = await requestCapture({ silent: true });
    if (!snap && !autoCaptureWarnShown) {
      autoCaptureWarnShown = true;
      setStatus(
        "Auto-capture was blocked by Chrome. Use Capture frame now (or turn auto-capture off).",
        "warn"
      );
    }
  }, Number(el.debounceMs.value) || 900);
}

async function insertTimestampLink() {
  const res = await chrome.runtime.sendMessage({ type: "GET_VIDEO_STATE" });
  if (!res || !res.ok || res.currentTime == null) {
    setStatus("No video detected on the active tab.", "warn");
    return;
  }
  const href = res.href || "";
  const url = buildJumpUrl(href, res.currentTime);
  const label = formatTime(res.currentTime);
  const chunk = `[${label}](${url})`;
  const ta = el.noteText;
  const start = ta.selectionStart ?? ta.value.length;
  const end = ta.selectionEnd ?? ta.value.length;
  const before = ta.value.slice(0, start);
  const after = ta.value.slice(end);
  ta.value = before + chunk + after;
  const pos = start + chunk.length;
  ta.focus();
  ta.setSelectionRange(pos, pos);
  await persist();
  setStatus("Inserted timestamp link.", "ok");
}

el.noteText.addEventListener("input", () => {
  scheduleDebouncedCapture();
  persist();
});

el.autoCapture.addEventListener("change", () => {
  persist();
});

el.debounceMs.addEventListener("input", () => {
  el.debounceLabel.textContent = `${el.debounceMs.value} ms`;
  session.debounceMs = Number(el.debounceMs.value) || 900;
  persist();
});

el.btnCapture.addEventListener("click", async () => {
  await requestCapture();
});

el.btnTimestamp.addEventListener("click", () => {
  insertTimestampLink();
});

el.btnExportJson.addEventListener("click", async () => {
  await persist();
  const payload = {
    exportedAt: new Date().toISOString(),
    noteText: session.noteText,
    snapshots: session.snapshots.map((s) => ({
      seconds: s.seconds,
      pageUrl: s.pageUrl,
      pageTitle: s.pageTitle,
      createdAt: s.createdAt,
      jumpUrl: buildJumpUrl(s.pageUrl || "", s.seconds),
      imageDataUrl: s.imageDataUrl,
    })),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `video-notetaker-${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
  setStatus("Exported JSON download started.", "ok");
});

el.btnClear.addEventListener("click", async () => {
  if (!confirm("Clear all notes and snapshots in this extension?")) return;
  session = {
    noteText: "",
    snapshots: [],
    autoCapture: el.autoCapture.checked,
    debounceMs: Number(el.debounceMs.value) || 900,
  };
  el.noteText.value = "";
  await persist();
  renderSnapshots();
  setStatus("Session cleared.", "ok");
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") persist();
});

load().then(() => {
  setStatus('Open a video tab, then type here — or click "Capture frame now".', "");
});
