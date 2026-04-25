"""
Streaming agent runner.
Generator version of the agent loop from step7 — yields events at each
step so the FastAPI server can stream them to the Chrome extension.
"""

import json
import os
import sys
from pathlib import Path
from typing import Iterator

import requests
from dotenv import load_dotenv

THIS_DIR = Path(__file__).resolve().parent
PARENT_DIR = THIS_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from step3_tools import TOOLS  # noqa: E402
from step5_robust_parsing import parse_llm_response  # noqa: E402

ENV_PATH = PARENT_DIR.parent / ".env"
load_dotenv(ENV_PATH)

API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("MODEL", "gemini-2.0-flash")


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
- Respond with ONLY the JSON. No prose around the JSON. No code fences.
- Plan multi-step: e.g. transcript -> summary, or transcript -> concepts -> arxiv.
- When passing a transcript to a tool, use the EXACT 'transcript' field
  from the previous get_video_transcript tool result.
- Once you have enough information, give the final answer.

FORMAT OF THE FINAL ANSWER (the value of "answer"):
The "answer" string MUST be well-formatted MARKDOWN, NOT a single
run-on paragraph. Specifically:
  - Use "## " section headings where it helps (e.g. "## Summary",
    "## Key Concepts", "## Related Papers").
  - Use "- " bullets or "1. ", "2. " numbered lists, ONE ITEM PER LINE.
  - Use **bold** to highlight names, terms, or final verdicts.
  - Use `code` for short technical terms.
  - For links, ALWAYS use markdown link syntax: [Title](URL).
  - Separate sections with a BLANK LINE so the popup can render
    paragraphs and lists correctly.
  - Keep it compact and scannable — no walls of text.

ESCAPING NEWLINES IN JSON:
Because your reply is a JSON object, real newlines inside the "answer"
string MUST be written as the two characters \\n. Example:
  {"answer": "## Summary\\n\\nThis video covers...\\n\\n## Papers\\n\\n1. [Paper A](https://...)\\n2. [Paper B](https://...)"}

SPECIAL RULE — QUIZ MODE (overrides the markdown format):
When the user wants a QUIZ, after calling generate_quiz, your final answer
MUST be a JSON object of the form:
  {"answer": "<the EXACT quiz JSON array string from the tool result>"}
That is, the value of "answer" must be the raw stringified JSON array
that came out of generate_quiz — no prose, no markdown, no fences.
The frontend will parse it and render an interactive quiz.
"""


def _ask_gemini(prompt: str) -> str:
    if not API_KEY:
        raise RuntimeError(f"GEMINI_API_KEY missing. Expected at {ENV_PATH}")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    r = requests.post(url, headers={"x-goog-api-key": API_KEY}, json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini error {r.status_code}: {r.text[:300]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _truncate(s: str, n: int = 600) -> str:
    return s if len(s) <= n else s[:n] + f"... [truncated, total {len(s)} chars]"


def _event(event_type: str, **payload) -> dict:
    return {"type": event_type, **payload}


def run_agent_streaming(user_query: str, max_iterations: int = 8) -> Iterator[dict]:
    """
    Generator that yields reasoning events as the agent runs.
    Each event is a dict with a 'type' field, e.g.:
      {"type": "start", "query": "..."}
      {"type": "iteration", "n": 1}
      {"type": "llm_decision", "raw": "...", "parsed": {...}}
      {"type": "tool_call", "tool_name": "...", "tool_arguments": {...}}
      {"type": "tool_result", "tool_name": "...", "result_preview": "..."}
      {"type": "final_answer", "answer": "..."}
      {"type": "error", "message": "..."}
      {"type": "done"}
    """
    yield _event("start", query=user_query)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]

    for iteration in range(1, max_iterations + 1):
        yield _event("iteration", n=iteration)

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

        try:
            raw = _ask_gemini(prompt)
        except Exception as e:
            yield _event("error", message=f"LLM call failed: {e}")
            yield _event("done")
            return

        try:
            parsed = parse_llm_response(raw)
        except ValueError as e:
            yield _event("parse_error", raw=_truncate(raw, 400), message=str(e))
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {"role": "user", "content": "Please respond with valid JSON only."}
            )
            continue

        yield _event("llm_decision", raw=_truncate(raw, 500), parsed=parsed)

        if "answer" in parsed:
            yield _event("final_answer", answer=parsed["answer"])
            yield _event("done")
            return

        if "tool_name" not in parsed:
            yield _event(
                "warning", message="LLM reply lacked tool_name and answer; retrying."
            )
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

        yield _event(
            "tool_call",
            tool_name=tool_name,
            tool_arguments_keys=list(tool_args.keys()),
        )

        if tool_name not in TOOLS:
            err = f"Unknown tool '{tool_name}'. Available: {list(TOOLS)}"
            yield _event("tool_result", tool_name=tool_name, result_preview=err, error=True)
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "tool", "content": json.dumps({"error": err})})
            continue

        try:
            tool_result = TOOLS[tool_name](**tool_args)
        except Exception as e:
            tool_result = json.dumps({"error": f"Tool '{tool_name}' raised: {e}"})

        yield _event(
            "tool_result",
            tool_name=tool_name,
            result_preview=_truncate(tool_result, 600),
        )

        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "tool", "content": tool_result})

    yield _event("error", message="Max iterations reached without final answer.")
    yield _event("done")
