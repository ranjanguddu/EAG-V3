"""
AI Pulse — MCP server.

Daily radar for AI/ML research (arXiv) + cyber-security news (RSS) + recent
CVEs (NVD), with a local bookmark store and a Prefab UI tool that renders
everything as an interactive dashboard.

Tools exposed
-------------
Internet:
  fetch_arxiv_papers(category, max_results)
  fetch_cybersec_news(source, limit)
  fetch_recent_cves(min_severity, days, limit)

CRUD on data/feed_db.json:
  bookmark_item(item_id, item_type, title, url, summary, tags_csv)
  mark_read(item_id)
  delete_bookmark(item_id)
  list_bookmarks(filter_tag)

UI:
  render_dashboard()  -> Prefab app  (also writes data/dashboard.html)

Run
---
  # As an MCP server (stdio) — what an agent / Claude Desktop launches:
  python ai_pulse_server.py

  # Inspector — manual, browser-based testing:
  fastmcp dev ai_pulse_server.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import feedparser  # type: ignore
import requests
from defusedxml import ElementTree as DET  # noqa: F401  # imported for safe XML semantics
from fastmcp import FastMCP

# Prefab UI is optional — the server still works (and an HTML fallback is
# always written), but the @mcp.tool(app=True) UI tool needs the package.
try:
    from prefab_ui.app import PrefabApp  # type: ignore
    from prefab_ui.components import (  # type: ignore
        Badge,
        Card,
        CardContent,
        CardHeader,
        CardTitle,
        Column,
        H1,
        H3,
        Muted,
        Row,
        Tab,
        Tabs,
        Text,
    )
    from prefab_ui.components.charts import (  # type: ignore
        BarChart,
        ChartSeries,
        PieChart,
    )

    PREFAB_AVAILABLE = True
except Exception:  # pragma: no cover - optional dep
    PREFAB_AVAILABLE = False


mcp = FastMCP("AIPulseServer")

# ---------------------------------------------------------------------------
# Sandbox + local store. Every file write is confined to ./data
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "feed_db.json"
DASHBOARD_HTML = DATA_DIR / "dashboard.html"
GENERATED_APP = DATA_DIR / "generated_app.py"  # rewritten by render_dashboard
                                                # so `prefab serve` hot-reloads

REQUEST_TIMEOUT = 15  # seconds, applied to every outbound HTTP call
USER_AGENT = "AIPulseMCP/1.0 (educational; contact: student)"

# Cyber-sec RSS feeds we trust. Add/remove as you like.
CYBERSEC_SOURCES: dict[str, str] = {
    "thehackernews": "https://feeds.feedburner.com/TheHackersNews",
    "bleepingcomputer": "https://www.bleepingcomputer.com/feed/",
    "krebs": "https://krebsonsecurity.com/feed/",
}

# arXiv categories we expect (validated against an allow-list to prevent
# parameter injection into the API URL).
ARXIV_CATEGORIES = {
    "cs.AI",
    "cs.LG",
    "cs.CL",
    "cs.CV",
    "cs.CR",
    "cs.NE",
    "stat.ML",
}

CVE_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


# ---------------------------------------------------------------------------
# Tiny JSON store (instead of SQLite — deliberately easy to inspect by hand).
# ---------------------------------------------------------------------------

def _load_db() -> dict[str, Any]:
    if not DB_PATH.exists():
        return {"bookmarks": {}}
    try:
        return json.loads(DB_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Corrupt file — back it up and start fresh rather than crashing.
        backup = DB_PATH.with_suffix(".bak")
        DB_PATH.rename(backup)
        return {"bookmarks": {}}


def _save_db(db: dict[str, Any]) -> None:
    DB_PATH.write_text(json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8")


def _stable_id(prefix: str, raw: str) -> str:
    """Deterministic short id so the same item never gets bookmarked twice."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()[:80]
    return f"{prefix}:{slug}" if slug else f"{prefix}:{int(time.time())}"


def _truncate(text: str, n: int = 400) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# ===========================================================================
# 1. INTERNET FETCH TOOLS
# ===========================================================================

