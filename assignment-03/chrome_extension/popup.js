"use strict";

const BACKEND_URL = "http://127.0.0.1:8007/run_agent";
const YOUTUBE_HOST_RE = /^https?:\/\/(www\.|m\.)?youtube\.com\/watch/i;

const els = {
  videoUrl: document.getElementById("video-url"),
  query: document.getElementById("query"),
  run: document.getElementById("run"),
  clear: document.getElementById("clear"),
  status: document.getElementById("status"),
  progress: document.getElementById("progress"),
  chain: document.getElementById("chain"),
  answer: document.getElementById("answer"),
  chips: document.querySelectorAll(".chip"),
};

let currentVideoUrl = null;
let abortController = null;

// ---------- helpers ----------

function setStatus(state, label) {
  els.status.className = `status ${state}`;
  els.status.textContent = label;
}

function setProgress(text) {
  els.progress.textContent = text || "";
}

function clearOutput() {
  els.chain.innerHTML = "";
  els.answer.classList.add("empty");
  els.answer.textContent = "Ask a question to begin.";
  setStatus("idle", "Idle");
  setProgress("");
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Lightweight, SAFE markdown renderer — escapes first, then applies inline rules.
// Supports: # h1/h2/h3, **bold**, *italic*, `code`, - bullets, 1. ordered,
// [text](url) markdown links, bare http(s)/www URLs, paragraphs.
// Also fixes a common LLM bug: numbered/bulleted lists jammed onto one line
// (e.g. "...subject: 1. Foo, 2. Bar, 3. Baz") get split into real list items.
function renderMarkdown(text) {
  // Pre-pass on the RAW text: split inline numbered/bullet lists onto separate lines.
  let pre = (text || "").replace(/\r/g, "");
  // " 1. ", " 2. " etc mid-paragraph -> newline + "1. "
  pre = pre.replace(/(\S)\s+(\d{1,2})\.\s+/g, "$1\n$2. ");
  // " - " or " * " bullet markers mid-paragraph (avoid breaking inside words)
  pre = pre.replace(/([.!?:])\s+-\s+/g, "$1\n- ");

  const safeUrl = (u) => {
    try {
      const url = new URL(u);
      if (url.protocol === "http:" || url.protocol === "https:") return url.toString();
    } catch {}
    return null;
  };

  // Inline transforms on already-escaped text. Order matters:
  //   1. markdown links [text](url)
  //   2. bare URLs (http/https/www)
  //   3. bold, italic, code
  const inline = (s) => {
    // Markdown link: [text](url) — both already HTML-escaped, so " is &quot;
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label, urlEsc) => {
      const url = urlEsc.replace(/&amp;/g, "&");
      const safe = safeUrl(url);
      return safe
        ? `<a href="${escapeHtml(safe)}" target="_blank" rel="noopener noreferrer">${label}</a>`
        : label;
    });
    // Bare URLs (don't double-wrap things already inside an <a>)
    s = s.replace(
      /(?<!href=")(\bhttps?:\/\/[^\s<]+|\bwww\.[^\s<]+)/g,
      (m) => {
        const url = m.startsWith("www.") ? `https://${m}` : m;
        const safe = safeUrl(url);
        return safe
          ? `<a href="${escapeHtml(safe)}" target="_blank" rel="noopener noreferrer">${m}</a>`
          : m;
      }
    );
    // Code spans
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    // Bold then italic
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^\*])\*([^*]+)\*/g, "$1<em>$2</em>");
    return s;
  };

  const safe = escapeHtml(pre);
  const lines = safe.split(/\n/);

  const html = [];
  let inUl = false;
  let inOl = false;

  const closeLists = () => {
    if (inUl) { html.push("</ul>"); inUl = false; }
    if (inOl) { html.push("</ol>"); inOl = false; }
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      closeLists();
      continue;
    }
    if (/^### /.test(line)) {
      closeLists();
      html.push(`<h3>${inline(line.slice(4))}</h3>`);
    } else if (/^## /.test(line)) {
      closeLists();
      html.push(`<h2>${inline(line.slice(3))}</h2>`);
    } else if (/^# /.test(line)) {
      closeLists();
      html.push(`<h1>${inline(line.slice(2))}</h1>`);
    } else if (/^[-*]\s+/.test(line)) {
      if (!inUl) { closeLists(); html.push("<ul>"); inUl = true; }
      html.push(`<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`);
    } else if (/^\d+\.\s+/.test(line)) {
      if (!inOl) { closeLists(); html.push("<ol>"); inOl = true; }
      html.push(`<li>${inline(line.replace(/^\d+\.\s+/, ""))}</li>`);
    } else {
      closeLists();
      html.push(`<p>${inline(line)}</p>`);
    }
  }
  closeLists();
  return html.join("");
}

