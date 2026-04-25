# YouTube Learning Companion Agent

An **agentic AI Chrome extension** that helps you learn from YouTube videos.
It chains tool calls (transcript → summary → key concepts → arXiv papers → quiz)
and shows the **complete reasoning chain** in real time.

Built step-by-step following the agent loop pattern:

> **LLM decides → Tool executes → Result feeds back → LLM decides again**

---

## Architecture

```
┌─────────────────────────┐         ┌──────────────────────────┐
│  Chrome Extension       │ HTTP    │  FastAPI Backend         │
│  popup.html / popup.js  │────────▶│  /run_agent (SSE stream) │
│  reads current YT tab   │  POST   │  wraps agent loop        │
└─────────────────────────┘         └────────────┬─────────────┘
                                                  │
                                                  ▼
                                       ┌──────────────────────┐
                                       │  Gemini API +        │
                                       │  6 custom tools      │
                                       │  (transcript, arXiv, │
                                       │   summary, quiz...)  │
                                       └──────────────────────┘
```

---

## Folder Structure

```
youtube_agent/
├── README.md                   ← you are here
├── requirements.txt
├── step1_basic_llm.py          ← Step 1: raw LLM call (hallucinates)
├── step2_system_prompt.py      ← Step 2: system prompt → JSON output
├── step3_tools.py              ← Step 3: 6 real tool functions + registry
├── step4_test_format.py        ← Step 4: test JSON parseability
├── step5_robust_parsing.py     ← Step 5: handle messy LLM output
├── step6_one_loop.py           ← Step 6: one round trip (LLM → tool → LLM)
├── step7_agent_loop.py         ← Step 7: full multi-step agent loop
├── backend/
│   ├── agent_runner.py         ← streaming generator version of agent
│   └── server.py               ← FastAPI server (SSE endpoint)
└── chrome_extension/
    ├── manifest.json           ← Chrome extension config (MV3)
    ├── popup.html              ← UI
    ├── popup.css               ← styling
    └── popup.js                ← logic + SSE consumer
```

---

## Setup (one-time)

```bash
cd /Users/vikasran/Documents/personal-data/vikash/EAG-V3/assignment-03/youtube_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The agent reads `GEMINI_API_KEY` from
`/Users/vikasran/Documents/personal-data/vikash/EAG-V3/.env`:

```
GEMINI_API_KEY=your_key_here
MODEL=gemini-2.0-flash
```

---

## Run the CLI agent (steps 1–7)

Each step is independently runnable:

```bash
source .venv/bin/activate
python step1_basic_llm.py        # see hallucination
python step2_system_prompt.py    # LLM now returns JSON tool requests
python step3_tools.py            # all 6 tools tested standalone
python step4_test_format.py      # raw json.loads (will fail sometimes)
python step5_robust_parsing.py   # parse_llm_response() handles fences
python step6_one_loop.py         # one full LLM ↔ tool round trip
python step7_agent_loop.py       # full multi-step agent loop
```

---

## Run the Chrome Extension

### 1. Start the backend

```bash
cd /Users/vikasran/Documents/personal-data/vikash/EAG-V3/assignment-03/youtube_agent
source .venv/bin/activate
uvicorn backend.server:app --host 127.0.0.1 --port 8000 --reload
```

Verify it's up:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","service":"youtube-agent"}
```

### 2. Load the extension in Chrome

1. Open Chrome → `chrome://extensions`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked**
4. Select the folder:
   `/Users/vikasran/Documents/personal-data/vikash/EAG-V3/assignment-03/youtube_agent/chrome_extension`
5. The extension appears in your toolbar.

### 3. Use it

1. Open any YouTube video page (e.g. https://www.youtube.com/watch?v=PeMlggyqz0Y)
2. Click the extension icon
3. Type a question, OR click a quick chip (Summarise / Concepts / Quiz / Related papers)
4. Click **Run agent** — watch the reasoning chain stream live, then see the final answer.

---

## The 6 Tools

| # | Tool | What it does | Source |
|---|------|--------------|--------|
| 1 | `get_video_metadata(url)` | Title, channel, thumbnail | YouTube oEmbed (free) |
| 2 | `get_video_transcript(url)` | Full caption text | youtube-transcript-api (free) |
| 3 | `summarize_video(transcript)` | Headline + paragraph + 5 bullets | Gemini |
| 4 | `extract_key_concepts(transcript)` | Up to 7 concepts | Gemini |
| 5 | `search_arxiv_papers(concept)` | Top 3 academic papers | arXiv API (free) |
| 6 | `generate_quiz(transcript)` | 5 MCQs with answers | Gemini |

> Assignment requires ≥3 custom tools — we have **6**.

---

## Assignment Coverage Checklist

- [x] LLM called multiple times in a loop
- [x] Each call carries the full conversation history
- [x] Reasoning chain visible (CLI + extension popup)
- [x] At least 3 custom tools (we have 6)
- [x] Chrome plugin frontend
- [x] Robust parsing (handles fenced JSON, commentary, etc.)
- [x] Graceful failure modes (parse errors, unknown tools, max iterations)

---

## Sample Queries to Try

- *"Give me a short summary of this video"*
- *"What are the 3 most important concepts in this video?"*
- *"Generate a 5-question quiz on this video"*
- *"Find related arXiv papers for the most important concept"*
- *"Summarise this video AND quiz me on it"* (4+ tool chain)

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Popup says "Backend unreachable" | Make sure `uvicorn` is running on port 8000 |
| Popup says "open a YouTube /watch tab" | Navigate to a YouTube `/watch?v=...` URL |
| Tool 2 (transcript) fails | The video may not have captions — try another short video |
| Long reasoning takes time | Multi-step agents are slow by nature; see CLI logs for progress |
| Gemini quota errors | Free tier has limits; wait a minute or use a different model |