@mcp.tool()
def fetch_arxiv_papers(category: str = "cs.LG", max_results: int = 10) -> str:
    """Fetch the latest papers from arXiv for a category.

    Args:
        category: One of cs.AI, cs.LG, cs.CL, cs.CV, cs.CR, cs.NE, stat.ML.
        max_results: How many papers to return (1-50).

    Returns:
        JSON string: {"papers": [{"id","title","authors","summary","url","published","category"}]}
    """
    if category not in ARXIV_CATEGORIES:
        raise ValueError(
            f"category must be one of {sorted(ARXIV_CATEGORIES)}, got {category!r}"
        )
    max_results = max(1, min(50, int(max_results)))

    # arXiv's Atom API. We sort by submitted date so "latest" really is latest.
    url = (
        "http://export.arxiv.org/api/query"
        f"?search_query=cat:{quote_plus(category)}"
        f"&start=0&max_results={max_results}"
        "&sortBy=submittedDate&sortOrder=descending"
    )
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()

    # feedparser handles Atom safely and ignores DTDs/external entities.
    parsed = feedparser.parse(resp.content)
    papers: list[dict[str, Any]] = []
    for entry in parsed.entries:
        arxiv_id = (entry.get("id") or "").rsplit("/", 1)[-1]
        papers.append(
            {
                "id": _stable_id("arxiv", arxiv_id or entry.get("title", "")),
                "title": _truncate(entry.get("title", ""), 200),
                "authors": [a.get("name", "") for a in entry.get("authors", [])][:6],
                "summary": _truncate(entry.get("summary", ""), 500),
                "url": entry.get("link", ""),
                "published": entry.get("published", ""),
                "category": category,
            }
        )
    return json.dumps({"category": category, "count": len(papers), "papers": papers})


@mcp.tool()
def fetch_cybersec_news(source: str = "thehackernews", limit: int = 10) -> str:
    """Fetch the latest cyber-security news from a trusted RSS feed.

    Args:
        source: One of: thehackernews, bleepingcomputer, krebs.
        limit: How many articles to return (1-30).

    Returns:
        JSON string: {"articles": [{"id","title","summary","url","published","source"}]}
    """
    if source not in CYBERSEC_SOURCES:
        raise ValueError(
            f"source must be one of {sorted(CYBERSEC_SOURCES)}, got {source!r}"
        )
    limit = max(1, min(30, int(limit)))

    parsed = feedparser.parse(
        CYBERSEC_SOURCES[source],
        request_headers={"User-Agent": USER_AGENT},
    )
    articles: list[dict[str, Any]] = []
    for entry in parsed.entries[:limit]:
        link = entry.get("link", "")
        articles.append(
            {
                "id": _stable_id(f"news-{source}", link or entry.get("title", "")),
                "title": _truncate(entry.get("title", ""), 200),
                "summary": _truncate(entry.get("summary", ""), 400),
                "url": link,
                "published": entry.get("published", ""),
                "source": source,
            }
        )
    return json.dumps({"source": source, "count": len(articles), "articles": articles})