// ---------- quiz detection + interactive rendering ----------

function tryParseQuiz(text) {
  if (!text || typeof text !== "string") return null;
  // Strip optional ```json ... ``` fences the LLM may add
  let s = text.trim();
  if (s.startsWith("```")) {
    const lines = s.split("\n");
    const closed = lines[lines.length - 1].trim().startsWith("```");
    s = lines.slice(1, closed ? -1 : undefined).join("\n").trim();
    if (s.toLowerCase().startsWith("json")) s = s.slice(4).trim();
  }
  // Quick shape check before parsing to avoid noisy errors
  if (!s.startsWith("[") || !s.endsWith("]")) return null;
  let arr;
  try {
    arr = JSON.parse(s);
  } catch {
    return null;
  }
  if (!Array.isArray(arr) || arr.length === 0) return null;
  const ok = arr.every(
    (q) =>
      q &&
      typeof q.q === "string" &&
      q.options &&
      typeof q.options === "object" &&
      ["A", "B", "C", "D"].every((k) => typeof q.options[k] === "string") &&
      typeof q.answer === "string"
  );
  return ok ? arr : null;
}

function renderQuiz(quiz) {
  els.answer.classList.remove("empty");
  els.answer.innerHTML = "";

  const wrap = document.createElement("div");
  wrap.className = "quiz";

  const title = document.createElement("h3");
  title.textContent = "Quiz time! Pick your answers, then submit.";
  wrap.appendChild(title);

  quiz.forEach((q, idx) => {
    const card = document.createElement("div");
    card.className = "quiz-card";
    card.dataset.correct = q.answer;
    card.dataset.explanation = q.explanation || "";

    const question = document.createElement("div");
    question.className = "quiz-q";
    question.textContent = `${idx + 1}. ${q.q}`;
    card.appendChild(question);

    const opts = document.createElement("div");
    opts.className = "quiz-opts";
    ["A", "B", "C", "D"].forEach((letter) => {
      const id = `q${idx}-${letter}`;
      const label = document.createElement("label");
      label.className = "quiz-opt";
      label.htmlFor = id;
      const input = document.createElement("input");
      input.type = "radio";
      input.name = `q${idx}`;
      input.value = letter;
      input.id = id;
      const span = document.createElement("span");
      span.innerHTML = `<strong>${letter}.</strong> ${escapeHtml(q.options[letter])}`;
      label.appendChild(input);
      label.appendChild(span);
      opts.appendChild(label);
    });
    card.appendChild(opts);

    const feedback = document.createElement("div");
    feedback.className = "quiz-feedback hidden";
    card.appendChild(feedback);

    wrap.appendChild(card);
  });

  const btnRow = document.createElement("div");
  btnRow.className = "quiz-actions";

  const submitBtn = document.createElement("button");
  submitBtn.id = "quiz-submit";
  submitBtn.className = "primary";
  submitBtn.textContent = "Submit answers";
  btnRow.appendChild(submitBtn);

  const resetBtn = document.createElement("button");
  resetBtn.id = "quiz-reset";
  resetBtn.className = "ghost";
  resetBtn.textContent = "Reset";
  btnRow.appendChild(resetBtn);

  wrap.appendChild(btnRow);

  const score = document.createElement("div");
  score.className = "quiz-score hidden";
  wrap.appendChild(score);

  els.answer.appendChild(wrap);

  submitBtn.addEventListener("click", () => gradeQuiz(wrap));
  resetBtn.addEventListener("click", () => renderQuiz(quiz));
}

