"""
Step 7: The Full Agent Loop
============================
A complete `run_agent(user_query)` function that:
  - Builds a conversation history (every turn remembered).
  - Calls Gemini, parses its reply.
  - If reply is a tool call: runs the tool, appends result to history, loops.
  - If reply is a final answer: returns it.
  - Stops after MAX_ITERATIONS as a safety net.

Every step prints a visible REASONING CHAIN so you (and your Chrome
extension UI later) can see exactly what the agent did.

Run:
    source .venv/bin/activate
    python step7_agent_loop.py
"""

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from step3_tools import TOOLS
from step5_robust_parsing import parse_llm_response

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
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
   IMPORTANT: Most other tools need a transcript first.

3. summarize_video(transcript: str)
   Generates a concise summary of the transcript.

4. extract_key_concepts(transcript: str)
   Returns a JSON list of important concepts from the transcript.

5. search_arxiv_papers(concept: str)
   Searches arXiv for academic papers on a given concept.

6. generate_quiz(transcript: str)
   Creates 5 MCQs to test understanding of the transcript.

You MUST respond in ONE of these two JSON formats:

If you need to use a tool:
{"tool_name": "<name>", "tool_arguments": {"<arg_name>": "<value>"}}

If you have the final answer:
{"answer": "<your final answer>"}

RULES:
- Respond with ONLY the JSON. No prose. No markdown.
- Plan multi-step: e.g. transcript -> summary, or transcript -> concepts -> arxiv.
- When passing a transcript to a tool, use the EXACT 'transcript' field
  from the previous get_video_transcript tool result.
- Once you have enough information, give the final answer.
"""


# ---------------------------------------------------------------------------
def ask_gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    r = requests.post(url, headers={"x-goog-api-key": API_KEY}, json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini error {r.status_code}: {r.text}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _truncate(s: str, n: int = 400) -> str:
    return s if len(s) <= n else s[:n] + f"... [truncated, total {len(s)} chars]"


# ---------------------------------------------------------------------------
def run_agent(user_query: str, max_iterations: int = 8) -> dict:
    """
    Run the agent loop until it produces a final answer or hits max_iterations.
    Returns a dict with:
      - 'final_answer': str | None
      - 'reasoning_chain': list of step dicts (the visible trace)
    """
    print("\n" + "=" * 75)
    print(f"🟢 USER QUERY: {user_query}")
    print("=" * 75)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]
    reasoning_chain: list[dict] = []

    for iteration in range(1, max_iterations + 1):
        print(f"\n--- Iteration {iteration} ---")

        # 1. Build a single prompt string from message history.
        prompt_parts = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                prompt_parts.append(content)
            elif role == "user":
                prompt_parts.append(f"User: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
            elif role == "tool":
                prompt_parts.append(f"Tool Result: {content}")
        prompt = "\n\n".join(prompt_parts)

        # 2. Call the LLM.
        try:
            raw = ask_gemini(prompt)
        except Exception as e:
            print(f"❌ LLM call failed: {e}")
            reasoning_chain.append({"iteration": iteration, "error": str(e)})
            break

        print(f"🧠 LLM raw: {_truncate(raw, 250)}")

        # 3. Parse the reply.
        try:
            parsed = parse_llm_response(raw)
        except ValueError as e:
            print(f"⚠️  Parse failed ({e}). Asking the LLM to retry with valid JSON.")
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "Please respond with valid JSON only."})
            reasoning_chain.append({
                "iteration": iteration,
                "type": "parse_error",
                "raw": raw,
            })
            continue

        # 4a. Final answer? -> return.
        if "answer" in parsed:
            print(f"\n✅ FINAL ANSWER reached on iteration {iteration}")
            reasoning_chain.append({
                "iteration": iteration,
                "type": "final_answer",
                "answer": parsed["answer"],
            })
            print("\n" + "=" * 75)
            print(f"🎯 ANSWER:\n{parsed['answer']}")
            print("=" * 75)
            return {
                "final_answer": parsed["answer"],
                "reasoning_chain": reasoning_chain,
            }

        # 4b. Tool call?
        if "tool_name" not in parsed:
            print("⚠️  Reply has neither 'tool_name' nor 'answer'. Asking again.")
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "Reply must include either tool_name or answer."})
            continue

        tool_name = parsed["tool_name"]
        tool_args = parsed.get("tool_arguments", {})

        print(f"🔧 TOOL CALL: {tool_name}({list(tool_args.keys())})")

        if tool_name not in TOOLS:
            err = f"Unknown tool '{tool_name}'. Available: {list(TOOLS)}"
            print(f"⚠️  {err}")
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "tool", "content": json.dumps({"error": err})})
            reasoning_chain.append({
                "iteration": iteration,
                "type": "unknown_tool",
                "tool_name": tool_name,
            })
            continue

        # 5. Execute the tool.
        try:
            tool_result = TOOLS[tool_name](**tool_args)
        except Exception as e:
            tool_result = json.dumps({"error": f"Tool '{tool_name}' raised: {e}"})

        print(f"📥 TOOL RESULT: {_truncate(tool_result, 300)}")

        reasoning_chain.append({
            "iteration": iteration,
            "type": "tool_call",
            "tool_name": tool_name,
            "tool_arguments_keys": list(tool_args.keys()),
            "tool_result_preview": _truncate(tool_result, 300),
        })

        # 6. Append both the assistant turn and the tool result to history.
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "tool", "content": tool_result})

    print("\n⏹  Max iterations reached without a final answer.")
    return {
        "final_answer": None,
        "reasoning_chain": reasoning_chain,
    }


# ---------------------------------------------------------------------------
def main() -> None:
    test_url = "https://www.youtube.com/watch?v=PeMlggyqz0Y"

    queries = [
        f"What is the title and channel of this video? {test_url}",
        f"Give me a short summary of this video: {test_url}",
        (
            f"Watch this video: {test_url}\n"
            "Then: (1) summarise it, (2) tell me 3 key concepts, "
            "(3) find one related arXiv paper for the most important concept."
        ),
    ]

    for q in queries:
        result = run_agent(q, max_iterations=8)
        print(f"\n📊 Reasoning chain had {len(result['reasoning_chain'])} step(s).\n")
        print("#" * 75)


if __name__ == "__main__":
    main()
