"""
make_llm_logs.py
================
Runs the agent loop on a handful of representative queries and writes a
clean, paste-ready log file (`llm_logs.txt`) for the assignment submission.

What gets logged for every query:
  - The user query
  - For each iteration:
      * The full prompt sent to the LLM (so the grader sees that ALL past
        history is being passed each turn)
      * The raw LLM reply
      * The parsed JSON
      * Tool call + arguments (if any)
      * Tool result (truncated for readability)
  - The final answer

Run:
    source .venv/bin/activate
    python make_llm_logs.py

Then submit the generated `llm_logs.txt`.
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

from step3_tools import TOOLS
from step5_robust_parsing import parse_llm_response
from backend.agent_runner import SYSTEM_PROMPT

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH)

API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("MODEL", "gemini-2.0-flash")

if not API_KEY:
    sys.exit(f"GEMINI_API_KEY not found. Expected in {ENV_PATH}")

OUT_PATH = Path(__file__).resolve().parent / "llm_logs.txt"


def ask_gemini(prompt: str) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{MODEL}:generateContent"
    )
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    r = requests.post(
        url, headers={"x-goog-api-key": API_KEY}, json=body, timeout=120
    )
    if r.status_code != 200:
        raise RuntimeError(f"Gemini error {r.status_code}: {r.text[:300]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"... [truncated, total {len(s)} chars]"


def run_agent_logged(user_query: str, fh, max_iterations: int = 8) -> None:
    bar = "=" * 78

    fh.write(f"\n{bar}\n")
    fh.write(f"USER QUERY:\n{user_query}\n")
    fh.write(f"{bar}\n")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]

    for iteration in range(1, max_iterations + 1):
        fh.write(f"\n--- Iteration {iteration} ---\n")

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

        fh.write(
            "\n[PROMPT SENT TO LLM] (full conversation history, "
            f"{len(messages)} message(s), {len(prompt)} chars total)\n"
        )
        fh.write(truncate(prompt, 4000))
        fh.write("\n")

        try:
            raw = ask_gemini(prompt)
        except Exception as e:
            fh.write(f"\n[LLM ERROR] {e}\n")
            return

        fh.write(f"\n[LLM RAW REPLY]\n{raw.strip()}\n")

        try:
            parsed = parse_llm_response(raw)
        except ValueError as e:
            fh.write(f"\n[PARSE ERROR] {e}\n")
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {"role": "user", "content": "Please respond with valid JSON only."}
            )
            continue

        fh.write(f"\n[LLM PARSED] {json.dumps(parsed)[:400]}\n")

        if "answer" in parsed:
            fh.write("\n[FINAL ANSWER]\n")
            fh.write(parsed["answer"])
            fh.write("\n")
            return

        if "tool_name" not in parsed:
            fh.write("\n[WARN] reply has neither tool_name nor answer; retrying.\n")
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": "Reply must include either tool_name or answer.",
                }
            )
            continue

        tool_name = parsed["tool_name"]
        tool_args = parsed.get("tool_arguments", {}) or {}

        # Avoid logging giant transcripts as arg values.
        printable_args = {
            k: (truncate(v, 120) if isinstance(v, str) else v)
            for k, v in tool_args.items()
        }
        fh.write(
            f"\n[TOOL CALL] {tool_name}({json.dumps(printable_args)[:400]})\n"
        )

        if tool_name not in TOOLS:
            err = f"Unknown tool '{tool_name}'. Available: {list(TOOLS)}"
            fh.write(f"[TOOL ERROR] {err}\n")
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {"role": "tool", "content": json.dumps({"error": err})}
            )
            continue

        try:
            tool_result = TOOLS[tool_name](**tool_args)
        except Exception as e:
            tool_result = json.dumps({"error": f"Tool '{tool_name}' raised: {e}"})

        fh.write(f"\n[TOOL RESULT]\n{truncate(tool_result, 800)}\n")

        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "tool", "content": tool_result})

    fh.write("\n[MAX ITERATIONS REACHED — no final answer]\n")


def main() -> None:
    test_url = "https://www.youtube.com/watch?v=PeMlggyqz0Y"

    queries = [
        # 1) Single-tool path: metadata only.
        f"What is the title and channel of this video? {test_url}",
        # 2) Two-tool path: transcript -> summary.
        f"Give me a short summary of this video: {test_url}",
        # 3) Multi-tool / multi-step: transcript -> concepts -> arxiv search.
        (
            f"Watch this video: {test_url}\n"
            "Then: (1) summarise it, (2) tell me 3 key concepts, "
            "(3) find one related arXiv paper for the most important concept."
        ),
    ]

    with OUT_PATH.open("w", encoding="utf-8") as fh:
        fh.write("YouTube Learning Companion Agent — LLM Interaction Logs\n")
        fh.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
        fh.write(f"Model: {MODEL}\n")
        fh.write(f"Total queries: {len(queries)}\n")

        fh.write("\n" + "#" * 78 + "\n")
        fh.write("SYSTEM PROMPT (sent on EVERY iteration as part of full history)\n")
        fh.write("#" * 78 + "\n")
        fh.write(SYSTEM_PROMPT.strip() + "\n")

        for i, q in enumerate(queries, 1):
            fh.write("\n\n" + "#" * 78 + "\n")
            fh.write(f"# QUERY {i}/{len(queries)}\n")
            fh.write("#" * 78 + "\n")
            print(f"[{i}/{len(queries)}] running: {q[:70]}...")
            try:
                run_agent_logged(q, fh, max_iterations=8)
            except Exception as e:
                fh.write(f"\n[FATAL ERROR running this query] {e}\n")

    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"\nWrote logs to: {OUT_PATH}  ({size_kb:.1f} KB)")
    print("Open it, copy the contents, and paste into your submission.")


if __name__ == "__main__":
    main()
