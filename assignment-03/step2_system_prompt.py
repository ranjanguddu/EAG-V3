"""
Step 2: System Prompt That Makes It an Agent
=============================================
We give Gemini a "rulebook" (system prompt) that says:
  - You are an agent.
  - Here are 6 tools you can use.
  - Respond ONLY in JSON: either ask for a tool OR give a final answer.

We DO NOT call any tools yet. We just want to see if the LLM
stops hallucinating and starts ASKING for help via JSON.

Run:
    source .venv/bin/activate
    python step2_system_prompt.py
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


SYSTEM_PROMPT = """You are a YouTube Learning Companion Agent.
You help users understand YouTube videos deeply.

You CANNOT watch YouTube videos directly. You MUST use tools.

You have access to the following tools:

1. get_video_metadata(video_url: str) -> dict
   Returns title, channel, duration of a YouTube video.

2. get_video_transcript(video_url: str) -> str
   Returns the full text transcript (captions) of the video.

3. summarize_video(transcript: str) -> str
   Generates a concise summary of the transcript text.

4. extract_key_concepts(transcript: str) -> list
   Returns a list of important technical concepts/topics mentioned.

5. search_arxiv_papers(concept: str) -> list
   Searches arXiv for academic papers related to a concept.

6. generate_quiz(transcript: str) -> list
   Creates 5 multiple-choice questions to test understanding.

You MUST respond in ONE of these two JSON formats:

If you need to use a tool:
{"tool_name": "<name>", "tool_arguments": {"<arg_name>": "<value>"}}

If you have the final answer:
{"answer": "<your final answer>"}

IMPORTANT RULES:
- Respond with ONLY the JSON. No other text. No markdown. No code fences.
- NEVER make up information about a video you have not seen the transcript of.
- Always start by fetching the transcript when asked about a specific video.
"""


def ask_gemini(prompt: str) -> str:
    """Call Gemini and return raw text response."""
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

    test_video_url = "https://www.youtube.com/watch?v=PeMlggyqz0Y"
    user_message = f"Please summarize this YouTube video for me: {test_video_url}"

    full_prompt = f"{SYSTEM_PROMPT}\n\nUser: {user_message}"

    print("=" * 70)
    print("USER ASKS:")
    print(user_message)
    print("=" * 70)

    response = ask_gemini(full_prompt)

    print("\n📦 RAW LLM RESPONSE:")
    print("-" * 70)
    print(response)
    print("-" * 70)

    print("\n👀 What to look for:")
    print("   ✅ The LLM should now reply with JSON like:")
    print('      {"tool_name": "get_video_transcript", "tool_arguments": {...}}')
    print("   ✅ It is NO LONGER hallucinating a fake summary.")
    print("   ✅ It is REQUESTING a tool — that is agent behavior!")
    print("\n   ⚠️  Sometimes Gemini wraps JSON in ```json ... ``` fences.")
    print("       That is the problem we will solve in Step 5 (robust parsing).")


if __name__ == "__main__":
    main()
