"""
Agentic loop over ai_pulse_server.py using Gemini.

The model picks tools from the AI Pulse MCP server one at a time:
  1. fetch_arxiv_papers       — get the latest AI/ML research
  2. fetch_cybersec_news      — get the latest threat news
  3. fetch_recent_cves        — get high-severity CVEs
  4. bookmark_item            — save the interesting ones (CRUD on local file)
  5. render_dashboard         — produce the final UI

We feed each tool result back into the prompt until the model emits FINAL_ANSWER.

Run:
    # Default task ("daily radar")
    python agent.py

    # Custom task
    python agent.py "Fetch top 5 cs.CR papers and bookmark anything about LLMs"

Env:
    GEMINI_API_KEY in a .env file alongside this script.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import os
import socketserver
import sys
import threading
import webbrowser
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_ITERATIONS = 30
LLM_SLEEP_SECONDS = 2
LLM_TIMEOUT = 30

# Local-only HTTP server for the dashboard. Bound to 127.0.0.1 (no external
# exposure) and serves only the data/ directory.
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
DATA_DIR = Path(__file__).parent / "data"

DEFAULT_TASK = (
    "Build me a daily AI/ML + cybersec radar:\n"
    "1) Fetch 3 latest arXiv papers in cs.LG.\n"
    "2) Fetch 3 latest cybersec articles from thehackernews.\n"
    "3) Fetch up to 2 HIGH-or-CRITICAL CVEs from the past 7 days.\n"
    "4) Bookmark each item with sensible tags ('ai-research' for papers, "
    "'cybersec' for news, 'cve' for CVEs). One bookmark_item call per item.\n"
    "5) IMPORTANT: After all bookmarks, call render_dashboard EXACTLY ONCE.\n"
    "6) Then emit FINAL_ANSWER with a one-line summary."
)


_api_key = os.getenv("GEMINI_API_KEY")
if not _api_key:
    print(
        "ERROR: GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.",
        file=sys.stderr,
    )
    sys.exit(1)
client = genai.Client(api_key=_api_key)


async def generate_with_timeout(prompt: str, timeout: int = LLM_TIMEOUT):
    """Run the blocking Gemini call in a thread with a timeout."""
    loop = asyncio.get_event_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(
            None,
            lambda: client.models.generate_content(model=MODEL, contents=prompt),
        ),
        timeout=timeout,
    )


def describe_tools(tools) -> str:
    lines = []
    for i, t in enumerate(tools, 1):
        props = (t.inputSchema or {}).get("properties", {})
        params = (
            ", ".join(f"{n}: {p.get('type', '?')}" for n, p in props.items())
            or "no params"
        )
        lines.append(f"{i}. {t.name}({params}) — {t.description or ''}")
    return "\n".join(lines)


def coerce(value: str, schema_type: str):
    """Convert a pipe-delimited string arg into the type the tool expects."""
    if schema_type == "integer":
        return int(value)
    if schema_type == "number":
        return float(value)
    if schema_type == "boolean":
        return value.lower() in ("true", "1", "yes")
    # Strings (and arrays / objects when the LLM passes JSON) are kept as-is;
    # our tools accept CSV strings rather than arrays to keep parsing simple.
    return value


async def main(task: str) -> None:
    server_params = StdioServerParameters(
        command="python",
        args=["ai_pulse_server.py"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected to AI Pulse MCP server")

            tools = (await session.list_tools()).tools
            tools_desc = describe_tools(tools)
            print(f"Loaded {len(tools)} tools\n")

            # Track what the agent actually does so we can auto-recover.
            called_render = False

            system_prompt = f"""You are AI Pulse — an autonomous research-and-threat agent.
Your job is to call tools ONE AT A TIME to gather information from the
internet, save the interesting items to a local bookmark store, and then
render a dashboard.

Available tools:
{tools_desc}

Respond with EXACTLY ONE line, in one of these two formats:
  FUNCTION_CALL: tool_name|arg1|arg2|...
  FINAL_ANSWER: <short natural-language summary of what you did>

Rules:
- Provide arguments in the exact order the tool declares them.
- For empty optional string args, pass an empty value (||).
- Tags must be a comma-separated string (no spaces around commas), e.g. "llm,security".
- Do not invent tools that are not listed above.
- After each FUNCTION_CALL you'll see the result; use it to choose the next step.
- When fetch_* returns JSON, parse the items mentally and bookmark them with
  the bookmark_item tool. Use the 'id', 'title', 'url' from each item, and
  pick a short summary.
