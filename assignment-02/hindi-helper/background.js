const MODELS = ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite"];
const URL_BASE = "https://generativelanguage.googleapis.com/v1beta/models";
const CACHE_MAX = 200;
const RETRY_DELAYS_MS = [1000, 3000, 7000];

const cache = new Map();

function cacheGet(key) {
  if (!cache.has(key)) return null;
  const val = cache.get(key);
  cache.delete(key);
  cache.set(key, val);
  return val;
}

function cacheSet(key, val) {
  cache.set(key, val);
  if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value);
}

function buildPrompt(text, mode) {
  if (mode === "word") {
    return `You are an English-to-Hindi dictionary.
Give the single most common Hindi meaning of the given English word, written in DEVANAGARI script (Hindi script), NOT in Roman/Latin letters.
Rules:
- Output ONLY the Hindi word in Devanagari. No Roman letters. No English. No quotes. No punctuation. No explanation.
- Examples:
  power -> शक्ति
  performance -> प्रदर्शन
  book -> किताब
  water -> पानी
  happy -> खुश
Word: ${text}`;
  }
  return `Translate the following English text into natural conversational Hindi, written in DEVANAGARI script (Hindi script), NOT in Roman/Latin letters.
Rules:
- Output ONLY the Hindi translation in Devanagari. No Roman letters. No English. No quotes. No explanation. Do not summarize.
- Examples:
  "Ram is playing" -> "राम खेल रहा है"
  "I am going to school" -> "मैं स्कूल जा रहा हूँ"
Text:
${text}`;
}

function friendlyError(status, body) {
  if (status === 429) {
    const isDaily = /per day|daily|RequestsPerDay/i.test(body);
    return isDaily
      ? "Daily Gemini free-tier quota exhausted. Resets ~midnight US Pacific. Use a different API key or enable billing."
      : "Per-minute rate limit hit (free tier). Even after retries it failed — wait ~60s and try again.";
  }
  if (status === 400) return "Bad request to Gemini. Likely an invalid API key. " + body.slice(0, 160);
  if (status === 401 || status === 403) return "Gemini rejected the API key. Re-check at https://aistudio.google.com/apikey and re-save in Options.";
  if (status === 404) return "Model not available for your key.";
  return `Gemini ${status}: ${body.slice(0, 180)}`;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function callGemini(model, apiKey, prompt) {
  return fetch(`${URL_BASE}/${model}:generateContent`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": apiKey },
    body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] })
  });
}

async function callWithRetry(apiKey, prompt) {
  let lastStatus = 0;
  let lastBody = "";

  for (const model of MODELS) {
    for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
      const res = await callGemini(model, apiKey, prompt);
      if (res.ok) return res;

      lastStatus = res.status;
      lastBody = await res.text();

      const retriable = res.status === 429 || res.status === 500 || res.status === 503;
      if (!retriable) return { ok: false, status: lastStatus, _body: lastBody };

      if (attempt < RETRY_DELAYS_MS.length) {
        await sleep(RETRY_DELAYS_MS[attempt]);
      }
    }
  }
  return { ok: false, status: lastStatus, _body: lastBody };
}

async function ask(text, mode) {
  const { apiKey } = await chrome.storage.local.get("apiKey");
  if (!apiKey) throw new Error("Set Gemini API key in extension Options.");

  const cacheKey = mode + "::" + text.toLowerCase();
  const cached = cacheGet(cacheKey);
  if (cached) return cached;

  const prompt = buildPrompt(text, mode);
  const res = await callWithRetry(apiKey, prompt);

  if (!res.ok) {
    const body = res._body ?? (typeof res.text === "function" ? await res.text() : "");
    throw new Error(friendlyError(res.status, body));
  }

  const reply = (await res.json()).candidates?.[0]?.content?.parts?.[0]?.text?.trim() || "(empty)";
  cacheSet(cacheKey, reply);
  return reply;
}

chrome.runtime.onMessage.addListener((msg, _s, sendResponse) => {
  if (msg?.type !== "ASK") return;
  ask(msg.text, msg.mode)
    .then(reply => sendResponse({ ok: true, reply }))
    .catch(err => sendResponse({ ok: false, error: err.message }));
  return true;
});
