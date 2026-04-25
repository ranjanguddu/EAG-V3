"""
Step 1: Talk to an LLM
======================
Just call Gemini and see what comes back.

Goal: Prove that the raw LLM CANNOT actually watch a YouTube video.
It will give us a confident, plausible-sounding answer — that is HALLUCINATED.
This is exactly the gap our agent (with tools) will fill in later steps.

Run:
    source .venv/bin/activate
    python step1_basic_llm.py
"""

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


def ask_gemini(prompt: str) -> str:
    """Call Gemini with a single prompt and return the text response."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    r = requests.post(
        url,
        headers={"x-goog-api-key": API_KEY},
        json=body,
        timeout=30,
    )
    if r.status_code != 200:
        sys.exit(f"❌ {r.status_code}: {r.text}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def main() -> None:
    print(f"Using model: {MODEL}\n")

    print("=" * 70)
    print("Test 1: Ask something the LLM CAN answer (general knowledge)")
    print("=" * 70)
    answer1 = ask_gemini("In one sentence, what is a transformer in deep learning?")
    print(answer1)

    print("\n" + "=" * 70)
    print("Test 2: Ask about a SPECIFIC YouTube video (LLM cannot watch it!)")
    print("=" * 70)
    yt_url = "https://www.youtube.com/watch?v=zjkBMFhNj_g"
    prompt2 = (
        f"Watch this YouTube video and give me a 3-bullet summary: {yt_url}\n"
        "Be specific about what is said in the video."
    )
    answer2 = ask_gemini(prompt2)
    print(answer2)

    print("\n" + "=" * 70)
    print("👀 Notice the difference:")
    print("   - Test 1 = real knowledge")
    print("   - Test 2 = the LLM CANNOT actually open URLs.")
    print("            It either refuses, or worse, HALLUCINATES a fake summary.")
    print("   This is exactly why we need TOOLS (next steps).")
    print("=" * 70)


if __name__ == "__main__":
    main()
