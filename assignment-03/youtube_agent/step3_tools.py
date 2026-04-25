"""
Step 3: Define Tools as Python Functions
=========================================
We finally implement the 6 tools that the system prompt promised.

Pattern (from the notes):
  - Each tool is a regular Python function.
  - Each tool returns a string (JSON-encoded for structured data).
  - All tools are registered in a `TOOLS` dictionary so the agent
    can look them up by name later.

We DO NOT involve the LLM yet — we just test each tool standalone
to make sure they actually work before wiring them up.

Run:
    source .venv/bin/activate
    python step3_tools.py
"""

import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_PATH)

API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("MODEL", "gemini-2.0-flash")


# ---------------------------------------------------------------------------
# Internal helper: call Gemini (used by summarize / extract / quiz tools)
# ---------------------------------------------------------------------------
def _call_gemini(prompt: str) -> str:
    """Plain Gemini call. Returns text. Raises on error."""
    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    r = requests.post(url, headers={"x-goog-api-key": API_KEY}, json=body, timeout=60)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


# ---------------------------------------------------------------------------
# Internal helper: pull the 11-char video ID out of any YouTube URL
# ---------------------------------------------------------------------------
def _extract_video_id(video_url: str) -> str:
    """Extract video ID from common YouTube URL formats."""
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, video_url)
        if m:
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_url):
        return video_url
    raise ValueError(f"Could not extract video ID from: {video_url}")


# ---------------------------------------------------------------------------
# TOOL 1: get_video_metadata
# ---------------------------------------------------------------------------
def get_video_metadata(video_url: str) -> str:
    """Fetch title, channel, thumbnail using YouTube's free oEmbed API."""
    try:
        oembed = "https://www.youtube.com/oembed"
        params = {"url": video_url, "format": "json"}
        r = requests.get(oembed, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return json.dumps({
            "title": data.get("title"),
            "channel": data.get("author_name"),
            "thumbnail": data.get("thumbnail_url"),
            "video_id": _extract_video_id(video_url),
        })
    except Exception as e:
        return json.dumps({"error": f"metadata fetch failed: {e}"})


# ---------------------------------------------------------------------------
# TOOL 2: get_video_transcript
# ---------------------------------------------------------------------------
def get_video_transcript(video_url: str) -> str:
    """Pull captions via youtube-transcript-api (no API key needed)."""
    try:
        video_id = _extract_video_id(video_url)
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id)
        text = " ".join(snippet.text for snippet in fetched.snippets)
        return json.dumps({
            "video_id": video_id,
            "language": fetched.language_code,
            "word_count": len(text.split()),
            "transcript": text,
        })
    except Exception as e:
        return json.dumps({"error": f"transcript fetch failed: {e}"})


# ---------------------------------------------------------------------------
# TOOL 3: summarize_video
# ---------------------------------------------------------------------------
def summarize_video(transcript: str) -> str:
    """Generate a concise summary of a transcript using Gemini."""
    try:
        prompt = (
            "Summarize the following video transcript in:\n"
            "1. One single-sentence headline.\n"
            "2. A short paragraph (3-4 sentences).\n"
            "3. Five bullet-point key takeaways.\n\n"
            f"TRANSCRIPT:\n{transcript}"
        )
        summary = _call_gemini(prompt)
        return json.dumps({"summary": summary})
    except Exception as e:
        return json.dumps({"error": f"summarization failed: {e}"})


# ---------------------------------------------------------------------------
# TOOL 4: extract_key_concepts
# ---------------------------------------------------------------------------
def extract_key_concepts(transcript: str) -> str:
    """Ask Gemini to extract a clean list of technical concepts/topics."""
    try:
        prompt = (
            "Extract the most important technical concepts, topics, or named "
            "entities from this transcript. Reply ONLY with a JSON array of "
            "strings, max 7 items, no commentary, no markdown fences.\n\n"
            f"TRANSCRIPT:\n{transcript}"
        )
        raw = _call_gemini(prompt).strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
            raw = "\n".join(lines).strip()
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        try:
            concepts = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            concepts = json.loads(m.group()) if m else []
        return json.dumps({"concepts": concepts})
    except Exception as e:
        return json.dumps({"error": f"concept extraction failed: {e}"})