function gradeQuiz(wrap) {
  const cards = wrap.querySelectorAll(".quiz-card");
  let correct = 0;
  let unanswered = 0;

  cards.forEach((card) => {
    const correctLetter = card.dataset.correct;
    const explanation = card.dataset.explanation;
    const selected = card.querySelector('input[type="radio"]:checked');
    const feedback = card.querySelector(".quiz-feedback");

    card.classList.remove("correct", "wrong", "skipped");
    feedback.classList.remove("hidden");

    if (!selected) {
      unanswered += 1;
      card.classList.add("skipped");
      feedback.innerHTML = `<strong>Skipped.</strong> Correct answer: <code>${correctLetter}</code>. ${escapeHtml(explanation)}`;
      return;
    }

    if (selected.value === correctLetter) {
      correct += 1;
      card.classList.add("correct");
      feedback.innerHTML = `<strong>Correct ✅</strong> ${escapeHtml(explanation)}`;
    } else {
      card.classList.add("wrong");
      feedback.innerHTML = `<strong>Incorrect ❌</strong> Correct answer: <code>${correctLetter}</code>. ${escapeHtml(explanation)}`;
    }

    // Lock options after grading
    card.querySelectorAll('input[type="radio"]').forEach((r) => (r.disabled = true));
  });

  const total = cards.length;
  const score = wrap.querySelector(".quiz-score");
  const pct = total ? Math.round((correct / total) * 100) : 0;
  score.classList.remove("hidden");
  score.innerHTML =
    `Score: <strong>${correct}/${total}</strong> (${pct}%)` +
    (unanswered ? ` — <em>${unanswered} unanswered</em>` : "");
}

function setAnswer(text) {
  if (!text) {
    els.answer.classList.add("empty");
    els.answer.textContent = "(no answer)";
    return;
  }
  const quiz = tryParseQuiz(text);
  if (quiz) {
    renderQuiz(quiz);
    return;
  }
  els.answer.classList.remove("empty");
  els.answer.innerHTML = renderMarkdown(text);
}

function nowHHMMSS() {
  return new Date().toTimeString().slice(0, 8);
}

function appendDebug(event) {
  const li = document.createElement("li");
  li.classList.add(event.type || "info");

  const head = document.createElement("div");
  head.className = "head";

  const title = document.createElement("span");
  const when = document.createElement("span");
  when.className = "when";
  when.textContent = nowHHMMSS();

  let body = "";

  switch (event.type) {
    case "start":
      title.textContent = "▶ Agent started";
      body = event.query || "";
      break;
    case "iteration":
      title.textContent = `↻ Iteration ${event.n}`;
      break;
    case "llm_decision": {
      const parsed = event.parsed || {};
      if (parsed.tool_name) {
        title.textContent = `🧠 LLM decided: call ${parsed.tool_name}`;
      } else if (parsed.answer) {
        title.textContent = "🧠 LLM decided: respond";
      } else {
        title.textContent = "🧠 LLM responded";
      }
      body = JSON.stringify(parsed, null, 2);
      break;
    }
    case "tool_call":
      title.textContent = `🔧 Tool call: ${event.tool_name}`;
      body = (event.tool_arguments_keys || []).length
        ? `args: ${(event.tool_arguments_keys || []).join(", ")}`
        : "(no args)";
      break;
    case "tool_result":
      title.textContent = event.error
        ? `❌ Tool error: ${event.tool_name}`
        : `📥 Tool result: ${event.tool_name}`;
      body = event.result_preview || "";
      break;
    case "parse_error":
      title.textContent = "⚠ Parse error";
      body = `${event.message || ""}\n\n${event.raw || ""}`;
      break;
    case "warning":
      title.textContent = "⚠ Warning";
      body = event.message || "";
      break;
    case "error":
      title.textContent = "❌ Error";
      body = event.message || "";
      break;
    case "final_answer":
      title.textContent = "✅ Final answer ready";
      body = event.answer || "";
      break;
    case "done":
      title.textContent = "■ Done";
      break;
    default:
      title.textContent = event.type || "event";
      body = JSON.stringify(event, null, 2);
  }

  head.appendChild(title);
  head.appendChild(when);
  li.appendChild(head);

  if (body) {
    const pre = document.createElement("pre");
    pre.textContent = body;
    li.appendChild(pre);
  }

  els.chain.appendChild(li);
}

