"""
Step 5: Robust Parsing
======================
Build a single function `parse_llm_response()` that handles every
common way LLMs mangle their JSON output:

    1. Clean JSON                      -> works directly
    2. Wrapped in ```json ... ```      -> strip the fences
    3. Wrapped in ``` ... ```          -> strip the fences
    4. Surrounded by commentary text   -> regex fallback
    5. Total garbage                   -> raise a clear error

We test the parser against 6 fixtures (no LLM call needed),
then run it once on a LIVE Gemini response to prove end-to-end safety.

Run:
    source .venv/bin/activate
    python step5_robust_parsing.py
"""

import json
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("MODEL", "gemini-2.0-flash")


# ---------------------------------------------------------------------------
# THE PARSER (the star of this step)
# ---------------------------------------------------------------------------
def parse_llm_response(response_text: str) -> dict:
    """
    Parse an LLM response into a Python dict.
    Handles: clean JSON, ```json fences, ``` fences, leading/trailing commentary.
    Raises ValueError with a helpful message if parsing is impossible.
    """
    if not isinstance(response_text, str):
        raise ValueError(f"Expected str, got {type(response_text).__name__}")

    text = response_text.strip()
    if not text:
        raise ValueError("Empty LLM response")

    # Defense 1: strip markdown code fences (```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        lines = text.split("\n")
        # remove the opening fence (line 0)
        lines = lines[1:]
        # remove the closing fence (last line) if present
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        # strip a leading 'json' language hint that sometimes lingers
        if text.lower().startswith("json"):
            text = text[4:].lstrip(":").strip()

    # Defense 2: try a clean parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Defense 3: regex fallback — find first JSON object or array
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    arr_match = re.search(r"\[.*\]", text, re.DOTALL)

    candidates = []
    if obj_match:
        candidates.append(obj_match.group())
    if arr_match:
        candidates.append(arr_match.group())

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise ValueError(f"Could not parse LLM response as JSON. Raw text was:\n{response_text}")


# ---------------------------------------------------------------------------
# Fixture-based unit tests (no network)
# ---------------------------------------------------------------------------
def _run_fixture_tests() -> int:
    """Return number of failures."""
    fixtures = [
        ("clean JSON object",
         '{"tool_name": "get_video_transcript", "tool_arguments": {"video_url": "abc"}}'),
        ("clean JSON answer",
         '{"answer": "Hello world"}'),
        ("wrapped in ```json fences",
         '```json\n{"tool_name": "summarize_video", "tool_arguments": {"transcript": "x"}}\n```'),
        ("wrapped in plain ``` fences",
         '```\n{"answer": "42"}\n```'),
        ("leading commentary then JSON",
         'Sure! Here is the JSON you asked for:\n{"tool_name": "get_video_metadata", "tool_arguments": {"video_url": "xyz"}}'),
        ("trailing commentary after JSON",
         '{"answer": "done"}\n\nLet me know if you need anything else!'),
    ]

    failures = 0
    for label, raw in fixtures:
        try:
            parsed = parse_llm_response(raw)
            print(f"✅ {label:38s} -> {json.dumps(parsed)[:80]}")
        except Exception as e:
            print(f"❌ {label:38s} -> FAIL: {e}")
            failures += 1
    return failures


# ---------------------------------------------------------------------------
# Bonus: run once against a LIVE Gemini call to prove end-to-end safety
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a YouTube Learning Companion Agent.

You CANNOT watch videos. You MUST use tools.

Tools:
1. get_video_metadata(video_url)
2. get_video_transcript(video_url)
3. summarize_video(transcript)
4. extract_key_concepts(transcript)
5. search_arxiv_papers(concept)
6. generate_quiz(transcript)

Reply with ONE JSON only:
{"tool_name": "<name>", "tool_arguments": {...}}
OR
{"answer": "<final answer>"}

No prose. No markdown.
"""


def _ask_gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    r = requests.post(url, headers={"x-goog-api-key": API_KEY}, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _live_test() -> None:
    if not API_KEY:
        print("\n(Skipping live test — no GEMINI_API_KEY)")
        return
    test_url = "https://www.youtube.com/watch?v=PeMlggyqz0Y"
    user_message = f"Summarize this video: {test_url}"
    raw = _ask_gemini(f"{SYSTEM_PROMPT}\n\nUser: {user_message}")

    print("\n📦 Live Gemini raw response:")
    print("-" * 70)
    print(raw)
    print("-" * 70)

    try:
        parsed = parse_llm_response(raw)
        print("\n✅ parse_llm_response() succeeded:")
        print(json.dumps(parsed, indent=2))
    except ValueError as e:
        print(f"\n❌ Even the robust parser failed: {e}")


def main() -> None:
    print("=" * 70)
    print("Fixture tests (offline, no LLM call)")
    print("=" * 70)
    failures = _run_fixture_tests()
    print()
    if failures == 0:
        print("🎉 All 6 fixtures parsed correctly.")
    else:
        print(f"⚠️  {failures} fixture(s) failed — fix parser before continuing.")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("Live test against Gemini")
    print("=" * 70)
    _live_test()


if __name__ == "__main__":
    main()
