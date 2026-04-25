"""
Step 6: Call the Tool and Feed Results Back
============================================
First end-to-end round trip:
  User -> LLM -> tool request -> tool runs -> result fed back -> LLM -> answer

We HAND-CODE the two LLM calls (no loop yet) so every link in the
chain is visible. Step 7 will turn this into a proper loop.

Run:
    source .venv/bin/activate
    python step6_one_loop.py
"""

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from step3_tools import TOOLS
from step5_robust_parsing import parse_llm_response

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
   Returns title, channel, thumbnail of a YouTube video.

2. get_video_transcript(video_url: str)
   Returns the full text transcript of the video.

3. summarize_video(transcript: str)
   Generates a concise summary of the transcript.

4. extract_key_concepts(transcript: str)
   Returns a list of important technical concepts.

5. search_arxiv_papers(concept: str)
   Searches arXiv for academic papers on a concept.

6. generate_quiz(transcript: str)
   Creates 5 MCQs to test understanding.

You MUST respond in ONE of these two JSON formats:

If you need to use a tool:
{"tool_name": "<name>", "tool_arguments": {"<arg_name>": "<value>"}}

If you have the final answer:
{"answer": "<your final answer>"}

IMPORTANT:
- Respond with ONLY the JSON. No prose. No markdown.
- After receiving a tool result, give the final answer.
"""


def ask_gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    r = requests.post(url, headers={"x-goog-api-key": API_KEY}, json=body, timeout=60)
    if r.status_code != 200:
        sys.exit(f"❌ {r.status_code}: {r.text}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def main() -> None:
    test_url = "https://www.youtube.com/watch?v=PeMlggyqz0Y"
    user_message = (
        f"What is the title and channel name of this YouTube video? {test_url}"
    )

    print("=" * 70)
    print(f"USER: {user_message}")
    print("=" * 70)

    # ---------------- Round 1: ask LLM what to do ----------------
    print("\n🧠 [Round 1] Sending system prompt + user message to Gemini...")
    conversation = f"{SYSTEM_PROMPT}\n\nUser: {user_message}"

    raw1 = ask_gemini(conversation)
    print("\n📦 LLM raw reply:")
    print(raw1)

    parsed1 = parse_llm_response(raw1)
    print("\n✅ Parsed:")
    print(json.dumps(parsed1, indent=2))

    if "answer" in parsed1:
        print("\n🎯 LLM answered without needing a tool — done.")
        print(f"Final answer: {parsed1['answer']}")
        return

    if "tool_name" not in parsed1:
        sys.exit("❌ LLM reply had neither 'tool_name' nor 'answer'.")

    tool_name = parsed1["tool_name"]
    tool_args = parsed1.get("tool_arguments", {})

    if tool_name not in TOOLS:
        sys.exit(f"❌ LLM asked for unknown tool '{tool_name}'. Available: {list(TOOLS)}")

    # ---------------- Execute the tool ----------------
    print(f"\n🔧 Executing tool: {tool_name}({tool_args})")
    tool_result = TOOLS[tool_name](**tool_args)
    print("\n📥 Tool result:")
    try:
        print(json.dumps(json.loads(tool_result), indent=2)[:600])
    except Exception:
        print(tool_result[:600])

    # ---------------- Round 2: feed result back to LLM ----------------
    print("\n🧠 [Round 2] Sending tool result back to Gemini...")
    conversation += f"\n\nAssistant: {raw1}"
    conversation += f"\n\nTool Result ({tool_name}): {tool_result}"
    conversation += "\n\nBased on the tool result, give your final answer in JSON."

    raw2 = ask_gemini(conversation)
    print("\n📦 LLM raw reply (round 2):")
    print(raw2)

    parsed2 = parse_llm_response(raw2)
    print("\n✅ Parsed:")
    print(json.dumps(parsed2, indent=2))

    print("\n" + "=" * 70)
    if "answer" in parsed2:
        print(f"🎯 Final answer: {parsed2['answer']}")
    else:
        print("⚠️  LLM still wants more tools. That's why we need a LOOP (Step 7).")
    print("=" * 70)


if __name__ == "__main__":
    main()