// ---------- tab/url ----------

async function readCurrentTabUrl() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url) return null;
    if (YOUTUBE_HOST_RE.test(tab.url)) return tab.url;
    return null;
  } catch (e) {
    return null;
  }
}

async function init() {
  clearOutput();
  currentVideoUrl = await readCurrentTabUrl();
  if (currentVideoUrl) {
    els.videoUrl.textContent = currentVideoUrl;
    els.query.disabled = false;
    els.run.disabled = false;
  } else {
    els.videoUrl.textContent = "(open a YouTube /watch tab to use this)";
    els.query.disabled = true;
    els.run.disabled = true;
  }
}

// ---------- agent run ----------

async function runAgent(question) {
  if (!currentVideoUrl) {
    setStatus("error", "No YouTube tab");
    return;
  }
  if (!question || !question.trim()) {
    setStatus("error", "Empty question");
    return;
  }

  els.run.disabled = true;
  els.chain.innerHTML = "";
  setAnswer(null);
  els.answer.classList.add("empty");
  els.answer.textContent = "Working...";
  setStatus("running", "Running");
  setProgress("Starting...");

  const fullQuery = `${question.trim()}\n\nVideo URL: ${currentVideoUrl}`;
  abortController = new AbortController();

  let response;
  try {
    response = await fetch(BACKEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: fullQuery, max_iterations: 8 }),
      signal: abortController.signal,
    });
  } catch (e) {
    setStatus("error", "Backend unreachable");
    setProgress("");
    setAnswer(`Could not reach the backend at ${BACKEND_URL}\n\n${e.message}`);
    els.run.disabled = false;
    return;
  }

  if (!response.ok || !response.body) {
    setStatus("error", `HTTP ${response.status}`);
    setProgress("");
    setAnswer(`Backend returned status ${response.status}.`);
    els.run.disabled = false;
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let gotFinal = false;
  let lastError = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 2);
      if (!frame.startsWith("data:")) continue;
      const jsonText = frame.slice(5).trim();
      let event;
      try {
        event = JSON.parse(jsonText);
      } catch (e) {
        continue;
      }

      appendDebug(event);

      switch (event.type) {
        case "iteration":
          setProgress(`Step ${event.n}…`);
          break;
        case "tool_call":
          setProgress(`Calling ${event.tool_name}…`);
          break;
        case "tool_result":
          setProgress(`Got result from ${event.tool_name}`);
          break;
        case "final_answer":
          gotFinal = true;
          setAnswer(event.answer);
          setStatus("done", "Done");
          setProgress("");
          break;
        case "error":
          lastError = event.message || "Unknown error";
          break;
      }
    }
  }

  if (!gotFinal) {
    setStatus("error", "No answer");
    setProgress("");
    setAnswer(lastError ? `Agent failed: ${lastError}` : "Agent ended without producing an answer.");
  }
  els.run.disabled = false;
}

// ---------- wire UI ----------

els.run.addEventListener("click", () => runAgent(els.query.value));
els.clear.addEventListener("click", clearOutput);
els.query.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !els.run.disabled) runAgent(els.query.value);
});
els.chips.forEach((chip) => {
  chip.addEventListener("click", () => {
    els.query.value = chip.dataset.q || "";
    runAgent(els.query.value);
  });
});

init();