# ---------------------------------------------------------------------------
# TOOL 5: search_arxiv_papers
# ---------------------------------------------------------------------------
def search_arxiv_papers(concept: str) -> str:
    """Search arXiv (free API, no key) for papers on a given concept."""
    try:
        safe = urllib.parse.quote_plus(concept)
        url = (
            "http://export.arxiv.org/api/query?"
            f"search_query=all:{safe}&start=0&max_results=3&"
            "sortBy=relevance&sortOrder=descending"
        )
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        ns = {"a": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(r.text)
        papers = []
        for entry in root.findall("a:entry", ns):
            papers.append({
                "title": (entry.findtext("a:title", default="", namespaces=ns) or "").strip(),
                "summary": (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()[:300],
                "url": entry.findtext("a:id", default="", namespaces=ns),
                "published": entry.findtext("a:published", default="", namespaces=ns),
            })
        return json.dumps({"concept": concept, "papers": papers})
    except Exception as e:
        return json.dumps({"error": f"arxiv search failed: {e}"})


# ---------------------------------------------------------------------------
# TOOL 6: generate_quiz
# ---------------------------------------------------------------------------
def generate_quiz(transcript: str) -> str:
    """Ask Gemini to generate 5 MCQs (with explanations) based on the transcript."""
    try:
        prompt = (
            "Create exactly 5 multiple-choice questions to test understanding "
            "of this video transcript. Each question must have:\n"
            "  - 4 distinct options labelled A, B, C, D\n"
            "  - exactly ONE correct answer letter\n"
            "  - a brief explanation (1-2 sentences) of WHY that answer is correct\n"
            "Reply ONLY with a JSON array, no commentary, no markdown fences. "
            'Schema: [{"q": "...", "options": {"A": "...", "B": "...", '
            '"C": "...", "D": "..."}, "answer": "A", '
            '"explanation": "..."}]\n\n'
            f"TRANSCRIPT:\n{transcript}"
        )
        raw = _call_gemini(prompt).strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
            raw = "\n".join(lines).strip()
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        try:
            quiz = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            quiz = json.loads(m.group()) if m else []
        return json.dumps({"quiz": quiz})
    except Exception as e:
        return json.dumps({"error": f"quiz generation failed: {e}"})


# ---------------------------------------------------------------------------
# Tool registry — the LLM will name a tool, and we will look it up here.
# ---------------------------------------------------------------------------
TOOLS = {
    "get_video_metadata": get_video_metadata,
    "get_video_transcript": get_video_transcript,
    "summarize_video": summarize_video,
    "extract_key_concepts": extract_key_concepts,
    "search_arxiv_papers": search_arxiv_papers,
    "generate_quiz": generate_quiz,
}


# ---------------------------------------------------------------------------
# Standalone test harness — runs each tool with the demo video.
# ---------------------------------------------------------------------------
def _pretty(label: str, raw_json: str, max_chars: int = 600) -> None:
    print(f"\n--- {label} ---")
    try:
        data = json.loads(raw_json)
        out = json.dumps(data, indent=2, ensure_ascii=False)
    except Exception:
        out = raw_json
    print(out if len(out) <= max_chars else out[:max_chars] + "\n... [truncated]")


def main() -> None:
    if not API_KEY:
        sys.exit(f"❌ GEMINI_API_KEY not found. Expected in {ENV_PATH}")

    test_url = "https://www.youtube.com/watch?v=PeMlggyqz0Y"
    print(f"Testing every tool against:\n{test_url}\n")

    print("=" * 70)
    print("TOOL 1: get_video_metadata")
    print("=" * 70)
    meta = get_video_metadata(test_url)
    _pretty("metadata", meta)

    print("\n" + "=" * 70)
    print("TOOL 2: get_video_transcript")
    print("=" * 70)
    transcript_raw = get_video_transcript(test_url)
    _pretty("transcript (truncated)", transcript_raw, max_chars=400)
    transcript_data = json.loads(transcript_raw)
    transcript_text = transcript_data.get("transcript", "")
    if not transcript_text:
        print("\n⚠️  No transcript available — remaining tools will be skipped.")
        return

    print("\n" + "=" * 70)
    print("TOOL 3: summarize_video")
    print("=" * 70)
    _pretty("summary", summarize_video(transcript_text), max_chars=1200)

    print("\n" + "=" * 70)
    print("TOOL 4: extract_key_concepts")
    print("=" * 70)
    concepts_raw = extract_key_concepts(transcript_text)
    _pretty("concepts", concepts_raw)

    concepts_data = json.loads(concepts_raw)
    first_concept = (concepts_data.get("concepts") or ["machine learning"])[0]

    print("\n" + "=" * 70)
    print(f"TOOL 5: search_arxiv_papers  (concept = '{first_concept}')")
    print("=" * 70)
    _pretty("arxiv papers", search_arxiv_papers(first_concept), max_chars=1200)

    print("\n" + "=" * 70)
    print("TOOL 6: generate_quiz")
    print("=" * 70)
    _pretty("quiz", generate_quiz(transcript_text), max_chars=1500)

    print("\n" + "=" * 70)
    print("✅ All 6 tools ran. Registry contents:")
    for name in TOOLS:
        print(f"   - {name}")
    print("=" * 70)


if __name__ == "__main__":
    main()
