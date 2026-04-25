"""
FastAPI backend server for the YouTube Learning Companion Agent.

Endpoints:
  GET  /health        -> sanity check
  POST /run_agent     -> streams Server-Sent Events (SSE) with reasoning steps

CORS: allows any localhost origin and chrome-extension:// origins.

Run:
    cd /Users/vikasran/Documents/personal-data/vikash/EAG-V3/assignment-03/youtube_agent
    source .venv/bin/activate
    uvicorn backend.server:app --host 127.0.0.1 --port 8000 --reload
"""

import json
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.agent_runner import run_agent_streaming

app = FastAPI(title="YouTube Learning Companion Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class AgentQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    max_iterations: int = Field(default=8, ge=1, le=12)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "youtube-agent"}


async def _sse_stream(query: str, max_iterations: int) -> AsyncIterator[bytes]:
    """Wrap the synchronous generator in an async iterator that emits SSE frames."""
    for event in run_agent_streaming(query, max_iterations=max_iterations):
        payload = json.dumps(event, ensure_ascii=False)
        yield f"data: {payload}\n\n".encode("utf-8")


@app.post("/run_agent")
async def run_agent_endpoint(body: AgentQuery) -> StreamingResponse:
    return StreamingResponse(
        _sse_stream(body.query, body.max_iterations),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