@mcp.tool()
def fetch_recent_cves(
    min_severity: str = "HIGH", days: int = 7, limit: int = 10
) -> str:
    """Fetch recent CVEs from the NVD with a minimum severity threshold.

    Args:
        min_severity: LOW | MEDIUM | HIGH | CRITICAL.
        days: How many days back to look (1-30).
        limit: How many CVEs to return (1-50).

    Returns:
        JSON string: {"cves": [{"id","title","summary","severity","score","url","published"}]}
    """
    sev = (min_severity or "").upper()
    if sev not in CVE_SEVERITIES:
        raise ValueError(f"min_severity must be in {sorted(CVE_SEVERITIES)}, got {min_severity!r}")
    days = max(1, min(30, int(days)))
    limit = max(1, min(50, int(limit)))

    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=days)
    fmt = "%Y-%m-%dT%H:%M:%S.000"
    url = (
        "https://services.nvd.nist.gov/rest/json/cves/2.0"
        f"?pubStartDate={start.strftime(fmt)}"
        f"&pubEndDate={end.strftime(fmt)}"
        f"&resultsPerPage={limit * 4}"  # we filter client-side, fetch a few extra
    )

    try:
        resp = requests.get(
            url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        # NVD throttles aggressively without an API key — degrade gracefully.
        return json.dumps({"error": f"NVD fetch failed: {e}", "cves": []})

    severity_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    threshold = severity_rank[sev]

    cves: list[dict[str, Any]] = []
    for vuln in data.get("vulnerabilities", []):
        cve = vuln.get("cve") or {}
        cve_id = cve.get("id") or ""
        if not cve_id:
            continue

        # Pick the best available CVSS metric (v3.1 > v3.0 > v2).
        metrics = cve.get("metrics", {}) or {}
        score = 0.0
        item_sev = "LOW"
        for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
            if metrics.get(key):
                m = metrics[key][0].get("cvssData", {}) or {}
                score = float(m.get("baseScore") or 0.0)
                item_sev = (m.get("baseSeverity") or "").upper() or _score_to_sev(score)
                break

        if severity_rank.get(item_sev, 0) < threshold:
            continue

        descriptions = cve.get("descriptions", []) or []
        en_desc = next(
            (d.get("value", "") for d in descriptions if d.get("lang") == "en"),
            "",
        )

        cves.append(
            {
                "id": _stable_id("cve", cve_id),
                "cve_id": cve_id,
                "title": cve_id,
                "summary": _truncate(en_desc, 400),
                "severity": item_sev,
                "score": score,
                "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                "published": cve.get("published", ""),
            }
        )
        if len(cves) >= limit:
            break

    return json.dumps({"min_severity": sev, "count": len(cves), "cves": cves})


def _score_to_sev(score: float) -> str:
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MEDIUM"
    return "LOW"


# ===========================================================================
# 2. CRUD ON LOCAL BOOKMARK STORE
# ===========================================================================

ALLOWED_TYPES = {"paper", "news", "cve"}


@mcp.tool()
def bookmark_item(
    item_id: str,
    item_type: str,
    title: str,
    url: str,
    summary: str = "",
    tags_csv: str = "",
) -> str:
    """Save an item (paper / news / cve) to the local bookmark store.

    Args:
        item_id: Stable id from a fetch_* tool (e.g. 'arxiv:2401.12345').
        item_type: 'paper', 'news', or 'cve'.
        title: Short title to display.
        url: Canonical link to the original.
        summary: Short summary / abstract.
        tags_csv: Comma-separated tags, e.g. "llm,prompt-injection".

    Returns:
        Status message.
    """
    if item_type not in ALLOWED_TYPES:
        raise ValueError(f"item_type must be one of {sorted(ALLOWED_TYPES)}")
    if not item_id or not title:
        raise ValueError("item_id and title are required")

    tags = [t.strip().lower() for t in (tags_csv or "").split(",") if t.strip()]

    db = _load_db()
    db["bookmarks"][item_id] = {
        "item_id": item_id,
        "type": item_type,
        "title": _truncate(title, 200),
        "url": url,
        "summary": _truncate(summary, 500),
        "tags": tags,
        "read": False,
        "saved_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    }
    _save_db(db)
    return f"Bookmarked {item_id} ({item_type}) with tags={tags or '[]'}"


@mcp.tool()
def mark_read(item_id: str) -> str:
    """Mark a bookmark as read."""
    db = _load_db()
    if item_id not in db["bookmarks"]:
        raise ValueError(f"Unknown bookmark id: {item_id}")
    db["bookmarks"][item_id]["read"] = True
    _save_db(db)
    return f"Marked {item_id} as read"


@mcp.tool()
def delete_bookmark(item_id: str) -> str:
    """Delete a bookmark by id."""
    db = _load_db()
    if item_id not in db["bookmarks"]:
        raise ValueError(f"Unknown bookmark id: {item_id}")
    del db["bookmarks"][item_id]
    _save_db(db)
    return f"Deleted {item_id}"


@mcp.tool()
def list_bookmarks(filter_tag: str = "") -> str:
    """List bookmarks, optionally filtered by a single tag.

    Args:
        filter_tag: Empty string returns everything; otherwise only items with that tag.

    Returns:
        JSON string: {"count": N, "bookmarks": [...]}
    """
    db = _load_db()
    items = list(db["bookmarks"].values())
    if filter_tag:
        tag = filter_tag.strip().lower()
        items = [b for b in items if tag in b.get("tags", [])]
    items.sort(key=lambda b: b.get("saved_at", ""), reverse=True)
    return json.dumps({"count": len(items), "bookmarks": items})


# ===========================================================================
# 3. UI — render the dashboard via Prefab and an HTML fallback
# ===========================================================================

def _bookmark_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = {"paper": 0, "news": 0, "cve": 0}
    by_severity = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    tag_counts: dict[str, int] = {}
    unread = 0
    for b in items:
        by_type[b.get("type", "")] = by_type.get(b.get("type", ""), 0) + 1
        if not b.get("read"):
            unread += 1
        for t in b.get("tags", []):
            tag_counts[t] = tag_counts.get(t, 0) + 1
        if b.get("type") == "cve":
            sev = (b.get("summary_severity") or "").upper()
            # severity isn't always carried on the bookmark; ignore quietly
            if sev in by_severity:
                by_severity[sev] += 1
    top_tags = sorted(tag_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
    return {
        "total": len(items),
        "unread": unread,
        "by_type": by_type,
        "top_tags": top_tags,
        "by_severity": by_severity,
    }


def _build_prefab_app_source(items: list[dict[str, Any]], stats: dict[str, Any]) -> str:
    """Produce a self-contained Prefab Python app from current bookmarks.

    The output is written to ``data/generated_app.py`` and is what
    ``prefab serve`` watches for hot-reload. We render to source (instead of
    constructing PrefabApp inline) so the agent can run on any host while a
    dedicated Prefab dev server owns the actual rendering.
    """
    papers = [b for b in items if b.get("type") == "paper"]
    news = [b for b in items if b.get("type") == "news"]
    cves = [b for b in items if b.get("type") == "cve"]

    type_breakdown = [
        {"name": k, "value": v} for k, v in stats["by_type"].items() if v > 0
    ]
    tag_data = [{"tag": t, "count": n} for t, n in stats["top_tags"]]

    lines: list[str] = [
        '"""Auto-generated by ai_pulse_server.py — do not edit by hand."""',
        "from prefab_ui.app import PrefabApp",
        "from prefab_ui.components import (",
        "    Badge, Card, CardContent, CardHeader, CardTitle,",
        "    Column, H1, H3, Muted, Row, Tab, Tabs, Text,",
        ")",
        "from prefab_ui.components.charts import (",
        "    BarChart, ChartSeries, PieChart,",
        ")",
        "",
        'with PrefabApp(css_class="max-w-6xl mx-auto p-6") as app:',
        "    with Card():",
        "        with CardHeader():",
        '            CardTitle("AI Pulse — daily radar")',
        "        with CardContent():",
        "            with Column(gap=4):",
        # ---- stats row -----------------------------------------------------
        "                with Row(gap=6):",
    ]

    def _stat(label: str, value: int) -> list[str]:
        return [
            "                    with Column(gap=1):",
            f"                        Muted({label!r})",
            f"                        H1({str(value)!r})",
        ]

    lines += _stat("Bookmarks", stats["total"])
    lines += _stat("Unread", stats["unread"])
    lines += _stat("Papers", stats["by_type"].get("paper", 0))
    lines += _stat("News", stats["by_type"].get("news", 0))
    lines += _stat("CVEs", stats["by_type"].get("cve", 0))

    # ---- pie chart of types -------------------------------------------------
    if type_breakdown:
        lines.append(
            f"                PieChart(data={type_breakdown!r}, "
            f"data_key='value', name_key='name', show_legend=True)"
        )

    # ---- bar chart of top tags ---------------------------------------------
    if tag_data:
        lines += [
            f"                BarChart(data={tag_data!r},",
            "                         series=[ChartSeries(data_key='count', label='count')],",
            "                         x_axis='tag', show_legend=False)",
        ]

    # ---- tabs of cards ------------------------------------------------------
    lines += [
        '                with Tabs(value="papers"):',
    ]
    for label, value, group in (
        ("Papers", "papers", papers),
        ("News", "news", news),
        ("CVEs", "cves", cves),
    ):
        lines += [
            f"                    with Tab({label!r}, value={value!r}):",
            "                        with Column(gap=3):",
        ]
        if not group:
            lines.append(
                f"                            Muted({f'No {label.lower()} bookmarked yet.'!r})"
            )
            continue
        for b in group[:20]:
            title = b.get("title", "")
            summary = b.get("summary", "")
            saved = b.get("saved_at", "")
            tags = b.get("tags", [])
            lines += [
                "                            with Card():",
                "                                with CardContent():",
                "                                    with Column(gap=1):",
                f"                                        H3({title!r})",
                f"                                        Text({summary!r})",
            ]
            if tags:
                lines.append("                                        with Row(gap=2):")
                for t in tags:
                    lines.append(
                        f"                                            Badge({t!r}, variant='default')"
                    )
            lines.append(f"                                        Muted({f'saved {saved}'!r})")

    return "\n".join(lines) + "\n"


def _write_prefab_app(items: list[dict[str, Any]], stats: dict[str, Any]) -> Path:
    """Generate ``data/generated_app.py`` and syntax-check it before saving."""
    source = _build_prefab_app_source(items, stats)
    compile(source, "<generated_app>", "exec")  # fail loudly on broken codegen
    GENERATED_APP.write_text(source, encoding="utf-8")
    return GENERATED_APP


def _write_html_fallback(items: list[dict[str, Any]], stats: dict[str, Any]) -> Path:
    """Always-on HTML dashboard — works even without prefab installed.

    Self-contained: no external JS/CSS, safe escaping of all user content.
    """
    from html import escape

    def card(b: dict[str, Any]) -> str:
        kind = b.get("type", "")
        badge_color = {
            "paper": "#6366f1",
            "news": "#0ea5e9",
            "cve": "#ef4444",
        }.get(kind, "#64748b")
        tags = " ".join(
            f'<span class="tag">{escape(t)}</span>' for t in b.get("tags", [])
        )
        return f"""
        <article class="card">
          <div class="row">
            <span class="badge" style="background:{badge_color}">{escape(kind.upper())}</span>
            {'<span class="unread">UNREAD</span>' if not b.get('read') else ''}
          </div>
          <h3><a href="{escape(b.get('url', '#'))}" target="_blank" rel="noopener noreferrer">{escape(b.get('title', ''))}</a></h3>
          <p>{escape(b.get('summary', ''))}</p>
          <div class="row tags">{tags}</div>
          <div class="muted">saved {escape(b.get('saved_at', ''))}</div>
        </article>
        """

    cards_html = "\n".join(card(b) for b in items) or '<p class="muted">No bookmarks yet — call <code>bookmark_item</code> first.</p>'
    top_tags_html = " ".join(
        f'<span class="tag">{escape(t)} · {n}</span>' for t, n in stats["top_tags"]
    ) or '<span class="muted">no tags yet</span>'

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>AI Pulse — your daily radar</title>
<style>
  body {{ font: 15px/1.5 -apple-system, system-ui, sans-serif; margin: 0;
         background:#0f172a; color:#e2e8f0; }}
  header {{ padding: 32px 40px; background:#020617; border-bottom:1px solid #1e293b; }}
  header h1 {{ margin:0; font-size:28px; letter-spacing:-0.02em; }}
  header p {{ margin:6px 0 0; color:#94a3b8; }}
  .stats {{ display:flex; gap:16px; padding:24px 40px; flex-wrap:wrap;
            background:#0b1220; border-bottom:1px solid #1e293b; }}
  .stat {{ background:#1e293b; border-radius:12px; padding:14px 20px; min-width:130px; }}
  .stat .n {{ font-size:24px; font-weight:600; }}
  .stat .l {{ color:#94a3b8; font-size:12px; text-transform:uppercase; letter-spacing:0.06em; }}
  main {{ padding: 24px 40px 60px; }}
  .grid {{ display:grid; gap:16px; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }}
  .card {{ background:#1e293b; border-radius:12px; padding:18px; border:1px solid #334155; }}
  .card h3 {{ margin:6px 0 8px; font-size:16px; }}
  .card a {{ color:#e2e8f0; text-decoration:none; }}
  .card a:hover {{ color:#93c5fd; }}
  .card p {{ margin:0; color:#cbd5e1; font-size:14px; }}
  .row {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
  .badge {{ color:white; font-size:11px; padding:3px 8px; border-radius:999px;
            font-weight:600; letter-spacing:0.04em; }}
  .unread {{ color:#fbbf24; font-size:11px; font-weight:600; letter-spacing:0.04em; }}
  .tags {{ margin-top:10px; }}
  .tag {{ background:#334155; color:#cbd5e1; font-size:12px; padding:2px 8px; border-radius:6px; }}
  .muted {{ color:#64748b; font-size:12px; margin-top:8px; }}
  .toptags {{ padding: 0 40px 24px; }}
</style>
</head>
<body>
<header>
  <h1>AI Pulse</h1>
  <p>Your daily radar for AI/ML research and cyber-security threats.</p>
</header>
<section class="stats">
  <div class="stat"><div class="n">{stats['total']}</div><div class="l">Bookmarks</div></div>
  <div class="stat"><div class="n">{stats['unread']}</div><div class="l">Unread</div></div>
  <div class="stat"><div class="n">{stats['by_type'].get('paper', 0)}</div><div class="l">arXiv papers</div></div>
  <div class="stat"><div class="n">{stats['by_type'].get('news', 0)}</div><div class="l">News</div></div>
  <div class="stat"><div class="n">{stats['by_type'].get('cve', 0)}</div><div class="l">CVEs</div></div>
</section>
<section class="toptags row">
  <strong style="margin-right:8px">Top tags:</strong> {top_tags_html}
</section>
<main>
  <div class="grid">{cards_html}</div>
</main>
</body>
</html>
"""
    DASHBOARD_HTML.write_text(html, encoding="utf-8")
    return DASHBOARD_HTML


if PREFAB_AVAILABLE:

    @mcp.tool(app=True)
    def render_dashboard() -> "PrefabApp":
        """Render the AI Pulse dashboard as a Prefab UI.

        Side effects:
          * writes data/generated_app.py (live-reloads in `prefab serve`)
          * writes data/dashboard.html  (open directly in any browser)
        """
        db = _load_db()
        items = list(db["bookmarks"].values())
        items.sort(key=lambda b: b.get("saved_at", ""), reverse=True)
        stats = _bookmark_stats(items)
        _write_prefab_app(items, stats)
        _write_html_fallback(items, stats)

        papers = [b for b in items if b.get("type") == "paper"]
        news = [b for b in items if b.get("type") == "news"]
        cves = [b for b in items if b.get("type") == "cve"]

        with PrefabApp(css_class="max-w-5xl mx-auto p-6") as app:
            with Card():
                with CardHeader():
                    CardTitle("AI Pulse — daily radar")
                with CardContent():
                    with Column(gap=4):
                        with Row(gap=4):
                            with Column(gap=1):
                                Muted("Bookmarks")
                                H1(str(stats["total"]))
                            with Column(gap=1):
                                Muted("Unread")
                                H1(str(stats["unread"]))
                            with Column(gap=1):
                                Muted("Papers")
                                H1(str(stats["by_type"].get("paper", 0)))
                            with Column(gap=1):
                                Muted("News")
                                H1(str(stats["by_type"].get("news", 0)))
                            with Column(gap=1):
                                Muted("CVEs")
                                H1(str(stats["by_type"].get("cve", 0)))

                        type_breakdown = [
                            {"name": k, "value": v}
                            for k, v in stats["by_type"].items()
                            if v > 0
                        ]
                        if type_breakdown:
                            PieChart(
                                data=type_breakdown,
                                data_key="value",
                                name_key="name",
                                show_legend=True,
                            )

                        if stats["top_tags"]:
                            tag_data = [{"tag": t, "count": n} for t, n in stats["top_tags"]]
                            BarChart(
                                data=tag_data,
                                series=[ChartSeries(data_key="count", label="count")],
                                x_axis="tag",
                                show_legend=False,
                            )

                        with Tabs(value="papers"):
                            for label, value, group in (
                                ("Papers", "papers", papers),
                                ("News", "news", news),
                                ("CVEs", "cves", cves),
                            ):
                                with Tab(label, value=value):
                                    with Column(gap=3):
                                        if not group:
                                            Muted(f"No {label.lower()} bookmarked yet.")
                                        for b in group[:20]:
                                            with Card():
                                                with CardContent():
                                                    with Column(gap=1):
                                                        H3(b.get("title", ""))
                                                        Text(b.get("summary", ""))
                                                        with Row(gap=2):
                                                            for t in b.get("tags", []):
                                                                Badge(t, variant="default")
                                                        Muted(f"saved {b.get('saved_at', '')}")
        return app

else:

    @mcp.tool()
    def render_dashboard() -> str:
        """Render the AI Pulse dashboard as a self-contained HTML file.

        prefab_ui isn't installed, so we fall back to a clean HTML page.
        We also write data/generated_app.py for whenever you do install Prefab
        and run `python serve_dashboard.py`.

        Returns the absolute path of the generated HTML file.
        """
        db = _load_db()
        items = list(db["bookmarks"].values())
        items.sort(key=lambda b: b.get("saved_at", ""), reverse=True)
        stats = _bookmark_stats(items)
        try:
            _write_prefab_app(items, stats)
        except Exception:
            # Prefab DSL output may fail to compile if a tag/title is exotic;
            # the HTML fallback still works, so keep going.
            pass
        path = _write_html_fallback(items, stats)
        return f"Wrote {path}  (open it in your browser)"


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"AI Pulse MCP server starting — data dir: {DATA_DIR}", file=sys.stderr)
    if len(sys.argv) > 1 and sys.argv[1] == "dev":
        mcp.run()
    else:
        mcp.run(transport="stdio")
