# AI Pulse — Session 4 / Assignment 4

A daily radar for **AI/ML research (arXiv)** + **cyber-security news (RSS)** + **recent CVEs (NVD)**, built as an **MCP server** with a **Prefab UI** dashboard, driven by a Gemini-powered agentic loop.

This satisfies all three requirements of the assignment:

| Requirement | Where it lives |
|---|---|
| Internet / API call | `fetch_arxiv_papers`, `fetch_cybersec_news`, `fetch_recent_cves` |
| CRUD on a local file | `bookmark_item`, `mark_read`, `delete_bookmark`, `list_bookmarks` (all on `data/feed_db.json`) |
| Communicate back via UI | `render_dashboard` — Prefab UI + a self-contained HTML fallback at `data/dashboard.html` |

## Project layout

```
assignment-04/
├── ai_pulse_server.py    # the MCP server (all tools)
├── agent.py              # Gemini-driven agentic loop
├── requirements.txt
├── .env.example          # copy to .env and add your key
├── .gitignore
├── README.md
└── data/                 # created at runtime; feed_db.json + dashboard.html land here
```

## Quick start

```bash
# 1. Install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Add your free Gemini API key
cp .env.example .env
# then edit .env and paste the key from https://aistudio.google.com/apikey

# 3a. Run the agent end-to-end (fetch -> bookmark -> dashboard)
python agent.py

# 3b. OR open the inspector and play with tools manually
fastmcp dev ai_pulse_server.py
```

After the agentic loop finishes, the script automatically:
1. Starts a tiny local HTTP server bound to `127.0.0.1:8765`.
2. Opens **http://127.0.0.1:8765/dashboard.html** in your browser.
3. Stays running until you hit `Ctrl+C`.

If you only want to (re-)view the dashboard without running the agent again, use:

```bash
python serve.py
```

## Tools the server exposes

### Fetch from the internet
- `fetch_arxiv_papers(category, max_results)` — Latest papers from arXiv. Allowed categories: `cs.AI cs.LG cs.CL cs.CV cs.CR cs.NE stat.ML`.
- `fetch_cybersec_news(source, limit)` — RSS from `thehackernews | bleepingcomputer | krebs`.
- `fetch_recent_cves(min_severity, days, limit)` — Recent CVEs from the NVD API filtered by severity (`LOW | MEDIUM | HIGH | CRITICAL`).

### CRUD on `data/feed_db.json`
- `bookmark_item(item_id, item_type, title, url, summary, tags_csv)`
- `mark_read(item_id)` / `delete_bookmark(item_id)`
- `list_bookmarks(filter_tag)`

### UI
- `render_dashboard()` — returns a Prefab app (and writes `data/dashboard.html` as a fallback).

## How the agentic loop works

`agent.py` does the same dance the instructor showed in class:

1. Spawns the MCP server as a subprocess (stdio transport).
2. Calls `list_tools()` and prepends every tool's signature + docstring to the system prompt.
3. Loops: send prompt → Gemini replies with `FUNCTION_CALL: tool|arg|arg...` → we execute the MCP tool → append the result to history → repeat until Gemini emits `FINAL_ANSWER:`.

The default task tells Gemini to:
- Pull 5 recent `cs.LG` papers, 5 articles from The Hacker News, 3 high-severity CVEs.
- Bookmark each item with appropriate tags.
- Call `render_dashboard()` once.
- Wrap up with a summary.

Override the task by passing it on the command line:

```bash
python agent.py "Fetch top 5 cs.CR papers and bookmark anything mentioning prompt injection"
```

## Demo prompt for the YouTube video

> *"Fetch today's 5 latest arXiv cs.CL papers, the top 5 articles from The Hacker News, and any HIGH-or-CRITICAL CVEs from the past 48 hours. Bookmark anything related to LLMs, prompt injection, or MCP security. Then render a dashboard."*

The video should clearly show:
1. The agentic loop printing each `FUNCTION_CALL:` line.
2. The bookmarks landing in `data/feed_db.json`.
3. The Prefab UI / `data/dashboard.html` opening in the browser.

## Security notes

- All file operations are sandboxed under `./data` — no path-traversal possible.
- All HTTP calls have a 15-second timeout and pin a User-Agent.
- Outbound URLs are constructed from allow-listed parameters (no raw user URLs are ever fetched).
- XML parsing uses `feedparser` (which disables external entities by default) and `defusedxml` is in the deps for any custom parsing you add later.
- The Gemini API key lives in `.env`, which is `.gitignore`d — no credentials in source.
- HTML output escapes every user-visible field via `html.escape` to prevent stored-XSS in the dashboard.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `prefab_ui not installed` | The HTML dashboard still works. Install `pip install prefab-ui` for the React UI tool. |
| NVD fetch fails / 403 | NVD throttles aggressively without an API key. The tool degrades gracefully and returns an empty list. Re-run after a short wait. |
| Gemini quota errors | Switch model via `GEMINI_MODEL=gemini-2.0-flash python agent.py`. |
| `FUNCTION_CALL:` parsing breaks | The model sometimes wraps its reply in code fences. The agent already grabs the first matching line, but reduce the task complexity if it loops. |
