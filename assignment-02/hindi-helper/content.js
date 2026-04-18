const BTN = "hh-btn", BOX = "hh-box";

function clear() {
  document.getElementById(BTN)?.remove();
  document.getElementById(BOX)?.remove();
}

function showBox(x, y, text) {
  document.getElementById(BOX)?.remove();
  const box = document.createElement("div");
  box.id = BOX;
  box.style.left = x + "px";
  box.style.top = y + "px";

  const body = document.createElement("div");
  body.className = "hh-body";
  body.textContent = text;

  const close = document.createElement("button");
  close.className = "hh-close";
  close.textContent = "×";
  close.title = "Close";
  close.onmousedown = e => e.preventDefault();
  close.onclick = () => box.remove();

  box.appendChild(close);
  box.appendChild(body);
  document.body.appendChild(box);
}

document.addEventListener("mouseup", e => {
  if (e.target?.closest?.(`#${BTN}, #${BOX}`)) return;
  setTimeout(() => {
    try {
      const sel = window.getSelection();
      const text = sel?.toString().trim();
      if (!text) { clear(); return; }
      if (!sel.rangeCount) return;
      const mode = /\s/.test(text) ? "translate" : "word";
      const r = sel.getRangeAt(0).getBoundingClientRect();
      if (!r.width && !r.height) return;
      clear();
      const btn = document.createElement("button");
      btn.id = BTN;
      btn.textContent = mode === "word" ? "Get Hindi meaning →" : "Translate to Hindi →";
      btn.title = "Click to get Hindi " + (mode === "word" ? "meaning" : "translation");
      btn.style.left = (r.left + scrollX) + "px";
      btn.style.top = (r.bottom + scrollY + 6) + "px";
      btn.onmousedown = ev => ev.preventDefault();
      btn.onclick = () => {
        const x = parseFloat(btn.style.left), y = parseFloat(btn.style.top);
        btn.remove();
        showBox(x, y, "Loading… (auto-retries if rate-limited)");
        try {
          chrome.runtime.sendMessage(
            { type: "ASK", text: text.slice(0, 4000), mode },
            resp => {
              const lastErr = chrome.runtime.lastError?.message;
              if (lastErr) return showBox(x, y, "Error: " + lastErr + " — reload the page after reloading the extension.");
              showBox(x, y, resp?.ok ? resp.reply : "Error: " + (resp?.error || "no response from background"));
            }
          );
        } catch (err) {
          showBox(x, y, "Error: " + err.message + " — reload the page after reloading the extension.");
        }
      };
      document.body.appendChild(btn);
    } catch (err) {
      console.error("[Hindi Helper] mouseup handler failed:", err);
    }
  }, 0);
});

document.addEventListener("mousedown", e => {
  if (e.target.closest(`#${BTN}, #${BOX}`)) return;
  document.getElementById(BTN)?.remove();
});
document.addEventListener("keydown", e => { if (e.key === "Escape") clear(); });
