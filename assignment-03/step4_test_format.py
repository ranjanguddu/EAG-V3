"""
Step 4: Check If the LLM Responds Properly
==========================================
We give Gemini the system prompt + a user question, then try to
parse its reply as JSON RAW (no cleanup). We do this 3 times so
you can see how often the LLM behaves vs misbehaves.

This proves WHY Step 5 (robust parsing) is necessary.

Run:
    source .venv/bin/activate
    python step4_test_format.py
"""

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("MODEL", "gemini-2.0-flash")

if not API_KEY:
    sys.exit(f"❌ GEMINI_API_KEY not found. Expected in {ENV_PATH}")


SYSTEM_PROMPT = """You are a YouTube Learning Companion Agent.
You help users understand YouTube videos deeply.

You CANNOT watch YouTube videos directly. You MUST use tools.

Available tools:
1. get_video_metadata(video_url: str)
2. get_video_transcript(video_url: str)
3. summarize_video(transcript: str)
4. extract_key_concepts(transcript: str)
5. search_arxiv_papers(concept: str)
6. generate_quiz(transcript: str)

You MUST respond in ONE of these two JSON formats:

If you need to use a tool:
{"tool_name": "<name>", "tool_arguments": {"<arg_name>": "<value>"}}

If you have the final answer:
{"answer": "<your final answer>"}

IMPORTANT: Respond with ONLY the JSON. No other text. No markdown. No code fences.
"""


def ask_gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    r = requests.post(url, headers={"x-goog-api-key": API_KEY}, json=body, timeout=30)
    if r.status_code != 200:
        sys.exit(f"❌ {r.status_code}: {r.text}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def try_parse_raw(response_text: str) -> tuple[bool, str]:
    """Try json.loads() WITHOUT any cleanup. Return (ok, message)."""
    try:
        parsed = json.loads(response_text)
        return True, json.dumps(parsed, indent=2)
    except json.JSONDecodeError as e:
        return False, f"JSONDecodeError: {e}"


def main() -> None:
    test_url = "https://www.youtube.com/watch?v=PeMlggyqz0Y"
    user_message = f"Please summarize this YouTube video for me: {test_url}"
    full_prompt = f"{SYSTEM_PROMPT}\n\nUser: {user_message}"

    successes = 0
    failures = 0

    for attempt in range(1, 4):
        print("=" * 70)
        print(f"ATTEMPT {attempt}")
        print("=" * 70)

        raw = ask_gemini(full_prompt)
        print("\n📦 Raw LLM response:")
        print("-" * 70)
        print(raw)
        print("-" * 70)

        ok, msg = try_parse_raw(raw)
        if ok:
            print("\n✅ Parsed successfully (no cleanup needed):")
            print(msg)
            successes += 1
        else:
            print("\n❌ FAILED to parse raw text directly.")
            print(msg)
            print("   ↳ This is exactly why we need robust parsing in Step 5.")
            failures += 1

        print()

    print("=" * 70)
    print(f"SUMMARY: {successes} clean / {failures} messy out of 3 attempts")
    print("=" * 70)
    if failures > 0:
        print("👉 Even one failure proves the point: LLMs are non-deterministic.")
        print("   In Step 5 we will write parse_llm_response() to handle this.")
    else:
        print("👉 All clean this time — but run again, you WILL see fences eventually.")
        print("   Step 5 will make our code resilient regardless.")


if __name__ == "__main__":
    main()