- Always call render_dashboard exactly once near the end, BEFORE FINAL_ANSWER.
- Aim for the shortest sensible sequence of tool calls.
"""

            history: list[str] = []
            for iteration in range(1, MAX_ITERATIONS + 1):
                print(f"\n--- Iteration {iteration} ---")

                context = "\n".join(history) if history else "(no prior steps)"
                prompt = (
                    f"{system_prompt}\n"
                    f"Task: {task}\n\n"
                    f"Previous steps:\n{context}\n\n"
                    "What is your next single action?"
                )

                if LLM_SLEEP_SECONDS:
                    await asyncio.sleep(LLM_SLEEP_SECONDS)

                try:
                    response = await generate_with_timeout(prompt)
                except (FuturesTimeout, asyncio.TimeoutError):
                    print("LLM timed out — stopping.")
                    break
                except Exception as e:  # noqa: BLE001 - surfacing all upstream errors
                    print(f"LLM error: {e}")
                    break

                raw = (response.text or "").strip()
                # Take the first non-empty line that looks like one of our verbs
                line = next(
                    (
                        ln.strip()
                        for ln in raw.splitlines()
                        if ln.strip().startswith(("FUNCTION_CALL:", "FINAL_ANSWER:"))
                    ),
                    raw.splitlines()[0].strip() if raw.splitlines() else "",
                )
                print(f"LLM: {line}")

                if line.startswith("FINAL_ANSWER:"):
                    print("\n=== Agent done ===")
                    print(line)
                    break

                if not line.startswith("FUNCTION_CALL:"):
                    print("Unexpected response format — stopping.")
                    break

                _, call = line.split(":", 1)
                parts = [p.strip() for p in call.split("|")]
                func_name, raw_args = parts[0], parts[1:]

                tool = next((t for t in tools if t.name == func_name), None)
                if tool is None:
                    msg = f"Unknown tool {func_name!r}"
                    print(msg)
                    history.append(f"Iteration {iteration}: {msg}")
                    continue

                props = (tool.inputSchema or {}).get("properties", {})
                arguments = {
                    name: coerce(val, info.get("type", "string"))
                    for (name, info), val in zip(props.items(), raw_args)
                }

                print(f"→ {func_name}({arguments})")
                try:
                    result = await session.call_tool(func_name, arguments=arguments)
                    payload = (
                        result.content[0].text
                        if result.content and hasattr(result.content[0], "text")
                        else str(result)
                    )
                    if func_name == "render_dashboard":
                        called_render = True
                except Exception as e:  # noqa: BLE001
                    payload = f"ERROR: {e}"

                # Tool results can be huge JSON blobs; trim what we feed back.
                short = payload if len(payload) <= 1500 else payload[:1500] + "…[truncated]"
                print(f"← {short[:240]}{'…' if len(short) > 240 else ''}")
                history.append(
                    f"Iteration {iteration}: called {func_name}({arguments}) → {short}"
                )
            else:
                print("\nReached MAX_ITERATIONS without FINAL_ANSWER.")

            # Safety net: ensure the dashboard exists no matter how the loop ended.
            if not called_render:
                print("\nAgent did not call render_dashboard — doing it ourselves.")
                try:
                    await session.call_tool("render_dashboard", arguments={})
                except Exception as e:  # noqa: BLE001
                    print(f"Auto-render failed: {e}")


def serve_dashboard_forever() -> None:
    """Start a tiny local HTTP server for data/dashboard.html and open it.

    Bound to 127.0.0.1 only — never reachable from the network.
    Press Ctrl+C in the terminal to stop the server.
    """
    dashboard_path = DATA_DIR / "dashboard.html"
    if not dashboard_path.exists():
        print(
            f"\nNo dashboard found at {dashboard_path}. "
            "Did the agent call render_dashboard?",
        )
        return

    # Serve only the data/ directory (least-privilege: no other files exposed).
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(DATA_DIR)
    )

    # Be polite about a busy port — let the OS reuse if needed.
    socketserver.TCPServer.allow_reuse_address = True

    try:
        httpd = socketserver.TCPServer((DASHBOARD_HOST, DASHBOARD_PORT), handler)
    except OSError as e:
        print(
            f"\nCould not start dashboard server on port {DASHBOARD_PORT}: {e}\n"
            f"Open the file directly instead: file://{dashboard_path}"
        )
        return

    url = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/dashboard.html"
    print("\n" + "=" * 60)
    print(f"  Dashboard live at:  {url}")
    print("  Press Ctrl+C to stop the server.")
    print("=" * 60 + "\n")

    # Open the browser shortly after the server is up.
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard server stopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    user_task = " ".join(sys.argv[1:]).strip() or DEFAULT_TASK
    asyncio.run(main(user_task))
    # After the agentic loop finishes, expose the dashboard at a real URL.
    serve_dashboard_forever()
